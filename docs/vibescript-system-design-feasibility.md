# Workbench-specific native and VibeScript architecture

## Scope

VibeCAD has one human-selected global modeling engine per saved project. The
engine and active FreeCAD workbench resolve to exactly one CAD authoring
surface. The resolver is shared by ChatGPT subscription, OpenAI-compatible,
Anthropic, offline/debug, and editor paths.

The core invariant is:

```text
(active workbench, modeling engine) -> one exact CAD pack
```

There is no union, fallback, adjacent-pack injection, or cross-workbench tool
discovery. Conversation and view tools remain common, but CAD authoring tools
come from exactly one of these sources:

- `native`: the active `WorkbenchToolPack`;
- `vibescript`: the active `VibeScriptWorkbenchPack`;
- `build123d`: its existing Part Design-only surface; or
- `openscad`: its existing Part Design-only surface.

The AI cannot select a workbench or modeling engine.

## Current production status

This document describes the enforced architecture and its delivery gate.
Metadata or a prototype adapter does not make a domain available. A domain is
exposed only after its real native worker, validator, publisher, persistence
lifecycle, save/reopen behavior, diagnostics, and integration tests pass the
production-readiness gate.

All 17 supported user-workbench domains currently pass that gate: Part Design,
Sketcher, Part, Draft, Surface, Assembly, Spreadsheet, Material, Mesh,
MeshPart, Points, Reverse Engineering, Inspection, Robot, FEM, CAM, and
TechDraw. `NoneWorkbench`, `TestWorkbench`, unknown workbenches, and any future
domain without a complete implementation still resolve to a precise core-only
unavailable surface.

## Global engine behavior

Project manifests persist `modeling_engine` with one of `native`, `vibescript`,
`build123d`, or `openscad`. New projects default to VibeScript. Version-1
manifests that stored `partdesign_engine` are read once and rewritten with
`modeling_engine`; there is no second engine accessor or forwarding API.

The selector is visible in every supported user workbench. Native and
VibeScript are global choices. build123d and OpenSCAD are listed only in Part
Design and only when their preference and runtime are ready. If the human
leaves Part Design with either selected, VibeCAD visibly persists VibeScript
before the next provider turn. Returning to Part Design does not restore the
old engine.

Engine changes are rejected while a FreeCAD edit session is open. The GUI also
locks the selector during an AI run. If a workbench changes during a run, the
turn snapshot becomes invalid immediately and the required Part Design-only
transition is deferred until the run ends.

## Exact surface resolution

Each turn records:

- workbench;
- engine;
- VibeScript domain, when present;
- deterministic surface ID;
- exact ordered tool names;
- schema count; and
- SHA-256 schema digest.

Every attempted call is checked against the live workbench, engine, and
surface ID. A change rejects the old call with `TURN_SURFACE_INVALIDATED`; the
new surface is used on the next turn. Duplicate names, malformed schemas,
multiple scripted engines, native-plus-VibeScript declarations, foreign native
packs, multiple VibeScript domains, and undeclared calls fail closed.

`NoneWorkbench`, `TestWorkbench`, an unknown workbench, a native pack with no
authoring implementation, and a VibeScript domain that has not passed its
production gate receive only core conversation/view capabilities and a precise
unavailable reason.

## Provider and context boundary

Every provider path uses the same sparse turn-start contract. The current user
message is sent exactly once. Conversation turns, persisted tool traces, Intent
Memory, inferred working sets, object inventories, shape summaries, domain
snapshots, and recompute diagnostics are not injected. Saved conversations are
a human UI record, not a second model-context channel.

The deterministic CAD packet contains only:

- active workbench, global engine, domain, surface ID, and availability;
- document name, stable UID, object count, and current edit object; and
- the exact explicit selection, or exact size/count omission metadata when that
  selection exceeds its fixed limit.

Exact active tool schemas are sent through the provider's tool-declaration
channel and have a hard aggregate byte limit. Native mode declares only the
active native authoring pack. VibeScript mode declares only five mutation tools
for its active domain. Both use `core.inspect` as the single model-facing read
interface. Domain-qualified `describe_api` and `inspect_program` are internal
lifecycle operations reached through `core.inspect`; no unqualified VibeScript
entry points or forwarding aliases remain registered.

`core.inspect` reads only an explicitly requested document page, selection,
object, active domain, program, runtime API, or project reference image. Results
are deterministic, JSON-Pointer addressable, and capped at 32 KiB. Domain API
inspection is generated from the actual exported runtime API. Newly attached
reference images and **Attach View** are delivered once; persisted images are
not resent merely because another turn starts. Context-debug captures show the
exact outbound request, resolved surface, and declarations.

## Part Design domain contract

Part Design uses the same schema-v2 registry, seven-operation lifecycle,
isolated worker, revision guards, failed-candidate retention, inspection path,
and stable publication boundary as every other VibeScript domain. Its only
domain-specific behavior is its explicit source-parametric Body/sketch/feature
API and the requirement that every published output validate as exactly one OCC
Solid. The model sees the same five mutation tools plus `core.inspect` that it
sees in every other VibeScript workbench. There is no separate Part Design
runner, unqualified tool namespace, or alternate editor path.

Saved schema-v1 Part Design data is discovered in its existing artifact
directory and presented as a non-executable migration record. Its accepted live
objects remain in the document, but source edits and input changes are rejected
until `vibescript.partdesign.reconfigure_program` supplies a complete schema-v2
source, input contract, inputs, and outputs. Schema-v1 source never executes.

## Schema-v2 program contract

Every VibeScript program persists:

- a stable 32-character program ID;
- domain and workbench;
- source;
- a bounded JSON input schema;
- validated inputs;
- stable named, typed expected outputs;
- working and accepted revisions;
- the complete accepted contract;
- latest candidate and durable attempt directory; and
- stable live output identities.

Inputs support bounded JSON scalars, arrays, enums, and references containing
exactly `document_uid` and `object_name`. Raw filesystem paths, traversal,
arbitrary objects, non-finite numbers, and unbounded arrays are rejected.

Source receives only immutable `doc` metadata, immutable validated `inputs`,
and the active domain `api`. Imports, private/dunder access, `open`, dynamic
execution, document lifecycle methods, GUI access, and unrestricted file or
network access are unavailable. Every `result` key must exactly match the
declared output order and every value must originate from the active domain
API.

## Common lifecycle

Every domain exposes exactly five qualified mutation tools to the model:

```text
vibescript.<domain>.create_program
vibescript.<domain>.edit_source
vibescript.<domain>.set_inputs
vibescript.<domain>.reconfigure_program
vibescript.<domain>.delete_program
```

