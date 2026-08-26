# Pass 03 Correction 01 — Host Analysis Runtime

**Frozen upstream remains:** `halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`  
**Upstream writes:** none  
**Correction type:** architectural migration correction; same immutable Pass-03 source baseline

## Correction

Pass 03 correctly concluded that VibeCADAero must not grow a second generic scheduler. It was too conservative in saying Aero should retain its solver-neutral job contract until VibeCAD happens to expose a generic durable job service.

The target is now explicit:

> **VibeCAD SHALL own one domain-neutral Analysis Job Runtime. Engineering domains SHALL own physics, preparation, parsing, qualification, and publication semantics. Compute providers SHALL own where/how prepared immutable work executes.**

The existing VibeCAD implementation already contains two working seeds that must be evolved rather than bypassed:

1. `VibeCADNativeBackground.py` / `VibeCADNativeBackgroundRuntime.py` provide a host-owned prepare/worker/commit orchestration pattern, status/progress/cancel surface, bounded in-memory job tracking, one-running-job-per-document semantics, and the public `native.job` control surface.
2. `VibeCADNativeAnalyzeSolverExecution.py` plus `VibeCADNativeAnalyzeSolverExecutionProcess.py` provide detached FEM input capture, input hashing, process launch, progress/cancellation/timeout, direct-process termination/cleanup, exact-source revalidation, and transaction-safe result publication.

These are not to be replaced by an Aero framework. They are to be **characterized, separated by responsibility, and generalized behind compatibility facades**.

## Non-destructive rule

This is a strangler migration, not a big-bang rewrite.

The migration SHALL NOT initially change:

- FEM solver input files;
- FEM command lines or environment;
- FEM result object graphs;
- Native History behavior;
- Native mutation receipts;
- existing `native.job` actions or schemas;
- `/v1/native`, `/v1/aero`, or `/v1/run` routing semantics;
- existing error codes visible to current FEM clients;
- one-active-job-per-document behavior;
- current timeout/cancel behavior;
- FreeCAD document-thread authority;
- VibeCADAero public authority.

Aero becomes a client only after the extracted runtime has demonstrated behavioral parity with existing FEM.

## What changes in Pass-03 planning

`AeroJobStore.py` is now **TRANSITIONAL / REFERENCE ONLY**. It is retained so no accepted lifecycle semantics disappear, but it is not the target owner of durable jobs, process execution, provider scheduling, cancellation, artifact persistence, or result publication.

The target host runtime is defined in:

- `SOURCE_VERIFIED_HOST_RUNTIME_BASELINE.md`
- `HOST_ANALYSIS_RUNTIME_CONTRACT.md`
- `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md`
- `NON_DESTRUCTIVE_MIGRATION_MATRIX.md`
- `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md`
- `HOST_ANALYSIS_RUNTIME_RISK_REGISTER.md`
- `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md`
- `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`
- `HOST_ANALYSIS_RUNTIME_ARCHITECTURAL_DECISIONS.md`

No code in active upstream has been changed by this correction.

## Deepening — destructive-change audit

The migration is now constrained by a source-level destructive-change audit. The essential corrections are:

- FEM state identity/currentness remains FEM-owned; it is not generic runtime state.
- `analyze.solver_execution` remains the stable FEM capability; `native.job` remains the compatibility lifecycle surface.
- the current cancellation check has a check→commit race; production extraction requires an atomic cancellation/commit gate;
- current process cancellation terminates/kills the direct `Popen` process only; a production provider must own the launched process tree/group;
- current background state is in-memory; durability/restart recovery is new functionality, not parity already provided;
- durable descriptors may never contain live FreeCAD objects, callbacks, futures, locks, events or process handles;
- exact document UID/revision/domain-currentness revalidation remains mandatory before publication;
- legacy FEM remains available behind an internal rollback path until characterization and A/B parity gates pass.

The deeper normative documents are:

- `DESTRUCTIVE_CHANGE_AUDIT.md`
- `HOST_RUNTIME_STATE_MACHINE.md`
- `HOST_RUNTIME_PERSISTENCE_AND_RECOVERY.md`
- `HOST_RUNTIME_DOCUMENT_LIFECYCLE.md`
- `HOST_RUNTIME_PROCESS_CONTROL.md`
- `HOST_RUNTIME_COMPATIBILITY_CONTRACT.md`
- `HOST_RUNTIME_PROVIDER_CONTRACT.md`
- `HOST_RUNTIME_REGRESSION_GATES.md`
- `HOST_RUNTIME_EXTRACTION_SEQUENCE.md`
- `LIVE_DRIFT_AFTER_PASS_03.md`
- `CORRECTION_01_DEEPENING_LEDGER.md`

A pure-Python reference model in `proposed_overlay/reference_host_runtime/` proves the atomic commit-gate invariant. It is reference evidence only and is not claimed integrated upstream.


## Deepened safety correction

The migration now explicitly covers the failure angles that become visible only once execution is durable: observation-only shadowing, per-solver cutover, callback replay, publication idempotency, Save-As/clone/reopen identity, document close, application shutdown, remote reconnect, orphan classification, crash-consistent artifact/metadata promotion, schema recovery, downstream Python import compatibility, installed-package parity and multi-level rollback.

These are architecture requirements, not future cleanup items. They must be designed before the first extraction PR and implemented at the dependency-appropriate stage.

## Publication authority deepening

The correction now explicitly separates submission provenance, detached execution authority, and fresh CAD publication authority. This is required for durable CFD jobs that outlive a Native turn/document activity. See `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`. The initial FEM migration intentionally preserves its original ticket/global-revision publication semantics; this new durable coordinator is added later rather than hidden inside extraction.
