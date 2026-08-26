# Host Analysis Runtime — Non-Destructive Migration Plan

## Executive decision

This is a foundational VibeCAD change and must be implemented as a sequence of small, reversible compatibility-preserving extractions.

**Do not write a new scheduler beside the old systems. Do not rewrite FEM. Do not migrate Aero first.**

The migration starts from two existing working host seeds:

- **Native Background** — generic-ish orchestration/status/cancel shell, currently ephemeral and intentionally bounded;
- **Detached FEM execution** — proven immutable-input/process/stale-publication mechanics, currently FEM-specific.

The target is one VibeCAD Analysis Runtime beneath both FEM and Aero.

---

## 1. Why this requires architectural surgery rather than copying code

The current code has responsibilities interleaved at several levels:

1. `VibeCADNativeBackground.py` mixes a broadly reusable job lifecycle with Native-specific error wrapping, one-job-per-document policy and in-memory retention.
2. `VibeCADNativeBackgroundRuntime.py` exposes the document-scoped `native.job` control capability with `status` and `cancel`; FEM job creation occurs through `analyze.solver_execution` operation `run`.
3. `VibeCADNativeAnalyzeSolverExecutionProcess.py` is almost entirely generic local process mechanics but its names/errors are FEM/Native Analyze flavored.
4. `VibeCADNativeAnalyzeSolverExecution.py` deliberately combines domain preparation/import with generic detached workdir/input hashing/execution orchestration.
5. `VibeCADNativeAnalyzeSolverState.py` is correctly FEM-specific and must remain so.
6. `NativeMutationBoundary` is a separate host authority for publication and must remain separate.

The rewrite is safe only if those responsibilities are separated **without changing observable FEM behavior first**.

---

## 2. Architectural target modules

Recommended host namespace (names may adapt to upstream conventions after the next freeze):

```text
src/Mod/VibeCAD/
  VibeCADAnalysisContracts.py
  VibeCADAnalysisDependencies.py
  VibeCADAnalysisArtifacts.py
  VibeCADAnalysisProviders.py
  VibeCADAnalysisLocalProvider.py
  VibeCADAnalysisRuntime.py
  VibeCADAnalysisJobs.py
  VibeCADAnalysisPersistence.py       # later PR, not initial extraction
  VibeCADAnalysisPublication.py
```

Compatibility modules remain:

```text
VibeCADNativeBackground.py
VibeCADNativeBackgroundRuntime.py
VibeCADNativeAnalyzeSolverExecutionProcess.py
VibeCADNativeAnalyzeSolverExecution.py
```

During migration these call/re-export the new host internals. External Python imports and Native surface do not move all at once.

### Dependency direction

```text
VibeCADAnalysis* host layer
       ▲           ▲
       │           │
FEM adapters    Aero adapters
       │           │
FEM domain      VibeCADAero domain
```

Forbidden:

- `VibeCADAnalysis*` importing VibeCADAero;
- `VibeCADAnalysis*` importing specific CalculiX/Elmer/OpenFOAM/FluidX3D classes;
- generic provider choosing a physics solver;
- Aero calling FEM internals directly;
- worker execution importing live document objects as job state.

---

## 3. Migration sequence — small PRs, each independently revertible

### PR A — Characterization only

**Behavioral change: none.**

Add black-box/characterization tests around current behavior before moving a line of execution code.

Capture for each currently supported FEM solver path:

- prepared working-directory shape;
- command tuple/argv;
- relevant environment values;
- input count and SHA-256 algorithm behavior;
- timeout mapping;
- progress-stage order/ranges;
- cancellation before launch and during run;
- process failure/stdout-stderr tail behavior;
- exact source-state stale rejection;
- History stale rejection;
- KeepResultsOnReRun stale rejection;
- result graph/root/resource insertion;
- NativeMutationDraft identities;
- receipt behavior;
- public job-creation response and `native.job` status/cancel response shape;
- public error codes.

