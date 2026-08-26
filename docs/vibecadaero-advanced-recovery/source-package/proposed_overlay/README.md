# VibeCADAero Reconciliation Pass 03 Correction 01 — Proposed Overlay

This tree is a **reference implementation/handoff**, not a drop-in replacement for the live upstream tree.

It contains executable reference semantics for:

- CFD case/result/artifact/frame contracts;
- host-aligned evidence and artifact taxonomy;
- geometry-readiness states distinct from exactness;
- Native revision/result attachment and repair-transition checks;
- detached solver-input hashing/stale-result attachment invariants;
- solver-neutral Aero job-domain records;
- local and Kaggle compute providers;
- FluidX3D and CfdOF/OpenFOAM adapters;
- mesh and field correspondence;
- deterministic/explainable routing;
- versioned solver qualification;
- dynamic/unsteady/6-DOF reference models;
- one-time informational third-party notice.

`AeroJobStore.py` is now explicitly **TRANSITIONAL / REFERENCE ONLY**. Correction 01 requires one host-owned VibeCAD Analysis Runtime extracted from the existing Native Background + detached FEM paths. FEM must prove parity first; Aero then consumes that host service. Do not promote this overlay store into production scheduling/persistence authority.

The first-use notice checkbox text is exactly **“I understand.”** It is informational only and does not classify VibeCAD/Aero use or control solver eligibility.

## Test

From this `proposed_overlay/` directory:

```bash
python -m compileall -q .
pytest -q
```

The package includes `tests/conftest.py`, so no caller-side `PYTHONPATH` is required. Correction-01 validation: **45 tests passed**.


## Reference host-runtime proof

`reference_host_runtime/VibeCADAnalysisJobState.py` is a FreeCAD-independent proof model for the atomic cancellation/publication gate identified during Correction-01 deepening. `reference_host_runtime/VibeCADAnalysisPublication.py` models inert durable publication descriptors/currentness and proves that source/currentness/fresh-host-authorization are distinct prerequisites. It is **not** claimed to be installed or integrated upstream. Production implementation must follow the compatibility, document-lifecycle, persistence and process-control gates in the package root.
