# Design Modeling Completion Ledger

Status: In Progress
Scope owner: VibeCAD native multi-body modeling
Last updated: 2026-08-10

This document defines the design-level modeling architecture and the exact gate
for calling it complete. It is intentionally short and must remain under five
pages.

## Product contract

One saved VibeCAD design is one modeling space:

```text
Design
├─ Sketches       reusable profile definitions
├─ Bodies         stable modeled-part identities
├─ Components     assembly/BOM groupings of Bodies
└─ History        globally ordered modeling operations
```

- A **Sketch** belongs to the Design, not to a Body or Component. Any number of
  operations and Bodies may reference it. Its support/frame is an explicit
  dependency and does not determine ownership. A profile operation may use the
  complete sketch or persist exact selectable closed areas from that sketch.
- A **Body** identifies one modeled part and owns its complete current shape.
  With `AllowCompound` enabled, that shape may contain one or more topological
  solids and all ordinary Body operations act on the complete shape without
  changing Body identity. Disabling `AllowCompound` retains the traditional
  single-solid restriction. A Body owns visibility, appearance, material,
  assembly identity, and one stable rendered publication, but it does not own
  sketches, Body states, or the user-visible operation history.
- A **Component** groups Bodies that move, assemble, document, manufacture, and
  appear in a BOM as one unit. It owns a coordinate frame and product metadata,
  but it does not own sketches or History operations.
- A **History operation** owns its parameters, definition inputs, explicit
  target Body identities, the exact target coordinate frames at that History
  position, exact input Body states, and exact output Body states. One
  operation may advance zero, one, or many Bodies atomically, including Bodies
  in different Components.
- A **Body state** is an immutable Design-owned result node. It records the
  Body identity, producing operation, and exact previous state. It never lives
  inside a Body or Component.
- A **Body publication** is the Body's only rendered child. It resolves the
  newest state active at the current History marker into the Body's local
  coordinate frame. It is never accepted as a modeling input.
- An assembly **Occurrence** places a Component definition. It is not a
  modeling input and never becomes a feature dependency.
- Tree folders and rendered publication objects are **presentation** only.
  They never become modeling dependencies.

## Human workflow

The Model ribbon must expose these primary actions together:

1. **New Component** creates a physical/assembly grouping and its coordinate
   frame. It never changes sketch or History ownership.
2. **New Sketch** creates a reusable Design sketch. An active Body or Component
   is not required; the user selects its support/frame explicitly.
3. **New Body** creates a stable modeled-part identity, optionally assigned to a
   selected Component.
4. Extrude, Revolve, and later profile-based commands accept one reusable
   sketch, either its complete profile or selected filled areas, plus an
   explicit target set:
   - `New Body` creates a new Body, with an optional destination Component;
   - `Join`, `Cut`, and `Intersect` advance every selected target Body;
   - no target is inferred from tree order, label, visibility, active Body, or
     geometric intersection.
5. Accept commits one History operation and all Body outputs in one undoable
   transaction. Cancel restores every input Body and leaves no resources.

A sketch can therefore drive several features, and one cut or revolve can
modify several independent Bodies across Component boundaries without copying
the sketch or duplicating the operation. `Join` applies the generated tool to
each target while preserving Body identities. Combining separate Body
identities into one is a distinct Union operation with an explicit surviving
Body identity. Split remains the explicit operation for turning selected
result regions into independent Body identities; it is not required merely
because one Body contains several solids.

## Persistent model

Every semantic object carries a saved UUID independent of its label, tree
position, document object name, or transient object ID:

| Object | Required identity and links |
|---|---|
| Design | `DesignId`, schema version |
| Component | `ComponentId`, owning `DesignId` |
| Sketch | `SketchId`, owning `DesignId`, explicit support/frame link |
| Body | `BodyId`, optional `ComponentId`, `AllowCompound`, one stable publication |
| Operation | `OperationId`, owning `DesignId`, ordered input/output state links, target `BodyId` list |
| Body state | `BodyStateId`, `BodyId`, producing `OperationId`, previous state link |
| Body publication | `BodyId`, current `BodyStateId`; presentation only |

Feature dependencies may store only reusable definitions and exact immutable
Design-root Body-state features. A support may reference a Component coordinate
frame, but an operation must never store a Body container, Component container,
assembly occurrence, Body publication, tree folder, or view object as a shape
input. Each Body contains exactly one stable publication and changes only that
publication's current-state link as History advances. The application validates
this contract and rejects a cycle before mutating the document.

Body-state geometry is stored in the Body's local definition coordinates.
Each operation stores a parallel target-frame snapshot used to transform its
Design-wide tool into every target Body. Later Component or occurrence motion
therefore cannot silently rewrite earlier feature geometry or enter the
shape-dependency graph.