No new service. No rename. No persistence. No concurrency change.

**Merge gate:** all existing tests + new characterization tests green on supported CI platforms.

### PR B — Add contracts and adapter ports, old implementation underneath

Add pure data contracts/interfaces only. Wrap current FEM execution into an adapter that still invokes the old implementation.

The objective is to establish dependency direction and type boundaries without moving behavior.

**Merge gate:** byte/semantic parity of prepared command/input/hash outputs; no public API change.

### PR C — Extract local process execution behind compatibility facade

Move physics-neutral logic from `VibeCADNativeAnalyzeSolverExecutionProcess.py` into `VibeCADAnalysisLocalProvider.py`.

The old module remains and delegates to the new implementation, preserving:

- current Windows process flags;
- child-tree cleanup behavior;
- polling cadence semantics unless tests prove independence;
- timeout/cancel error mapping;
- bounded output tails;
- argv/no-shell behavior.

**Do not** touch input generation, solver state, result import, NativeBackground, or persistence in this PR.

**Rollback:** restore facade call to old implementation; no durable data migration exists.

### PR D — Extract artifact/input sealing primitives

Move generic filesystem validation/hash/manifest behavior out of FEM execution.

Preserve current FEM SHA behavior exactly through compatibility mode before introducing richer artifact manifests. New manifest fields may be added without changing the digest used by existing FEM stale checks.

Security tests cover:

- symlinks;
- traversal;
- file-count/byte bounds;
- large-file streaming;
- deterministic ordering;
- path normalization;
- partial/corrupt inputs.

No scheduler changes in this PR.

### PR E — Extract host runtime orchestration from NativeBackground without changing public policy

Introduce the new runtime state machine internally, but make the current `NativeBackgroundManager` facade preserve:

- one active job per document;
- in-memory retention limit/behavior;
- `native.job` public shape;
- current prepare→worker→commit semantics;
- cancellation behavior.

The generic runtime may model more states internally, but the facade maps them back to the current surface until a separately reviewed API evolution is approved.

No durable persistence yet.

### PR F — Migrate FEM to the host runtime one solver family at a time

Order recommendation:

1. CalculiX path with the strongest current test coverage;
2. alternate CalculiX/ccx_tools path;
3. Elmer;
4. Z88;
5. Mystran.

For each solver:

- generate identical inputs;
- produce identical command/env;
- run through generic LocalProcessProvider;
- parse with existing importer;
- publish through existing Native mutation path;
- compare result graph and receipts;
- keep compatibility functions/modules.

Do not migrate all solvers in one PR unless they are literally thin mappings and characterization proves parity.

### PR G — Stabilization / no-feature interval

Run the host runtime with FEM as its only real engineering client for at least one release/merge interval or equivalent test soak before adding Aero as a dependency.

Fix lifecycle bugs here rather than while simultaneously adding CFD.

### PR H — Add durable job metadata as a separate subsystem

Only after in-memory parity is proven, add persistence.

Recommended:

- per-user SQLite database under VibeCAD app-data;
- immutable artifact storage outside FCStd;
- schema version + migrations;
- provider IDs for reconnect;
- atomic state transitions;
- explicit orphan/recovery semantics.

Initial persisted version should preserve one-active-job-per-document unless concurrency is deliberately changed later.

Persistence must be optically transparent to existing FEM UX: no mandatory new user controls.

### PR H2 — Add durable publication coordinator after persistence, before production Aero

Add inert `SubmissionAuthorization`/`PublicationDescriptor` records and a host publication coordinator that:

- never serializes a live Native ticket/context/callback as authority;
- rebinds exact `Document.Uid` / `document_uid`;
- validates the exact successful job/output-manifest identity;
- invokes domain currentness validation;
- obtains fresh Native mutation authorization on the document thread;
- publishes idempotently using an exact publication identity/receipt;
- exposes `AWAITING_SOURCE`, `AWAITING_PUBLICATION`, and `STALE`/`QUARANTINED` without treating successful compute as failed.

