# Native Tool Sharpening Results

## Objective

VibeCAD's Native tools should make the correct CAD operation the obvious choice
without tool hints, corrective steering, or knowledge of VibeCAD internals. The
public surface is judged by first-call selection, schema accuracy, geometric
accuracy, and the amount of provider context required to achieve those results.

The reusable benchmark corpus and fixed run rules live in
`docs/native-tool-sharpening-benchmark.md`.

## Acceptance method

Each live run starts from a clean document or an immutable fixture and uses an
ordinary engineering request. A run is accepted only from the document and tool
trace:

- every mutation must be a published Native tool call;
- rejected calls count even when the model later recovers;
- final geometry is measured independently from the saved document;
- validity, solid count, Body history, neutral export, and visible presentation
  are checked where the case requires them;
- timeout before a provider emits a tool trace is an infrastructure result, not a
  CAD-tool result.

Unit tests prove closed contracts and deterministic runtime behavior. Real GUI
gates prove document transactions, Body ownership, recompute, undo/redo,
save/reopen, provider dispatch, and OpenCASCADE geometry.

## Final Model surface

### One Body workflow

PartDesign is the only creation path for ordinary solid-feature modeling.

- `model.primitive` creates Box, Cylinder, Sphere, Cone, Ellipsoid, Torus, Prism,
  Wedge, or Tube Bodies.
- `model.extrude`, `model.revolve`, `model.loft`, `model.sweep`, and
  `model.helix` create focused Body features from Sketch profiles.
- `model.hole` creates holes.
- `model.dressup` creates Fillet, Chamfer, Draft, or Thickness features.
- `model.transform` creates Mirror, Linear Pattern, Circular Pattern, or Scale
  features.
- `model.boolean` owns combine, split, and section operations.
- `model.history` owns feature deletion, suppression, and Body Tip changes;
  `model.recompute` recomputes an exact Body.

Each profile operation has one provider-visible request shape. For example,
`model.revolve` accepts:

```json
{
  "label": "Flanged Shaft",
  "profile": {"object_name": "Sketch"},
  "profile_scope": "entire_sketch",
  "axis": {"kind": "global_axis", "axis": "Z"},
  "extent": {"kind": "angle", "angle_degrees": 360}
}
```

Each focused tool exposes only the fields meaningful to that operation and
adapts into the shared feature runtime. Omitting `combine` creates a new Body.
Supplying `combine` performs `join`, `cut`, or `intersect` against named Bodies.
Axes use the same two forms throughout Modeling: a global X/Y/Z axis or an exact
object subelement. There are no `model.box` or `model.cylinder` aliases and no
duplicate standalone Part extrusion, revolution, loft, sweep, or mirror tools.

### Retained Part value

`model.part` contains only construction, surface, compound, cross-section,
offset, projection, and repair operations that PartDesign does not replace.
`model.surface` contains the dedicated surface workflows. These are not
alternate ways to build an ordinary solid Body.

### Human ribbon parity

The Model ribbon presents the same boundary:

- Create and Remove Material: PartDesign Extrude, Revolve, Loft, Sweep, Helix,
  primitives, and Hole.
- Finish Shape: PartDesign Fillet, Chamfer, Draft, and Thickness.
- Transform Features: PartDesign Scale, Mirror, Linear Pattern, and Circular
  Pattern.
- Construction and Surface Geometry: the retained unique Part construction and
  surface tools.
- Boolean, Split, and Repair: the retained composition and repair tools.

The obsolete `Part_Extrude`, `Part_Revolve`, `Part_Loft`, `Part_Sweep`,
`Part_Mirror`, and `Part_Scale` creation commands are not registered. Their
legacy document object and view-provider support remains so existing FCStd files
can still open, display, and be inspected.

## Production corrections found by acceptance

### Provider-visible schemas

The provider resolver previously measured projected schemas but returned the raw
schemas. It now returns the same compact, provider-valid object schemas it
validates and budgets. A capability with one possible operation omits the
redundant operation discriminator. Dispatch resolves that sole operation from
the frozen provider schema instead of relying on an alias or heuristic.

### Exact feature contracts

Live failures exposed four competing axis grammars, generic result objects,
ambiguous inspection targets, and feature-specific fields mixed into one payload.
The final contracts use one axis grammar, nested exact feature branches, one
`inspect.query.targets` array, and no caller-authored result identity.

### Legacy Body promotion

A Body containing one legacy feature could pass preflight and then fail because
its Tip was not the stable Design publication. Target enrollment now promotes
that state through the central `DesignModel` legacy-body initialization path
before an operation records its input. This single correction applies to human
commands and AI tools and preserves stable Body identity.

