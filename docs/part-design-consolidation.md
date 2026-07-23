# Part and Part Design Consolidation

VibeCAD ships Part Design as its one general 3D modeling workbench. The old
Part workbench is not registered. The Part geometry kernel, `Part::` document
types, Python modules, and compatibility command/tool identifiers remain
installed because Part Design and existing FCStd files depend on them.

## One tool per modeling intent

The shipped menus, toolbars, dialogs, and AI-native surface select one winner
for every overlapping operation:

| Intent | Shipped implementation | Reason |
| --- | --- | --- |
| Solid primitives | Part Design | Body-native additive/subtractive history |
| Add/remove material | Part Design | Body-native features and termination modes |
| Fillet, chamfer, thickness | Part Design | Body-native dress-up history |
| Datum geometry | Part Design | Body and attachment integration |
| General BREP boolean | Part | Works on arbitrary shapes and across Bodies |
| Standalone/surface geometry | Part | Distinct non-Body construction capability |
| Copy, compound, split, join, repair | Part | No equivalent superior Part Design operation |

The generic Part primitives dialog exposes only the distinct standalone
geometry it still owns: plane, helix, spiral, circle, ellipse, vertex, line,
and polygon. Duplicate Part box, cylinder, cone, sphere, ellipsoid, torus,
prism, and wedge creation remains available only while editing an existing
legacy object.

Old command identifiers remain registered for macros and saved integrations,
but redundant commands are absent from the shipped menu and toolbars. The UI
contains one top-level **Part Design** menu and no **Part Tools** menu.

## Body ownership and transactions

A `PartDesign::Body` can own both sequential `PartDesign::Feature` nodes and
explicit-link Part nodes such as `Part::Box`, `Part::Extrusion`, `Part::Fuse`,
and `Part::Cut`. Any eligible shaped feature can be the Body Tip and supplies
the Body's displayed shape. Internally, `Body::isResultFeature()` names this
contract explicitly; the historical `isSolidFeature()` API keeps its original
Part Design-only meaning. `Body::isSolid()` checks actual solid topology.

Part command results are adopted by a compiled Part Design modeling-context
bridge. It queues ordinary Part results created inside a command transaction,
validates the complete dependency graph, and performs adoption immediately
before the transaction closes. Adoption is all-or-nothing:

- An unowned result and its unowned eligible dependencies enter the active
  Body together.
- An object already owned by a Body, `App::Part`, or another group is never
  stolen.
- A graph with an externally owned dependency remains at document root.
- Aborted commands do not leave partial Body membership.
- Pending results are keyed by document, stable object/Body IDs, and the
  creating transaction ID. Committing or aborting one document therefore
  cannot consume another document's queue, and reusing an object name cannot
  revive a stale result.
- Explicit Python callers use `PartDesignGui.adoptPartResult(result, body)`;
  there is no asynchronous Python observer.

Create, undo, redo, and FCStd save/reopen preserve the mixed graph and Tip.

## Cross-container operations

General Part operations are valid across Bodies and `App::Part` containers.
Their C++ modeling links are adaptive, including boolean operands, compound
members, extrusion/revolution references, mirror/scale sources,
loft/sweep/ruled-surface inputs, face/project/thickness selections, and
copy/refine/reverse sources. A same-container or entirely unowned graph keeps
historical local-link behavior, including normal `App::Part` grouping. A link
promotes to global scope only when its assigned input actually crosses a Body
or `App::Part` boundary, and the scope is reconstructed after FCStd restore.
Origin and datum references remain owned by the Body Origin; the prospective
Body owner is evaluated before insertion so those links are promoted without
attempting illegal second group membership.

FeaturePython join, split, tolerance, and compound tools create the equivalent
global dynamic properties. Restored legacy local properties are migrated in
place while preserving value, group, documentation, editor mode, and property
status. A failed remove-and-recreate migration restores the original property.