Do **not** migrate FEM to these new publication semantics in this PR. The existing FEM original-ticket/global-revision publication path remains the compatibility reference until this coordinator is independently proven.

### PR I — Provider interface + LocalProcessProvider becomes canonical

By this point local execution is the reference provider and existing FEM uses it. Formalize provider capability descriptors and reconnect/cancel semantics.

Do not add Kaggle in the same PR.

### PR J — Aero becomes the second domain client

Implement Aero adapters:

- OpenFOAM/CfdOF preparation/result parsing;
- FluidX3D preparation/result parsing;
- Aero dependency snapshot;
- Aero currentness resolver;
- Aero publication/evidence adapter.

Aero uses the host job identity/lifecycle/provider/artifact store. `AeroJobStore` becomes a compatibility/migration reader or is retired after proving no external dependency relies on it.

### PR K — Remote/Kaggle provider

Add Kaggle only after local provider and durable provider job identity/reconnect are stable.

This PR must not alter aerodynamic solver semantics. It changes execution location only.

### PR L — Remove obsolete internals after external-import audit

Only after:

- repository-wide import search;
- downstream/internal docs search;
- compatibility release window;
- all tests passing;
- no user-facing schema dependency;

may old duplicate implementation code be deleted. Compatibility modules may remain as thin re-exports indefinitely if cheap.

---

## 4. FEM must be the proving client

Aero is not permitted to become the first validation of the generic runtime.

Why:

- FEM already works in upstream;
- current test behavior provides a baseline;
- a generic runtime that cannot reproduce existing FEM exactly is not mature enough to host expensive CFD;
- this prevents Aero needs from distorting the host abstraction before the host contract is proven.

The success criterion is not “new code passes its own unit tests.” It is:

> **Existing FEM behavior remains observationally equivalent while its execution mechanics move behind the new host seam.**

---

## 5. What must remain domain-specific

Do not genericize these merely because they participate in jobs:

### FEM

- `SOLVER_SPECS`;
- solver-specific property canonicalization;
- `PreparedSolverTarget` semantics;
- CalculiX/Elmer/Z88/Mystran input writers/importers;
- FEM result graph semantics;
- FEM qualification/analysis interpretation.

### Aero

- `AeroCase`;
- geometry/body/solver frame semantics;
- reference quantities;
- atmosphere/boundary conditions;
- mesh readiness rules;
- OpenFOAM/FluidX3D solver configuration;
- force/field/coefficient parsing;
- aerodynamic qualification envelope;
- CFD currentness dependencies.

The host runtime sees immutable descriptions and artifacts, not the engineering rules themselves.

---

## 6. FreeCAD document integrity and publication

### Frozen FEM preparation boundary

At `df07a5e`, `NativeAnalyzeSolverExecutionRuntime.execute()` prepares the FEM `SolverExecutionRequest` **before** submitting the background job. The background worker receives a sealed request and runs the solver. Therefore extraction must not move existing FEM input writers/live-object traversal into a worker thread as an incidental cleanup. Preserve that boundary first. If a future optimization splits main-thread domain capture from detached file materialization, it is a separately tested change.


### Prepare phase

The domain adapter prepares on the document thread if it needs live FreeCAD. It must finish by sealing files and serializable dependency records.

After sealing, worker execution cannot rely on mutable live FreeCAD objects.

### Execute phase

Runs off the document thread. External solver execution, provider transport and pure parsing are detached.

### Revalidation phase

Returns to the document thread and asks the domain to resolve current dependencies.

### Publication phase

If current:

- domain builds a publication draft;
- host invokes existing Native mutation transaction/boundary;
- recompute/validation/receipt behavior remains host-owned.

If stale:

- do not mutate current document;
- preserve successful solver artifacts/result as historical/quarantined evidence;
- expose why it is stale and what dependency changed;
- allow explicit future compare/re-run workflow rather than silently discarding compute.

