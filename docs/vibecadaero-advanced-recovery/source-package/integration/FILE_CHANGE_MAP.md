# Integration File-Change Map — Pass 03 Correction 01

This is a target map, not an instruction to overwrite live files.

## Existing VibeCADAero files to extend

### `src/Mod/VibeCADAero/VibeCADAero.py`

Add public high-fidelity case/solve/job/status/result entry points while retaining all current low-order/report/repair APIs.

### `src/Mod/VibeCADAero/AeroStamp.py`

Generalize method/evidence/qualification stamps. Remove the assumption that every Aero result is “not CFD”; retain `not_airworthy` honesty.

### `src/Mod/VibeCADAero/AeroResults.py`

Add case/solver/job/provenance/qualification/field/current-stale properties and external artifact references. Do not replace the FeaturePython authority.

### `src/Mod/VibeCADAero/AeroConfig.py`

Add atmosphere/reference/body-frame/solver transform and solver settings while preserving current defaults.

### `src/Mod/VibeCADAero/AeroPreview.py`

Preserve geometry fingerprint compatibility. Converge mutation authorization toward host Native preview control rather than expanding this into a general preview system.

### `src/Mod/VibeCADAero/Commands.py`, `InitGui.py`

Extend existing Aero ribbon/control surface for cases/jobs/fields. Do not revive a parallel legacy workbench.

### `src/Mod/VibeCADAero/CMakeLists.txt`

Enumerate every new runtime/test/vendor integration file explicitly.

## Existing VibeCAD host files/seams to integrate carefully

### `src/Mod/VibeCAD/VibeCADAgentControl.py`

Thread actual Native structural revision into `/v1/aero` repair proposal/application. Add job/status high-fidelity Aero operations through the same public Aero authority; do not re-enable raw mutation exec.

### Native preview control/state/dispatch

Prefer registration/adapters over invasive duplication. Use host list/apply/reject/user-explicit preservation for CAD-changing Aero operations once supported cleanly.

### Host Analysis Runtime — new VibeCAD infrastructure target

Add, using live naming conventions after re-reconciliation, a host-level analysis namespace conceptually equivalent to:

- `VibeCADAnalysisContracts.py`
- `VibeCADAnalysisDependencies.py`
- `VibeCADAnalysisArtifacts.py`
- `VibeCADAnalysisProviders.py`
- `VibeCADAnalysisLocalProvider.py`
- `VibeCADAnalysisRuntime.py`
- `VibeCADAnalysisJobs.py`
- `VibeCADAnalysisPublication.py`
- `VibeCADAnalysisPersistence.py` **only in the later persistence phase**

Do not drop these files into upstream wholesale. Extract functionality behind compatibility facades in the staged plan.

### Existing Native Background files

Preserve `VibeCADNativeBackground.py`, `VibeCADNativeBackgroundRuntime.py`, and `VibeCADNativeBackgroundSchema.py` public behavior during migration. They become compatibility/orchestration facades over the host runtime rather than being deleted early.

### Existing detached FEM files

Extract only generic process/artifact/orchestration mechanics. Keep FEM state, solver builders/importers, result semantics and current public functions compatible. `VibeCADNativeAnalyzeSolverState.py` remains FEM-owned.

### Native mutation boundary

Keep existing Native transaction/recompute/receipt authority. Analysis runtime publication routes through it; worker execution never replaces it.

### `VibeCADAeroContext.py`

Expose bounded high-fidelity status/evidence without solver-trace bloat.

## Proposed new VibeCADAero modules from reference overlay

- `AeroCFDContracts.py`
- `AeroCFD.py`
- `AeroHostEvidence.py`
- `AeroGeometryReadiness.py`
- `AeroNativeRepairBridge.py` (transition/reference; may disappear once direct host integration lands)
- `AeroDetachedExecution.py` (**transitional reference; generic mechanics target host runtime**)
- `AeroJobStore.py` (**transitional/reference only; not production job authority**)
- `AeroRouting.py`
- `AeroQualification.py`
- `AeroLocalCompute.py`
- `AeroKaggle.py`
- `AeroLBM.py`
- `AeroOpenFOAM.py`
- `AeroMesh.py`
- `AeroFieldResults.py`
- `AeroDynamicStall.py`
- `AeroStripTheory.py`
- `AeroUnsteady.py`
- `AeroSixDOF.py`
- `openfoam_collect.py`

## Vendor/build target

`src/Mod/VibeCADAero/vendor/FluidX3D/` (or repository-standard third-party equivalent decided at integration time) with pinned upstream source and authoritative license/notice materials. Do not invent product-wide purpose/use profiles.
