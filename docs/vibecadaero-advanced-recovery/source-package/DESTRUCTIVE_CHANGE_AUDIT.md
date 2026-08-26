# Destructive-Change Audit — Host Analysis Runtime Extraction

**Design anchor:** `halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`  
**Live drift checked:** `main@24fe48bb3fdcb84b558d34e23fedb0988ee4e548`  
**Upstream writes:** none

## Executive finding

A host-owned Analysis Runtime is still the correct target, but the safe extraction boundary is narrower than an ordinary scheduler refactor.

The current FEM path contains two kinds of code that happen to be adjacent:

1. **generic execution mechanics** — queueing, lifecycle, progress, cancellation, timeout, process supervision, generic artifact/log bookkeeping, provider submission/reconnection, main-thread publication scheduling; and
2. **FEM engineering semantics** — which document state defines a solve, exact solver settings, analysis ownership, mesh membership, result-root replacement, History semantics, stale-result identity, solver-specific import, and transactional result publication.

Only the first category may move into a generic host service. The second must remain owned by the FEM adapter/domain.

## Hard DO-NOT-EXTRACT boundary

The following are not generic job-runtime responsibilities and SHALL NOT be generalized by copying their current implementation into a host scheduler:

- `compute_solver_input_state()` and every semantic dependency it includes;
- FEM solver settings identity and implementation identity;
- analysis membership/ownership resolution;
- FEM mesh validity and solver/mesh association rules;
- result-root selection/removal rules;
- `vibecad_fem_history` role/owner/child semantics;
- solver-specific preparation (Elmer/CalculiX/Z88);
- solver-specific result import;
- result-object state hashes and currentness semantics;
- History event `solve` semantics;
- FEM transaction names, visible errors, and current capability identity;
- FEM claim/interpretation semantics.

The generic runtime may call a FEM-owned callback/adapter that computes or validates these things. It may never redefine them.

## Stable external seams

### `analyze.solver_execution`

This remains the FEM domain capability. Migration SHALL preserve:

- capability name;
- target schema;
- native domain/action identity;
- transaction behavior;
- visible error family;
- request modes;
- current response envelope, including background `job` and `next` fields.

The generic host runtime sits beneath this binding. Existing clients should not know a migration occurred.

### `native.job`

This remains the existing lifecycle-control surface during the strangler migration. Its current status/cancel behavior is compatibility-critical. New host-wide job discovery or recovery APIs, if needed later, are **additive** and cannot silently alter current document-guarded semantics.

## Concrete hazards found in the frozen implementation

### H1 — accepted cancellation can race into commit

The current background manager checks cancellation, validates the runtime context, checks cancellation again, and only afterward changes phase to finalizing/committing. `cancel()` can acquire the manager lock in the gap between that final check and the phase transition, return `cancel_accepted: true`, and the queued main-thread callback can still proceed into document mutation.

**Required correction:** an atomic commit gate under the same lifecycle lock:

1. reject if terminal;
2. if cancellation was requested, terminalize/decline commit;
3. otherwise transition to a non-cancellable `committing` phase **before releasing the lock**;
4. only then begin document mutation.

After the gate wins, cancellation must return `cancel_accepted: false`. Before the gate wins, accepted cancellation must make commit impossible.

This is a migration blocker.

### H2 — current stop helper controls only the direct `Popen`

The frozen helper calls `terminate()`/`kill()`/`wait()` on the direct process. That is a verified source fact. Whether solver launchers, MPI ranks or helpers actually survive depends on platform/process behavior and must be characterized rather than assumed.

**Required treatment:** add child-spawning process tests on supported platforms. If descendants survive, land process-group/tree ownership as a dedicated correctness PR before provider extraction. The target provider should then own the complete launched process tree, but the architectural extraction must not hide that behavior change.

### H3 — current jobs are in-memory only

Manager records contain live threading primitives, futures, callbacks, and request context. They are not a durable restart format.

**Required correction:** persistence uses a separate durable descriptor. Raw FreeCAD/Python runtime objects are never serialized.

### H4 — prepared FEM requests contain live FreeCAD objects

The current detached FEM request is suitable for the current in-process design but not as a durable cross-restart payload.