Publication failure cannot leave half-created result objects. Existing mutation rollback semantics remain authoritative.

---

## 7. Dependency currentness: avoid both under- and over-invalidation

A naive global document revision check can invalidate a four-hour CFD solve because a user renamed an unrelated spreadsheet. A too-narrow check can attach a result to changed geometry.

The target therefore supports **domain-contributed dependency fingerprints**, while migration begins by preserving current FEM exact checks.

### Aero currentness recommendation

Required current dependencies should include at minimum:

- analyzed object/body identity;
- geometry content/revision;
- CAD→body frame;
- Aero case/config digest;
- aerodynamic references;
- atmosphere;
- solver settings/version;
- mesh/voxel settings and resulting mesh hash;
- moving/propulsion state when used.

Host structural revision may be stored for provenance and used when the operation truly depends on all structural state, but should not automatically be the sole invalidation rule for every CFD job.

### Stale classification should explain itself

A `CurrentnessReport` should identify changed dependency keys so the user/agent can distinguish:

- geometry changed;
- only solver settings changed;
- atmosphere changed;
- unrelated host edit;
- source document missing;
- source identity ambiguous.

---

## 8. Persistence, restart and recovery

Do not combine persistence with process extraction.

When durability is added:

### Metadata

Use a transactional local database. SQLite is recommended because it is standard-library, inspectable, transactional and avoids another service dependency.

### Artifacts

Use immutable/content-addressed or job-addressed directories outside FCStd. The database stores manifest references, not large blobs.

### Document

FCStd may store compact provenance/result references necessary for document portability and evidence, but not gigabytes of raw fields.

### Restart

Provider-specific recovery:

- local process with no reconnect proof -> `ORPHANED`;
- remote provider with stable ID -> reconnect/status/collect;
- completed artifact set -> validate manifest before considering solved;
- missing/corrupt artifact -> explicit integrity failure;
- closed/missing source document -> retain unattached job/result.

Never infer success from the mere existence of an output filename.

---

## 9. Concurrency strategy

Do not change concurrency while extracting architecture.

Initial migration preserves current **one active job per document** behavior even though the generic runtime can eventually support more.

After stability, concurrency can become an explicit product/engineering change with rules for:

- independent analyses in one document;
- provider resource limits;
- result publication ordering;
- per-document vs global queues;
- cancellation and priority;
- simultaneous read-only compute from one frozen source;
- memory/disk/GPU pressure.

This separation avoids blaming runtime extraction for concurrency bugs.

---

## 10. Document lifecycle angles

Explicitly test and define:

- document close while PREPARING;
- document close while RUNNING;
- document reopen with same stable UID;
- Save As / copy semantics;
- duplicate document labels;
- document deletion;
- source object deletion/suppression;
- undo/redo after job submission;
- Native structural revision changes;
- branch/history changes;
- application shutdown during local/remote jobs.

Display names are never authority. Stable IDs + content dependencies are.

---

## 11. Artifact/storage lifecycle

Artifact lifecycle must be explicit and non-destructive.

- Never silently delete a successful result before publication/inspection.
- Cancellation cleanup may remove disposable workspace files only after required diagnostics/receipts are captured.
- Historical/quarantined results are distinguishable from temporary workspace.
- Retention/cleanup behavior is documented and user-controlled at product level; no hidden destructive garbage collection.
- Hashing streams large files; do not read multi-GB CFD data into memory.
- Logs are bounded for UI/database but full log artifacts may be retained according to explicit retention policy.

---

## 12. Security/integrity angles

This is engineering execution infrastructure and must not become arbitrary shell execution.

- argv execution, no shell interpolation by default;
- configured executables resolved explicitly;
- working directories controlled;
- symlinks/traversal rejected in sealed bundles;
- safe archive extraction;
- secrets injected at runtime, not persisted or logged;
- environment logging redacts secret-bearing keys/values;
- remote downloads hash-validated;
- provider responses treated as untrusted until schema/artifact validation;
- stdout/stderr bounded in status records;
- child process tree cleanup retained cross-platform.

