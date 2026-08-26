# Diff From Pass 02

> **Correction 01 historical-note:** this document records the original Pass-03 decision at freeze time. Its conditional “if/when a generic host job service exists” disposition is **SUPERSEDED** by `CORRECTION_01_HOST_ANALYSIS_RUNTIME.md`: the target is now to extract one host Analysis Runtime deliberately from Native Background + detached FEM, with FEM parity first.

**Pass 02:** `d0a933e40005b4affe9303f27d1eae5cd36eb030`  
**Pass 03:** `df07a5e82ec2fb31515e10b33822253d69d496ff`  
**Delta:** 41 commits ahead / 0 behind / 50 changed files

## What upstream implemented that Pass 02 had treated as future/partial

### 1. Generic Native preview operator controls

Pending previews now have host-owned list/apply/reject control paths. Preview application can also verify that `user_explicit` intent has not changed. The default automatic apply behavior is off and stale previews are refused.

**Disposition:** Pass-02 proposals for an Aero-owned generic preview controller are **SUPERSEDED**. Aero CAD mutations should integrate with the host path.

### 2. Better detached solver semantics

The live FEM path now demonstrates:

- detached work directories;
- bounded/frozen input generation;
- SHA-256 input identity;
- progress/cancellation;
- solver-state and History revalidation before result attachment;
- explicit failure/unavailable/stale outcomes;
- `model_unqualified` after solver completion rather than pretending successful execution means validated physics.

**Disposition:** Pass-02 `AeroJobStore` remains useful only as a transitional/reference domain-lifecycle record. Its execution semantics are **MERGED** into the required host-runtime target. Correction 01 no longer waits for a generic service to appear: VibeCAD should extract one domain-neutral Analysis Runtime from Native Background + detached FEM, prove FEM parity first, then add durable persistence/publication authority before Aero becomes the second production client.

### 3. Host artifact taxonomy

The host now distinguishes:

- exact STEP/native B-rep artifacts;
- derived mesh/STL artifacts;
- presentation screenshots.

**Disposition:** Aero adopts this vocabulary. CFD meshes, voxel grids and solver fields are derived artifacts even when their parent geometry is exact.

### 4. Evidence ladder improvements

Current Native/FEM work distinguishes:

`analysis prepared → not_solved`  
`solver completed → model_unqualified`  
`direct CAD measurement → measured`

**Disposition:** Pass-02 qualification work is **SUPPORTED/STRENGTHENED**. Aero must not use one generic “pass” for solver completion.

### 5. `/v1/run` is harder to misuse

The host increasingly refuses CAD/Aero mutations executed through raw Python and points mutations to the proper Native/Aero control surfaces.

**Disposition:** Raw-exec integration ideas are **SUPERSEDED**.

### 6. Product claim wording is more precise

Upstream explicitly removed wording that called accepted B-rep “manufacturable solid” geometry.

**Disposition:** Aero geometry status must likewise separate accepted/exact B-rep from closed/watertight/CFD-ready/manufacturable/airworthy claims.

## What did NOT change

- VibeCADAero remains the public Aero authority.
- Existing NeuralFoil/AeroSandbox/hover/JSBSim capabilities remain valid.
- `AeroPreview.py` still exists as the current Aero repair preview record.
- `AeroStamp.py` remains low-order-shaped and must become method-aware before CFD lands.
- `AeroResults.py` still needs additive CFD provenance/field/qualification properties.
- FluidX3D and CfdOF source pins remain unchanged.
- The full accepted high-fidelity scope remains intact.

## Still-open upstream/Aero integration issues

1. `/v1/aero` does not yet thread the host Native structural revision through repair propose/apply.
2. Aero repair authorization still has a parallel preview record rather than direct host Native preview ownership.
3. Host preview records are still retained after consume/reject and are not restored as outstanding previews from persisted Native state.
4. Detached job infrastructure remains FEM-specific; there is not yet a stable generic host compute-job API for CFD.
5. Host evidence/artifact vocabulary is better, but Aero report/stamp/context schemas have not adopted it yet.
