# VibeCADAero Reconciliation Pass 03 — Correction 01

**Frozen VibeCAD upstream:** `df07a5e82ec2fb31515e10b33822253d69d496ff`  
**Pass 02 baseline:** `d0a933e40005b4affe9303f27d1eae5cd36eb030`  
**Upstream delta:** **41 commits ahead / 0 behind, 50 changed files**  
**Upstream writes performed:** **none**

This is the third immutable reconciliation of the full VibeCADAero high-fidelity design against the actively changing `halthinks/vibecad` repository. **Correction 01 does not change the frozen upstream SHA.** It corrects the target architecture for long-running engineering execution after a deeper review of the existing Native Background and detached FEM implementations.

## Why Pass 03 is materially different

Pass 02 established the correct separation between Native CAD authority, long-running Aero compute jobs, and Aero engineering evidence. Since then, VibeCAD has implemented enough adjacent infrastructure that several Pass-02 proposals should now **reuse host semantics instead of becoming parallel Aero infrastructure**.

The frozen host now includes, among other changes:

- generic pending Native preview listing, apply and reject controls;
- default-off automatic preview application and explicit stale refusal;
- preservation checks for `user_explicit` intent when a preview is applied;
- held Native session query/idle behavior;
- stronger refusal of CAD/Aero mutation through `/v1/run`;
- exact/derived/presentation artifact classes;
- direct-measurement evidence distinct from presentation pixels;
- detached FEM solver execution with frozen input hashes, cancel/progress, detached work directories and stale-before-attach checks;
- explicit `not_solved` and `model_unqualified` semantics;
- corrected product language that no longer equates accepted B-rep with manufacturability.

These are not cosmetic changes. They establish a host vocabulary and host control-plane pattern that Aero should adopt.

## Pass 03 canonical architecture

```text
VibeCAD host authority
├─ structural revision + mutation receipts
├─ Native preview/apply/reject UI + dispatcher
├─ user-explicit intent preservation
├─ shared artifact/evidence vocabulary
├─ Native Background orchestration seed
├─ detached-solver execution seed
└─ **target: one domain-neutral VibeCAD Analysis Job Runtime**
          │
          ▼
VibeCADAero domain authority
├─ aerodynamic case + geometry/frame identity
├─ aerodynamic job payload/case identity (host runtime owns job authority)
├─ FluidX3D / OpenFOAM / low-order solver adapters
├─ fields / force / coefficient / qualification evidence
├─ unsteady / moving-body / 6DOF / FSI physics
└─ refinement / engineering-knowledge accumulation
```

A CFD job remains **not a Native mutation preview**. The host preview system authorizes CAD mutation proposals; detached CFD is evidence-producing work that may outlive a Native turn, active document, or process. Current FEM keeps its strict submission-ticket/global-revision attachment behavior during extraction. Durable Aero later uses exact frozen case/artifact provenance + domain dependency currentness + **fresh Native publication authorization**; it never replays a serialized submission context as standing mutation permission.

## Major Pass 03 reconciliations

1. **Host preview UI is no longer missing.** `VibeCADNativePreviewControl.py` / preview commands now own generic apply/reject behavior. Aero must integrate CAD-changing repairs with that host path instead of building its own generic preview broker.
2. **Aero repair host-revision propagation is still missing.** The public Aero repair API has a `native_revision` seam, but `/v1/aero` still does not provide the host revision. This remains an immediate integration item.
3. **Pass-02 `AeroJobStore` is transitional, not target authority.** The real solution is now explicit: extract one domain-neutral **VibeCAD Analysis Job Runtime** from the two working host seeds—Native Background orchestration and detached FEM execution. FEM becomes the proving client; Aero becomes the second client only after parity. The migration is compatibility-first and non-destructive.
4. **Evidence taxonomy is now shared with the host.** Prepared analysis is `not_solved`; successful numerical completion can still be `model_unqualified`; direct measurement is distinct; screenshots remain presentation-only.
5. **Artifact exactness is not CFD readiness.** STEP/native B-rep can be exact while still not closed/watertight/domain-ready. Mesh/voxel/field artifacts are derived even when derived from exact geometry.
6. **Raw execution is no longer an acceptable escape hatch.** Aero mutations/solves belong on `/v1/aero` and registered Native/domain surfaces, not `/v1/run` exec.
7. **The preview-store retention/persistence concern remains open.** Current preview entries are still retained in the host state record after consume/reject, while outstanding previews are not part of persisted Native-state export.

## New Pass 03 reference modules

- `AeroHostEvidence.py` — host-aligned evidence and artifact semantics.
- `AeroGeometryReadiness.py` — exact representation vs. CFD-readiness states.
- `AeroNativeRepairBridge.py` — transition contract for host revision + geometry + user-explicit intent preservation.
- `AeroDetachedExecution.py` — transitional reference semantics that are now targeted for host extraction rather than permanent Aero ownership.
- Pass-02 `AeroJobStore.py` remains in the overlay only as a **transitional/reference lifecycle model**. It must not become production scheduling/persistence authority.