These are integrity protections, not purpose/license controls.

---

## 13. Cross-platform preservation

The current process runner contains Windows-specific process flags/cleanup behavior. Extraction must preserve this behavior and add platform tests where possible.

Validate separately on:

- Windows FreeCAD distribution;
- Linux;
- macOS if supported by VibeCAD/Aero dependencies;
- WSL scenarios only where intentionally supported.

Path normalization, executable suffixes, process groups, line endings, file locking and shutdown behavior differ by platform and should not be “cleaned up” in the same PR as architecture extraction.

---

## 14. Observability without telemetry

Every job should expose local structured events such as:

```text
prepared
input_sealed
queued
provider_submitted
process_started
progress
cancel_requested
process_exited
outputs_collected
source_validated
quarantined
publication_started
published
failed
```

Record stage timing, provider receipt, artifact references and normalized failure locally.

No remote telemetry is required for the architecture.

---

## 15. Routing relationship

Routing is upstream of job execution.

A routing decision selects:

- domain solver/model;
- compute provider;
- resource estimate;
- fallback policy.

The Analysis Runtime executes the selected prepared job. It does not secretly switch physics because a provider is unavailable.

If fallback is allowed, a new/updated routing decision and new prepared-analysis identity should record that choice.

---

## 16. Coupled/refinement workflows

The generic runtime should support parent/child job relationships later, but do not add workflow orchestration to the extraction PR.

Future consumers include:

- mesh refinement sweeps;
- cross-solver validation;
- transient chunks/checkpoints;
- FSI partitioned iterations;
- parameter sweeps;
- Monte Carlo/UQ;
- multi-fidelity refinement loops.

These should compose host jobs, not teach the host runtime aerodynamic/FEM coupling semantics.

---

## 17. Acceptance gates before Aero adoption

Aero may depend on the new host runtime only when all are true:

1. all current FEM unit/integration tests pass;
2. characterization golden outputs match;
3. command/env/input hash parity demonstrated;
4. cancellation and timeout parity demonstrated;
5. provider-owned process-tree cancellation/timeout proven on Windows and POSIX while preserving visible cancel semantics;
6. existing public `native.job` schema/behavior preserved;
7. existing error-code compatibility preserved;
8. result graph/History/receipt parity demonstrated;
9. no worker-thread FreeCAD access introduced;
10. no new dependency from generic runtime to FEM/Aero;
11. source-stale rejection remains correct;
12. publication rollback remains atomic;
13. CMake/install registration complete;
14. documentation explicitly identifies current vs target behavior;
15. clean rollback to prior implementation is possible without data conversion for extraction phases.

Durable persistence has its own later acceptance gates before remote reconnect is trusted.

---

## 18. Definition of done

This migration is done only when:

- FEM runs through the generic runtime with parity;
- Aero high-fidelity external solvers run through the same runtime;
- Local and remote providers use one provider contract;
- one host job identity/lifecycle is authoritative;
- `AeroJobStore` is no longer a scheduling/persistence authority;
- generic job code has no FEM/Aero physics imports;
- Native mutation remains publication authority;
- evidence/qualification remains separate from execution success;
- restart/recovery semantics are explicit and tested;
- no accepted VibeCAD/FEM capability was lost to the rewrite.

## Deepening: mandatory extraction order

The implementation sequence in `HOST_RUNTIME_EXTRACTION_SEQUENCE.md` is now normative. In particular:

1. add characterization tests before production extraction;
2. add the neutral lifecycle core unused;
3. prove the atomic cancellation/commit gate;
4. extract/harden local process supervision behind a provider interface;
5. create a FEM adapter while retaining the legacy path;
6. route the existing `analyze.solver_execution` capability through the host runtime without changing its public contract;
7. add durability/recovery only after boundary/parity is stable;
8. adopt Aero only after FEM proves parity/burn-in;
9. delete duplication only after call-graph, regression and rollback evidence.

