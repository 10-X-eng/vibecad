# Analysis in VibeCAD

The Analyze ribbon is where a model becomes an engineering study. The intended
workflow starts with the result the user needs, not with knowledge of solver
objects or command order. A user should be able to ask VibeCAD to analyze a
part, assembly, thermal problem, or flow problem; VibeCAD should inspect the
document, prepare the study, and ask only for engineering inputs that cannot be
determined from the model.

The ribbon and VibeCAD assistant use the same document operations. Switching to
the **Analyze** ribbon changes the assistant's tools on the next message without
changing conversations or modifying the source CAD geometry.

## Current solver support

| Solver | VibeCAD integration | Runtime availability |
| --- | --- | --- |
| CalculiX | Structural writer, runner, and result import | Included in packaged builds |
| Elmer | Multiphysics writer, background runner, and result import | Install `ElmerSolver` and `ElmerGrid` separately |
| OpenFOAM | Steady incompressible laminar case writer, background runner, and VTK result import | Install OpenFOAM Foundation 14 on Linux |

Elmer support in the document model does not mean the Elmer applications are
installed. VibeCAD must be able to resolve both `ElmerSolver` and `ElmerGrid`.
Put them on the operating-system executable path or set their exact locations in
**Preferences > FEM > Elmer**. MPI is optional; parallel Elmer runs also require
the configured MPI launcher.

The current OpenFOAM integration is native Linux only. It resolves and runs
`ideasUnvToFoam`, `transformPoints`, `checkMesh`, `foamRun`, and `foamToVTK`
from the installed OpenFOAM environment. On Ubuntu 24.04, follow the
[OpenFOAM Foundation package instructions](https://openfoam.org/download/ubuntu/)
and install `openfoam14`. The Foundation's supported Windows route uses WSL and
its macOS route uses Multipass; VibeCAD does not yet bridge those environments.
See the [official Elmer project](https://github.com/ElmerCSC/elmerfem) for Elmer
packages and source builds.

## Starting an analysis today

1. Open a saved document containing valid analysis geometry.
2. Select the **Analyze** ribbon.
3. Choose **Study Setup**, name the study, and select its physics and study type.
   The same panel shows the exact geometry, material, condition, mesh, solver,
   and result state as the study is completed.
4. Use the **Assignments** section to inspect each material, load, boundary,
   connection, and mesh region. **Highlight** selects its exact geometry,
   **Isolate** hides the other study geometry until **Show All**, **Edit** opens
   its native editor, and **Validate** checks every live reference.
5. Add the material and physics equation required by the study.
6. Select exact model faces or bodies and add loads and boundary conditions.
7. Create a Gmsh or Netgen FEM mesh and inspect its quality.
8. Select the solver and choose **Run Solver**. A progress window remains
   cancellable while the solver runs outside the UI thread.
9. Inspect the imported result and create post-processing views as needed.

The solver does not infer missing engineering inputs. A generated mesh and a
solver object alone are not a ready study: material properties, physics, units,
loads, and boundary conditions must form a complete and physically meaningful
problem.

## Current OpenFOAM workflow

The initial OpenFOAM path solves one steady, incompressible, isothermal,
laminar fluid domain:

1. Create or select the solid that represents the fluid volume, not the
   surrounding hardware.
2. Declare a steady fluid study and add one whole-domain fluid material with
   positive density and kinematic viscosity.
3. Create and generate one first-order Gmsh volume mesh from that domain.
4. Assign one typed fluid boundary to every domain face. Each face belongs to
   exactly one boundary; at least one inlet or outlet must define pressure.
5. Add an OpenFOAM solver, adjust iteration and residual settings if needed,
   then choose **Run Solver**.
6. Inspect the imported FEM post pipeline and its durable solver-output record.

Supported face conditions are no-slip and slip walls, symmetry, velocity,
volumetric-flow and mass-flow inlets, total-pressure inlet/outlet, static-
pressure outlet, velocity outlet, and outflow outlet. Turbulence, heat transfer,
compressible flow, transient flow, moving/rotating regions, multiphase flow,
multiple fluid regions, automatic exterior-fluid construction, and Windows or
macOS runtime bridges are not implemented yet. VibeCAD rejects those study
definitions instead of silently changing their physics.

## What VibeCAD assistance must provide

VibeCAD assistance should turn a request such as “check this bracket for the
expected load” or “analyze airflow through this duct” into a complete proposed
study. It should:

1. inspect the selected geometry and existing document state;
2. identify the analysis type and solver capabilities required;
3. show the proposed materials, equations, loads, constraints, and boundaries;
4. ask concise questions for missing physical values or design intent;
5. create the study, mesh it, validate it, and report exact blockers;
6. run the solver without blocking the interface; and
7. present convergence, units, extrema, and result views in useful engineering
   terms.

Loads and boundaries must be easy to verify before solving. Each one needs a
stable name, its physical value and units, its referenced faces or regions, and
a clear viewport overlay. Users and the assistant need the same operations to
list, highlight, isolate, edit, and validate these assignments. Hidden face
indices or an unstructured object dump are not an acceptable interface.

## CFD geometry is different from CAD geometry

Solid CAD normally describes the hardware, while CFD solves the fluid around or
inside it. A CFD study therefore needs a valid fluid domain plus named inlet,
outlet or far-field boundaries, walls, and any rotating or moving regions.
VibeCAD must make those regions visible and preserve their identities when the
CAD model changes.

## Work in progress

The FEA/CFD work is being delivered in this order:

1. reliable solver discovery and diagnostics shared by the ribbon and AI;
2. packaged or explicitly bridged Elmer and OpenFOAM runtimes;
3. a study-first Analyze entry flow;
4. visible, inspectable boundary and load assignments;
5. expand OpenFOAM beyond the current Linux steady-laminar path;
6. unsteered Qwen and GPT-5.6 Terra validation against real solver artifacts.

The tool-tuning method and acceptance rules are recorded in
[docs/fea-tool-sharpening.md](docs/fea-tool-sharpening.md).
