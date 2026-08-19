# Part and Part Design Consolidation

VibeCAD ships Part Design as its one general 3D modeling workbench. The old
Part workbench is not registered. The Part geometry kernel, `Part::` document
types, Python modules, and view providers remain installed because Part Design
and existing FCStd files depend on them.

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

The duplicate `Part_Extrude`, `Part_Revolve`, `Part_Loft`, `Part_Sweep`,
`Part_Mirror`, and `Part_Scale` creation commands are not registered. The UI
contains one top-level **Part Design** menu and no **Part Tools** menu. Legacy
objects created by older documents still load and display through their retained
document types and view providers.

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

The active Native pack advertises one semantic operation per intent.
`model.primitive` creates solid Body primitives. `model.feature` creates
Extrude, Revolve, Loft, Sweep, and Helix features from Sketch profiles.
`model.dressup` owns Fillet, Chamfer, Draft, and Thickness, while
`model.transform` owns Mirror, Linear Pattern, Circular Pattern, and Scale.
`model.boolean` owns Body composition, split, and section operations. Exact
inspection and the non-overlapping construction, surface, repair, fastener,
history, and structure tools complete the surface.

The provider does not advertise Pad, Pocket, Groove, standalone Part feature
duplicates, or per-feature aliases. Each `model.feature` request contains an
exact nested feature branch. Omitting `combine` creates a new Body; `join`,
`cut`, and `intersect` combine the result with named Bodies. Global and
subelement axes use the same typed representation throughout Modeling.

Dispatch validates the frozen provider-visible schema and never silently drops
an option. Runtime diagnostics identify the exact invalid field or target while
transactions preserve the document on failure.

The Part Design VibeScript pack exposes the same consolidated modeling intent.
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

## Legacy document support

Existing `Part::` object types and serialized properties are unchanged, and
legacy FCStd files are not rewritten merely by opening them. Existing root
objects stay at root until an explicit move or a new transaction produces a
legally adoptable graph. Saved `PartWorkbench` preferences resolve to Part
Design. This document-loading support does not reintroduce the removed creation
commands or duplicate AI tools.

Keep an untouched copy before saving a mixed Body for use with an older build.
Older builds understand the retained Part objects but may reject an ordinary
Part feature as a Part Design Body Tip. For rollback, move ordinary Part nodes
out of the Body in this build and save a compatibility copy.