## Correction 01 — host Analysis Runtime migration

The migration is fully specified rather than deferred:

- `CORRECTION_01_HOST_ANALYSIS_RUNTIME.md`
- `SOURCE_VERIFIED_HOST_RUNTIME_BASELINE.md`
- `HOST_ANALYSIS_RUNTIME_CONTRACT.md`
- `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md`
- `NON_DESTRUCTIVE_MIGRATION_MATRIX.md`
- `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md`
- `HOST_ANALYSIS_RUNTIME_RISK_REGISTER.md`
- `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md`
- `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`
- `HOST_ANALYSIS_RUNTIME_ARCHITECTURAL_DECISIONS.md`
- `CURRENT_UPSTREAM_DRIFT_CHECK.md`

The central non-destructive rule is **characterize → facade → extract → migrate FEM one solver at a time → stabilize → add persistence → add durable publication authority → adopt Aero → add remote providers → retire duplicates only after audit**. No extraction PR is allowed to simultaneously redesign solver physics, change public Native schemas, expand concurrency, persistence, and publication semantics.

A crucial correction is now explicit: a durable job stores **provenance, not standing CAD mutation permission**. Current FEM keeps its existing original-ticket/global-revision behavior through extraction. Long-running Aero/CFD later uses three separate authorities—submission, detached execution, and a fresh provenance-bound publication authorization after exact document rebind/currentness validation. Live Native contexts/tickets/callbacks are never serialized as future authority.

Correction 01 also defines explicit **shadow-observation, cutover, rollback, Save-As/reopen identity, crash consistency, idempotent publication, callback-race, packaging/import compatibility, and persistence-recovery semantics**. Shadow observation never launches a second solver and never produces a second result graph; exactly one execution/publication authority exists for a real run.

## External anchors

- FluidX3D: `8986874e626e0aebd317ab16c420b39e30dfa273` — unchanged at this pass recheck.
- CfdOF: `a90f60c2313ceba09c236c81f0693d93357d1614` — unchanged at this pass recheck.
- Kaggle CLI: current 2.2.x line retains `kaggle quota`; current changelog also warns that the default image's P100 path is unusable for normal GPU compute and recommends T4 unless a compatible stack is installed. The routing plan must discover live accelerator availability rather than assume a GPU type.
- Gmsh: current documentation remains 4.15.2; existing explicit TRI3/QUAD4/TRI6 conversion policy remains valid.

## Third-party notice rule — unchanged

VibeCAD/VibeCADAero is not classified by FluidX3D's component-specific use restrictions. The one-time Aero notice is purely informational and uses exactly:

> **I understand.**

It is shown once, stored as one local unversioned acknowledgement bit, and does not govern the user's CAD designs or the rest of VibeCAD/Aero.

## Validation

See `VALIDATION_REPORT.md` and `TEST_OUTPUT.txt`. Pure reference tests are reproducible with bare `pytest -q`; no hidden `PYTHONPATH` is required.

## Start here

- `CANONICAL_ENGINEERING_PAPER.md`
- `CANONICAL_ARCHITECTURE.md`
- `IMPLEMENTATION_SPEC.md`
- `BUILDER_HANDOFF.md`
- `NATIVE_CONTROL_RECONCILIATION.md`
- `EVIDENCE_AND_ARTIFACT_TAXONOMY.md`
- `AERO_JOB_REUSE_DECISION.md`
- `SOURCE_VERIFIED_HOST_RUNTIME_BASELINE.md`
- `HOST_ANALYSIS_RUNTIME_CONTRACT.md`
- `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md`
- `NON_DESTRUCTIVE_MIGRATION_MATRIX.md`
- `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md`
- `HOST_ANALYSIS_RUNTIME_RISK_REGISTER.md`
- `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md`
- `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`
- `HOST_ANALYSIS_RUNTIME_ARCHITECTURAL_DECISIONS.md`
- `CURRENT_UPSTREAM_DRIFT_CHECK.md`
- `GEOMETRY_READINESS_MODEL.md`
- `MISSING_FROM_PLAN.md`
- `KNOWN_ERRORS_AND_BUGS.md`
- `RECONCILIATION_LEDGER.md`
- `DIFF_FROM_PASS_02.md`
- `proposed_overlay/`

**This remains a read-only handoff package. Do not overwrite active upstream work. Freeze and reconcile again before implementation.**

## Correction 01 deepening — destructive-change audit

Correction 01 was deepened after another source-level audit of the frozen FEM/background path and a live-drift check. The package now includes an explicit safe-extraction boundary, atomic lifecycle model, persistence/recovery contract, CAD document lifecycle rules, process-tree supervision requirements, compatibility/rollback contract, provider contract, regression gates, and an exact staged extraction sequence.

New primary documents:

- `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md` — separates submission provenance, execution authority and fresh CAD publication authorization for durable jobs.

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

Reference code under `proposed_overlay/reference_host_runtime/` is intentionally not claimed integrated upstream. Current overlay validation proves 45 pure-Python tests only; FreeCAD/FEM/provider integration remains a future authorized implementation gate.