The complete VibeScript provider surface contains at most eleven tools: bounded
shared conversation/view/inspection capabilities and five domain mutation
tools. Domain-qualified `describe_api` and `inspect_program` complete the common
seven-operation internal lifecycle but are reached through `core.inspect`, not
declared redundantly to the model.

The lifecycle is designed around the model's operating loop, not a human-only
editor workflow. At turn start the model receives only the sparse surface and
document packet described above. It calls `core.inspect` with `scope='domain'`
to discover existing programs and exact document references, `scope='api'` for
actual runtime exports, and `scope='program'` for source, revisions, and stable
live identities. This avoids paying for unrelated domain data and prevents an
absent turn-start inventory from being mistaken for an empty document.

The three mutation tools are intentionally non-overlapping: `edit_source` is an
exact source-only edit, `set_inputs` is an RFC 7396 value-only patch, and
`reconfigure_program` replaces source, schema, inputs, and output declarations
together when the contract really changes. Tool descriptions and inspection
results tell the model which one to choose. A successful write returns the exact
next revision guard and a `core.inspect` program verification call. A failed
candidate returns the failed working revision, whether an accepted live state
was preserved, and the exact inspection/repair loop. `core.inspect` program scope reports
the same machine-readable state, preventing the model from accidentally using
the older accepted revision as its next write guard.

Updates use a working-revision guard. `edit_source` performs ordered exact
replacements and rejects any old string that does not occur exactly once.
Candidates are persisted before execution. Execution, validation, and
publication failures retain the attempt and its diagnostics while the previous
accepted revision and live objects remain authoritative.

Successful regeneration updates compatible native objects in place. Changing
the native type of an existing stable output is rejected. Deletion first
quarantines artifacts off the document thread, removes only objects carrying
the program identity in one transaction, restores the quarantine if native
deletion fails, and purges it after success.

## Worker and publication boundary

Provider requests and source preparation run on background threads. Source
executes only in a windowless `FreeCADCmd --safe-mode` process with timeout,
memory, CPU, file-size, descriptor, and operation limits. The worker writes a
bounded JSON report and project-local candidate artifacts rather than using
unbounded process pipes.

For production-ready geometry domains, BREP construction and validation happen
outside the GUI process. Detached values are imported and validated on the
provider worker, and the document thread performs only bounded publication. It
does not wait for a provider, subprocess, or artifact operation and does not
run a synchronous fallback.

Assembly solving follows this boundary: authenticated component BREPs,
connector-frame derivation, native joint construction, solve, placement
readback, kinematic simulation, native exploded-view construction, exploded
placement/line derivation, and diagnostics all run in the isolated worker. The
host independently rederives exploded bounds, ordered placements, and line
endpoints from authenticated BREPs. The document thread only applies validated
links, placements, detached connector frames, joint parameters, simulation
settings, exploded move transforms/references, and diagnostic properties; it
does not recompute, solve, calculate an explosion, or read an artifact.

Sketcher follows the same boundary. A windowless worker constructs the real
`Sketcher::SketchObject`, applies attachment and authenticated external
references, adds constraints in one no-solve batch, solves, and returns native
DoF, conflict, redundancy, residual, and profile diagnostics. The live
publisher recreates only that validated graph in the stable sketch identity;
it neither solves nor recomputes on the document thread.

Draft follows the same boundary. A windowless worker creates and recomputes
the real Draft proxies, expands array placements, and exports validated shape
artifacts plus exact editable-property readback. The live publisher applies
only those detached shapes, properties, placements, and links to stable native
Draft identities. It performs no proxy execution, recompute, or geometry work.

Surface follows the same boundary. B-spline construction, variational filling,
geometric blending, native extension, lofting, offset/thickness operations,
sewing, topology validation, and BREP export run in the isolated worker. The
document thread assigns only an already validated detached Shape and bounded
operation readback to a stable inert `Part::Feature`; it performs no OCC
construction, native Surface recompute, or BREP I/O.

Spreadsheet follows the same boundary. The isolated worker creates a real
`Spreadsheet::Sheet`, applies the complete cell/alias/formula/unit/format batch,
recomputes it, rejects invalid native state, and returns deterministic assigned
state plus bounded evaluated-value readback. Live publication replays that
validated batch without recompute and must reproduce the worker digest. Because
FreeCAD document transactions do not restore the sheet's internal cell store,
VibeCAD also captures the accepted definition before mutation and explicitly
replays it after an aborted publication; the restored digest must match. A live
sheet edited outside its accepted VibeScript revision is detected before
mutation rather than silently overwritten.

Material follows the same boundary. The isolated worker opens the native
FreeCAD material catalog, resolves exact UUIDs, hashes the complete bounded card,
and verifies explicitly required physical and appearance properties. The host
independently resolves the same card away from the document thread. Live
publication receives the already authenticated `Materials.Material` value and
performs no catalog or artifact access. Assigning `ShapeMaterial` preserves the
target's complete current `ShapeAppearance`; display-only styling is a separate
ownership channel. Stable carrier properties persist the original and accepted
native material or `App::PropertyMaterialList` state so failed publication,
retirement, save/reopen, and deletion can restore the exact target without a
catalog lookup. Human edits and competing target/channel owners are rejected
before mutation.

Mesh and MeshPart follow the same boundary. Mesh construction, repair,
topology diagnostics, BREP-to-mesh tessellation, mesh selection, boundary
extraction, face conversion, sewing, solid construction, refinement, and
BMS/BREP I/O run in the isolated worker. MeshPart exposes only the canonical
`mesh_from_shape` and `shape_from_mesh` operations; native overloads are selected
by explicit method, representation, and selection parameters instead of
duplicating provider-facing tools. The worker avoids the crash-prone
`makeShapeFromMesh(..., sew=True)` binding, performs separate verified sewing,
and normalizes topology before solid construction. Publication only assigns an
authenticated detached Mesh or Shape to a stable native object and preserves
human placement. Explicit rollback restores copied kernels, properties,
expressions, metadata, and exact object names after failed publication or
deletion.

Points follows the same boundary: native import, placement baking, filtering,
sampling, attribute alignment, diagnostics, and artifact export happen in the
isolated worker.

Reverse Engineering and Inspection follow the same boundary. Native curve and
surface fitting, segmentation, triangulation/reconstruction, point-to-shape
distance evaluation, tolerance classification, and metric aggregation happen
in the worker. The host authenticates every BREP, mesh, point, distance, and
fit-metric payload before publishing stable native reconstruction or inspection
records. Publication does not refit geometry or recompute measurements.

