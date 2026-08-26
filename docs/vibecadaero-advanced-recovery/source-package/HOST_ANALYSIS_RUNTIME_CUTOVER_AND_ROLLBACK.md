# Host Analysis Runtime — Cutover, Compatibility, and Rollback Protocol

**Frozen design anchor:** `halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`  
**Purpose:** make the host-runtime extraction reversible, observable, and non-destructive to working VibeCAD/FEM behavior.

## 1. Core cutover law

The new Analysis Runtime is introduced by **ownership transfer behind compatibility facades**, not by replacing public entry points.

At every migration step there is exactly one execution authority for a real solver run. The legacy and new paths may both *observe/serialize/compare* the same prepared request, lifecycle events, and publication draft, but they MUST NOT both launch the solver or both mutate the document.

This avoids:

- duplicate expensive solves;
- duplicate result graphs;
- nondeterministic publication races;
- doubled CPU/GPU load;
- confusing cancellation ownership;
- false parity caused by two different source snapshots.

## 2. Compatibility surfaces that are protected

During extraction, treat the following as compatibility contracts:

1. Python import paths used by the repository and likely extensions:
   - `VibeCADNativeBackground*`
   - `VibeCADNativeAnalyzeSolverExecution*`
   - solver execution schema/runtime/bindings modules.
2. Public Native action names and argument shapes, especially `native.job` (`status`/`cancel`) and `analyze.solver_execution` (`run`).
3. `native.job` result/status/cancel response shape and current error mapping.
4. Existing FEM solver command line, environment, working-directory assumptions, timeout behavior, log bound, and cancellation semantics.
5. FEM History behavior, result graph topology, result-retention semantics, mutation draft, and receipts.
6. Current one-active-job-per-document policy.
7. FreeCAD document-thread mutation authority.
8. CMake/install/package registration.
9. Existing tests and supported platform behavior.

The internal host runtime may become richer, but compatibility adapters map back to these surfaces until an intentionally separate API evolution is designed.

## 3. Four operating modes of the migration

These are implementation phases, not user settings.

### Mode A — Legacy authoritative

Current upstream code is the sole authority. New code is contracts/tests only.

### Mode B — Shadow observation

The legacy path still prepares, executes, validates, and publishes. The new runtime receives a **read-only normalized trace** of:

- request identity;
- input manifest/digest;
- command/environment summary;
- lifecycle transitions;
- cancel/timeout events;
- result/publication summary.

The new runtime does not launch a process and does not publish. It calculates what it *would* have recorded. Tests compare the traces.

This is the safest way to discover contract mismatches without changing solver behavior.

### Mode C — New mechanics, legacy facade

A compatibility module receives the old call and delegates exactly one internal responsibility to the new host code, e.g. local process execution. All surrounding preparation/publication remains legacy.

Every transfer is one responsibility at a time.

### Mode D — Host runtime authoritative, public facade preserved

FEM runs through the host runtime but existing imports/actions still call compatibility facades. Only after a stabilization interval is Aero allowed to become a production client.

## 4. Cutover unit

The minimum safe cutover unit is **one responsibility for one solver family**, not “all analysis execution.”

Recommended sequence:

1. generic process launch/monitor/cancel mechanics;
2. generic input sealing/manifest mechanics in compatibility digest mode;
3. generic in-memory lifecycle/orchestration;
4. CalculiX primary path;
5. CalculiX `ccx_tools` fallback;
6. Elmer;
7. Z88;
8. Mystran;
9. stabilization;
10. persistence;
11. Aero client.

A failed migration of one solver family must not force rollback of already-proven independent host primitives.

## 5. Golden behavioral trace

Before each cutover, capture a canonical trace from the existing path:

```text
request created
input files + current legacy digest
command tuple
selected environment keys
progress events
cancel/timeout outcome
process exit summary
source-currentness decision
result graph identities/roles
History mutation
NativeMutationDraft
receipt/error response
cleanup behavior
```

The trace stores normalized values, not unstable temporary absolute paths or timestamps.

After extraction, compare the same trace. Differences are classified as:

- expected representational difference with preserved semantics;
- intentional behavior change in a separately scoped change;
- regression (blocks cutover).

## 6. Publication idempotency

Result publication is a high-risk duplicate-mutation boundary.

Target host semantics SHALL support a publication identity derived from at least:

- job ID;
- analysis ID;
- prepared-input identity;
- domain publication identity/version.

The publication adapter must be able to answer whether the exact publication was already committed. A repeated callback, reconnect, UI retry, or process recovery must not create duplicate FEM/Aero result trees.

During initial FEM extraction, preserve existing result-graph behavior. Add idempotency instrumentation before changing result topology.

## 7. Callback ordering and race containment

The runtime must tolerate:

- duplicate provider status callbacks;
- provider status arriving after local cancellation request;
- cancel racing process exit;
- document close racing result publication;
- app shutdown racing worker completion;
- result collection completing twice after retry/reconnect;
- UI polling while a transition is in flight.

Rules:

- job state transitions are monotonic and validated;
- terminal execution state cannot be silently reopened;
- provider events are deduplicated by provider event/attempt identity when available;
- publication has one owning transaction;
- cancellation request and terminal cancellation are distinct facts;
- stale/currentness and execution success remain separate axes.

For existing FEM, race resolution must first reproduce current behavior. Improved semantics are a later explicit change.

## 8. Rollback levels

### Level 0 — Revert one extraction commit

Before persistence exists, the preferred rollback is code-level: compatibility facade points back to the legacy implementation.

### Level 1 — Per-solver implementation rollback

If one solver migration regresses, route that solver's compatibility adapter back to its legacy path while other proven host primitives remain.

This routing is an implementation seam, not a user-facing preference.

### Level 2 — Runtime ownership rollback

If generic orchestration is defective, `NativeBackgroundManager` remains the public facade and can restore its prior internal execution path without changing agent/API clients.

### Level 3 — Persistence rollback

Persistence is introduced only after execution parity. Its schema migration requires:

- a pre-migration backup/copy or recoverable journal strategy;
- transactional migration;
- old-reader compatibility for at least the immediately prior schema where practical;
- no deletion of immutable artifacts merely because metadata migration fails.

A persistence rollback must never rewrite or delete the user's FCStd document.

## 9. No destructive database migration

The initial persistent store begins as a new host subsystem. Existing in-memory Native jobs are not durable today, so there is no legacy job database to destructively transform.

Once durable schemas exist:

- schema versions are explicit;
- migrations are forward, transactional, and tested from fixture databases;
- failed migration opens the job store read-only/recovery mode rather than guessing;
- immutable artifacts remain addressable even if job metadata requires repair;
- data cleanup is explicit and visible; no hidden destructive garbage collection is used as a migration shortcut.

## 10. External-import safety

Old Python modules are cheap compatibility insurance. Do not remove them simply because repository-local imports disappeared.

Before deletion:

1. repository-wide import/reference search;
2. docs/examples/agent manifest search;
3. packaging/export search;
4. deprecation period if the module is plausibly public;
5. leave thin re-export wrappers indefinitely when maintenance cost is trivial.

The objective is architectural consolidation without needlessly breaking FreeCAD macros, local extensions, or downstream automation.

## 11. Packaging and install parity

Every extraction PR that adds host modules must prove:

- modules are listed in `src/Mod/VibeCAD/CMakeLists.txt` or the live equivalent;
- source-tree tests and installed-tree import smoke both pass;
- Windows/macOS/Linux packaging does not omit the new runtime;
- no dependency is accidentally added to core VibeCAD merely because Aero will later use it;
- generic host modules have no import-time dependency on FluidX3D, OpenFOAM/CfdOF, Kaggle, or Aero.

## 12. Performance parity

The refactor must not make existing FEM materially worse before Aero is added.

Track:

- document-thread prepare time;
- input hashing wall time and peak memory;
- process launch latency;
- polling/progress overhead;
- cancellation latency;
- result publication time;
- log memory/disk behavior;
- application startup/import cost.

The generic manifest implementation must stream large solver files and must not read multi-GB inputs wholly into memory.

## 13. Definition of safe cutover

A solver path is considered safely cut over only when:

- legacy characterization trace exists;
- new trace is equivalent;
- process/input/result/currentness/receipt tests pass;
- no live FreeCAD object crosses the worker boundary;
- no duplicate execution/publication is possible;
- public schema/error compatibility is preserved;
- installed package imports succeed;
- rollback route is still available;
- supported-platform checks are green or a pre-existing platform limitation is documented unchanged.

Only then may the old implementation behind that facade stop being authoritative.

## Durable publication cutover is a separate authority migration

Do not combine execution cutover with a change from the current FEM submission-time `NativeCallTicket` to a durable/fresh publication model. During FEM extraction, legacy publication semantics remain the oracle.

After persistence exists, the new publication coordinator may be activated for synthetic fixtures and then Aero. Its durable records contain exact job/submission/output/publication provenance but **never standing mutation permission**. A live `NativeRuntimeContext`, reusable `NativeCallTicket`, callback or transaction is not reconstructed from disk. Exact source `Document.Uid`, domain currentness, replay receipt and fresh Native mutation authorization are required.

Rollback of the publication coordinator therefore means jobs remain safely `AWAITING_SOURCE`/`AWAITING_PUBLICATION` or historical evidence; it must not require destructive database or FCStd rewrites.