When all operands have the same owner, the result may join that owner. When
operands belong to different containers, each operand remains where it is and
the result stays at their common document level. This avoids illegal second
GeoFeatureGroup membership and avoids out-of-scope links.

## Tree contract

Ownership and dependency are separate concepts in the model tree:

- Every Body `Group` member is displayed directly below that Body.
- A Part result does not visually absorb sibling inputs that the Body owns.
- Link properties still retain the exact dependency graph.
- Tree cache invalidation follows Body membership changes, including changes
  queued while another tree refresh is running.
- Adding a visible shaped result hides earlier visible shaped results and
  advances the Body Tip.

## AI-native contract

The active Part Design native pack advertises one semantic operation per
intent: `model.extrude`, `model.revolve`, `model.loft`, `model.sweep`,
`model.helix`, `model.mirror`, `model.boolean`, `model.fillet`,
`model.chamfer`, and `model.thickness`, plus canonical inspection and the
non-overlapping structural Part Design tools.

Material-forming tools require explicit `add_material` or `remove_material`
intent. Extrude, revolve, loft, and sweep also accept explicit `new_solid` or
`new_surface` intent when the retained Part implementation is the correct
standalone-geometry path. `Pad`, `Pocket`, and `Groove` do not appear in the
provider-facing vocabulary. Historical `part.*`, `partdesign.*`, and VibeScript
`pad`/`pocket`/`groove` entry points remain callable for saved integrations but
are enumerated as compatibility-only and are never advertised beside the
canonical operations.

Dispatch never silently drops an option. Standalone revolve maps `midplane`
to the native Part `Symmetric` property. Body transforms accept ordinary Part
results in whole-shape mode; feature-delta mode deliberately requires a
`PartDesign::FeatureAddSub` source and reports how to select whole-shape mode
otherwise. Options that have no equivalent in a selected standalone Part
implementation, such as extrusion refinement, fail with an explicit contract
error instead of appearing to succeed.

The Part Design VibeScript pack exposes the same consolidated kernel surface.
Sketch curves keep the short `line`, `arc`, `circle`, `ellipse`, and `bspline`
names; spatial curves use explicit `*_3d` names. Primitives, topology
construction, standalone surfaces, boolean and section operations, patterns,
dress-ups, repair, offsets, transforms, projection, geometric selection, and
measurement checks all remain in one source-parametric graph. `api.body`
publishes one exact solid; `api.publish` publishes an exact solid, shell, face,
wire, or compound.

Part Design also reuses the Material workbench's catalog and publication
semantics. `api.material` selects an exact native card UUID and declares the
physical or appearance properties the model consumes. `api.appearance` accepts
the 0-255 RGB values shown in FreeCAD's color editor together with
transparency, line/point styling, display mode, visibility, and selectability.
Passing those immutable values as `material=` and `appearance=` to
`api.body`/`api.publish` makes physical `ShapeMaterial` and the controlled
display subset part of the parametric output revision. Updates retain object
identity, source removal restores the pre-script baseline, conflicting manual
or Material-program ownership is rejected, and state persists through
save/reopen.

Material operations never use volume change alone as proof. The isolated
worker compares exact added and removed regions, so an additive feature must
add material without deleting its base and a subtractive feature must remove
material without adding any. Disconnected objects are not chained through a
Body to fake a single solid. Each strand or component is built with
`operation='new_solid'`, grouped with `api.compound`, and published with type
`compound`. This is the required representation for thread networks and
stitching unless a deliberate boolean union actually produces one connected
valid solid.

## Compatibility and rollback

Existing `Part::` object types and serialized properties are unchanged, and
legacy FCStd files are not rewritten merely by opening them. Existing root
objects stay at root until an explicit move or a new transaction produces a
legally adoptable graph. Saved `PartWorkbench` preferences resolve to Part
Design.

Keep an untouched copy before saving a mixed Body for use with an older build.
Older builds understand the retained Part objects but may reject an ordinary
Part feature as a Part Design Body Tip. For rollback, move ordinary Part nodes
out of the Body in this build and save a compatibility copy.
