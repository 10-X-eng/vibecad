# Host Analysis Runtime — Exact Extraction Sequence

## Phase A — characterization-only commit(s)

1. Fresh-freeze upstream.
2. Add/strengthen tests around current `VibeCADNativeBackground`, FEM execution, state hashing, document lifecycle, process cancellation/timeout, and `native.job` payloads.
3. No production behavior changes except test seams if required.

**Exit:** legacy behavior is executable evidence.

## Phase A1 — isolate cancellation/commit correctness

Before generalization, add a targeted concurrency regression test around the existing `NativeBackground` check-to-commit window. If the frozen/live implementation still permits `cancel_accepted == true` followed by commit, fix that race in a tiny host correctness PR without changing solver preparation, process execution, persistence, schemas, or result topology.

Re-run the characterization suite and make the corrected behavior the new oracle.

## Phase A2 — characterize owned process-tree behavior

The current shared `stop_process()` controls the direct `Popen`. Test representative direct and child-spawning processes on supported platforms. If descendants can survive cancel/timeout, harden process ownership/termination in a separate process-control PR with exact platform tests. Do not bundle that fix into the generic provider extraction.

**Exit:** the execution oracle is behaviorally understood and known correctness fixes are isolated from architectural movement.

## Phase B — introduce neutral state core, unused

Add host-neutral modules (names illustrative; choose final names against live repo):

- `VibeCADAnalysisJobState.py`
- `VibeCADAnalysisRuntime.py`
- `VibeCADAnalysisProvider.py`
- `VibeCADAnalysisArtifacts.py`
- `VibeCADAnalysisPersistence.py`

Register every installed file/test in CMake.

Implement lifecycle transition table + atomic commit gate first. Keep old background manager live.

**Exit:** pure tests prove state semantics; no FEM caller changed.

## Phase C — extract local process provider

Move/copy generic mechanics from FEM process helper into `LocalProcessProvider` behind a compatibility wrapper. Preserve FEM command generation and solver setup outside the provider.

Add process-tree ownership and termination tests.

**Exit:** FEM can exercise the new provider without changing domain preparation/import.

## Phase D — create FEM adapter, reversible internal routing seam remains

FEM adapter owns:

- prepare;
- solver input fingerprint/currentness;
- solver execution descriptor;
- parser/importer;
- validate-before-publish;
- result replacement/History/state hashes;
- transaction-safe publication.

Route only selected tests/cases through the adapter initially using a development/internal routing seam. This is not a user-facing product setting and is removed/reduced after stabilization.

**Exit:** A/B parity.

## Phase E — route existing `analyze.solver_execution` through host runtime

Do not change binding/schema/capability name. Background response still returns the existing `job/next` shape. `native.job` remains lifecycle facade.

**Exit:** full FEM regression suite + GUI/headless smoke + supported-platform CI.

## Phase F — add persistence behind runtime

Persistence is not required to prove extraction parity. Introduce it after the runtime boundary is stable so recovery semantics cannot destabilize the initial refactor.

- durable descriptor schema;
- artifact sealing;
- local interrupted classification;
- remote reconnect capability;
- restart fault tests.

**Exit:** recovery gates pass.

## Phase F2 — add durable publication authority, still without changing FEM semantics

Add the host publication coordinator described in `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`:

- inert submission/publication descriptors in persistence;
- exact `Document.Uid` rebind after its persistence/copy behavior is characterized;
- domain `CurrentnessReport`;
- artifact/job/publication replay identity;
- `AWAITING_SOURCE`, `AWAITING_PUBLICATION`, stale/quarantined states;
- fresh document-thread Native publication authorization;
- exactly one publication receipt/result graph.

Do **not** switch FEM to this new publication model in the same phase. Existing FEM remains the compatibility oracle. Exercise the coordinator with synthetic/reference domain fixtures first.

**Exit:** restart/rebind/publication replay tests pass without serialized live Native authority.

## Phase G — Aero becomes second consumer

Wire VibeCADAero cases/jobs through host runtime/provider layer while retaining:

- `VibeCADAero.py` public authority;
- `AeroStamp.py` evidence authority;
- `AeroResults.py` durable engineering result authority;
- solver/provider separation;
- FluidX3D/OpenFOAM/Kaggle-specific domain/provider policies.

**Exit:** Aero tests + real dependency smoke/qualification gates.

## Phase H — retire duplication

Delete or reduce legacy scheduling/process code only after:

- call-graph search proves no hidden callers;
- parity/burn-in passes;
- rollback window completed;
- migration ledger records exact superseded files/functions;
- CMake/docs/manifests updated.

No “cleanup” deletion is allowed to precede evidence.