Robot follows the same boundary. The worker instantiates the native robot,
constructs waypoint trajectories and dress-ups, runs bounded simulation, and
returns authenticated placements, timing, reachability, and trajectory samples.
The document thread applies frozen native robot and trajectory state plus a
bounded simulation record; it does not simulate or wait for Robot execution.

FEM follows the same boundary. The worker builds native analyses, CalculiX
solver/material/constraint/load-case objects, and FEM meshes from authenticated
semantic geometry references. Requested Gmsh and CalculiX runs execute inside
the cancellable worker process, with bounded input/result artifacts and explicit
capability errors when an executable is absent. Publication installs validated
native analysis state and result readback without meshing or solving live.

CAM follows the same boundary. One `tool(kind=...)`, one
`operation(strategy=...)`, and one `postprocess(processor=...)` replace variant
tool families. Native Path job construction, toolpath generation, circular
motion analysis, stock/removal simulation, and postprocessing run in the worker.
G-code stays an authenticated project artifact until the human explicitly
exports it; the document thread installs only validated Path objects and paths.

TechDraw follows the same boundary. Its canonical graph API is exactly
`template`, `view`, `projection`, `dimension`, `annotation`, and `page`; sheet
sizes, orientations, directions, dimension kinds, conventions, and alignment
are selectors. The worker performs native projection and dimension evaluation,
then serializes fixed projection and dimension snapshots. The live document
uses the additive precomputed-state APIs and never projects, recomputes, or
reads artifacts on the document thread.

## Workbench domains

| Workbench | Domain | Accepted output types | Status |
|---|---|---|---|
| Part Design | `partdesign` | exact single `solid` publications | Production-ready |
| Sketcher | `sketcher` | `sketch` | Production-ready |
| Part | `part` | `solid`, `shell`, `face`, `wire`, `compound` | Production-ready |
| Draft | `draft` | `wire`, `circle`, `rectangle`, `bspline`, `array`, `text` | Production-ready |
| Surface | `surface` | `surface`, `face`, `shell`, `fill`, `blend`, `extension`, `loft`, `solid` | Production-ready |
| Assembly | `assembly` | `assembly`, `component_link`, `joint`, `solver_diagnostics`, `motion`, `simulation`, `exploded_view`, `bom` | Production-ready for rigid and authenticated flexible hierarchies, worker-generated kinematics, native exploded views, and native frozen BOMs |
| Spreadsheet | `spreadsheet` | `sheet` | Production-ready |
| Material | `material` | `material_assignment`, `appearance` | Production-ready |
| Mesh | `mesh` | `mesh` | Production-ready |
| MeshPart | `meshpart` | `mesh`, `solid`, `shell`, `face`, `wire`, `compound` | Production-ready |
| Points | `points` | `points` | Production-ready |
| Reverse Engineering | `reverse_engineering` | `curve`, `surface`, `brep`, `mesh`, `fit_metrics` | Production-ready |
| Inspection | `inspection` | `inspection_group`, `inspection_feature`, `measurement`, `report` | Production-ready |
| Robot | `robot` | `robot`, `trajectory`, `dressup`, `simulation` | Production-ready |
| FEM | `fem` | `analysis`, `solver`, `material`, `constraint`, `load_case`, `mesh`, `result` | Production-ready |
| CAM | `cam` | `job`, `stock`, `tool`, `operation`, `toolpath` | Production-ready |
| TechDraw | `techdraw` | `page`, `template`, `view`, `projection`, `dimension`, `annotation` | Production-ready |

Every domain publisher must use the corresponding native FreeCAD object types
rather than generic stand-ins: `Sketcher::SketchObject`,
`Assembly::AssemblyObject` and native links/joints, `Spreadsheet::Sheet`,
native `Materials::PropertyMaterial` assignments and reversible display carriers,
`Mesh::Feature`, `Points::Feature`, TechDraw objects, Path objects, and the
domain's native structured engineering records. Physical material assignment
must remain distinct from appearance-only changes. A future domain stays gated
until that behavior is implemented and proven.

### Part production contract

The Part domain exposes 49 canonical OCC operations spanning authenticated
document references, primitives, curves, topology construction, generators,
booleans, repair/finishing, transformations, projection, and refinement. It
does not expose kernel overloads as separate authoring concepts. `helix` selects
`standard` or `segmented` representation, and `project` selects `parallel` or
`perspective` mode. The redundant `long_helix`, `project_parallel`, and
`project_perspective` entry points do not exist; source uses the canonical
operations and their explicit selectors.

The worker authenticates every referenced BREP, executes the graph through real
Part/OCC methods, rejects invalid or null topology, verifies the declared Wire,
Face, Shell, Solid, or Compound class, and exports a hashed BREP. The host
imports and independently validates detached topology before publication.
Stable `Part::Feature` outputs update in place; compatible whole-object links
survive, while unmanaged transient subelement consumers block regeneration or
deletion. No OCC construction or BREP I/O runs on the document thread.

### Sketcher production contract

The Sketcher domain exports explicit point, line, circular/conic arc, circle,
ellipse, exact or interpolated B-spline, and authenticated external-geometry
constructors plus `constraint` and `sketch`. Its constraint graph covers the
native geometric and dimensional families, including angle-via-point,
reference/driving state, active and virtual state, expressions, text/group
constraints, and conic/B-spline internal alignments. Composite GUI creation
commands such as rectangles, polygons, and slots are expressed as immutable
primitive graphs, so regeneration does not depend on interactive edit state.

Support and external geometry use stable document references. Raw
`FaceN`/`EdgeN`/`VertexN` selections are accepted only for non-transient native
topology; regenerating scripted sources require a published semantic
interface. Candidate and host validation both resolve the exact selection.
External links are published as real native Sketcher references, including
multiple projected subelements from the same source object, with deterministic
negative geometry IDs and exact live readback.

The accepted object preserves identity through source edits, input changes,
save/close/reopen, and compatible downstream whole-object links. Foreign
transient subelement consumers block regeneration. Failed candidates retain
their exact solver or publication stage while the prior accepted sketch stays
live. On-demand Sketcher domain inspection bounds its source inventory to 32
sketches and 128 geometry, constraint, expression, or external-reference
entries per sketch before the shared 32 KiB result boundary is applied.

### Draft production contract

The Draft domain exports exactly `wire`, `circle`, `rectangle`, `bspline`,
`array`, and `text`. Programs build an immutable graph of native editable
objects rather than flattening parametric Draft content to anonymous BREP.
Placements use finite translations and normalized quaternions. Arrays support
orthogonal and polar layouts, link and copied-shape modes, internal graph
bases, authenticated external bases, and chained arrays; hidden graph nodes,
cycles, invalid fuse combinations, and version-dependent silent defaults are
rejected with operation-specific errors.

