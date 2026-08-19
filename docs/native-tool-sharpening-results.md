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
- `model.feature` creates Extrude, Revolve, Loft, Sweep, or Helix features from
  Sketch profiles.
- `model.hole` creates holes.
- `model.dressup` creates Fillet, Chamfer, Draft, or Thickness features.
- `model.transform` creates Mirror, Linear Pattern, Circular Pattern, or Scale
  features.
- `model.boolean` owns combine, split, and section operations.
- `model.history` owns feature deletion, suppression, and Body Tip changes;
  `model.recompute` recomputes an exact Body.

`model.feature` has one provider-visible request shape:

```json
{
  "label": "Flanged Shaft",
  "profile": {"object_name": "Sketch"},
  "feature": {
    "kind": "revolve",
    "axis": {"kind": "global_axis", "axis": "Z"},
    "angle_degrees": 360
  }
}
```

The exact `feature.kind` branch exposes only the fields meaningful to that
feature. Omitting `combine` creates a new Body. Supplying `combine` performs
`join`, `cut`, or `intersect` against named Bodies. Axes use the same two forms
throughout Modeling: a global X/Y/Z axis or an exact object subelement.

There are no `model.extrude`, `model.revolve`, `model.box`, or `model.cylinder`
aliases and no duplicate standalone Part extrusion, revolution, loft, sweep, or
mirror tools.

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

## Live model evidence

### Canonical revolution fixture

The immutable fixture contains one fully constrained, closed, face-buildable
eight-edge Sketch on the XZ plane and no solid. The request is:

> Revolve the prepared Hollow Flanged Shaft Profile 360 degrees around its exact
> global Z axis to create one solid Body. Report overall dimensions, volume,
> validity, and solid count.

The independent expected result is one solid with bounds
`36 x 36 x 50 mm` and volume `18221.2373908208 mm3` (`5800*pi`).

#### GPT-5.6 Terra, subscription, high reasoning

The final accepted rollout is retained as
`terra-model-feature-revolve-canonical-run-9.FCStd`, `.step`, and `.png` in the
local benchmark artifacts directory.

- one user turn;
- `model.feature` selected on the first mutation;
- the canonical profile, nested revolve feature, and global Z axis supplied on
  the first call;
- all subsequent inspection, view, export, and save calls accepted;
- zero rejected calls and no corrective steering;
- exact dimensions and volume, one valid solid, valid Body history, and valid
  STEP export.

This is acceptance evidence for the final canonical feature contract.

#### Qwen local run

The final Qwen 9B run used a 65,536-token context and the same fixture and prompt.
The provider did not emit its first trace within the fixed 1,500-second allowance.
The partial artifact is retained as
`qwen-model-feature-revolve-canonical-run-2.FCStd`. Because no tool selection was
emitted, this run is classified as local inference throughput, not a success or
failure of the final tool contract. Earlier Qwen runs were useful discovery
evidence, but they targeted tool aliases that have since been removed and are not
claimed as final-surface acceptance.

## Deterministic evidence

The release build completes after the command and DesignModel changes.

Focused Python contract, registry, provider, dispatch, and runtime suite:

```text
275 passed
```

Real GUI/provider lifecycle gates:

```text
VIBECAD_NATIVE_MODEL_PROFILES_GUI_OK
VIBECAD_NATIVE_MODEL_BRACKET_WORKFLOW_GUI_OK
```

The profile gate exercises all five canonical feature kinds, advanced
termination and orientation modes, global and subelement axes, join/cut/intersect,
invalid-call rollback, undo/redo, save/reopen, stable operation and Body IDs, and
`PartDesign.validateDesign`.

The bracket gate starts from a clean profile and exercises the complete Native
session/turn path used by the application, ending with one valid editable Body.

## Current conclusion

The Model ribbon now has one ordinary solid-modeling language for humans and AI:
PartDesign Body features. Retained Part tools provide only capabilities that add
real construction, surface, composition, or repair value. The canonical feature
contract has a zero-retry Terra-high live pass and complete deterministic GUI
coverage. Qwen's remaining final-run limitation is measured local throughput,
not an observed schema or CAD execution defect.
