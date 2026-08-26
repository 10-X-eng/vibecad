# Geometry Readiness Model — Pass 03

## Core correction

`artifact_class=exact` is a provenance statement, not a CFD-readiness statement.

An exact Native B-rep can contain open shells, self-intersections, unsuitable slivers, disconnected bodies, invalid normals or topology that cannot produce a trustworthy external-fluid domain. Conversely, a derived surface mesh can be the correct solver input once its derivation and checks are recorded.

## Canonical readiness ladder

1. `unknown`
2. `brep_accepted`
3. `surface_closed`
4. `surface_watertight`
5. `fluid_domain_ready`
6. `mesh_ready`
7. `solver_input_frozen`

Each transition records checks and failures; it does not rely on filenames.

## Required checks by stage

### B-rep accepted

- source object exists;
- shape validity inspected;
- intended aerodynamic bodies resolved;
- frame/reference transform resolved.

### Surface closed / watertight

- closed-shell expectation explicit;
- manifold/topology checks;
- no uncontrolled holes/self-intersections;
- component role/moving-body identity retained;
- tessellation tolerance recorded.

### Fluid-domain ready

- external/internal domain type explicit;
- far-field/domain extents recorded;
- inlet/outlet/farfield/wall roles unambiguous;
- blockage/domain rules checked.

### Mesh ready

- mesher/version/settings recorded;
- element/cell validity checks;
- near-wall/refinement policy recorded;
- source-face correspondence retained where fields will return to CAD.

### Solver input frozen

- input tree/content hash;
- geometry/case/native revision identity;
- solver build/model/settings;
- immutable work directory or equivalent frozen artifact.

## Claims explicitly NOT implied

None of these statuses mean “manufacturable,” “flight tested,” or “airworthy.”