Candidate execution creates real Draft proxies in `FreeCADCmd`, recomputes
them there, validates the requested shape contract, and returns exact native
property and placement readback. Publication creates stable native Draft
proxies and applies the validated detached shape and properties without live
proxy execution. Link/copy proxy mode cannot silently drift across revisions.
Whole-object consumers survive compatible regeneration; transient Draft
subelement consumers block it, while external bases are revision-bound by
authenticated BREP digests.

The lifecycle covers exact-source edits, input regeneration, failed-candidate
retention, save/close/reopen, stable base links, guarded deletion, and bounded
on-demand inspection. Text remains structured native Draft text and never accepts
arbitrary Python values. Provider source can neither access the graph counter
nor inject raw filesystem paths or unvalidated objects.

### Surface production contract

The Surface domain exports exactly `line`, `circle`, `bezier`, `bspline`,
`wire`, `from_object`, `face`, `surface`, `boundary`, `curve_constraint`,
`face_constraint`, `point_constraint`, `fill`, `blend`, `extend`, `loft`,
`thicken`, and `shell`. Intermediate curves, wires, and filling constraints are
typed immutable graph values and cannot be published accidentally. Accepted
outputs preserve their declared native class: surface, face, fill, blend, and
extension outputs are exact OCC Faces; shell output is one connected OCC
Shell; solid output is one OCC Solid; and a non-solid loft remains a Face or
Shell rather than being flattened or promoted.

The worker uses `Part.BSplineSurface` for bounded rectangular-grid
interpolation and approximation, `Surface::Filling` for boundary, curve, face,
point, and initial-face constraints with complete bounded solver controls,
`Surface::GeomFillSurface` for Stretched, Coons, and Curved blends,
`Surface::Extend` for independent U/V extension, `Part.makeLoft` for open or
solid lofts, OCC offset/thickness APIs for thickening and hollowing, and
`Surface::Sewing` for shells and closed solids. Planar faces with holes are
constructed as one orientation-correct multi-wire Face; a boolean-cut Shell is
not accepted as a face. Disconnected sewing, silent native downgrades, null or
invalid Shapes, and a mismatch between the declared output type and exact OCC
`ShapeType` fail the candidate with operation and correction details.

Document inputs use authenticated revision-bound BREPs. Whole Shapes and exact
1-based subelements are allowed for stable native topology; regenerating
scripted sources require named semantic interfaces for subelement selection.
Both the isolated worker and host validator independently check reference
identity, BREP digest, topology range, semantic-interface cardinality, and
requested topology type. Source changes mark dependent accepted outputs stale.

Publication updates the same inert `Part::Feature` identities using only the
detached validated Shapes and native operation readback. Compatible
whole-object consumers survive regeneration, unmanaged Face/Edge/Vertex
consumers block mutation, and failed source, worker, validation, or publication
candidates leave the prior accepted revision live. The lifecycle covers exact
source edits, input regeneration, reconfiguration and safe output retirement,
bounded Surface input context, save/close/reopen, and guarded deletion.

### Spreadsheet production contract

The Spreadsheet domain exports exactly `sheet`, `cell`, and `range_style`.
`api.cell` accepts a native address from `A1` through `ZZ16384`, one bounded JSON
scalar or formula, an optional unique alias, numeric unit, display unit, and
explicit style, alignment, foreground, and background values. Formulas cannot
be disguised as strings beginning with `=`. Values and formulas are mutually
exclusive, non-finite numbers are rejected, aliases cannot be cell addresses,
and RGB channels are bounded. `api.range_style` normalizes one rectangular A1
range and requires at least one formatting property. `api.sheet` rejects
duplicate addresses or case-insensitive aliases and limits the complete batch
to 10,000 cell/range operations.

The worker creates literal cells first, assigns every alias second, and parses
formulas and quantities only after all alias targets exist. It then applies
range formatting in source order, cell-level overrides, display units, and
column/row dimensions before one native recompute. Formula syntax, unit errors,
reserved aliases, missing external objects, and cycles produce the native
stage, target, exception, state/status, and correction while the accepted sheet
remains live. External document and file references are intentionally absent
from the isolated candidate; supported formulas refer to cells or aliases in
the same sheet.

The host independently reauthorizes the exact immutable graph, counts, digest,
bounded readback schemas and ordering, formatting precedence, native type,
valid recompute state, and global summary. Publication updates the same stable
`Spreadsheet::Sheet`, performs no recompute or process/artifact wait, and
compares its complete assigned-state digest with the worker. It preflights the
current live digest to protect human edits. On any subsequent publication
failure it restores the prior accepted batch through a separate native replay
path after transaction abort and verifies that rollback digest. The lifecycle
covers create, inspect, exact edit, failed-candidate retention, input update,
reconfiguration, stable downstream links and expressions, save/close/reopen,
bounded native-sheet context, and guarded deletion.

### Material production contract

The Material domain exports exactly `material`, `assign`, and `appearance`.
`material` accepts one canonical catalog UUID plus optional exact native physical
and appearance property requirements. `assign` accepts only a stable document
reference persisted in program inputs and a value returned by `material`.
`appearance` accepts the same authenticated target-reference form, an optional
value returned by `material`, and/or an explicit subset of shape, line, or point
color; transparency; line or point size; display mode; visibility; and
selectability. A card contributes its native ambient, diffuse, specular,
emissive, shininess, and transparency values; explicit shape color and
transparency override the corresponding card fields. Colors use normalized RGB,
transparency uses an integer percentage, and display modes must match the live
view provider's enumerated values. There is no generic output helper, name-based
catalog fallback, hidden target lookup, or headless appearance no-op.

The worker reconstructs every immutable graph through the exported API, checks
one-owner-per-target/channel rules, opens `Materials.MaterialManager`, resolves
the exact UUID, and hashes bounded card identity, models, tags, and all physical,
appearance, and legacy properties. Required properties are read through the
native card API; missing properties report the material, missing names, bounded
available names, stage, and corrective action. File and URL values are hashed
but not exposed. The host reauthorizes the graph and input reference, checks the
captured target capability, independently resolves and hashes the same catalog
card under a lock, and attaches that already validated native material to the
candidate. Card-derived appearance is independently parsed and compared in both
stages; malformed standard colors, out-of-range values, cards without a standard
appearance model, or catalog drift reject the candidate before publication.

