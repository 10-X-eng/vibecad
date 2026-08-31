# Mesh ribbon: production tools and validation

## Purpose

The Mesh ribbon handles downloaded triangle meshes and point clouds: import,
inspect, repair, reduce, separate or combine, reconstruct approximate geometry,
and convert to CAD topology when the mesh permits it. Humans choose the ribbon;
Native assistance receives the same operations and refreshes its tool surface
between turns.

Every result names its real representation. A mesh remains a mesh. Mesh-to-shape
creates a faceted shell or solid, not recovered design intent. Creating sketches,
features, and dimensions from scan geometry is parametric reconstruction and is a
separate modeling task.

## Production surface

The composed ribbon has 52 Mesh, Points, and reverse-engineering actions. A saved
document exposes 22 focused Mesh families plus eight shared tools, at most 30 tools
total. Document state can remove an inapplicable shared tool before a turn.

| Human purpose | Native tools | Production behavior |
|---|---|---|
| Import and regular meshes | `mesh.io` | Imports a human-selected file in the background or creates one editable regular-mesh feature. |
| Export | `mesh.export` | Exports one exact current mesh or point cloud through a human-authorized path, without mutating History. |
| CAD to mesh | `mesh.from_shape` | Tessellates current CAD geometry with standard or Gmsh settings in an isolated process. |
| Mesh to CAD | `mesh.to_shape` | Creates an automatically refined faceted shell or, only from valid closed volume topology, a refined faceted solid. |
| Curve on mesh | `mesh.curve_on_mesh` | Projects anchors to the exact mesh and retains an editable linked curve. |
| Repair | `mesh.repair`, `mesh.fill_holes` | Repairs requested defect classes or selected boundaries; a clean repair is a verified no-op. |
| Modify and reduce | `mesh.modify`, `mesh.smooth`, `mesh.remesh`, `mesh.decimate`, `mesh.scale` | Runs retained parameterized operations against exact source state. |
| Boolean and cut | `mesh.boolean`, `mesh.cut` | Validates operands and publishes exact retained outputs or a specific refusal. |
| Components | `mesh.combine`, `mesh.separate`, `mesh.segment` | Combines meshes, separates connected components, or creates linked facet subsets. |
| Inspect | `mesh.inspect`, `mesh.curvature` | Reads exact topology, defects, bounds, facets, solid state, and retained curvature without changing revision. |
| Compare | `inspect.compare` | Computes cached signed deviations in an isolated process and publishes frozen Inspection results without blocking the document thread. |
| Point clouds | `mesh.points` | Imports, converts, structures, merges, or cuts point clouds while preserving supported attributes. |
| Reconstruction | `mesh.rebuild`, `mesh.approximate` | Runs Poisson reconstruction, triangulation, and analytic or polynomial fitting as approximate outputs. |
| Parametric reconstruction | `mesh.reconstruct_parametric` | Separate modeling task: ask the human to select printables `reverse/<body>.ir.json` (`schema_version: 1`), validate it off-thread, rebuild one retained millimetre B-rep, and optionally publish human-authorized STEP/STL files. Does **not** overload `mesh.to_shape`. |

The shared tools are `native.job`, `state.read`, `view.control`, `inspect.query`,
`inspect.compare`, `document.save`, `document.undo`, and `workspace.switch`. Provider schemas for a
complete saved-document surface currently occupy about 44 KiB.

## Operating model

Heavy geometry preparation runs away from the Qt document thread. The document
thread only validates current state and commits the prepared result in one
transaction. Each job reports progress, supports cancellation where the kernel can
stop safely, rejects stale source state, and caches immutable preparation inputs.
Human commands and Native calls share these operations.

Mutations are one undo step and survive save/reopen. Replacement operations hide
only the inputs they replace. Source-preserving operations retain their inputs.
Mesh results are grouped under **Meshes** in the tree; multi-output operations use
one expandable operation group with linked result resources.

## Common workflows

### Downloaded mesh

1. Import the file.
2. Inspect topology and defects.
3. Apply only the indicated repair operations and inspect again.
4. Reduce or remesh only when the intended tolerance allows it.
5. Convert to a faceted shell or solid only when the verified topology supports it.