### Description signal

Provider descriptions state capability and geometry semantics. Warning,
prohibition, migration, and recovery prose was removed from descriptions.
Closed schemas and runtime diagnostics still enforce every invariant and return
the exact rejected field and required shape when a call is invalid.

### Focused profile operations

The Model ribbon presents Extrude, Revolve, Loft, Sweep, and Helix as distinct
human operations. Native now presents the same five focused tools instead of
collapsing their incompatible direction, axis, extent, path, and helix grammars
into one `model.feature` union. All five adapt into the existing exact feature
runtime; there is no second geometry implementation.

The focused schemas total 15,787 JSON bytes. The collapsed schema was 8,822
bytes, so exact operation-specific inputs cost 6,965 bytes while the complete
Model surface remains within its 64-KiB schema budget. The Model tool-count cap
is 32 and the current surface uses 29.

Focused Extrude uses a direct length/up-to/two-sided extent. Its canonical
length form is `{"kind":"length","length_mm":12}` and the Sketch normal is
used unless an axis or vector is supplied. Profile topology is explicit:
`entire_sketch` uses the complete closed profile, while
`selected_internal_faces` requires exact `InternalFaceN` identities.

## Live model evidence

### Canonical revolution fixture

The immutable fixture contains one fully constrained, closed, face-buildable
eight-edge Sketch on the XZ plane and no solid. The request is:

> Revolve the prepared Hollow Flanged Shaft Profile 360 degrees around its exact
> global Z axis to create one solid Body. Report overall dimensions, volume,
> validity, and solid count.

The independent expected result is one solid with bounds
`36 x 36 x 50 mm` and volume `18221.2373908208 mm3` (`5800*pi`).

#### Qwen local, 65,536-token context

With the collapsed `model.feature` schema, Qwen selected the right family but
needed four calls: three rejected payloads mixed helix parameters, an invented
extent kind, and an Extrude direction into a Revolve before the fourth call
created the exact solid. The retained artifact is
`modeling/qwen-model-feature-compact-1/result.FCStd`.

With the focused surface, the unchanged request selected `model.revolve` and
supplied the exact whole profile, global Z axis, and 360-degree extent on the
first call. The retained `result.FCStd`, `.step`, and `.png` are under
`modeling/qwen-model-revolve-focused-2/`.

- one user turn;
- one accepted tool call and zero rejected calls;
- exact `36 x 36 x 50 mm` bounds and `18221.2373908208 mm3` volume;
- one valid solid and valid STEP export.

### Annular Extrude fixture

The second fixture contains one face-buildable Sketch with an 80-mm outer circle
and 32-mm inner circle and no Body. The unchanged request asks for a 12-mm normal
Extrude. The independent result is one solid with `80 x 80 x 12 mm` bounds and
`50667.606317096186 mm3` volume.

The first focused Extrude schema still exposed the internal `one_side/sides`
layout. Qwen needed two rejected calls before creating the correct geometry. A
direct length extent removed both payload errors. The next run revealed a
topology ambiguity: Qwen selected both internal faces and produced two solids.
Making whole-Sketch versus selected-face scope explicit removed that ambiguity.

The final Qwen artifact is retained under
`modeling/qwen-model-extrude-focused-3/`:

- one user turn;
- one `model.extrude` call and zero rejected calls;
- `profile_scope: "entire_sketch"` selected on the first call;
- exact dimensions and volume, one valid annular solid, and valid STEP export.

GPT-5.6 Terra through ChatGPT subscription at high reasoning repeated the final
case with no steering. Its artifact is retained under
`modeling/terra-high-model-extrude-focused-1/`:

- one accepted `model.extrude` mutation;
- two accepted inspections and one accepted viewport capture;
- zero rejected calls;
- exact dimensions, volume, topology, validity, and STEP export.

### NTS-D01 impeller baseline

The 2026-08-19 Qwen 9B run used a 65,536-token context, the original attached
compressor-wheel image, and the unchanged request:

> Recreate this turbo to the best of your ability.

The rollout and partial FCStd are retained under
`native-tool-sharpening/impeller/baseline-qwen` in the local benchmark artifacts
directory.

- Vision correctly identified a central bore, hub, and curved, twisted blades.
- The first mutation chose `model.revolution_sketch`; `model.primitive` was never
  called.
- After the automatic Sketch transition, the model created concentric circles and
  fifteen standalone points in the axis-profile Sketch, then attempted unrelated
  circular arcs.
- One negative-angle call was rejected by the published schema and corrected on
  the next call.
- The run was stopped after the geometry had diverged from a revolvable hub profile
  and continued to accumulate unconstrained, non-face geometry.