Publication uses stable `App::FeaturePython` ownership carriers with a native
`App::PropertyLink` to the human object. A physical carrier persists both the
pre-program and accepted values as `Materials::PropertyMaterial`; assigning the
accepted card preserves the target's complete current display state, so physical
properties do not silently take ownership of styling. An appearance carrier
persists complete baseline and accepted `App::PropertyMaterialList` values when
a card, shape color, or transparency is controlled, plus exact
baseline/accepted values for the other selected properties. Applying a card
updates every existing per-face display material while preserving the list
cardinality and never assigning `ShapeMaterial`. Color digests use FreeCAD's
actual 8-bit save format, avoiding false human-edit conflicts after reopen.

Before any update or deletion, the publisher compares the live material or
controlled appearance state with the persisted accepted state and rejects
out-of-band edits. A foreign program cannot own the same target and channel.
Updates first restore every old baseline, then carry original baselines forward
for retained ownership, capture newly controlled fields, apply physical outputs
before display outputs, and verify native readback. Every touched target has an
explicit full snapshot; a post-mutation failure restores that snapshot even if
FreeCAD transaction rollback is incomplete. Retiring or deleting an output
restores its original target state without opening the catalog. The provider
context contains a bounded path-free catalog index and bounded target capability,
current-card, display-state, and exact-display-mode records.

### Mesh production contract

The Mesh domain exports exactly `mesh`, `from_object`, `transform`, `repair`,
and `diagnostics`. Programs build immutable operation graphs from bounded finite
triangle arrays or an authenticated stable native `Mesh::Feature` reference.
Transforms bake a translation, normalized quaternion rotation, and strictly
positive non-uniform scale. Repair exposes ordered duplicate-point/facet,
degeneration, non-manifold edge/point, self-intersection, bounded hole-fill,
decimation, and normal-harmonization passes. Diagnostics report native point,
facet, edge, component and boundary counts; corruption, range, neighbourhood,
orientation, manifold and solid checks; bounds, area, volume, center of gravity,
and Euler characteristic. Programs can require closed, solid, manifold,
consistently oriented, or intersection-free results and bound component or open
edge counts; a failed requirement rejects the candidate without touching the
accepted revision.

The isolated `FreeCADCmd` worker reconstructs every nested operation through the
actual exported API, enforces a one-megabyte graph contract and coordinate
bounds, performs native `Mesh.Mesh` construction and repair, and exports a BMS
artifact with a SHA-256 digest. Self-intersection presence is always evaluated;
native detail enumeration is limited to at most 128 facets and only 64 details
enter diagnostics, preventing an unbounded intersection report. The host
authenticates the artifact, imports a detached native mesh, recomputes the full
diagnostic contract independently, validates the exact operation trace and
global summary, and rejects any native or serialized drift before publication.

Publication updates a stable `Mesh::Feature` in place and preserves its human
document Placement and unrelated human properties. Worker transforms remain
baked into the local mesh kernel; local geometry is validated independently of
the document Placement. Publication and deletion capture a bounded complete
property snapshot plus a native mesh copy. If native transaction rollback is
incomplete, the publisher restores the accepted kernel and metadata or
recreates the missing `Mesh::Feature` under its exact stable name, including
human-authored properties and Placement. Whole-object consumers survive
regeneration and block deletion until detached. Mesh construction, repair,
diagnostics, BMS I/O, and worker waits remain off the document thread; the live
callback only assigns already validated native state and bounded properties.

`core.inspect` domain scope exposes a bounded inventory of native Mesh objects
with stable program identities, point/facet/edge counts, world-space bounds, and a
compact accepted-validation summary. It does not rerun mesh diagnostics on the
document thread.

### MeshPart production contract

MeshPart exposes exactly two conversion directions: `mesh_from_shape` and
`shape_from_mesh`. Backend, deflection, segment, sewing, refinement, and target
topology are explicit selectors on those operations instead of duplicate
provider methods. Inputs are authenticated stable BREP or placement-baked BMS
references; raw paths and unverified live kernels are forbidden.

The worker executes native OpenCascade or Mefisto conversion, preserves selected
source-face groups, performs explicit verified sewing rather than the
crash-prone combined binding, and exports a typed BMS or BREP artifact. Host
validation rechecks the source digest, backend report, segment selection,
topology class, and artifact hash. Publication updates stable `Mesh::Feature`
or `Part::Feature` identities while preserving human placement/properties and
guarding downstream links; conversion and artifact I/O never run live.

### Points production contract

The Points domain deliberately exports one best provider-facing operation:
`point_cloud(source, *, pipeline, invalid_points, preserve_attributes, label)`.
The source is exactly one bounded inline coordinate array, stable native
`Points::Feature` reference, or project-approved artifact reference. Raw paths
are forbidden. The ordered pipeline contains transform, crop-box or
deduplication filters, and voxel, stride, or endpoint-preserving limit sampling.
These are stages of one authenticated operation rather than redundant
load/transform/filter/downsample tools. Irrelevant method fields, identity
transforms, invalid bounds, non-finite values, and unsafe sizes are rejected at
the API boundary.

The human approves ASC, XYZ, PCD, PLY, or E57 files from the Model Code Editor.
Copying, hashing, registry reads, and guarded removal run on background threads.
Programs and providers receive only a stable 32-character artifact ID, bounded
metadata, and SHA-256; the original path is never persisted in source, inputs,
or model-visible inspection results. Every candidate reauthenticates size and content before
copying only the referenced artifact into its exact domain worker bundle.
Removal is rejected while either a working or accepted Points program references
the ID.

The worker imports the native cloud, bakes source placement, keeps Color,
Intensity, and Normal arrays aligned through every stage when requested,
validates structured Width/Height, and returns authenticated ASC and typed
attribute sidecars. The host independently checks coordinate and attribute
digests, operation trace, counts, bounds, and structured dimensions. Publication
updates a stable `Points::Feature` in place, preserves its human Placement,
properties, expressions, and downstream whole-object links, and publishes the
validated attributes without geometry generation or artifact I/O on the
document thread. Explicit publication and deletion rollback restore the native
point kernel, attributes, metadata, object name, and accepted revision even when
native transaction rollback is incomplete.

On-demand Points domain inspection exposes only stable document references,
native counts, bounds, at most eight sample coordinates, attribute presence, compact accepted
validation, and project-approved artifact metadata without paths. It never
materializes a complete live cloud on the document thread.

### Assembly production contract

The Assembly domain exports exactly `assembly`, `component`, `connector`,
`joint`, `solve`, `motion`, `simulation`, `exploded_view`, and
`bill_of_materials`. Grounding is the single
`component(..., grounded=True)` operation; there is no redundant mutating
`ground` alias. A program builds one immutable graph and returns its assembly,
every component occurrence, every joint, and one solver diagnostics output
under stable names. A kinematic program additionally returns every motion and
exactly one simulation that consumes the same assembly. Component inputs are
stable document references whose exact solid Shapes, authenticated nested
assembly graph, and semantic-interface metadata are hashed into the candidate
revision.