### CAD geometry for printing or exchange

1. Select current CAD geometry.
2. Create a mesh with the required surface and angular deviation.
3. Inspect the resulting mesh.
4. Export the exact current mesh.

### Multiple components

Use `mesh.separate` when connected components need stable individual identities.
Use `mesh.combine` when separate mesh objects should become one mesh object. Combining
does not weld disconnected topology or imply one solid volume.

## Formats

Mesh import accepts STL/AST, BMS, OBJ, OFF, Inventor, PLY, Nastran/BDF, and 3MF.
Mesh export supports binary or ASCII STL, binary mesh, OBJ, OFF, PLY, Nastran, and
3MF. Point-cloud export supports ASC, PCD, and PLY.

STL carries no authoritative unit metadata. Placement and current document units
remain explicit VibeCAD state; import does not guess scale from shape size.

Current 3MF import uses the Mesh kernel's flattened mesh result. Multiple build
items, repeated instances, names, and declared model units are not yet retained as
separate document objects. This limitation is shared by human and Native import and
must not be described as structured 3MF import.

## Conversion limits

- Open, non-manifold, self-intersecting, or multi-volume topology is not silently
  converted into one solid.
- Conversion automatically removes redundant splitter edges and merges adjacent
  coplanar faces. It does not infer analytic curves or recover design intent.
- Repair can change triangles. When exact geometry must be preserved, an invalid
  solid conversion is refused.
- A faceted solid is editable CAD topology but is not a recovered feature tree.
- Parametric reconstruction from scan/mesh sketches is `mesh.reconstruct_parametric`. It consumes printables reverse IR and must not be implemented by calling `mesh.to_shape`.
- Reconstruction and surface fitting are approximations and report their retained
  parameters; they do not claim exact reverse engineering.
- Netgen-dependent paths exist only in builds that include Netgen.

## Reusable corpus

