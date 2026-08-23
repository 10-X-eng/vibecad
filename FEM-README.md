# Analysis in VibeCAD

The Analyze ribbon is where a model becomes an engineering study. The intended
workflow starts with the result the user needs, not with knowledge of solver
objects or command order. A user should be able to ask VibeCAD to analyze a
part, assembly, thermal problem, or flow problem; VibeCAD should inspect the
document, prepare the study, and ask only for engineering inputs that cannot be
determined from the model.

The current ribbon still exposes many finite-element building blocks directly.
A guided, study-first workflow and higher-quality AI analysis tools are under
development.

## Current solver support

| Solver | VibeCAD integration | Runtime availability |
| --- | --- | --- |
| CalculiX | Structural writer, runner, and result import | Included in packaged builds |
| Elmer | Multiphysics writer, runner, and result import | Install `ElmerSolver` and `ElmerGrid` separately |
| OpenFOAM | Not yet connected to VibeCAD studies | Not included |

Elmer support in the document model does not mean the Elmer applications are
installed. VibeCAD must be able to resolve both `ElmerSolver` and `ElmerGrid`.
Put them on the operating-system executable path or set their exact locations in
**Preferences > FEM > Elmer**. MPI is optional; parallel Elmer runs also require
the configured MPI launcher.

OpenFOAM is currently a platform integration limitation, not merely a missing
executable setting. VibeCAD does not yet write an OpenFOAM case, execute it, or
import its results. OpenFOAM runs natively on Linux; the OpenFOAM Foundation's
supported Windows route uses WSL and its macOS route uses Multipass. VibeCAD
needs explicit runners for those environments before it can offer the same CFD
workflow on all three platforms. See the [official OpenFOAM downloads](https://openfoam.org/download/)
and the [official Elmer project](https://github.com/ElmerCSC/elmerfem).

## Starting an analysis today

1. Open a saved document containing valid analysis geometry.
2. Select the **Analyze** ribbon.
3. Choose **New Analysis**. This creates an analysis container and, if configured
   in FEM preferences, its default solver.
4. Add the material and physics equation required by the study.
5. Select exact model faces or bodies and add loads and boundary conditions.
6. Create a Gmsh or Netgen FEM mesh and inspect its quality.
7. Open solver control, verify prerequisites, and run the solver.
8. Inspect the imported result and create post-processing views as needed.

The solver does not infer missing engineering inputs. A generated mesh and a
solver object alone are not a ready study: material properties, physics, units,
loads, and boundary conditions must form a complete and physically meaningful
problem.

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
5. OpenFOAM case generation, cancellable execution, and result import;
6. unsteered Qwen and GPT-5.6 Terra validation against real solver artifacts.

The tool-tuning method and acceptance rules are recorded in
[docs/fea-tool-sharpening.md](docs/fea-tool-sharpening.md).
