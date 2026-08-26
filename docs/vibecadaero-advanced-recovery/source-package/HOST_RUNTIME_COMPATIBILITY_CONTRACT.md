# Host Analysis Runtime — Compatibility Contract

## Goal

Extract shared execution mechanics with **zero intentional FEM behavior change** in the first migration stage, except fixes explicitly gated as correctness bugs (atomic cancellation/commit and owned process-tree cleanup).

## FEM public surface that remains stable

- capability: `analyze.solver_execution`;
- target schema: existing solver-execution context;
- native domain/action identity;
- transaction name/rollback behavior;
- synchronous FEM input/request preparation followed by background solver-execution semantics;
- solver restrictions and platform checks;
- error family and existing error codes where applicable;
- success/failure payload fields;
- background response `job` + `next` envelope;
- `native.job` status/cancel control flow;
- exact document/currentness guard;
- result-root replacement and History behavior;
- solver input/result hashing semantics.

## Compatibility adapter pattern

```text
existing binding/schema
        |
existing FEM execution runtime
        |
FEM AnalysisJobAdapter
  prepare/fingerprint/parse/publish
        |
Host Analysis Runtime
        |
ComputeProvider
```

The adapter is introduced under the existing call path. Existing callers are not redirected to a new capability.

## Feature-flag rollback

During migration, a host-level/internal switch shall permit:

- `legacy_fem_execution` — current implementation;
- `analysis_runtime_fem` — adapter routed through extracted runtime.

The switch is not a permanent public product option. It exists until parity, burn-in, and rollback criteria pass.

No migration step may delete the legacy path until:

- characterization suite is green;
- FEM result-object graph parity is demonstrated;
- cancellation/timeout parity is demonstrated;
- stale/currentness behavior is demonstrated;
- failure/error parity is demonstrated;
- FreeCAD GUI/headless smoke gates pass on supported platforms;
- rollback exercise succeeds.

## Known correctness hazards are separate PRs, not extraction side effects

Source review identifies two hazards worth addressing, but **neither is allowed to hide inside an architectural extraction PR**:

1. `NativeBackground.cancel()` can accept a cancellation while the job is still `waiting_to_commit`; the document-thread callback performs a second cancellation check and only afterward sets `committing/finalizing`. That creates a narrow check/transition race where a cancel can be accepted before the phase change yet the callback can continue into commit.
2. the shared `stop_process()` terminates/kills the direct `Popen` only. A solver/MPI/helper process tree may require stronger owned-process-tree semantics, but actual child behavior must be characterized per platform before changing it.

Required handling:

- characterize each behavior first;
- add a dedicated failing regression test proving the hazard;
- land the correctness fix in an isolated PR (or explicitly adjacent tiny PR) with no generic-runtime ownership transfer;
- re-baseline the characterization trace after the fix;
- only then use the corrected semantics as the runtime extraction oracle.

This prevents the rewrite from being credited with an unrelated behavior change and preserves clean rollback/diagnosis.

## Additive future capabilities

Durable history, cross-restart recovery, remote providers, artifact stores, multi-document concurrency, and global job discovery are additive host-runtime capabilities. They cannot be smuggled into the FEM extraction by altering existing public contracts without explicit schema/version decisions.