For a multi-Body operation, geometry is evaluated once from the shared
definition and parameters, applied independently to each exact input state,
and published only after every target succeeds. Partial output is forbidden.
Undo, redo, save, reopen, suppression, and parameter edits operate on the one
logical operation and its complete output set.

### Operation ports

The persistent operation contract has independent ordered input and output
ports; it must not assume one input Body always produces one output Body.

- Each input port records an exact Body state, Body identity, and coordinate
  frame.
- Each output port records a Body identity, output shape, presence state,
  coordinate frame, optional destination Component, and the input port whose
  state it advances (`none` means the operation creates that Body).
- Extrude, Revolve, Fillet, and similar pointwise tools normally use matching
  input/output ports. `New Body` uses no input and one created output.
- Union may consume several present inputs, publish the combined shape on one
  surviving Body, and publish absent states for the consumed Body identities.
- Split may consume one Body and publish several Bodies, retaining the source
  identity for one result and assigning saved identities to the others.
- Presence is History state, not viewport visibility. Moving the History
  marker before a Body-creating operation makes that Body absent; undoing a
  Union restores consumed Bodies without recreating identities.

The graph service owns port creation, identity allocation, reconciliation, and
validation. Individual tools supply geometry and user choices; they do not
invent alternative ownership rules.

## Compatibility and migration

The owner approved replacing the Body-pinned authoring workflow on 2026-07-30.
New native commands and VibeScript therefore use only the Design-wide graph.
Compatibility is confined to safe document migration:

- existing files remain readable; legacy Body-owned sketches and features are
  represented as imported initial Body state until edited or deliberately
  converted;
- legacy `Tip` remains a file-import fallback, but shared modeling selection
  resolves through the Body's exact modeling-state interface so a presentation
  publication can never become a new dependency;
- editing or attaching an unambiguous one-feature legacy Model fastener
  atomically promotes its existing Body and generator into an immutable initial
  state plus one generated Design operation; Body/generator identity, undo/redo,
  and save/reopen are preserved, while ambiguous consumers or History layouts
  fail without mutation;
- old public command/type names may remain callable for file or macro recovery,
  but they are not presented as parallel authoring paths in the shipped UI or
  AI context;
- migration never moves a sketch, guesses a target from visibility, or writes
  presentation links into modeling properties;
- unsupported legacy topology fails with an explicit conversion report and
  leaves the saved document unchanged.

## Implementation ledger

| Area | State | Complete when |
|---|---|---|
| Architecture | **Specified** | Design definitions/history, Body states, Component membership, occurrences, and presentation references are distinct and centrally enforced |
| Persistent identity | **Open** | Design, Component, Sketch, Body, Operation, and Body-state UUIDs survive copy, undo/redo, and reopen with defined collision behavior |
| Model ribbon | **Open** | New Component, New Sketch, and New Body are adjacent, correctly enabled, and do not create an implicit Body/Component dependency |
| Shared sketches | **Verified** | One Design sketch drives independent saved closed-area features in two or more Bodies; area identity survives parameter edits and reopen without transfer, clone, or duplicate geometry |
| Multi-Body Extrude | **Open** | New Body/Join/Cut/Intersect use explicit targets and commit atomic per-Body outputs |
| Multi-Body Revolve | **Open** | New Body/Join/Cut/Intersect satisfy the same contract with an explicit axis |
| Remaining native tools | **Open** | Every Model command is classified as component operation, single-Body operation, in-place edit, or read-only and uses the central reference boundary |
| VibeScript | **Open** | The API exposes component/sketch/body identities and explicit target lists without requiring Body-owned sketches |
| Tree and History | **Open** | Design shows global Sketches plus Components/Bodies; the global timeline shows one entry per logical operation and can filter by affected Component without changing ownership |
| Lifecycle | **Open** | Accept/cancel/delete/suppress/edit/undo/redo/save/reopen never creates cycles, partial outputs, abandoned geometry, or duplicate rendering |

## Objective `DONE` gate

This effort is complete only when all ledger rows are **Verified** and automated
plus on-screen acceptance proves all of the following:

- create two Components, three Bodies across them, and one global sketch from
  the Model ribbon;
- reuse that sketch for more than one additive feature without changing sketch
  ownership;
- select independent closed areas from that master sketch for different
  features without copying or trimming the sketch;
- use one sketch-driven Cut across all three Bodies and see one History entry;
- edit the sketch and operation and update all three Bodies atomically;
- cancel both initial creation and later editing with exact state restoration;
- suppress/unsuppress, delete, undo/redo, save/reopen, and copy the Component
  without identity loss or dependency cycles;
- use one Cut across Bodies in both Components, with the shared dependency
  visible in the operation target list;
- place multiple assembly occurrences of either Component without occurrence
  links entering the modeling graph;
- open legacy Body-based files without changing them until conversion is accepted;
- build the touched native targets and pass focused graph, GUI lifecycle,
  VibeScript, tree, timeline, and release smoke tests.

Progress is reported from this ledger, never from an estimated percentage.