An exploded presentation is one `exploded_view` output with an ordered move
list, rather than separate create-view/create-step/apply-line tools. Each move
references the exact returned component variables and selects exactly one
normal transform or native radial control distance. Components may occur in
later moves for staged explosions. Radial displacement is explicitly defined
as `(component bounds center - assembly bounds center) *
4*radial_distance_mm/assembly_diagonal`; a centered component is rejected with
the view name, move index, changed/unchanged components, and a direct repair.

A parts table is one `bill_of_materials` output rather than separate
add-column, set-cell, aggregate, or refresh tools. It owns built-in, native
property, and custom columns; hierarchy-detail settings; filtering; and
occurrence-path row overrides. The worker creates and reads a real
`Assembly::BomObject`. The host independently re-derives every row from the
authenticated component hierarchy, then publishes a stable frozen native BOM
with literal validated cells. File-name cells contain only source basenames,
never raw paths. Quantity aggregation retains all contributing stable
occurrence paths, and conflicting overrides report the exact paths and repair.

Connectors accept a component origin, an exact 1-based `FaceN`, `EdgeN`, or
`VertexN` on an immutable native snapshot, or a named published interface on a
regenerating scripted component. Transient topology is rejected with the
available semantic names. The domain supports FreeCAD's fixed, revolute,
cylindrical, slider, ball, distance, parallel, perpendicular, angle,
rack-pinion, screw, gears, and belt joints, including applicable motion limits,
coupling parameters, suppression, and connector-local offsets.

Publication creates a stable `Assembly::AssemblyObject`, `App::Link` or
`Assembly::AssemblyLink` occurrences, native `JointObject` instances, grounding
joints, native motion-property objects, a simulation object inside
`Assembly::SimulationGroup`, an actual `ExplodedView` under
`Assembly::ViewGroup` with stable managed `ExplodedViewStep` children, a frozen
`Assembly::BomObject` under `Assembly::BomGroup`, and a structured diagnostic
feature. Native
kinematics runs only in the isolated worker. Its complete bounded placement
trace is authenticated and retained with the attempt; the live document gets
settings, motion-effect diagnostics, digest/counts, and input/middle/final
preview frames without running the solver or reading the artifact. Updates
preserve output identity, including across joint-type and motion-formula
changes. A link cannot silently change between component and subassembly native
types. Source changes mark every output in the dependent program stale;
compatible whole-object consumers survive, while unmanaged transient
subelement consumers block mutation with a precise error.

For exploded views the isolated worker creates the real native proxy graph and
reads back every ordered final placement and explosion-line endpoint without
mutating the solved component state. The host reloads the revision-bound source
BREPs and independently derives the assembly bounds, native radial factors,
normal composition order, final placements, line lengths, proxy types, and
reference paths. Publication assigns only that accepted move state. Compatible
regeneration retains the view and per-index move identities, and
`core.inspect` program scope exposes bounded accepted evidence so the model can verify the
live result without applying the explosion.

Motion source uses seconds for `time`, radians for angular values, and
millimetres for linear values. Only `time`, `initialValue`, `pi`, arithmetic,
powers, and the documented bounded one-argument functions are accepted. A
time-dependent formula that produces no measurable movement is rejected with
the motion output, joint, formula, observed magnitude, and a direct correction.
`core.inspect` program scope returns bounded accepted solver, simulation, and exploded-view
and BOM evidence plus trace/table previews so the model can close the
discover-author-solve/simulate/present-repair-verify loop.

Rigid and authenticated flexible subassembly graphs use the same copy-ready
source occurrence paths. A flexible component is available only when on-demand
domain inspection marks its native Assembly source eligible. The worker reconstructs each
bounded hierarchy level and its native joints, solves the parent graph, and
returns per-occurrence placements; publication maps those placements to the
same stable paths while retaining `AssemblyLink` identities through regeneration
and reopen. Invalid paths report the failed segment and exact available segments
instead of falling back to a rigid or flattened representation.

### Reverse Engineering production contract

Reverse Engineering exports `fit_curve`, `fit_surface`, `reconstruct`,
`segment`, and `fit_metrics`. It accepts bounded authenticated point, mesh, and
BREP sources and records the exact native capability used. Approximation degree,
continuity, tolerance, segmentation, triangulation, reconstruction, and metric
choices are explicit; unsupported native capabilities fail with structured
diagnostics rather than silently changing algorithms.

The worker runs the native `ReverseEngineering` and OCC/mesh algorithms,
authenticates fitted geometry and metrics, and emits typed curve, surface, BREP,
mesh, or fit-report outputs. Publication installs stable native shape/mesh
objects and bounded fit records without repeating approximation. Source changes
invalidate dependent results; accepted live identities, compatible consumers,
save/reopen state, and explicit rollback are covered by the domain lifecycle.

### Inspection production contract

Inspection exports `comparison`, `group`, `measurement`, and `report`. A
comparison binds immutable nominal geometry to authenticated actual points,
defines a bounded native distance computation, and records tolerances and
pass/fail classification. Groups, scalar measurements, and reports reference
returned comparison nodes, preventing hidden or undeclared inspection state.

Distance generation and aggregate statistics run in the worker. The host checks
the complete bounded distance artifact, counts, extrema, RMS/mean values,
tolerance verdicts, and graph ownership before publishing native
`Inspection::Feature` and `Inspection::Group` objects plus stable measurement
and report records. Regeneration preserves compatible native identities and
explicitly restores large distance kernels if publication or deletion fails.

### Robot production contract

Robot exports `robot`, `waypoint`, `trajectory`, `dressup`, and `simulate`.
Waypoints are immutable graph nodes consumed by native trajectory outputs rather
than redundant standalone document objects. The API validates axis positions,
placements, timing, interpolation, velocity dress-ups, collision/reachability
requirements, sampling bounds, and exact robot/trajectory ownership.

The worker constructs real `Robot::RobotObject`, native trajectory, and dress-up
state, then performs the requested bounded simulation and writes authenticated
sample data. Publication freezes stable native Robot/trajectory/dress-up objects
and publishes structured simulation diagnostics without trajectory evaluation
on the document thread. Updates, save/reopen, downstream-link protection, and
failed publication/deletion restoration retain the accepted graph.

### FEM production contract

FEM exports `analysis`, `solver`, `material`, `constraint`, `load_case`, `mesh`,
and `solve`. The graph requires exactly one native analysis, solver, mesh, and
result and binds materials, constraints, and loads through authenticated whole
objects or semantic subelement references. Solver, mesher, element, material,
load, and execution choices are explicit and bounded.

