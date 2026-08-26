# Evidence and Artifact Taxonomy — Pass 03

## Why this exists

Pass 03 aligns Aero with the evidence distinctions now present in live VibeCAD. Three independent questions must never be collapsed:

1. **What kind of artifact is this?** exact / derived / presentation.
2. **What happened computationally?** prepared / solved / failed / unavailable.
3. **What claim does the evidence justify?** not_solved / model_unqualified / measured / qualified-model envelope / never airworthy by implication.

## Artifact provenance

| Artifact | Class | Meaning |
|---|---|---|
| Native B-rep / STEP | `exact` | Exact representation artifact from the CAD boundary representation/export path. Does not mean watertight-for-CFD, manufacturable or airworthy. |
| STL/OBJ/surface mesh | `derived` | Tessellated/derived representation. Preserve source geometry hash and conversion settings. |
| OpenFOAM volume mesh | `derived` | Solver discretization derived from geometry/domain/meshing settings. |
| FluidX3D voxel grid | `derived` | Lattice discretization derived from geometry/scaling/domain settings. |
| CFD result/field/VTM/VTK | `derived` | Numerical model output, not exact geometry evidence. |
| Screenshot/render/animation frame | `presentation` | Visual communication only; pixels are not measurements. |

## Evidence state ladder

### `evidence_waiting` + `not_solved`

A case, analysis graph or prepared solver input exists but no accepted solver result exists.

### `capability_unavailable`

The requested solver/compute dependency is unavailable. This is not a failed physics result.

### `failed`

Execution/parsing/validation failed. Preserve logs/artifacts without promoting a result.

### `model_unqualified`

The solver completed and produced a numerically parseable result, but the exact solver build/model/settings have not been shown to satisfy the relevant qualification envelope.

### `model_qualified`

Aero-specific proposed state: the exact solver build/model is backed by versioned benchmark evidence that covers the requested Reynolds/Mach/alpha/geometry regime. This still does not imply airworthiness.

### `measured`

Direct measurement from an authoritative geometric/source object. Do not assign this state to screenshots or numerical CFD output.

## Solver completion is not qualification

A completed FluidX3D/OpenFOAM result should initially carry `model_unqualified` unless an exact `SolverQualification` record applies. Qualification requires matching solver build/version, model, settings family and case envelope.

## Airworthiness

No state in this taxonomy establishes airworthiness. Aero's `not_airworthy` honesty requirement remains orthogonal to numerical fidelity.
