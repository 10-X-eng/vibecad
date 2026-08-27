# Engineering Experience component map

## Shell model

The Experience layer is a projection layer, not an engineering owner. The
shared shell is a contextual Engineering dock with pages enabled only when the
underlying contracts exist:

| Shared page | Contract source | Typical domain extensions |
| --- | --- | --- |
| Overview | Current domain summary | Study, Job, Assembly, Robot setup |
| Results | G1 result envelope plus domain payload | Fields, toolpath, simulation, validation |
| Findings | G1 finding envelopes | Geometry, solver, collision, compatibility |
| Activity | G2 attempts and G5 workflow runs | Solve, post, CAM, sequence evaluation |
| Provenance | G1 graph and G2 artifacts/publications | Source revision, solver, output, export |
| Workflow | G5 definition/run/node state | Analysis pipeline, Manufacture detached task |
| Compare | G6 candidates or domain-defined comparable results | Baseline/candidate, flow cases |

Domain pages remain owned by their domains:

- Analyze: Results, Fields, Convergence.
- Manufacture: Toolpath, Simulation, Output.
- Assembly: Interfaces, Joints, Motion, Sequence.
- Service: Target, Removal Set, Access, Uncertainty.
- Robot: Tasks, Frames, Validation.

## Viewport layer model

The implementation should support composable presentation concepts without
prematurely prescribing C++/Python class names:

1. base geometry;
2. selection;
3. engineering field;
4. mesh;
5. interface and connector;
6. finding/evidence marker;
7. governed preview;
8. motion/collision sweep;
9. assembly sequence emphasis; and
10. annotation/legend.

Each layer records its owning domain, source identity/currentness, visibility,
and presentation parameters. A layer cannot grant mutation authority or make a
stronger claim than its source.

## Current repository anchors

| Concern | Current anchor | Integration direction |
| --- | --- | --- |
| Theme | `src/Gui/Stylesheets/VibeDark.qss`, `VibeLight.qss` | Extend domain-specific selectors without replacing the theme |
| Analyze ribbon | `src/Gui/VibeCADRibbon.cpp` | Keep visualization in existing Analyze/domain surfaces |
| Flow result panel | `src/Mod/VibeCAD/VibeCADAnalyzeResultsGui.py` | Refactor underneath the contextual shell while retaining OpenFOAM behavior |
| Legacy FEM presentation | `VibeCADNativeAnalyzePresentation.py` | Wrap exact presentation; do not duplicate it |
| Field/result inspection | `VibeCADNativeAnalyzeResultState.py` | Project bounded field metadata, never copy large arrays into UI contracts |
| Common result/finding/provenance | `tool_impl/engineering_contracts.py` | Render independent state axes and domain payload |
| Durable activity/publication | Analysis persistence/publication facades | Render exact attempts, artifacts, currentness and receipts |
| Workflow | `tool_impl/analysis_workflow.py` | Render definition/run/node state without UI scheduling |
| Optimization | `tool_impl/governed_optimization.py` | Render immutable candidates/ranking; selection remains human-authorized |
| Manufacture | Native Manufacture Job/Post/CAMotics/Simulation owners | Shared shell over Manufacture semantics, not a generic CAM result engine |
| Assembly and Robot | Existing Native Assembly/mechanism/Robot owners | Add overlays only as G8-G12 contracts become authoritative |