**Required correction:** split:

- **durable prepared-work descriptor** — stable IDs, hashes, paths/artifact IDs, solver/provider metadata;
- **ephemeral execution handle** — live document/analysis/solver objects, callbacks, futures, cancellation events, process handles.

Publication rebinds and validates live objects on the document thread.

### H5 — document lifecycle is part of correctness

Current runtime-context validation protects against committing into the wrong/reopened/changed document using document name, document UID, revision seed, and exact-active-document checks.

**Required correction:** preserve this behavior exactly for the FEM migration. A closed, switched, or revised document blocks attachment. Results/artifacts may be retained as historical evidence, but no automatic stale CAD mutation is allowed.

### H6 — cleanup must be exactly-once in effect, not exactly-once in invocation

Workers, cancellation, failures, commit exceptions, shutdown, and recovery can all converge on cleanup.

**Required correction:** cleanup operations are idempotent and recorded. Temporary directories, provider resources, readers, and local processes must tolerate repeated reconciliation without double-delete or leaked resources.

## What may be extracted

The safe generic host-owned core may own:

- job IDs;
- lifecycle transition validation;
- queue admission and concurrency policy;
- generic progress/log/event records;
- cancellation intent and atomic commit gate;
- timeout policy plumbing;
- provider interface and provider job identity;
- local process supervision mechanics;
- durable job metadata;
- artifact-manifest bookkeeping and hashes;
- restart reconciliation;
- scheduling a domain-owned publication callback on the document thread;
- generic structured failure classification.

## What must remain adapter-owned

Every engineering domain supplies:

- `prepare()` — document-thread conversion from live domain state to immutable/durable work;
- `fingerprint()` / currentness descriptor — domain meaning only;
- `execute specification` — solver bundle/config, not location policy;
- `validate_before_publish()` — rebind + exact domain currentness check;
- `parse()` — solver-output interpretation;
- `publish()` — document-thread domain mutation transaction;
- `qualify()` — engineering evidence/claim decision;
- `cleanup_domain()` — any domain-specific temporary/resource semantics.

## Destructive migration anti-patterns prohibited

- Renaming `analyze.solver_execution` to a generic capability.
- Moving FEM state hashing into the runtime core.
- Replacing `native.job` before parity is proven.
- Persisting pickled FreeCAD objects/callbacks.
- Letting worker threads mutate FreeCAD documents.
- Treating process return code as engineering qualification.
- Auto-importing outputs after host restart without exact source-currentness revalidation.
- Deleting old FEM modules before call-graph and behavioral parity gates pass.
- Introducing Aero-only scheduling beside the host runtime.
- Generalizing one-active-job-per-document policy without first characterizing existing callers/tests.

## Implementation disposition

The extraction is approved as a **strangler migration target**, not as a rewrite. The existing FEM path is the characterization oracle until parity passes. Aero becomes the second consumer only after FEM proves the host runtime.


### H7 — FEM input generation is currently outside the background worker

`NativeAnalyzeSolverExecutionRuntime.execute()` calls `prepare_solver_execution_request()` before `NativeBackgroundManager.submit()`. The worker runs the already-prepared detached request. Moving input writers/live-object traversal into the worker during extraction would alter threading/FreeCAD behavior. Preserve first; optimize only in a separate tested change.

### H8 — durable publication cannot reuse serialized submission authority

Current FEM commits through the original `NativeCallTicket` and live runtime context; the ticket freezes a host structural revision at submission. That strict behavior is part of the FEM compatibility oracle. But serializing/replaying that ticket/context after restart would turn a historical submission into standing future mutation permission, while applying the global revision rule forever to Aero could also over-invalidate long CFD after unrelated edits.

**Required treatment:** preserve current FEM original-ticket/global-revision semantics during extraction. After persistence is stable, add a separate host publication coordinator. Persist inert `SubmissionAuthorization`/`PublicationDescriptor` provenance only. Durable Aero publication requires exact `Document.Uid` rebind, exact completed job/output identity, domain `CurrentnessReport`, replay/idempotency check and fresh Native mutation authorization on the document thread. Domain currentness never substitutes for host mutation authority.