The worker builds native FEM objects, runs requested Gmsh and CalculiX stages,
authenticates the generated mesh/input/result artifacts, and returns structured
topology and solver diagnostics. Missing external executables are precise
capability failures. Publication applies stable native analysis members and
prevalidated results without external-process waits, file I/O, meshing, or
solving. Compatible references survive; transient topology and stale source
revisions are rejected or marked stale under the existing contracts.

### CAM production contract

CAM exports `job`, `stock`, `tool`, `operation`, `generate_toolpath`, and
`postprocess`. Redundant cutter, strategy, and postprocessor methods are
collapsed into `tool(kind=...)`, `operation(strategy=...)`, and
`postprocess(processor=...)`. Stock definitions, feeds/speeds, cutter geometry,
boundary/base references, depth/step controls, path generation, simulation, and
postprocessing form one authenticated native Path graph.

The worker constructs real Path jobs/controllers/operations, generates and
reads back native paths, validates linear and circular records, performs bounded
stock/removal simulation, and invokes only the declared native postprocessor.
G-code is a hashed project artifact, never an implicit external export. The
host independently validates path records, operation/stock/tool relationships,
simulation facts, and artifact hashes before stable publication. Live callbacks
install only precomputed Path state and support explicit rollback and safe
dependency-ordered deletion.

### TechDraw production contract

TechDraw exports exactly `template`, `view`, `projection`, `dimension`,
`annotation`, and `page`. Projection directions and dimension conventions are
selectors rather than parallel method families. Every returned view, projection,
dimension, and annotation belongs to exactly one returned page; dimensions
reference exact worker-produced projected elements.

The worker builds native templates/pages/views/projection groups, evaluates all
requested directions, computes dimensions, and captures fixed-size projection
and dimension snapshots with authenticated BREP side artifacts. The host checks
graph ownership, every artifact and aligned descriptor, fixed state shape,
native readback, page order, and output type before publication.

Additive TechDraw APIs apply precomputed projection and dimension state to stable
native objects. Frozen views are excluded from live projection/update passes,
so the document callback performs no projection, dimension evaluation, artifact
I/O, or recompute. Deletion removes dimensions first, purges projection groups
through their native ownership API, and then removes pages/views/templates,
avoiding dangling anchors while preserving unmanaged objects.

## Model Code Editor

The editor follows the global engine and active domain. It clears selection
when either changes, filters the list to that domain, and defaults to `None`.
Opening the panel loads bounded metadata only and never creates a preview.
VibeScript builds are explicit and use the same worker, validator, publisher,
revision guard, and persistence path as provider-authored calls. Domains for
which a safe empty template is not meaningful remain creatable through their
qualified lifecycle tools rather than fabricating a live preview.
In the Points domain, a separate approved-data row displays complete stable IDs
and performs add/list/remove registry work in background threads; opening that
row still does not select a program or generate a preview.

## Failure and unsupported states

The architecture reports the stage that failed: surface, schema/contract,
precondition, worker execution, detached validation, or native publication.
It does not substitute a nearby workbench, mix tool packs, change engines, or
silently execute source in the GUI process.

Stable whole-object identity does not make transient `FaceN` or `EdgeN` names
semantic. The existing Part Design publication/reference contracts still
rebind supported semantic consumers, mark derived FEM/CAM/TechDraw results
stale, and reject unmanaged subelement references before mutation.

## Verification

Automated contracts cover the complete 17-workbench resolver matrix and assert
that only production-ready packs expose authoring tools. They also cover exact
single-domain schemas, unsupported startup surfaces,
Part Design schema digests, v1 migration, source/input policy, subscription
snapshot integrity, and duplicate/mixed/stale call rejection.

The Part integration executes every currently exported API operation against
real OCC geometry in FreeCADCmd, checks useful invalid-parameter and topology
selection errors, retains a rejected candidate without changing accepted
geometry, and recovers from that failed working revision. It covers create,
inspect, exact-source edit, input regeneration, reconfiguration, safe output
retirement, stable publication, save/close/reopen, and guarded deletion. A real
Qt event-loop heartbeat executes every production domain worker while all
subprocess waits remain on a background thread. Its substantial cases include
Part booleans/lofts, a 120-geometry Sketcher solve, a 1,200-instance Draft array,
Surface interpolation, an 800-cell Spreadsheet batch, native Material catalog
hashing, a 9,800-facet Mesh repair, Mefisto conversion, a
40,000-point pipeline, reconstruction and inspection fitting, Robot simulation,
FEM deck generation, CAM generation/simulation/postprocessing, six-direction
TechDraw projection with dimension evaluation, and a native Assembly solve.

The Sketcher integration executes every primitive geometry and native
constraint family in `FreeCADCmd`, including conic/internal alignments,
angle-via-point, expressions, construction/virtual/reference states, semantic
support, and native external geometry. It proves failed-candidate retention,
stable in-place regeneration, grouped external-link cleanup, whole-object and
subelement consumer protection, save/close/reopen, guarded deletion, bounded
on-demand inspection, and exact solver/profile diagnostics.

The Draft integration executes every exported constructor against real native
Draft proxies, including direct and chained orthogonal or polar arrays in both
link and copied-shape modes. It verifies exact property readback, silent face
downgrade rejection, external-base revision binding, failed-candidate
retention and recovery, proxy-mode drift rejection, downstream-reference
protection, stable publication, bounded context, save/close/reopen, and guarded
deletion.

The Surface integration executes all 18 exports and every declared native
engine against real OCC and Surface objects. It covers interpolated and
approximated B-spline surfaces; planar faces with holes; variational filling
with G1 support, curve, face, point, initial-face, and solver controls; all
three blend styles; native extension; open, ruled, and solid lofts; face
thickening and solid hollowing; shell and solid sewing; exact and semantic
references; disconnected-shell rejection; and exact ShapeType readback. Its
lifecycle proves failed-candidate retention, stable in-place regeneration,
source-digest staleness, downstream-reference protection, reconfiguration,
bounded context, save/close/reopen, and guarded deletion.

The Spreadsheet integration executes all three exports against real native
sheet objects and verifies text, finite numeric and boolean values, quantities,
same-sheet aliases and formulas, display units, overlapping range/cell format
precedence, colors, dimensions, deterministic readback, and evaluated values.
It rejects reserved aliases and cycles with native diagnostics, rejects altered
worker summaries, retains and recovers failed candidates, preserves stable
identity and downstream links/expressions, blocks unaccepted live edits, proves
explicit full-replay rollback after an injected post-mutation failure, clears
retired cells and dimensions during reconfiguration, captures bounded provider
context, and completes save/close/reopen and guarded deletion.