A fresh upstream SHA is mandatory before step 1. The observed post-Pass-03 drift is documented in `LIVE_DRIFT_AFTER_PASS_03.md` but is not a substitute for that fresh freeze.


## 18A. Correctness hazards discovered during source review

Two host behaviors require dedicated treatment before they become the generic runtime oracle:

### Cancellation/commit race

Current `NativeBackground` checks cancellation inside the document-thread callback, then updates the phase to `committing/finalizing`. Because `cancel()` can still accept while the phase is `waiting_to_commit`, characterize and, if reproducible, fix the check/transition race in an isolated correctness PR. The target invariant is linearizable: either cancellation is accepted and no publication begins, or commit ownership is acquired and cancellation is rejected.

### Process-tree ownership

Current `stop_process()` terminates/kills the direct process. Solvers that spawn MPI/helpers may outlive their parent on some platforms. Characterize first. If confirmed, harden owned process-tree cleanup in a separate process-control PR.

**Neither behavior change is to be hidden inside process/runtime extraction.** Re-baseline parity tests after each isolated fix.

## 19. Shadow-observation phase — compare without double execution

Before any solver path is cut over, the new runtime may run in **read-only shadow observation** behind the legacy authoritative path. It receives normalized request/lifecycle/publication facts and computes its would-be records, but it does not spawn a second process, collect a second output tree, or mutate the document.

This phase catches contract mismatches while preserving one source snapshot, one solver cost, one cancellation owner, and one result graph. See `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md`.

## 20. Publication idempotency and duplicate-event safety

The runtime must treat repeated callbacks, reconnects and UI retries as normal distributed-system behavior. A unique publication identity/receipt prevents duplicate result graphs. State transitions are validated and monotonic; terminal states are not reopened by late provider callbacks. Existing FEM behavior is preserved first, then richer retry/reconnect behavior is introduced separately.

## 21. Save-As, close, reopen and restart semantics

Path/label are never attachment authority. In-session document identity plus domain dependency fingerprints determine currentness. After durable persistence exists, jobs may continue with the document closed; completed output waits for an exact source to reopen and revalidate. Ambiguous matches remain `AWAITING_SOURCE`/quarantined rather than being auto-attached. See `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`.

## 22. Persistence and crash-consistency details

Persistence is transactional and artifact-first: immutable artifact bytes are sealed before metadata promotes them; publication receipts are recorded only after Native mutation succeeds. Incomplete restart state is recovered explicitly rather than guessed. Schema migrations never modify FCStd data and never delete solver artifacts as a repair shortcut.

## 23. API/import/install compatibility

Source compatibility is broader than Native JSON schemas. Old Python modules remain facades/re-exports while repository and plausible downstream imports are audited. Every new host module is registered in CMake/live packaging and validated from an installed tree, not only a source checkout. Generic host imports may not acquire Aero/FluidX3D/OpenFOAM/Kaggle dependencies.

## 24. Performance and UX non-regression

Capture document-thread preparation time, hash time/memory, process launch latency, cancel latency, publication time, log growth and startup/import cost. Input hashing/manifests stream large files. A refactor that preserves correctness but makes current FEM materially less usable is not complete.

## 25. Rollback doctrine

Before durable persistence, each extraction step is revertible by restoring the compatibility facade to the legacy internal implementation. After persistence is added, code rollback and data rollback are separate: schema migration is transactional/recoverable and immutable artifacts survive metadata rollback. Per-solver cutover is independently reversible.

## 26. Current-upstream check during Correction 01

The repository was rechecked after Pass 03. Current `main` was four commits ahead at `24fe48bb3fdcb84b558d34e23fedb0988ee4e548`; the delta was confined to Native preview ribbon/UI/CMake registration and did not touch the Analysis/Background/FEM execution boundary. Correction 01 therefore keeps `df07a5e...` as its immutable design baseline. Re-freeze before implementation.