Deterministic fixtures cover a clean tetrahedron, duplicate facets, an open
tetrahedron, disconnected components, a CAD box, malformed/empty input, and bounded
mesh density. The local benchmark directory also contains a generated two-object
3MF in millimetres and a generated STEP box reference; the zero-facet 3MF is created
inside the import acceptance test. These fixtures exercise representation and file
handling without third-party geometry. Real holdouts use the official
[Thingi10K dataset](https://github.com/Thingi10K/Thingi10K), selected from published
geometry properties rather than filenames. Thingi10K's tooling is Apache-2.0, while
each model keeps its own license.

The local holdout set is stored outside the repository under
`~/Documents/VibeCAD-Mesh-Benchmarks/thingi10k`:

| Case | File and attribution | Published license | VibeCAD strict evaluation |
|---|---|---|---|
| Clean solid | [110173, Double Sided Maze](https://thingiverse-production-new.s3.amazonaws.com/assets/2b/cf/74/15/be/lid.stl), totaldorkist | CC BY-SA | 100 facets, one component, watertight, no repair defects |
| Open/mixed defects | [73335, Durga Puppet](https://thingiverse-production-new.s3.amazonaws.com/assets/fb/c4/87/2c/4a/durgaarmleft.stl), anneyfresh | CC BY | 150 facets, open topology, orientation/non-manifold/degenerate defects |
| Non-manifold | [93074, Johnson Polyhedra Duals](https://thingiverse-production-new.s3.amazonaws.com/assets/66/e3/b7/79/15/j16_dual.stl), pmoews | CC BY | 34 facets, open and non-manifold |
| Orientation | [73337, Durga Puppet](https://thingiverse-production-new.s3.amazonaws.com/assets/a2/15/5f/58/ab/durgaback.stl), anneyfresh | CC BY | 316 facets, watertight with non-uniform orientation findings |
| Degenerate | [55278, FOBO](https://thingiverse-production-new.s3.amazonaws.com/assets/d7/89/8e/48/2c/part7_electronics_frame.stl), jdow | CC BY-NC | 432 facets with degeneration, duplication, and open topology |
| Multiple components | [54229, Johnson Polyhedra](https://thingiverse-production-new.s3.amazonaws.com/assets/87/d4/12/f1/73/hollow_j26.stl), pmoews | CC BY | 24 facets, two watertight components |
| Self-intersection | [59849, Truchet Tiles](https://thingiverse-production-new.s3.amazonaws.com/assets/12/4e/89/8a/e3/simple_frame.stl), DigitalBytes | CC BY | 28 facets with self-intersections and open topology |
| Dense clean solid | [472034, Big Boy Locomotive](https://thingiverse-production-new.s3.amazonaws.com/assets/20/a1/48/c3/78/Stoker_Pipe_1.STL), MakerBot | CC BY | 50,508 facets, one watertight component |

The published dataset metadata and VibeCAD's post-import evaluation are both kept
because readers can weld or reinterpret input topology. The VibeCAD result is the
acceptance authority for platform behavior.

## Validation evidence

Compiled GUI gates cover import/export, repair and modification, segmentation,
boolean/cut, inspection, curvature, standard and Gmsh tessellation, mesh conversion,
point clouds, and reverse engineering. They verify background dispatch, cancellation,
stale-result rejection, source visibility, tree identity, undo/redo, and save/reopen.
Visual Inspection also covers the human dialog path, multiple actual objects in one
atomic result group, cached reuse, and frozen save/reopen results.

Unsteered Qwen runs passed clean inspection, duplicate repair, hole filling,
component separation and combination, decimation, CAD-to-mesh conversion, and honest
refusal of invalid solid conversion with no failed tool calls in the final runs.
GPT-5.6 Terra at high effort independently selected `mesh.from_shape` for a CAD box
and produced one 12-facet watertight mesh with exact 20 x 15 x 10 mm bounds. It also
inspected a disconnected open STL and refused single-solid conversion without
mutation or failed calls.

### Model tuning record

The saved conversations and result documents remain in VibeCAD's local project
store and `~/Documents/VibeCAD-Mesh-Benchmarks`. Ordinary prompts were repeated on
fresh fixtures; no tool-use directions were added to the prompts.

| Prompt family | Observed failure | Production or fixture correction | Final evidence |
|---|---|---|---|
| Inspect and repair clean bunny | Qwen treated steep normal transitions as defects and tried seven unrelated mutations. | Inspection now distinguishes geometric transitions from repair defects; clean repair is an explicit no-op. | Qwen reported one watertight component, no repair defects, and made no mutation. |
| Repair duplicated tetrahedron | The broad repair path initially failed individual defect requests and encouraged tool hopping. | Repair accepts a defect set atomically and reports remaining open topology separately. | Qwen removed the duplicate/non-manifold topology and verified a watertight 56 mm³ result. |
| Fill open tetrahedron | No final-path failure. | Focused boundary discovery and hole-fill contract retained. | Qwen selected hole filling and verified zero open edges and 56 mm³ volume. |
| Separate components | Initial output labels were weak, but geometry and operation were correct. | Stable linked component results and exact topology returns. | Qwen created two `Mesh::FacetSubset` results with exact facet counts and bounds. |
| Combine two meshes | The first fixture already contained three prior merge results. | Every later run started from a clean source-only document. | Two clean runs selected combine once and returned one retained 8-facet merge. |
| Convert STL to editable solid | Qwen first confused editable mesh/shell/solid representations and did not call conversion. | The generic conversion choice was replaced on the provider surface by focused `mesh.to_shape` shell/solid variants with topology preconditions. | Qwen produced and identified one four-face faceted solid; invalid two-component input was refused without mutation. |
| Reduce mesh | No contract failure in the focused path. | Real-valued target/reduction parameters and exact postconditions retained. | Qwen produced requested 12,000- and 120-facet results. |
| Tessellate CAD solid | An early fixture had no source solid; later runs sometimes observed a result left by setup. | Fresh source-only STEP/FCStd fixture and exact shape target. | Qwen and Terra independently selected `mesh.from_shape`; final result was 12 facets with exact 20 x 15 x 10 mm bounds. |

These failures were classified as wrong selection, invalid fixture state, runtime or
postcondition failure, misleading result, or geometry regression before changing a
contract. Model taste was not used as evidence of a platform defect.