This baseline disproves the primitive-only hypothesis. The first observed defect
is operation selection: an axis-profile Sketch and standalone Point geometry looked
like suitable inputs for the pictured blade field. The next comparison removes
those attractors only from the benchmark provider surface; no production capability
is removed until repeated live evidence supports that boundary.

### Native Assembly surface

Assembly acceptance used the same outcome-only rule. Prompts stated the desired
mechanism or deliverable and its engineering values; they did not name tools,
fields, call order, prerequisite reads, or recovery steps. Rejected calls counted
even when a model later recovered.

The screw and rack-pinion contracts originally described their participants as
`first` and `second`. Qwen selected the correct screw capability but reversed the
Slider and Revolute roles on its first call. The provider contracts now name the
mechanical roles directly while deterministically adapting them to the established
joint runtime:

- screw: Slider joint/component, Revolute joint/component, and lead;
- rack-pinion: rack Slider/component, pinion Revolute/component, and pitch radius.

The unchanged plain requests then passed on the first mutation call with both
Qwen and GPT-5.6 Terra/high:

> Make LeadScrew move Carriage 4 mm per revolution. Save the assembly.

> Make Pinion drive Rack with a 20 mm pitch radius. Save the assembly.

The retained artifacts are under
`assembly/{qwen,terra-high}-native-{screw,rack-pinion}-plain-description-4/`.
Every run had zero rejected calls, produced the correct coupling ratio, saved and
reopened, and passed neutral STEP validation.

The final multi-part check started from a verified fixture containing seven source
parts and no Assembly, joints, Robot, simulation, or BOM. Qwen and Terra received
the identical request:

> Create a proper assembly from these parts and save it.

Qwen made 24 accepted calls with no rejection: it created the Assembly, inserted
all seven parts, grounded the base, discovered connectors, created five joints,
solved, saved, and reopened. Terra made 33 accepted calls with no rejection and
created six joints after broader connector inspection. Both results contain seven
occurrences, one grounded base, a valid joint graph, a clean solve, and valid FCStd
and STEP artifacts. Terra's result retained five degrees of freedom versus Qwen's
eleven; that design-quality difference came from model reasoning under an
intentionally minimal request, not tool-call ambiguity or failure. The artifacts
are retained under `assembly/{qwen,terra-high}-native-proper-assembly-minimal-1/`.

Focused live runs also cover relations, gears, belts, component interfaces,
fasteners, linked-subassembly rigidity, exploded views, Robot configuration and
paths, motion studies, playback, expandable BOM creation, and ASMT export. Each
family has a zero-rejection Qwen result and a Terra/high confirmation saved in the
same Assembly benchmark directory.

## Deterministic evidence

The release build completes after the command and DesignModel changes.

Focused Python contract, registry, provider, dispatch, and runtime suite:

```text
255 passed
```

Real GUI/provider lifecycle gates:

```text
VIBECAD_NATIVE_MODEL_PROFILES_GUI_OK
VIBECAD_NATIVE_MODEL_BRACKET_WORKFLOW_GUI_OK
VIBECAD_NATIVE_CODEX_CROSS_RIBBON_GUI_OK
```

Assembly, Robot, shared Native context, dispatch, registry, session, and surface
guardrails:

```text
516 passed
VIBECAD_NATIVE_RIBBON_SURFACE_GUI_OK
```

The live ribbon gate resolves the exact provider surface for every human ribbon.
Its final counts are Model 30, Assemble 38, Mesh 19, Analyze 31, Manufacturing 26,
Drawing 41, Parameters 11, Aero 9, Sketch setup 11, and Sketch edit 39 tools.
It also verifies surface-specific view operations and inter-turn workspace switching.

The profile gate exercises all five canonical feature kinds, advanced
termination and orientation modes, global and subelement axes, join/cut/intersect,
invalid-call rollback, undo/redo, save/reopen, stable operation and Body IDs, and
`PartDesign.validateDesign`.

The bracket gate starts from a clean profile and exercises the complete Native
session/turn path used by the application, ending with one valid editable Body.
The cross-ribbon gate freezes the exact tool set selected by the human across all
nine supported CAD work surfaces.

## Current conclusion

The Model ribbon now has one ordinary solid-modeling language for humans and AI:
PartDesign Body features. Retained Part tools provide only capabilities that add
real construction, surface, composition, or repair value. Focused Revolve and
Extrude each have zero-retry Qwen live passes, and the final Extrude contract has
a zero-retry Terra-high subscription pass. The saved artifacts prove exact CAD
geometry and topology rather than provider-only schema acceptance.
