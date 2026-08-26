# Package Tree — Pass 03 Correction 01

```text
VibeCADAero_Reconciliation_Pass_03_Correction_01_df07a5e/
├── history/
│   └── PASS_02_DIFF_FROM_PASS_01.md
├── integration/
│   └── FILE_CHANGE_MAP.md
├── proposed_overlay/
│   ├── fluidx3d_bridge/
│   │   ├── README.md
│   │   └── setup_vibecad.cpp
│   ├── reference_host_runtime/
│   │   ├── __init__.py
│   │   ├── README.md
│   │   ├── test_analysis_job_state.py
│   │   ├── test_analysis_publication.py
│   │   ├── VibeCADAnalysisJobState.py
│   │   └── VibeCADAnalysisPublication.py
│   ├── schema/
│   │   └── aero-cfd-result.schema.json
│   ├── src/
│   │   └── Mod/
│   │       └── VibeCADAero/
│   │           ├── tests/
│   │           │   ├── conftest.py
│   │           │   ├── test_aero_acknowledgement.py
│   │           │   ├── test_cfd_contracts.py
│   │           │   ├── test_detached_execution_overlay.py
│   │           │   ├── test_dynamic_stall_overlay.py
│   │           │   ├── test_fluidx3d_vendor_policy.py
│   │           │   ├── test_geometry_readiness_overlay.py
│   │           │   ├── test_host_evidence_overlay.py
│   │           │   ├── test_host_runtime_atomic_commit_gate.py
│   │           │   ├── test_host_runtime_migration_overlay.py
│   │           │   ├── test_host_runtime_plan_integrity.py
│   │           │   ├── test_job_store_overlay.py
│   │           │   ├── test_kaggle_overlay.py
│   │           │   ├── test_mesh_overlay.py
│   │           │   ├── test_native_bridge_overlay.py
│   │           │   ├── test_native_repair_bridge_overlay.py
│   │           │   ├── test_qualification_overlay.py
│   │           │   ├── test_routing_overlay.py
│   │           │   ├── test_sixdof_overlay.py
│   │           │   └── test_strip_overlay.py
│   │           ├── AeroAcknowledgement.py
│   │           ├── AeroCFD.py
│   │           ├── AeroCFDContracts.py
│   │           ├── AeroCFDUpstreamAdapter.py
│   │           ├── AeroDetachedExecution.py
│   │           ├── AeroDynamicStall.py
│   │           ├── AeroFieldResults.py
│   │           ├── AeroGeometryReadiness.py
│   │           ├── AeroHostEvidence.py
│   │           ├── AeroJobStore.py
│   │           ├── AeroKaggle.py
│   │           ├── AeroLBM.py
│   │           ├── AeroLocalCompute.py
│   │           ├── AeroMesh.py
│   │           ├── AeroNativeBridge.py
│   │           ├── AeroNativeRepairBridge.py
│   │           ├── AeroOpenFOAM.py
│   │           ├── AeroQualification.py
│   │           ├── AeroRouting.py
│   │           ├── AeroSixDOF.py
│   │           ├── AeroStripTheory.py
│   │           ├── AeroUnsteady.py
│   │           └── openfoam_collect.py
│   ├── vendor/
│   │   └── FluidX3D/
│   │       ├── FLUIDX3D_VENDOR_MANIFEST.json
│   │       ├── LICENSE.md
│   │       ├── VENDOR_POLICY.md
│   │       └── VIBECAD_VENDOR.md
│   └── README.md
├── sources/
│   ├── CONVERSATION_SOURCE.md
│   └── CONVERSATION_SOURCE.sha256
├── AERO_FIRST_USE_INFORMATIONAL_NOTICE.md
├── AERO_JOB_REUSE_DECISION.md
├── BUILDER_HANDOFF.md
├── CANONICAL_ARCHITECTURE.md
├── CANONICAL_CODE_REFERENCE.md
├── CANONICAL_ENGINEERING_PAPER.md
├── CORRECTION_01_DEEPENING_LEDGER.md
├── CORRECTION_01_HOST_ANALYSIS_RUNTIME.md
├── CURRENT_UPSTREAM_DRIFT_CHECK.md
├── DESTRUCTIVE_CHANGE_AUDIT.md
├── DIFF_FROM_PASS_01.md
├── DIFF_FROM_PASS_02.md
├── EVIDENCE_AND_ARTIFACT_TAXONOMY.md
├── FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md
├── FLUIDX3D_COMMERCIAL_LICENSING_STATUS.md
├── GEOMETRY_READINESS_MODEL.md
├── HOST_ANALYSIS_RUNTIME_ARCHITECTURAL_DECISIONS.md
├── HOST_ANALYSIS_RUNTIME_CONTRACT.md
├── HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md
├── HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md
├── HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md
├── HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md
├── HOST_ANALYSIS_RUNTIME_RISK_REGISTER.md
├── HOST_RUNTIME_COMPATIBILITY_CONTRACT.md
├── HOST_RUNTIME_DOCUMENT_LIFECYCLE.md
├── HOST_RUNTIME_EXTRACTION_SEQUENCE.md
├── HOST_RUNTIME_PERSISTENCE_AND_RECOVERY.md
├── HOST_RUNTIME_PROCESS_CONTROL.md
├── HOST_RUNTIME_PROVIDER_CONTRACT.md
├── HOST_RUNTIME_REGRESSION_GATES.md
├── HOST_RUNTIME_STATE_MACHINE.md
├── IMPLEMENTATION_SPEC.md
├── KNOWN_ERRORS_AND_BUGS.md
├── LIVE_DRIFT_AFTER_PASS_03.md
├── MISSING_FROM_PLAN.md
├── NATIVE_CONTROL_RECONCILIATION.md
├── NON_DESTRUCTIVE_MIGRATION_MATRIX.md
├── POLICY_AND_RESTRICTION_PRINCIPLES.md
├── README.md
├── RECHECK_PLAYBOOK.md
├── RECONCILIATION_LEDGER.md
├── SOURCE_TRACEABILITY.md
├── SOURCE_VERIFIED_HOST_RUNTIME_BASELINE.md
├── SYNC_MANIFEST.json
├── TEST_OUTPUT.txt
├── THIRD_PARTY_NOTICES.md
├── TREE.md
├── UPSTREAM_BASELINE.md
└── VALIDATION_REPORT.md
```