The Material integration runs in a GUI-backed FreeCAD process and executes all
three exports against real `Part::Feature` targets, native material cards, and
view providers. It proves required-property diagnostics, independent worker/host
card digests, authenticated input references, physical/display separation,
property-subset ownership, stable carriers, foreign-owner rejection, and
human-edit protection. Publication and deletion are run with
`Materials.MaterialManager` replaced by a raising function to prove that the
document thread never opens the catalog. The test injects a failure after
complete target mutation and verifies explicit restoration, then saves, closes,
reopens, and deletes the program while restoring the original
`Materials::PropertyMaterial` and complete display baselines.

The Assembly integration exercises all 13 native joint types in both candidate
execution and stable live publication, verifies limits, coupled-motion
parameters, suppression, semantic connector rejection/rebinding, solver codes
and diagnostics, rigid and authenticated nested flexible subassemblies with
stable occurrence paths, failed-candidate retention, source staleness,
downstream-reference protection, save/close/reopen, regeneration, and guarded
deletion. It also generates a native revolute simulation, validates
seven frames/fourteen component poses and measured motion, rejects worker-result
tampering and a zero-effect formula, proves stable motion/simulation identities,
exposes accepted evidence through `core.inspect` program scope, retains the authenticated
trace, and saves/reopens/deletes the graph. The same integration creates a real
native exploded view with ordered normal and radial moves, independently rejects
tampered line endpoints/final placements/proxy readback, proves stable view and
move identities across regeneration, retains the accepted live view after an
actionable centered-component failure, restores both proxy classes after
save/reopen, and deletes every managed move. Its provider-context test proves
the 24-component bound, domain-only program filtering, eligibility facts,
stable references, and published-interface hints. Its native BOM path verifies
built-in/property/custom columns, hierarchy controls, quantity aggregation,
copy-ready row overrides, authenticated literal cells, stable identity,
save/reopen, and deletion without live auto-generation.

The Mesh integration exercises explicit API signatures and bounds, quaternion
normalization, bounded self-intersection detail, hole filling, native repair,
non-uniform transformation, diagnostic requirements, exact BMS authentication,
and independent worker/host readback. It rejects altered diagnostics, hashes,
and repair traces; retains and recovers a failed working revision; preserves a
stable live identity, human Placement/property, and whole-object consumer across
edit, input, and reconfiguration updates; and verifies bounded on-demand inspection.
Injected post-assignment and committed mid-deletion failures prove explicit
publication restoration and stable-name recreation. The accepted object is
saved, closed, reopened, reference-guarded, and deleted. Its 9,800-facet worker
case is included in the Qt heartbeat while generation, repair, decimation,
self-intersection analysis, validation, and BMS transfer stay off the GUI
thread. Future domain claims are added only when their dedicated executable
tests pass; gated prototypes are not counted as verified.

The MeshPart integration exercises the two canonical, non-redundant conversion
operations across OpenCascade and Mefisto backends. It authenticates and hashes
both BREP and placement-baked BMS inputs, preserves source-face segments,
converts a closed placed mesh to a refined Solid, extracts a selected segment as
a Wire, and publishes stable native `Mesh::Feature` and `Part::Feature`
identities. It rejects irrelevant method options, invalid topology/facet/segment
selections, unavailable Netgen requests, altered diagnostics, artifact hashes,
backend reports, and unsafe conversion traces with structured corrective
details. Failed candidate retention, source-placement invalidation, exact
in-place regeneration, on-demand inspection, human properties/expressions and
placements, injected post-assignment rollback, save/close/reopen, external-link
deletion protection, committed-deletion recreation, and final deletion are all
covered. The Qt heartbeat also runs a substantial authenticated spherical BREP
through native Mefisto conversion while the event loop remains responsive.

The Points integration proves that `point_cloud` is the sole runtime export and
executes inline, stable-document, ASC, and PLY sources through the canonical
ordered pipeline. It verifies source-placement baking; Color, Intensity, Normal,
Width, and Height preservation; exact coordinate, attribute, fact, trace, and
summary authentication; stable in-place regeneration; human properties,
expressions, placements and whole-object links; bounded context; failed
candidate retention; explicit publication and deletion rollback; save/reopen;
artifact and external-reference deletion guards; and final deletion. A native
Qt test proves editor opening creates no selection or preview, stable artifact
IDs are visible, stale completions are ignored, and all artifact registry/file
operations run off the GUI thread. The shared Qt heartbeat includes a 40,000
point transform/crop/voxel candidate while the event loop remains responsive.

The Reverse Engineering integration executes every export with native curve and
surface fitting, triangulation/reconstruction, normal-based segmentation, and
fit metrics. It authenticates point/mesh/BREP sources and outputs, rejects
altered metrics or artifacts, preserves stable identities and consumers, proves
failed-candidate retention and explicit rollback, and completes save/reopen and
guarded deletion.

The Inspection integration evaluates native signed distances between
authenticated nominal geometry and actual points, publishes native groups and
features, and verifies scalar measurement/report aggregation and tolerance
verdicts. It rejects modified distance artifacts or summaries, retains stable
identities across regeneration, restores large accepted distance kernels after
injected failures, and covers context, staleness, save/reopen, and deletion.

The Robot integration constructs native robots, trajectories, and dress-ups,
then exercises bounded simulation and authenticated sample artifacts. It checks
waypoint/timing/readback contracts, reachability diagnostics, stable frozen
publication, context bounds, downstream protection, failed-candidate retention,
injected publication/deletion rollback, and save/close/reopen.

The FEM integration builds native analyses, CalculiX solvers, materials,
constraints, load cases, meshes, and result records through authenticated
semantic references. It verifies native mesh topology and bounded input decks,
explicit capability errors for missing external executables, result readback,
stable analysis membership, stale-source behavior, rollback, context,
save/reopen, and guarded deletion without document-thread execution.

The CAM integration exercises every canonical export and the tool-kind,
strategy, and postprocessor selectors across native Path operations. It covers
linear and circular motion, stock/removal and protected-model simulation,
postprocessed G-code authentication, stable native Path publication, context,
source/reference staleness, failed-candidate retention, injected rollback,
save/reopen, and dependency-ordered deletion.

The TechDraw integration exercises the six canonical graph roles, multiple
sheet/template configurations, six-direction projection, projected-element
selection, native dimension evaluation, annotations, and exact page ownership.
It authenticates fixed precomputed snapshots and aligned artifacts, proves that
publication does not project/recompute/read artifacts, retains stable native
identities through regeneration and save/reopen, restores injected failures,
and safely purges native projection ownership during deletion.
