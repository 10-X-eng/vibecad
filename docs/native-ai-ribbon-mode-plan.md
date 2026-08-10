# Native AI Ribbon Mode — Official Implementation Plan

Status: Official plan — active goal ledger
Implementation status: In progress; Native remains disabled
Scope owner: VibeCAD AI-assisted native authoring
Last updated: 2026-08-10
Checklist status: 362 complete / 384 pending / 746 total (48.5% by row count)

## Purpose

Reintroduce Native assistant mode as a clean, ribbon-scoped authoring system.
The human chooses and changes the active VibeCAD ribbon. The assistant receives
only the tools belonging to that human-selected surface and cannot activate a
different workbench or ribbon for itself. The assistant may finish the exact
human-opened Sketch edit task only through the explicit Leave Sketch control;
that state change ends the current assistant turn.

Native mode must be substantially easier for an AI to use than the retired
direct-tool surface. VibeCAD owns document identity, revisions, transactions,
the current working set, operation receipts, and recovery. The assistant owns
modeling intent and explicit operation parameters; it is never expected to
reconstruct the document graph from a long transcript of tool calls.

## Owner-approved breaking-change scope

The owner explicitly approved the following direction in chat on 2026-08-06:

- remove the obsolete workbench-pack and direct native-tool architecture;
- remove compatibility aliases and compatibility-only response fields;
- change native tool names, schemas, and result contracts where needed;
- remove provider-accessible workbench switching;
- do not preserve saved native conversations or third-party callers of the
  retired native tool contracts;
- keep VibeScript as a separate, supported authoring system rather than mixing
  Native mutations into VibeScript regeneration.

This approval applies to the obsolete Native assistant surface. It does not
authorize unrelated public API removal, removal of VibeScript tools, removal of
human ribbon commands, or general FreeCAD cleanup.

The owner approved this additional Sketch boundary refinement on 2026-08-09:

- `document.open` remains human-authorized and is never provider-callable;
- `document.save` is not exposed while a Sketch edit task is active;
- Native exposes one exact-target Leave Sketch operation, while Cancel Sketch
  remains human-controlled;
- Leave Sketch may finish only the current human-opened Sketch task and must not
  activate another workbench or ribbon; and
- leaving Sketch invalidates the frozen turn, so the assistant must wait for a
  new human turn before using the newly resolved surface.

Affected callers are old Native assistant conversations, external clients that
called the old direct CAD tool names, tests of old workbench packs, and code
that consumes their old result shapes. There will be no compatibility shim or
dual registration. The migration is to start a new Native conversation against
the new ribbon surface. Rollback is by reverting the ordered implementation
commits before release, not by retaining two runtime architectures.

## Tracking model

This document is the granular execution plan and definition of done. During
implementation it should be attached to one durable Codex goal whose objective
is the final product outcome. The goal provides persistence; this ledger
provides the exact work breakdown. Neither replaces the other.

No checkbox may be closed from inference. Each capability row is complete only
when all of the following exist:

1. a final provider-facing schema or an explicit human-only classification;
2. an exact-target implementation using the correct domain API;
3. the concise success and failure result contract;
4. a correct domain-state update and transaction receipt;
5. focused tests for success, invalid input, undo, redo, and save/reopen when
   the operation mutates the document;
6. live ribbon-surface coverage proving the action did not disappear or leak
   onto another surface.

A broad end-to-end test cannot close multiple unfinished capability rows.

### Implementation evidence

The following evidence is part of the ledger and must stay current as the
implementation changes:

- The C++ ribbon controller publishes `VibeCADActiveSurfaceId`, a monotonic
  `VibeCADActiveSurfaceRevision`, and `VibeCADActiveSurfaceManifest` from the
  same deduplicated command entries used to build the visible page.
- A clean-profile GUI gate currently observes Model 75, Assemble 53, Mesh 60,
  Analyze 103, Manufacture 54, Drawing 107, Parameters 24, Sketch setup 15,
  and Sketch edit 105 actions, with 527 unique command IDs. These are the
  current default-preference build counts. With advanced and experimental CAM
  plus separated Drawing dimensions enabled, the same gate observes
  Manufacture 60 and Drawing 112 on the current build. The classified union is
  Manufacture 61 when optional CAMotics is available, Drawing 113 across both
  dimension layouts, and 540 unique ribbon command IDs overall.
- `VibeCADNativeSurfaceVariants.py` now constrains conditional live surfaces to
  graphs the shipped workbenches can actually produce. Analyze covers both
  Netgen build states and the three valid VTK states as six environments with
  exact 81, 98, or 103-action group/composite graphs; VTK Python without VTK
  is rejected. Manufacture covers all eight CAM preference states, both Robot
  build states, and the valid OCL/CAMotics runtime combinations as 40 exact
  environments and 32 distinct visible graphs. Classification rejects an
  optional action under the wrong preference, a command moved to the wrong
  group, and flattened order disguised with the wrong composite parentage.
  Separate compiled GUI processes prove all eight CAM preference states on the
  current OCL-enabled/CAMotics-absent build: both simulator orders preserve
  Manufacture counts 54, 56, 57, and 60. The harness restores the exact prior
  preference values and key presence in `finally`; the default and maximum
  Drawing/CAM gates remain green.
- The clean-profile GUI gate now computes named stale-manifest and unclassified
  live-action deltas before enforcing exact default order. Its maximum variant
  also proves the supported Drawing and Manufacture inventory union, while the
  pure conditional matrix proves every allowed Analyze/Manufacture action
  belongs to at least one valid environment. Removing a shipped action or
  retaining an optional action in no valid graph therefore fails the gate.
- `VibeCADRibbonSurface.py` strictly validates the live schema, action order,
  dropdown parentage, duplicate IDs, controller agreement, and revision. It
  exposes no activation or switching API.
- VibeScript surface resolution no longer imports or calls
  `VibeCADWorkbenchTools`. All 17 registered VibeScript surface summaries had
  SHA-256 `05666abeb08e2f9ce89e6c254a6dd25cf76d8d47112395d753e95a8126397bf9`
  both before and after decoupling; 145 modeling-surface tests and 72
  provider/engine guardrail tests passed.
- The retired workbench-pack module and CMake entry are deleted. The provider
  registry now contains only the exact core/catalog/view tools, focused reads,
  saved assembly playback controls, and VibeScript tools that the current
  VibeScript surfaces advertise. A guardrail compares that set for exact
  equality, so an old direct Native name cannot remain quietly callable.
- No production module, runtime list, or old pack contract test imports
  `VibeCADWorkbenchTools`; negative guardrails are the only remaining textual
  references. The obsolete
  command-prefix, arbitrary command-list, object-template, tool-pack, and
  pack-filtered workbench-object context paths have been removed from
  `VibeCADCore.py`. Old direct implementation files remain unregistered only
  as migration inputs; each must either supply a proven domain algorithm to a
  new capability module or be deleted under steps 2.12–2.13.
- `VibeCADNativeActionManifest.py` explicitly classifies the proven default
  action graph, exact Analyze/Manufacture environment variants, and conditional
  Drawing IDs. It preserves live order, rejects unknown IDs, group drift, and
  composite-role drift, and contains no dispatch or activation API. Default,
  maximum, and all eight CAM-preference clean-profile GUI gates pass.
- `VibeCADNativeContextManifest.py` separately inventories 25 current context
  actions: nine Assembly, four CAM-only additions, ten Drawing, and two
  Inspection actions. Assembly context actions now have stable object names;
  source-drift tests prove the C++ and CAM context inventories exactly. The
  current VibeCAD fastener workflow adds no hidden context-only action: Model
  exposes four fastener commands and Assemble exposes Insert and Edit.
- `VibeCADNativeCapabilityRegistry.py` separates provider definitions from
  callable implementations, requires exact `domain.operation` names and
  discriminated variants, rejects open JSON objects and raw command dispatch,
  and enforces 24-tool and 64-KiB serialized-schema limits per surface.
  `VibeCADNativeSchemaRules.py` recursively rejects unbounded text and arrays,
  open nested objects, malformed required fields, and schema references. The
  production registry now contains the five finished common families, the
  finished Model structure/Sketch-readiness families, two compact typed
  `model.feature` variants covering all nine current Design primitives and all
  five reusable-profile Design operations, and the focused `model.hole`
  capability plus its read-only thread catalog. The focused `model.dressup`
  capability currently supplies Fillet, Chamfer, Draft, and Thickness. The
  compact typed `model.transform` capability now supplies Design Mirror,
  Design Linear Pattern, and Design Circular Pattern and is the shared contract
  boundary for the remaining Design transformations. The focused
  `model.surface` family now supplies Surface Filling, Geometric Fill,
  Sections, Extend Face, Curve on Mesh, and Blend Curve through one shared
  clean boundary. The registered Model definition set currently serializes to
  65,527 bytes, 9 bytes below the
  unchanged 64-KiB hard limit, without opening any schema object. An incomplete
  ribbon still exposes zero Native tools; no legacy `core.*` tool leaks through
  that unavailable surface.
- `VibeCADNativeTurn.py` freezes the exact human ribbon identity, ordered tool
  names, and canonical provider-schema digest without owning dispatch. It
  cannot start against the incomplete production registry; focused tests prove
  unchanged reauthorization and fail-closed invalidation for ribbon revision
  and schema changes. The module is 164 lines and remains separate from the
  registry and all domain execution modules.
- `VibeCADAuthoringMode.py` defines only `native | vibescript`, keeps unsaved
  choices in process memory, promotes the exact choice to the project manifest
  after first save, and restores saved choices without touching the CAD
  document. The service no longer hardcodes VibeScript. Build123d and OpenSCAD
  are rejected as authoring modes; the unrelated removed-workbench preference
  cleanup remains separate. The mode module is 143 lines.
- `VibeCADNativeState.py` owns monotonic per-document structural revisions,
  host-generated call tokens, stale preflight, bounded verified-result replay,
  and exact created/changed/deleted/replaced identities. Document observers
  count object creation, deletion, and structural property changes while
  filtering visibility, appearance, transient recompute pulses, selection,
  camera, tree, and UI events. It contains no tool execution.
  `VibeCADNativeStatePersistence.py` keeps atomic bounded state storage separate
  from the in-memory state machine. The state module is 681 lines. Four hundred
  twenty-five focused state, mode, Native domain, provider, VibeScript surface,
  registry, and guardrail tests pass; a clean GUI gate still reports the exact
  527-command default ribbon inventory.
- `VibeCADAuthoringModePolicy.py` contains the selector policy independently of
  Qt. The header exposes exactly VibeScript and Native, requires explicit human
  confirmation to take manual control, and fails closed during an assistant
  run, transaction, task/edit, recompute, unresolved editor work, or external
  MCP control. Separate clean-profile GUI gates prove those live blockers and
  prove first-save, close/reopen, changed-authority lockout, and independent
  multi-document mode restoration. Native remains disabled in production until
  the active ribbon registry is complete.
- `VibeCADNativeMutation.py` is the single immediate mutation runner. It
  reauthorizes the frozen ribbon before stale preflight, refuses nested
  transactions, buffers document-observer events until commit, recomputes only
  exact affected objects, requires a postcondition, and records one receipt.
  A real FreeCAD GUI gate proves one undo step, exact undo/redo, and rollback
  without a false authority change. The runner is 273 lines.
- `VibeCADNativeBackground.py` separately owns expensive detached preparation,
  bounded monotonic progress, cooperative cancellation, one active job per
  document, and document-thread commit dispatch. A Qt gate proves the event
  loop remains responsive, commit returns to the GUI/document thread, and
  closing a document cancels its active job. A frozen-turn test proves a ribbon
  change during preparation prevents commit. The manager is 350 lines.
- Exact target identity, active-domain snapshots, and common reads are split
  across `VibeCADNativeTargets.py`, `VibeCADNativeSnapshot.py`, eight narrow
  domain snapshot modules, `VibeCADNativeView.py`,
  `VibeCADNativeMeasure.py`, and `VibeCADNativeInspect.py`. Snapshots are rebuilt
  from the live document, contain only the active human-selected domain, include
  exact bounded selection when present, and never depend on a chat transcript.
- `VibeCADNativeDocument.py` is an 85-line guarded existing-path save and
  `VibeCADNativeUndo.py` is a 308-line session-only assistant-run history
  ledger. Local undo requires the exact FreeCAD transaction name, undo count,
  document revision, and current assistant run; it refuses unrelated human
  history and restores a failed undo attempt by redo before reporting failure.
  `VibeCADNativeApplicationManifest.py` separately classifies application-strip,
  document-tab, assistant, and debugger controls, with source-drift tests.
- A clean-profile FreeCAD GUI gate now proves real OCC distance, angle, radius,
  mass, element, and validity reads; direct Fit All and Isometric calls; grid
  and screenshot presentation without structural revision changes; guarded
  FCStd save; exact assistant-local undo; and refusal to undo a later human
  transaction. The gate now invokes the final five common provider schemas
  through `VibeCADNativeDispatch.py`, exact host-generated call tickets, and
  production common bindings rather than calling those helpers directly. The
  live gate also found and corrected the Python
  `Materials.Material.Name` handling needed to match the C++ Mass Properties
  default-density rule.
- Codex `callId` and Anthropic `tool_use.id` now reach the Native dispatcher
  unchanged. Duplicate provider calls replay the exact prior bounded result;
  reuse of an ID for another tool or argument set is refused. Native turn
  creation, provider-loop wrapping, provider context, registry assembly, and
  runtime assembly are separate 18–169-line modules. Normal failures contain
  only a short code, error, and actionable state fields; a separate debug sink
  receives internal diagnostics. The Native provider context contains one
  active-domain snapshot and optional exact selection, not legacy document,
  command, template, or workbench-pack summaries. A focused Native/provider/
  VibeScript guardrail run passes 492 tests, and the dispatcher-backed clean
  GUI gate reports `VIBECAD_NATIVE_COMMON_GUI_OK`.
- The first Model capability slice uses `PartDesign::Component`, empty
  `PartDesign::Body`, standalone Design-history Sketches, global reusable
  SubShapeBinders, and `PartDesign::DesignClone`; it does not wrap GUI commands
  or revive obsolete Body-owned Sketch/reference semantics. Its schema,
  bindings, runtime routing, object algorithms, reusable-definition algorithms,
  and readiness reader are separate 12–246-line modules. A dispatcher-backed
  clean-profile gate proves invalid-input no-ops, Component and Body structure,
  base-plane and exact planar-face Sketch support without edit mode, read-only
  readiness, exact History reference resolution, clone output identity,
  per-operation undo/redo, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_STRUCTURE_GUI_OK`.
- The Design primitive slice maps the nine current human primitive leaves to
  `PartDesign::DesignBox`, `DesignCylinder`, `DesignSphere`, `DesignCone`,
  `DesignEllipsoid`, `DesignTorus`, `DesignPrism`, `DesignWedge`, and
  `DesignTube`. Provider schemas, result/placement handling, primitive
  validation, runtime routing, and bindings remain separate 28–243-line
  modules. The implementation uses the native Design operation edit API and
  its exact current New Body, Join, Cut, and Intersect semantics. A separate
  497-line dispatcher-backed gate proves every primitive, explicit nontrivial
  placement, exact Component destination, invalid-input no-ops, Body-local
  downstream result frames, all four result modes, per-operation undo/redo,
  stable operation/Body identity, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PRIMITIVES_GUI_OK`.
- The Model profile slice maps the current Extrude, Revolve, Loft, Sweep, and
  Helix task controls directly onto global Design operations. It covers every
  current termination or definition mode, exact profile/axis/path/section/
  auxiliary references, taper, symmetric/reversed and handedness controls,
  sweep transition and orientation modes, and New Body/Join/Cut/Intersect
  results. Schema, input resolution, references, shared profile setup, and the
  five operation algorithms are split across focused 53–341-line modules. A
  dispatcher-backed 891-line clean-profile gate proves invalid-input no-ops,
  all current operation modes, the full five-by-four result matrix, exact
  undo/redo, and stable operation/Body IDs after FCStd save/reopen; it reports
  `VIBECAD_NATIVE_MODEL_PROFILES_GUI_OK`. Target-dependent global Extrude and
  Revolve terminations now consume exactly one immutable Design input state
  instead of inventing a Body-owned BaseFeature. Three focused C++ lifecycle
  regressions, all 39 broader Design-modeling tests, and the full Part Design
  VibeScript API integration pass with that core behavior.
- The focused Hole slice uses the global `PartDesign::Hole` operation with
  exact reusable Sketch input rather than GUI command dispatch or a Body-owned
  compatibility path. Its 60–561-line schema, live metric thread/head catalog,
  bindings, runtime, and native algorithm modules cover circle/arc, point, and
  mixed profile interpretation; plain, clearance, tap-drill, cosmetic-thread,
  and modeled-thread geometry; none, counterbore, countersink, counterdrill,
  and live catalog head definitions; dimension and Through All depth; flat and
  angled drill points; straight/tapered and reversed controls; all current
  thread depth modes; left/right hand; thread fit/class; and signed custom
  modeled-thread clearance. A 675-line dispatcher-backed GUI gate proves
  invalid-schema and invalid-catalog no-ops, exact multi-Body targeting, native
  cutter and property postconditions, concise receipts, semantic undo/redo,
  stable operation/Body/input-state identity after FCStd save/reopen, and
  materially distinct modeled-thread geometry. It reports
  `VIBECAD_NATIVE_MODEL_HOLE_GUI_OK modeled_thread_seconds=2.025`; focused
  Native tests, all 39 Design-modeling tests, and the full Part Design
  VibeScript integration remain green.
- The focused Fillet slice uses the global `PartDesign::DesignFillet`
  operation and fixed Modify semantics. Its 53–148-line schema, bindings,
  runtime, native algorithm, and shared exact-target modules expose only the
  current task controls: exact Edge/Face groups across exact Bodies, the task's
  Use All Edges mode, and radius. The implementation preflights every Body
  state and topological element, calls the native Design operation target API,
  verifies exact target offsets/elements and output identities, and does not
  expose or create the retired Body-tip Base/BaseFeature path. A 424-line
  dispatcher-backed GUI gate proves invalid-schema and invalid-element no-ops,
  multi-Body edges, face-boundary filleting, all-sharp-edge filleting, concise
  receipts, semantic undo/redo, impossible-radius kernel rollback, and stable
  operation/Body/input identities after FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_FILLET_GUI_OK`; all 39 Design-modeling tests and the
  full Part Design VibeScript integration remain green.
- The focused Chamfer slice uses the global `PartDesign::DesignChamfer`
  operation with the same fixed Modify and exact-target contract. Its
  53–242-line schema, shared targeting, bindings, runtime, and native algorithm
  modules cover all current task controls: equal distance, two distances,
  distance and angle, flip direction where enabled, explicit Edge/Face groups
  across exact Bodies, and Use All Edges. The parser and verifier reject
  inactive-mode fields, non-finite/bool-as-number inputs, stale topology,
  mismatched target offsets/elements, invalid output solids, and any retired
  Base/BaseFeature link. A 561-line dispatcher-backed GUI gate proves invalid
  schema and invalid-element no-ops, atomic multi-Body modification, Face
  boundary selection, every definition mode, both flip states, Use All Edges,
  concise receipts, semantic undo/redo, impossible-size kernel rollback, and
  exact operation/Body/input/property identity after FCStd save/reopen. It
  reports `VIBECAD_NATIVE_MODEL_CHAMFER_GUI_OK`; 248 focused Native tests, all
  39 Design-modeling tests, 222 VibeScript surface/guardrail tests, the Fillet
  regression gate, and the full Part Design VibeScript integration remain
  green.
- The focused Draft slice uses the global `PartDesign::DesignDraft` operation
  and fixed Modify semantics. Its 96–342-line schema, shared face targeting,
  runtime, and native algorithm modules cover every current task control:
  exact Face groups across exact Bodies, draft angle, reverse pull direction,
  automatic neutral-plane/pull inference, datum/sketch object references, and
  exact planar Face or linear Edge references. User-visible Body references
  resolve through `resolveDesignDefinitionSubelementReference` to immutable
  pre-operation History states; global Design controllers are never treated as
  legacy shape features. Preflight rejects stale/nonplanar/nonlinear geometry,
  and verification freezes canonical reference identities and captured
  Component frames. A 649-line dispatcher-backed GUI gate proves invalid
  schema and invalid-face no-ops, atomic multi-Body drafting in different
  Component frames, automatic and reversed drafting, object and subelement
  reference modes, concise receipts, semantic undo/redo, invalid geometric
  relationship rollback, unchanged accepted geometry and frames after moving
  the source Component, and exact operation/Body/input/reference/property
  identity after FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_DRAFT_GUI_OK`; 255 focused Native tests, all 39
  Design-modeling tests, 222 VibeScript surface/guardrail tests, the Fillet and
  Chamfer regression gates, and the full Part Design VibeScript integration
  remain green.
- The focused Thickness slice uses the global `PartDesign::DesignThickness`
  operation with fixed Modify semantics and no inherited Body-tip Base or
  BaseFeature link. Its 117–251-line schema/runtime modules and focused
  173-line algorithm expose every current task control: exact Face groups
  across exact Bodies, positive thickness, inward/outward direction, Skin,
  Pipe, and RectoVerso modes, Arc/Intersection joins, and intersection
  handling. Preflight rejects stale or non-Face topology before a transaction;
  verification freezes exact target offsets/elements, control values, input
  state and Body identities, and valid one-solid outputs. A 493-line
  dispatcher-backed GUI gate proves invalid-schema and invalid-face no-ops,
  atomic two-Body shelling, both directions, all three modes, both joins, both
  intersection-handling states, concise receipts, semantic undo/redo,
  impossible-thickness kernel rollback, and exact operation/Body/input/control
  identity after FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_THICKNESS_GUI_OK`; all four dress-up regression gates,
  268 focused Native tests, all 39 Design-modeling tests, 224 VibeScript
  surface/engine guardrails, and the full Part Design VibeScript integration
  remain green. The complete registered Model schema set is 52,728 bytes,
  below the unchanged 64-KiB hard limit.
- The Design Mirror slice adds the first compact typed `model.transform`
  pattern definition instead of exposing one wide tool per transformation.
  Its 47–397-line schema/runtime/source/algorithm modules distinguish one
  exact Body copy from one earlier additive or subtractive Design feature
  applied to 1–16 exact target Bodies. Result semantics are derived by the
  Design kernel: Body mode publishes one independently identified Body in the
  source Component, while Feature mode preserves Join or Cut on the exact
  target Bodies. Mirror planes support bounded numeric origin/normal vectors,
  datum or sketch planes, sketch `N_Axis`, and exact planar Faces resolved to
  immutable pre-operation History state. Verification freezes source,
  target, input-state, output-Body, reference, occurrence, result, and captured
  Component-frame identity. A 652-line dispatcher-backed GUI gate proves
  invalid-schema and stale-face no-ops, numeric and every supported reference
  form, independent Body output bounds, multi-Body additive and subtractive
  Feature modes, a moved-Component reference frame, concise receipts,
  semantic undo/redo, disconnected-addition kernel rollback, and stable
  operation/Body/input/reference identities after FCStd save/reopen. It
  reports `VIBECAD_NATIVE_MODEL_DESIGN_MIRROR_GUI_OK`; all nine current Model
  lifecycle gates, 282 focused Native tests, all 39 Design-modeling tests, 224
  VibeScript surface/engine guardrails, and the full Part Design VibeScript
  integration remain green. The complete registered schema set is 55,608
  bytes, 9,928 bytes below the unchanged 64-KiB hard limit.
- The Design Linear Pattern slice extends the same typed `model.transform`
  pattern contract with positive bounded spacing, 2–10,000 total occurrences,
  centered ordering, and exact directions from a nonzero numeric vector, datum
  axis, sketch, built-in or construction sketch axis, or straight Edge.
  Direction references retain the immutable pre-operation History object and
  captured Component frame. Body mode publishes exactly `occurrences - 1`
  independently identified Bodies beside the source; Feature mode preserves
  the source operation's fixed Join or Cut semantics across 1–16 exact target
  Bodies. The 434-line algorithm verifies occurrence counts, source and target
  state, output identity, result mode, spacing, centering, reference and frame,
  and valid one-solid results. A 701-line dispatcher-backed GUI gate proves
  invalid-schema and stale-Edge no-ops, uncentered and centered Body ordering,
  sketch-object, `H_Axis`, and real construction `Axis0` directions, a straight
  Edge in a moved Component, multi-Body additive and subtractive Feature modes,
  concise receipts, semantic undo/redo, disconnected-addition kernel rollback,
  and stable operation/Body/input/reference identities after FCStd save/reopen.
  It reports `VIBECAD_NATIVE_MODEL_DESIGN_LINEAR_PATTERN_GUI_OK`; all ten Model
  lifecycle gates, 294 focused Native tests, all 39 Design-modeling tests, 237
  VibeScript surface/engine/timeline guardrails, and the full Part Design
  VibeScript integration remain green. The complete registered Model schema set
  is 56,975 bytes, 8,561 bytes below the unchanged 64-KiB hard limit.
- The Design Circular Pattern slice adds the last shipped standalone Design
  pattern to the same typed `model.transform` contract. It exposes exactly the
  current task controls: numeric or referenced axis, positive angular extent up
  to 360 degrees, 2–10,000 total occurrences, and reversal. Exact axes support
  datum axes, sketches, built-in and construction sketch axes, and straight or
  circular Edges while retaining the immutable pre-operation History object and
  captured Component frame. The kernel preserves its distinct distribution
  rule: a full circle excludes a duplicate 360-degree source occurrence, while
  a partial angle includes both endpoints. Body mode publishes exactly
  `occurrences - 1` independently identified Bodies; Feature mode preserves
  fixed Join or Cut semantics across 1–16 exact target Bodies. The 464-line
  algorithm verifies occurrence count, source and target state, output identity,
  result mode, axis/reference/frame, angle, reversal, and valid one-solid
  results. A 779-line dispatcher-backed GUI gate proves invalid-schema and
  stale-Edge no-ops, full-circle and partial/reversed ordering, sketch-object,
  `H_Axis`, and real construction `Axis0` references, a circular Edge in a moved
  Component, multi-Body additive and subtractive Feature modes, concise
  receipts, semantic undo/redo, disconnected-addition kernel rollback, and
  stable operation/Body/input/reference identities after FCStd save/reopen. It
  reports `VIBECAD_NATIVE_MODEL_DESIGN_CIRCULAR_PATTERN_GUI_OK`; all eleven
  Model lifecycle gates, 308 focused Native tests, all 39 Design-modeling tests,
  237 VibeScript surface/engine/timeline guardrails, and the full Part Design
  VibeScript integration remain green. The complete registered Model schema set
  is 58,640 bytes, 6,896 bytes below the unchanged 64-KiB hard limit.
- The proven live Model action graph contains Design Mirror, Design Linear
  Pattern, and Design Circular Pattern, but no Design Multi-transform action.
  Step 9.29 is therefore complete without registering or reviving the retired
  `PartDesign_MultiTransform` command; the action-manifest drift gates will make
  a future shipped addition fail closed until it receives an explicit contract.
- The standalone Part Primitive slice is based on the real creation-mode task
  panel rather than the legacy edit pages still present in `DlgPrimitives.ui`.
  Its eight live choices are Plane, Helix, Spiral, Circle, Ellipse, Point, Line,
  and Regular polygon; Body-owned Box/Cylinder/Cone/Sphere/Ellipsoid/Torus/
  Prism/Wedge creation remains solely on the Design primitive path. The focused
  `model.part` `primitive` variant has exact closed definitions, bounded
  placement, native angle/enumeration handling, and cross-parameter preflight.
  The 42–350-line schema/runtime/binding/algorithm modules publish one root
  `Part` object as a durable Design definition with stable definition and Design
  identities. The 493-line dispatcher-backed GUI gate opens the actual human
  dialog to freeze those eight choices, exercises every kind plus both helix
  hands, tapered and untapered curves, partial/full arcs, explicit transformed
  placement, schema and kernel no-ops, concise receipts, exact undo/redo,
  postcondition rollback, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_PRIMITIVES_GUI_OK`.
- The standalone Shape Builder slice extends the same focused `model.part`
  capability with one compact `builder` variant for the six modes proven from
  the live task panel: Edge from vertices, Wire from edges, Face from vertices,
  Face from edges, Shell from faces, and Solid from shell. The contract exposes
  exactly the creation controls that affect results: Planar for either Face
  path, All Faces and Refine for Shell, and Refine for Solid. It accepts bounded
  exact object/subelement groups, rejects wrong or stale current-History
  geometry before a transaction, copies shell input before solid construction,
  and leaves every source byte-for-byte unchanged. The 350-line algorithm
  prepares kernel geometry read-only, then publishes one static root
  `Part::Feature` as a durable Design definition in one guarded transaction.
  Verification proves the exact partnered shape, orientation, placement,
  topology, stable identities, and concise length/area/volume receipt. The
  624-line dispatcher-backed GUI gate freezes the six human modes and their
  control-enablement matrix, exercises planar and filled faces through both
  input paths, explicit and all-face shells, refined and unrefined solids,
  multi-object exact targets, schema/stale/kernel no-ops, source immutability,
  exact rollback, undo/redo, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_BUILDER_GUI_OK`.
- The standalone Part Extrude slice extends `model.part` with the complete live
  retained-dialog contract: one to 32 exact current-History sources; normal,
  custom-vector, or exact straight-edge direction; independent forward and
  reverse lengths and taper angles; symmetric and reversed direction; and
  solid or shell output. The 109–572-line runtime/schema/algorithm modules
  resolve current modeling state before mutation, use native
  `Part::Extrusion` properties, publish multiple outputs as one durable Design
  definition with owned History resources, preserve exact replacement inputs,
  and return one concise grouped receipt. The 677-line dispatcher-backed GUI
  gate freezes the real task-panel controls and enablement matrix and proves
  normal/custom/edge modes, zero-length edge-magnitude semantics, taper,
  reversal, symmetry, multi-source grouping, invalid/stale/nonplanar/curved
  no-ops, source immutability, forced rollback, exact undo/redo, and FCStd
  save/reopen. It reports `VIBECAD_NATIVE_MODEL_PART_EXTRUDE_GUI_OK`.
- The standalone Part Revolve slice extends `model.part` with every live
  retained-dialog control: one to 32 exact current-History sources; a custom
  center/direction or an exact whole-object/EdgeN line or circular reference;
  signed angle, symmetric angle, and solid/shell output. Shared current-History
  resolution is isolated in a 194-line Part helper while the Revolve algorithm
  remains 418 lines. It creates real `Part::Revolution` objects and preserves
  native circular-reference zero-angle inheritance, grouped Design/History
  publication, replacement inputs, source and axis immutability, and concise
  receipts. The 701-line dispatcher-backed GUI gate freezes the real task-panel
  labels, defaults, preselection, and control enablement and proves custom,
  exact line, exact circular-arc, whole-edge, negative, symmetric, solid/shell,
  and multi-source cases; schema/stale/solid/invalid-axis no-ops; forced
  rollback; exact undo/redo; and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_REVOLVE_GUI_OK`.
- The standalone Part Mirror slice extends `model.part` with the complete live
  retained-dialog contract: one to 32 exact current-History shape sources,
  including solids and solid-bearing compounds; XY, XZ, and YZ planes with an
  explicit base point; and one exact current plane-object, planar FaceN, or
  circular EdgeN reference, with the native whole-object single-face or
  single-edge inference rules. Its 247-line shared History resolver and
  462-line Mirror algorithm create real `Part::Mirroring` objects, preserve
  transformed source placement and copied Part visuals, publish multi-source
  results as one durable Design definition with owned History resources, keep
  exact replacement inputs and reference geometry immutable, and return one
  concise grouped receipt. The dispatcher-backed GUI gate freezes the actual
  task-panel labels, defaults, preselection, selector state, and reference
  behavior and proves all three fixed planes, a real `Part::Plane`, explicit
  and inferred planar/circular references, transformed solid, compound, wire,
  and multi-source outputs, repeated-recompute stability, schema/stale/
  nonplanar/noncircular/ambiguous no-ops, forced rollback, exact undo/redo, and
  FCStd save/reopen. It reports `VIBECAD_NATIVE_MODEL_PART_MIRROR_GUI_OK`.
- The Body-aware Design Scale slice extends the existing compact
  `model.transform` family with the exact live `PartDesign_Scale` contract:
  one to 16 explicit current-History Bodies, uniform or independent Design-axis
  factors from `1e-6` through `1e6`, and one fixed Design-space center. The
  119–305-line schema/runtime/algorithm modules resolve each Body through the
  authoritative modeling-state resolver, freeze the exact state, Body identity,
  shape, and Component frame before mutation, and create one global
  `PartDesign::DesignScale` with fixed Modify semantics. Verification proves
  unchanged input/output frames, one output per Body, exact previous-input and
  presence ports, valid single-solid results, a null controller Shape, and no
  duplicate `Part::Scale`. The 815-line dispatcher-backed GUI gate freezes the
  actual human task panel's Body preselection, defaults, factor bounds, and
  uniform/non-uniform enablement; proves atomic two-Body uniform scaling,
  independent-axis scaling, a moved-Component Design-frame case, concise exact
  receipts, immutable prior states, schema/type/empty/current-History no-ops,
  forced postcondition rollback, exact undo/redo, repeated recompute, and FCStd
  save/reopen. It reports `VIBECAD_NATIVE_MODEL_DESIGN_SCALE_GUI_OK`; all three
  existing Design pattern lifecycle gates remain green.
- The standalone Face From Wires slice extends `model.part` without inventing
  controls absent from the human command: one to 32 whole-object exact
  current-History sources, no existing faces, at least one wire per source,
  and every discovered wire closed. The 184–452-line runtime/schema modules
  route one compact `make_face` variant into a focused 221-line algorithm that
  creates a real parametric `Part::Face`, fixes `FaceMakerClass` to the live
  `Part::FaceMakerUnified` behavior, retains ordered exact `Sources`, publishes
  one root Design definition, hides only visible replaced presentations, and
  returns only root, source/topology counts, shape type, and area. The 596-line
  dispatcher-backed GUI gate freezes the actual immediate-command activation
  predicate and proves a single face, a face with a hole assembled from two
  sources, disjoint compound faces, transformed-placement output, exact source
  immutability, schema/stale/empty/open-wire/existing-face/current-History
  no-ops, forced postcondition rollback, repeated recompute, exact undo/redo,
  and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_MAKE_FACE_GUI_OK`.
- The standalone Ruled Surface slice extends `model.part` with the exact
  source-preserving `Part_RuledSurface` command contract: exactly two ordered
  current-History curves, each either a whole Edge/Wire object or one exact
  EdgeN/WireN subelement, including two distinct subelements of one owner. No
  orientation control is invented because the human command fixes the real
  `Part::RuledSurface` feature to `Automatic`. The 204-line algorithm and
  209–496-line runtime/schema modules freeze both transformed curves before
  mutation, retain exact `Curve1`/`Curve2` links, publish one root Design
  definition, deliberately leave both sources visible, and reject replacement
  metadata. The 636-line dispatcher-backed GUI gate freezes the actual human
  selection predicate and proves same-object subedges, two whole edges, closed
  wires, transformed placements, exact kernel-equivalent geometry, schema/
  stale/face/compound/current-History no-ops, source immutability, forced
  rollback, repeated recompute, exact undo/redo, and FCStd save/reopen. It
  reports `VIBECAD_NATIVE_MODEL_PART_RULED_SURFACE_GUI_OK`.
- The standalone Part Loft slice extends `model.part` with the retained human
  task's exact contract: two to 32 ordered current-History profiles, each a
  whole Vertex/Edge/Wire/Face object or one exact VertexN/EdgeN/WireN/FaceN
  subelement, including multiple ordered subprofiles from one owner. The only
  provider controls are the live Solid, Ruled, and Closed checkboxes;
  `MaxDegree=5` and `Linearize=false` remain fixed implementation behavior.
  The focused 303-line algorithm creates one real root-level `Part::Loft`,
  retains exact `Sections` and grouped `ProfileLinks`, publishes one Design
  definition, records and hides only visible replaced presentations, and
  returns a concise geometry receipt. The 753-line dispatcher-backed GUI gate
  freezes human preselection order, labels, and defaults and proves solid,
  ruled, same-owner exact-subelement, transformed-placement, and meaningful
  closed-loop output; schema/stale/invalid-shape/current-History no-ops; source
  immutability; forced rollback; exact undo/redo; repeated recompute; and FCStd
  save/reopen. The Loft/Sweep view provider now claims each source once in
  stable order, eliminating duplicate tree children without changing stored
  links. The gate reports `VIBECAD_NATIVE_MODEL_PART_LOFT_GUI_OK`.
- The standalone Part Sweep slice extends `model.part` with the retained human
  task's exact contract: one to 32 ordered current-History profiles, each a
  whole Vertex/Edge/Wire/Face object or one exact VertexN/EdgeN/WireN/FaceN
  subelement, plus one whole Edge/Wire/connected edge-or-wire compound path or
  one to 64 ordered exact EdgeN path subelements. The provider exposes only the
  live Create solid and Frenet checkboxes; `Transition="Right corner"` and
  `Linearize=false` remain fixed task behavior. The focused 400-line algorithm
  creates one real root-level `Part::Sweep`, retains exact `Sections`, grouped
  `ProfileLinks`, and `Spine`, publishes one Design definition, records and
  hides only visible replaced presentations, and returns a concise topology
  receipt. Exact current-History snapshots now retain an exact BREP digest in
  addition to OCC partner identity, so an unchanged transformed wrapper that
  OCC reconstructs at transaction start remains valid while any geometry,
  placement, orientation, object, or subelement change is still rejected. The
  875-line dispatcher-backed GUI gate freezes human preselection, labels, and
  defaults and proves solid, non-solid, same-owner multisection, exact
  multi-edge path, transformed-placement, and whole-compound-path output;
  schema/stale/invalid/disconnected/current-History no-ops; source immutability;
  forced rollback; exact undo/redo; repeated recompute; unique stable tree
  children; and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_SWEEP_GUI_OK`; the Loft, Ruled Surface, and Part
  Mirror lifecycle gates remain green after the shared exact-target change.
- The standalone Part Section slice introduces the focused `model.boolean`
  family instead of continuing to grow `model.part`. Its only current variant
  accepts exactly two ordered whole-object current-History operands, matching
  the immediate human command's Base/Tool order and deliberately exposing no
  subelement, approximation, or refine controls. The 202-line algorithm
  creates one real root-level `Part::Section`, fixes `Approximation=false`,
  preserves the native object's current user-level Refine default, copies the
  first operand's line material, retains exact Base/Tool links, records and
  hides visible replaced presentations, and returns only result topology,
  length, and actual refine state. Its schema, runtime, and binding remain
  separate 44–62-line modules. The 611-line dispatcher-backed GUI gate freezes
  the actual immediate-command activation and display contract and proves
  overlapping solids, solid/plane curves, transformed placements, compound
  operands, and the human command's valid empty disjoint result; schema/stale/
  null-shape/current-History no-ops; exact preflight change rejection; source
  BREP immutability; forced rollback; exact undo/redo; repeated recompute; and
  FCStd save/reopen. It reports `VIBECAD_NATIVE_MODEL_PART_SECTION_GUI_OK`.
- The standalone Part Cross Sections slice extends the focused `model.part`
  family with the retained `Part::CrossSections` feature instead of a
  destructive shape copy or an AI-only slicing approximation. It accepts 1–32
  unique current-History source owners, preserving either each whole source or
  1–64 ordered exact Vertex/Edge/Wire/Face/Shell/Solid/CompSolid/Compound
  subelements. Its closed distribution contract exposes every live human
  control: XY/XZ/YZ plane, signed position, single or repeated sections,
  nonnegative spacing, count, and both-sides centering. Provider-created series
  are deliberately capped at 10,000 planes, consistent with Native pattern
  occurrence limits, while the exact human plane-position formula is retained.
  The 350-line algorithm creates one linked `Part::CrossSections` per selected
  owner, validates every result before publication, publishes all outputs as
  one Design block, preserves source geometry and visibility, and returns only
  grouped topology and length facts. Its 702-line dispatcher-backed GUI gate
  freezes the real task panel's activation, defaults, controls, and accepted
  output, then proves whole sources, exact compound subelements, multi-source
  batches, centered series, transformed placement, schema/stale/invalid/
  no-intersection/current-History no-ops, preflight change rejection, forced
  rollback, source BREP immutability, exact undo/redo, repeated recompute, and
  FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_CROSS_SECTIONS_GUI_OK`.
- The standalone Part 3D Offset slice adds one retained `Part::Offset` through
  `model.part`. Its whole-object current-History target and closed definition
  expose every final-geometry task control: signed distance, Skin/Pipe/
  RectoVerso mode, Arc/Tangent/Intersection join, intersection handling,
  self-intersection handling, and fill. The preview-only Update View checkbox
  remains human task-panel behavior rather than a meaningless provider field.
  The 241-line algorithm preserves the exact Source link, copies the human
  command's shape/line/point presentation, records and hides only a visible
  replaced presentation, publishes one root Design definition, and returns a
  concise topology/area/volume receipt. The 656-line dispatcher-backed GUI
  gate freezes actual activation, labels, choices, defaults, source hiding,
  and accepted output; proves every mode and boolean control, Arc and
  Intersection success, the OCC kernel's explicit Tangent rejection, filled
  face output, negative distance, transformed placement, schema/stale/null/
  current-History no-ops, exact preflight change rejection, forced rollback,
  source geometry and placement preservation, exact undo/redo, repeated
  recompute, presentation transfer, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_OFFSET_GUI_OK`.
- The standalone Part 2D Offset slice reuses the same focused retained-offset
  lifecycle while keeping a narrower truthful `Part::Offset2D` contract. It
  accepts one exact current-History whole shape only when transformed geometry
  is planar and contains no solid, exposes signed distance, Skin/Pipe, all
  three live join choices, intersection handling, and fill, and omits the
  hidden self-intersection and unsupported RectoVerso controls. The shared
  offset implementation remains 325 lines. Its 577-line dispatcher-backed GUI
  gate freezes the actual command eligibility, Pipe default, two visible
  modes, three joins, hidden self-intersection control, preview default,
  source hiding, and accepted result; proves Skin and Pipe, Arc/Tangent/
  Intersection, both intersection and fill states, negative distance, open and
  closed wires, faces, transformed planar placement, stale/null/solid/
  nonplanar/current-History no-ops, exact preflight change rejection, forced
  rollback, source geometry and placement preservation, exact undo/redo,
  repeated recompute, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_OFFSET_2D_GUI_OK`.
- The Projection on Surface slice adds one retained
  `Part::ProjectOnSurface` through `model.part`. Its closed contract accepts
  exactly one current-History target face, 1–64 distinct exact Edge/Wire/Face
  sources, All/Faces/Edges output mode, bounded nonnegative extrusion height,
  signed solid-depth offset, and one explicit bounded nonzero direction. The
  runtime normalizes that direction rather than exposing the human-only camera
  and axis-button controls. The 291-line algorithm retains exact SupportFace
  and ordered Projection links, preserves all source geometry, placement, and
  visibility, publishes one root Design definition without claiming replaced
  inputs, and returns only source count plus topology/area/volume facts. Its
  757-line dispatcher-backed GUI gate freezes the real command's blank
  provisional feature, role buttons, modes, ranges, defaults, direction
  controls, and cancel cleanup; proves All/Faces/Edges, extrusion, positive and
  negative offsets, normalized direction, ordered multiple sources,
  transformed placements, schema/stale/null/invalid-element/zero-direction/
  no-projection/current-History no-ops, exact preflight change rejection,
  forced rollback, source-preserving visibility, exact undo/redo, repeated
  recompute, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_PROJECTION_GUI_OK`.
- The standalone Part Compound slice maps `Part_Compound` to one retained
  root-level `Part::Compound`. Its closed contract accepts 1–64 ordered,
  distinct current-History whole shapes and exposes no subelement, refinement,
  or synthetic merge controls. The 197-line algorithm retains the exact Links
  list, resolves transformed current geometry, records only presentations that
  were visible as replaced inputs, preserves already-hidden inputs, validates
  one real Compound output, and returns only source count plus topology/area/
  volume facts. Its 606-line dispatcher-backed GUI gate freezes the actual
  selection-dependent immediate command, ordered Links, tree children, input
  hiding, and one-step undo; proves one and multiple sources, ordered mixed
  Vertex/Edge/Face/Solid inputs, transformed and nested Compound inputs,
  visible and hidden presentations, schema/duplicate/stale/null/current-
  History no-ops, exact preflight change rejection, forced rollback, source
  geometry and placement preservation, exact undo/redo, repeated recompute,
  and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_COMPOUND_GUI_OK`.
- The Compound separation slice follows the shipped ribbon action
  `PartDesign_Separate`, not the obsolete workbench-only explode command. Its
  closed `model.structure` variant accepts one exact active reusable
  Design-root multi-solid definition, one explicit optional destination
  Component, and the visible operation label; it exposes no subelement,
  refinement, selection, or raw-command escape hatch. The 481-line algorithm
  rejects Bodies, Links, grouped features, Design operations, single-solid
  definitions, inactive History, missing targets, and non-Components before
  mutation. It finalizes an uncommitted reusable source exactly as the human
  command does, creates one retained `PartDesign::DesignSeparate`, preserves
  stable Body IDs and region witnesses, copies physical material and the full
  shape/line/point/transparency/display presentation to every output, records
  and hides the replaced source, and returns only the operation, source,
  destination when present, Body references, count, and volumes. Its 663-line
  dispatcher-backed GUI gate freezes the real immediate command and its edit
  summary/output controls; proves root and Component-owned output, transformed
  mixed solids, local output frames versus Design-space preview geometry,
  material and appearance preservation, schema/missing/single-solid/Body/
  Design-operation/non-Component/group/Link/inactive-History no-ops, exact
  preflight change rejection, forced postcondition rollback including source
  publication and visibility, stable IDs/witnesses across recompute and exact
  undo/redo, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_DESIGN_SEPARATE_GUI_OK`. Its exact global-geometry
  postcondition compares mass properties and full boolean-intersection volume,
  not OCC's representation-dependent curved bounding boxes. No Native mapping
  or runtime path for legacy `Part_ExplodeCompound` was added.
- The Compound Filter slice maps the shipped `Part_CompoundFilter` action to a
  retained root-level `Part::FeaturePython` using the real
  `CompoundTools.CompoundFilter` proxy. Its compact closed `model.part`
  contract accepts one exact current-History Compound or CompSolid and exposes
  seven typed modes: bypass, specific-item selectors, collision, and volume,
  area, length, or distance windows. Specific items use bounded integer or
  two-/three-field slice selectors rather than raw filter grammar; collision
  and distance require an exact stencil; window modes use bounded percentages,
  an optional positive maximum override, and explicit inversion. The 568-line
  implementation validates the mode-specific field set before document
  preflight, evaluates the exact `Base.Shape` and optional `Stencil.Shape` used
  by the retained proxy, bounds synchronous work to 4,096 direct children,
  rejects empty results before mutation, preserves the durable native filter
  controls, records and hides only visible replaced presentations, publishes
  one root Design definition, and returns only mode, child counts, and useful
  topology/area/volume facts. Its 792-line dispatcher-backed GUI gate freezes
  the real command's selection rules, one-selection volume default,
  two-selection collision default, immediate creation, retained controls, and
  tree children; proves all seven modes, typed index/slice selection and
  inversion, optional and required stencils, transformed sources, mixed child
  topology, maximum overrides, hidden-presentation preservation, schema/
  missing/non-Compound/out-of-range/no-output/current-History no-ops, exact
  preflight change rejection, forced rollback, exact undo/redo, repeated
  recompute, and FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_COMPOUND_FILTER_GUI_OK`.
- The Design Combine slice maps the single shipped `PartDesign_Combine` action
  to one compact `model.boolean` variant covering each human task mode: Join,
  Cut, and Intersect. Its closed contract accepts one exact result Body, 1–15
  ordered distinct tool Bodies, explicit tool-preservation intent, and the
  visible operation label; it exposes no generic Boolean command, selection,
  refinement, fuzzy-tolerance, or raw-dispatch controls. The 381-line retained
  implementation requires one active current solid state and distinct
  persistent identity per Body, freezes each exact state and global frame,
  calls the native `PartDesign.setDesignCombineBodies` edit API, preserves the
  operation's human defaults, publishes the exact result/absence ports, and
  returns only the mode, tool-preservation state, and useful Body presence and
  volume facts. Its 839-line dispatcher-backed GUI gate freezes the actual
  first-selected result role, ordered tool roles, Join default, Join/Cut/
  Intersect selector, Keep tool Bodies control, preview, and cancel behavior;
  proves consuming Join, preserving three-Body Join, Cut, preserving
  Intersect, cross-Component frames, schema/missing/wrong-type/empty/inactive-
  History/disjoint-geometry no-ops, exact preflight change rejection, forced
  postcondition rollback, exact one-step undo/redo, repeated recompute, and
  durable ports, shapes, Body identities, absence states, and frames across
  FCStd save/reopen. It also verifies that `PreviewShape` follows its declared
  transient lifecycle rather than treating it as saved model state. It reports
  `VIBECAD_NATIVE_MODEL_DESIGN_COMBINE_GUI_OK`.
- The authoritative 75-action Model inventory contains no Boolean Fragments,
  XOR, standalone Fuse, or standalone Common leaf action. The only shipped
  general Boolean leaf is `PartDesign_Combine`, whose Join, Cut, and Intersect
  modes are covered above. Conditional row 9.51 therefore requires no extra
  provider operation or compatibility alias.
- The Part Join slice maps the three shipped leaves `Part_JoinConnect`,
  `Part_JoinEmbed`, and `Part_JoinCutout` to three exact operations in one
  focused `model.join` family; the composite `Part_CompJoinFeatures` remains a
  human menu and is never advertised as a provider operation. Connect accepts
  1–32 ordered distinct exact current-History whole shapes, allowing one source
  only when it is a Compound with at least two direct children; preflight
  expands and bounds nested compounds to 256 non-Compound leaves, rejects
  vertices and mixed dimensions, and uses the exact linked `Shape` consumed by
  the retained proxy. Embed and Cutout require an ordered exact base and tool.
  All three expose only their durable Refine and bounded Tolerance controls and
  visible label. The 361-line implementation uses the real
  `BOPTools.JoinFeatures` factories and proxies, preserves ordered global
  links and view-provider tree children, records and hides only visible input
  presentations, publishes one root Design operation, validates exact inputs
  again before mutation and commit, and returns concise operation, topology,
  area, and volume facts. Its 792-line dispatcher-backed GUI gate freezes the
  actual immediate human commands, default controls, ordered roles, automatic
  labels, multi-source and single-Compound Connect paths, and tree children;
  proves refined and tolerance-aware Connect, Embed and Cutout, transformed
  sources, initially hidden input preservation, schema/duplicate/missing/null/
  single-source/mixed-dimension/inactive-History no-ops, exact preflight change
  rejection, forced postcondition rollback, exact undo/redo, repeated
  recompute, and durable proxy types, links, identities, replacement metadata,
  visibility, and shapes across FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_PART_JOIN_GUI_OK`. OCC can vary a repeated general-fuse
  cylinder bound by 0.0024 mm with identical topology, area, and volume, so the
  gate uses the existing 0.005-mm geometric comparison tolerance while keeping
  identity, controls, topology, and roles exact.
- The Split slice maps only the shipped `PartDesign_Split` leaf to the `split`
  variant of the compact `model.boolean` family. Legacy `Part_Slice`,
  `Part_SliceApart`, Boolean Fragments, and XOR paths are absent from the live
  Model manifest and received no aliases or compatibility runtime. The closed
  contract accepts one exact active source Body, 1–32 ordered distinct exact
  Body or reusable Part definitions, 0–64 exact Face/Shell/Solid subelements
  per definition with 256 total, one explicit retained-region index, and the
  visible label. The 687-line implementation freezes the source state, stable
  Body and Component identities, frames, exact definition shapes, and selected
  subelements before mutation; calls the native
  `PartDesign.setDesignSplitDefinition` and
  `PartDesign.assignDesignSplitRegions` edit APIs; preserves the human-only
  Refine and FuzzyTolerance defaults; requires 2–256 valid solid regions; keeps
  the selected region on the source Body identity; gives every other region a
  stable new Body identity and strict interior witness; and verifies exact
  input/output ports, frames, predecessor state, presence, volume partition,
  and Design validity. Its concise result contains only the operation, source,
  splitter count, retained index, and useful per-region Body/witness/volume
  facts. The 900-line dispatcher-backed real GUI gate freezes the actual source
  selector, definition add/remove list, retained-region selector, accept, and
  cancel behavior; proves both retained sides, three-way splitting, exact
  subelements, Body-backed solid definitions, transformed frames, schema/type/
  empty/self/inactive-History/non-dividing/out-of-range no-ops, stale preflight
  rejection, forced verifier rollback, one-step undo/redo, recompute, and exact
  identities, ports, witnesses, and shapes across FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_DESIGN_SPLIT_GUI_OK`.
- The Defeaturing slice maps only the shipped immediate
  `Part_Defeaturing` action to the `defeature` variant of the compact
  `model.part` family. Its closed contract accepts 1–32 distinct exact current-
  History whole-shape sources, 1–64 distinct exact `FaceN` selections per
  source, 256 faces total, and the visible label. The 374-line implementation
  groups faces by source, freezes and heals every exact global source shape
  before mutation, creates one root-level `Part::Feature` per source in one
  transaction, publishes the human-equivalent resource/root timeline block,
  and hides only presentations that were visible. Durable replacement metadata
  and receipts retain exact History states rather than mutable Body
  presentations, while the Body remains the presentation hidden from the
  human. Creation and commit both prove unchanged sources, exact preflight
  output identity, valid healed solids, labels, timeline ownership, Design
  identity, replacement metadata, and presentation visibility. The concise
  result reports only root identity, source/result/resource and removed-face
  counts, output shape types, topology, area, and volume. Its 772-line
  dispatcher-backed real GUI gate freezes actual human multi-source selection,
  automatic labels, publication, visibility, and one-step undo; proves exact
  Native single/multi-face and multi-source execution, initially hidden input
  preservation, transformed and Body-backed sources, schema/missing/invalid/
  duplicate/inactive-History no-ops, stale-preflight rejection, forced verifier
  rollback, exact undo/redo, recompute, and durable identities, roles,
  ownership, shapes, replacement states, and Design IDs across FCStd
  save/reopen. It reports `VIBECAD_NATIVE_MODEL_PART_DEFEATURE_GUI_OK`.
- The Surface Filling slice maps only the shipped `Surface_Filling` action to
  the `filling` variant of the new focused `model.surface` family. Its compact,
  closed contract accepts one ordered array of 1–256 exact current-History
  constraints with explicit boundary-edge, non-boundary curve, free-face, and
  point kinds; exact optional adjacent support faces and C0/G1/G2 continuity;
  one optional exact initial face; the visible label; and every bounded native
  solver control. Omitted controls use the exact human defaults: degree 3, 15
  points per curve, two iterations, isotropy, 1e-5/1e-4 2D/3D tolerances, 0.01
  angular tolerance, 0.1 curvature tolerance, maximum degree 8, and nine
  segments. The 564-line implementation validates each kind's exact field set,
  subelement type, support-face adjacency, distinct resolved History state,
  degree relationship, and one connected closed boundary before mutation;
  creates the retained native `Surface::Filling`; preserves ordered exact
  links and source presentation visibility; publishes one root Design
  definition; and verifies every link, continuity, control, output Face,
  timeline role, Design identity, and unchanged input again before commit. Its
  concise result reports root identity, constraint counts, initial-face state,
  core degree/segmentation controls, and useful surface area/topology facts.
  The 886-line dispatcher-backed real GUI gate exercises actual human create,
  automatic add-edge mode, adjacent Face1/G1 editing, accept, cancel, defaults,
  and undo; proves Native defaults and every solver control, every constraint
  kind, support/initial faces, transformed and Body-backed exact History
  sources, initially hidden input preservation, malformed/missing/open/
  nonadjacent/duplicate/inactive-History no-ops, stale-preflight rejection,
  forced verifier rollback, exact undo/redo, recompute, and durable links,
  controls, identities, shapes, and visibility across FCStd save/reopen. It
  reports `VIBECAD_NATIVE_MODEL_SURFACE_FILLING_GUI_OK`.
- The Geometric Fill Surface slice maps only the shipped
  `Surface_GeomFillSurface` action to the `geometric_fill` variant of the
  focused `model.surface` family. Its compact, closed contract accepts two to
  four ordered, distinct exact current-History Part edges; one explicit
  reversal flag per edge; the visible label; and the native Stretched, Coons,
  or Curved filling style. Omitted style uses the exact human Stretched
  default. The 249-line implementation resolves and freezes every exact edge
  before mutation, rejects duplicate resolved History states, creates the
  retained native `Surface::GeomFillSurface`, preserves the exact ordered
  `BoundaryList`, `ReversedList`, native `FillType`, and all source
  presentations, publishes one root Design definition, and verifies the links,
  controls, output Face, timeline role, Design identity, and unchanged inputs
  again before commit. Its concise result reports only root identity, boundary,
  style, and reversal counts, output edge count, and surface area. The audit
  also found and corrected a shipped human-command invariant defect: a new
  feature's orientation list contained one phantom default flag, so edge
  reversal was ignored whenever the list and boundary counts differed. New
  features now start with an empty orientation list, and the Surface command
  regression proves exactly one persisted flag per selected edge. The 715-line
  dispatcher-backed real GUI gate exercises actual human automatic add-edge
  mode, all four selections, double-click reversal, style selection, accept,
  cancel, and undo; proves Native two-, three-, and four-edge construction, all
  three styles, explicit reversals, transformed and Body-backed exact History
  sources, initially hidden input preservation, schema/missing/non-edge/
  duplicate/inactive-History no-ops, stale-preflight rejection, forced verifier
  rollback, exact undo/redo, repeated recompute, and durable links, controls,
  identities, shapes, and visibility across FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_SURFACE_GEOMETRIC_FILL_GUI_OK`.
- The Surface Sections slice maps only the shipped `Surface_Sections` action
  to the `sections` variant of the focused `model.surface` family. Its compact,
  closed contract accepts two to 256 ordered, distinct exact current-History
  Part edges and the visible label; it exposes no Part Loft options because the
  retained human feature is specifically `Surface::Sections`. The 213-line
  implementation validates and freezes every exact edge before mutation,
  rejects duplicate resolved History states, creates the retained native
  feature with its exact ordered `NSections`, preserves all source
  presentations, publishes one root Design definition, and verifies every
  link, output Face, timeline role, Design identity, and unchanged input again
  before commit. Its concise result reports only root identity, section and
  output-edge counts, and surface area. The 641-line dispatcher-backed real GUI
  gate exercises the actual human automatic add-edge mode, edge removal,
  re-addition, drag-order model update, accept, cancel, and undo; proves Native
  two- and four-section construction, reverse ordering, transformed and
  Body-backed exact History sources, initially hidden input preservation,
  schema/missing/non-edge/duplicate/inactive-History no-ops, stale-preflight
  rejection, forced verifier rollback, exact undo/redo, repeated recompute, and
  durable links, identities, shapes, and visibility across FCStd save/reopen.
  It reports `VIBECAD_NATIVE_MODEL_SURFACE_SECTIONS_GUI_OK`.
- The Extend Face slice maps only the shipped `Surface_ExtendFace` action to
  the `extend` variant of the focused `model.surface` family. Its compact,
  closed contract accepts one exact current-History Part face, the visible
  label, independent U/V negative and positive parametric extensions, explicit
  symmetry flags, tolerance, and a bounded 2–512 sample grid. Omitted controls
  reproduce the human feature defaults exactly: 0.05 extension on all sides,
  both axes symmetric, 0.1 tolerance, and a 32-by-32 grid. The runtime requires
  equal paired values when an axis is symmetric. The 294-line implementation
  freezes the exact face before mutation, creates the retained native
  `Surface::Extend`, assigns controls without transient symmetry coupling,
  preserves the source presentation, publishes one root Design definition,
  and verifies the exact `Face` link, every durable control, output Face,
  timeline role, Design identity, and unchanged source again before commit.
  Its concise result reports root and source identities, face, U/V extension
  triples, sample grid, tolerance, and surface area. The audit also found and
  corrected a shipped persistence defect: XML restoration replayed asymmetric
  U/V values while default symmetry was still active, overwriting the first
  restored side. Symmetry coupling now remains active for live human edits but
  never rewrites serialized values during restore, and the shipped Surface
  persistence test proves an asymmetric range survives reopen exactly. The
  640-line dispatcher-backed real GUI gate exercises the immediate human exact
  one-face selection, native defaults, one-step undo/redo, and visibility;
  proves Native default and fully controlled asymmetric construction,
  transformed and Body-backed exact History sources, initially hidden input
  preservation, schema/missing/non-face/unequal-symmetry/inactive-History
  no-ops, stale-preflight rejection, forced verifier rollback, repeated
  recompute, exact undo/redo, and durable links, controls, identities, shapes,
  and visibility across FCStd save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_SURFACE_EXTEND_GUI_OK`.
- The Curve on Mesh slice maps only the shipped `Surface_CurveOnMesh` action
  to the `curve_on_mesh` variant of `model.surface`. Its compact closed
  contract takes one exact current-History `Mesh::Feature` and 2–64 ordered
  world-space pick rays, plus open/closed state, polyline or B-spline output,
  degree 1–8, C0–C3 continuity, fitting tolerance, and split angle. Omitted
  controls reproduce the effective human defaults exactly: open, approximated,
  degree 5, C2, 0.2 mm tolerance, and 45 degrees. The task panel's snap-pixel
  field is deliberately absent because the shipped handler does not consume
  it. The 441-line implementation resolves each forward ray to a source facet
  and barycentric weights, transforms each connection direction into source
  coordinates, fingerprints complete world-space mesh topology and segment
  membership, and creates the same recomputable `MeshPart::CurveOnMesh` object
  as the human task. It publishes one source-preserving History operation,
  retains every exact anchor and control, leaves source visibility unchanged,
  and verifies the complete native property contract and valid curve before
  commit. Its concise result reports root/source identities, anchor count,
  curve mode and controls, edge count, and length. Model state now includes a
  bounded mesh inventory with counts, visibility, and bounds so the provider
  can identify valid sources without changing ribbon or workbench. The
  829-line dispatcher-backed GUI gate drives the actual human task through
  viewport picks and its Create context action, measures only the resulting
  viewport quantization for parity, and proves Native open/default, fully
  controlled, closed, polyline, approximated, and hidden-source cases. It also
  proves missing/wrong/empty/future-History targets, misses, backward rays,
  duplicate anchors, complete-topology stale-preflight rejection, forced
  verifier rollback, exact undo/redo, repeated recompute, and durable source,
  anchor, direction, control, History, shape, and visibility state across FCStd
  save/reopen. It reports
  `VIBECAD_NATIVE_MODEL_SURFACE_CURVE_ON_MESH_GUI_OK`.
- The Blend Curve slice maps only the shipped `Surface_BlendCurve` action to
  the immediate `blend_curve` variant of `model.surface`. Its compact closed
  contract takes two distinct exact current-History `EdgeN` references. Each
  endpoint independently carries a relative parameter from 0 to 1, C0 or
  G1–G4 continuity, and derivative size from -100 to 100; omitted controls
  reproduce the human feature defaults exactly at both ends: parameter 0,
  G2, and size 1. The 305-line implementation freezes both exact BREP edges
  before mutation, rejects a Body whose current tip changes after preflight,
  creates the same retained `Surface::FeatureBlendCurve` as the human command,
  and verifies both links, all six controls, the expected Bezier degree,
  valid output edge, unchanged sources, History role, and Design identity
  before commit. Its concise result reports the root, two exact endpoint
  contracts, degree, and length. The dispatcher-backed real GUI gate drives
  the actual two-edge human command and its actual edit task controls, then
  proves identical Native controlled geometry. It also covers defaults,
  C0–G4 combinations, negative derivative sizing, relative endpoint
  placement, transformed, Body-backed, and initially hidden sources,
  malformed/missing/non-edge/duplicate/future-History no-ops, complete BREP
  and Body-tip stale-preflight rejection, forced-verifier rollback,
  suppression, repeated recompute, exact undo/redo, and durable links,
  controls, identities, shapes, and visibility across FCStd save/reopen. It
  reports `VIBECAD_NATIVE_MODEL_SURFACE_BLEND_CURVE_GUI_OK`.
- The assistant-local undo ledger now proves the exact host undo-name stack at
  every checkpoint. It accepts normal stack growth or the exact oldest-entry
  eviction transition at FreeCAD's configured history limit, keeps only a
  contiguous assistant-owned sequence across structural revisions, and checks
  the exact expected prior stack after undo. Focused tests cover bounded
  history and intervening human transactions, and the full Split gate proves a
  valid assistant undo remains available after the default 20-entry limit is
  reached.
- The shared Design schema omits redundant `minLength` keywords where an
  existing anchored nonempty regex already enforces the same accepted
  language. The shared label provider schema retains its 160-character bound;
  every mutation runtime still rejects a blank label synchronously before
  document preflight or transaction. Exact patterns, closed objects, and all
  runtime validation remain in force. The fixed 64-KiB ceiling was not raised.
- The Design Extrude provider contract now represents its live one-sided,
  symmetric, and two-sided layouts as one ordered `sides` array. One-sided and
  symmetric operations require exactly one entry; two-sided operations require
  exactly two, with the runtime enforcing that kind-dependent count before
  document preflight. This removes three serialized copies of the same closed
  termination grammar, makes side order explicit, and reduced the complete
  Model schema by 1,970 bytes. Focused contracts and the full real Design
  profile lifecycle gate remain green; no compatibility-only Native path was
  retained.
- The existing Design primitive, reusable-profile, and standalone Part
  primitive provider contracts are
  compact closed field unions with explicit per-kind field maps instead of
  repeated schema branches. Variant prose remains in the internal capability
  registry while each provider tool lists its operation names once instead of
  repeating prose inside every branch. The visible call shapes and bounded
  fields are unchanged; the runtime rejects missing or unrelated primitive,
  Extrude-direction, Revolve-extent, Sweep-orientation, and Helix-mode fields
  before any document preflight or transaction. Both full Design lifecycle
  gates remain green, and the hard schema ceiling was not raised.
- Concise primitive/profile field descriptions removed another 900 serialized
  bytes without changing any schema field, bound, accepted value, runtime
  validation, or result contract. The hard schema ceiling remains unchanged.
- Schema canonicalization emits JSON integers for integral numeric bounds while
  retaining real numbers for non-integral bounds. This preserves JSON Schema
  number semantics and every accepted value while removing 614 redundant
  serialized decimal suffix bytes; a focused contract test proves the exact
  normalization. The Combine contract and shorter Boolean/Compound Filter
  descriptions make no runtime or result-contract changes.
- Multi-operation capability families now serialize as one closed object with
  an ordered operation enum, a concise exact top-level field map, and the
  deduplicated bounded union of their typed field schemas. A single-operation
  request retains its fully discriminated branch. The runtime continues to
  require the exact field set for the selected operation before preflight, so
  the compact provider form adds no permissive execution path. This reduced
  the then-complete registered Model schema from 65,519 to 56,742 bytes without
  changing any operation name, argument field, bound, target identity, result,
  or hard ceiling. Focused tests prove closed objects, common-required fields,
  conflicting typed field unions, field-map descriptions, and operation order.
  Real common, structure, Design primitive, reusable-profile, dress-up,
  transform, standalone Part, and Boolean GUI gates all pass through the
  compact multi-operation schemas.
- The Part Design VibeScript provider-source lifecycle fixture now models the
  production document-thread boundary instead of calling `Document` mutation
  directly from its background operation. A bounded deterministic test
  dispatcher queues publication back to the main test thread while preserving
  asynchronous create/edit/delete behavior. The full integration again emits
  structured `"ok": true`, including failed-source recovery, same-source edit,
  live publication, and deletion. All 39 current Model lifecycle gates, 761
  focused Native/authoring/ribbon tests and 356 broad VibeScript-facing
  surface/engine/timeline/portability tests are green. The complete Part Design
  VibeScript integration emits structured `"ok": true`, including failed-source
  recovery, same-source edit, and deletion. The complete registered Model
  schema set is 65,204 bytes, 332 bytes below the unchanged 64-KiB hard limit.
  The complete Surface VibeScript native API integration also passes every
  explicit operation and its create/edit/save/reopen/delete lifecycle.
- Model standard-fastener insertion now uses a bounded `model.catalog`
  discovery tool and an exact-target `model.fastener` mutation tool over one
  shared human/Native fastener graph. The graph invokes the pinned 225-standard
  FreeCAD Fasteners generator at revision
  `033225ae84d65cfde0a39c2750dfa8e549a10cab`, creates a global
  `DesignGeneratedOperation` with stable source/result ownership, and performs
  targeted recompute without changing the existing human call default. The
  production verifier checks exact document identities and kernel geometry,
  while the concise receipt omits catalog and topology noise. Its GUI gate
  proves plain and modeled thread insertion, invalid-input no-ops, rollback,
  undo/redo, repeated recompute, and save/reopen; it emits
  `VIBECAD_NATIVE_MODEL_FASTENER_GUI_OK`. The exhaustive catalog gate covers
  all 225 standards, 3,580 nominal sizes, 5,355 resolved boundary/canonical
  keys, and 12 modeled-thread families; all 21 human Fasteners GUI tests pass.
  A new curved one-solid compound regression also found and fixed a core rigid
  placement conversion that previously altered mass properties by applying a
  general geometric transform. All 40 Design-modeling tests, the Hole lifecycle
  gate, 81 focused Native/catalog/registry tests, and the protected Part Design
  VibeScript lifecycle remain green. The complete registered Model schema is
  62,763 bytes, 2,773 bytes below the unchanged 64-KiB hard limit.
- Model standard-fastener editing is the second typed `model.fastener` variant
  and targets the published `PartDesign::Body` by exact internal name. The live
  Model snapshot now exposes every bounded editable fastener's Body, owning
  operation, part number, canonical key, and complete current constructor, so
  a later turn does not need the insertion transcript. Human and Native modern
  edits converge on one 599-line shared retained-graph implementation; legacy
  migration remains available only through the existing human command. Native
  preflight rejects stale, wrong-type, non-fastener, and incompatible-standard
  targets before opening a transaction, while the committed edit preserves the
  exact Body, publication, state, operation, and hidden generator identities.
  The upstream update helper's new targeted-recompute option is additive and
  defaults off, preserving all existing VibeScript and external call behavior.
  The expanded GUI gate proves human/Native geometry parity, exact receipts,
  wrong/stale/incompatible/schema no-ops, verifier rollback, undo/redo,
  repeated recompute, modeled threads, snapshot discovery, and save/reopen;
  it emits `VIBECAD_NATIVE_MODEL_FASTENER_GUI_OK`. All 21 human Fasteners GUI
  tests, 102 focused Native/catalog/snapshot/ribbon tests, the exhaustive
  225-standard catalog gate, and the complete Part Design VibeScript lifecycle
  are green. The complete registered Model schema is 63,150 bytes, 2,386 bytes
  below the unchanged 64-KiB hard limit.
- Model matching-fastener-hole creation is the third typed `model.fastener`
  variant and requires an exact retained fastener Body, one exact reusable
  Design-scope Sketch, a purpose and fit, and 1–16 explicit target Bodies. It
  resolves all catalog dimensions and rejects stale or wrong-type targets,
  Body-owned Sketches, the source fastener as a cut target, unsupported
  standards, and non-normal tapped fits before opening a transaction. The
  Native path creates the same global `PartDesign::DesignHole` history
  operation as the human command, shares the authoritative
  `resolve_fastener_hole` and `configure_fastener_hole_feature` algorithms,
  honors the user's current Hole-location preference, never opens a task
  panel, and returns only the operation, affected Bodies, derived fastener
  evidence, and exact receipt. Its dedicated GUI gate proves actual
  human/Native control, cutter, and Body-geometry parity; clearance, tapped
  multi-Body, counterbore, and countersink cases; schema/target/ownership/fit
  no-ops; forced verifier rollback; undo/redo; repeated recompute; and
  save/reopen. It emits
  `VIBECAD_NATIVE_MODEL_MATCHING_FASTENER_HOLE_GUI_OK`. All 21 human Fasteners
  GUI tests, 811 focused Native/schema/ribbon/guardrail tests, the exhaustive
  225-standard catalog gate, and the complete Part Design VibeScript lifecycle
  are green. The complete registered Model schema is 64,015 bytes, 1,521 bytes
  below the unchanged 64-KiB hard limit. New execution and gate modules are
  296 and 626 lines respectively.
- Model standard-fastener attachment is the fourth typed `model.fastener`
  variant and accepts one exact retained fastener Body plus one exact circular
  `EdgeN` on a retained Design-history Body. Provider preflight rejects stale,
  wrong-type, non-fastener, noncircular, self, already-attached, Assembly-link,
  and legacy Body-without-History targets before a transaction. Human GUI
  mapped-element tokens are canonicalized through their live host shape while
  the provider contract remains strictly `EdgeN`. Human and Native modern
  attachment share one 266-line retained-graph implementation that reorders
  the fastener History block after a later host operation, resolves the exact
  persistent Design subelement, and preserves the Body, publication, state,
  operation, and hidden generator identities. The production verifier proves
  the retained definition link, canonical edge, attachment center, aligned
  global placement, one valid solid, and local geometry agreement across the
  generator, operation output, state, publication, and Body before commit. Its
  609-line dispatcher-backed GUI gate drives the actual human ribbon command
  and Native tool, proves geometry/control parity without opening a dialog or
  changing workbench, and covers target no-ops, forced-verifier rollback,
  History ordering, exact receipts, undo/redo, repeated recompute, and durable
  save/reopen references. It emits
  `VIBECAD_NATIVE_MODEL_FASTENER_ATTACHMENT_GUI_OK`. All 21 human Fasteners GUI
  tests, 813 focused Native/schema/ribbon/packaging/guardrail tests, the
  exhaustive 225-standard catalog gate, and the complete Part Design
  VibeScript lifecycle are green. Source and build-tree packaged modules match;
  the complete registered Model schema is 64,370 bytes, 1,166 bytes below the
  unchanged 64-KiB hard limit.
- Model component-interface publication is now the exact-target
  `component.interface` capability over one explicitly named native component
  and one directly owned native LCS. Its closed schema requires the stable
  interface name, semantic kind, complete allowed-joint list, and explicit
  compatibility token; it is mapped only to the shared Publish Interface
  action on Model and Assemble and exposes no selection, command-dispatch, or
  workbench control. Preflight rejects stale or wrong-type targets, unowned
  LCS objects, duplicate names, VibeScript-owned components, malformed
  semantics, and identical publications before mutation. Human and Native
  paths retain one shared reference-contract algorithm, while Native adds
  frozen LCS-property state, exact re-resolution, one changed-LCS receipt,
  targeted recompute, and a postcondition that proves the persisted connector
  and placement frame before commit. The Model snapshot now lists bounded
  component-owned LCS objects and their published definitions so a later turn
  can continue without transcript memory. The retired
  `component.publish_interface` provider wrapper, registration, VibeScript
  surface leak, inventory entry, and old prompt instruction are deleted with
  no compatibility alias. The 609-line dispatcher-backed GUI gate drives the
  actual human ribbon dialog and Native tool, caught and fixed the human
  command's false-vs-None active-dialog predicate, and proves human/Native
  connector and frame parity, schema and target no-ops, stale preflight,
  forced-verifier rollback, exact receipt, update-in-place, undo/redo,
  repeated recompute, live snapshot/catalog discovery, and FCStd save/reopen.
  It emits `VIBECAD_NATIVE_COMPONENT_INTERFACE_GUI_OK`. The focused Native,
  schema, ribbon, surface, and retained-tool suite is 841/841 green; the full
  Part Design and Assembly VibeScript integration gates also complete
  successfully. Source and build-tree modules match, the retired name is
  absent from both trees, and the complete registered Model schema is 65,527
  bytes, 9 bytes below the unchanged 64-KiB hard limit.
- Model-to-Sketch isolation and dropdown coverage are now enforced against the
  live action graph. The static inventories prove that Model and Sketch edit
  share only Fit All, Isometric, and Grid; all other 102 contextual Sketch
  actions are disjoint, and an injected Sketch geometry action makes Model
  classification fail closed. The production provider resolves the real
  75-action Model page while exposing only `sketch.validate` from any
  `sketch.*` family; after the human enters Sketch edit, Native is unavailable
  with no tools, and returning to Model restores the identical tool schemas.
  Model's only three live composite parents are Design Primitive, Offset, and
  Join. Production classification now requires their exact ordered sets of
  nine, two, and three leaves respectively, while the parents remain
  non-provider menu nodes and every leaf retains its exact capability and
  operation mapping. Focused tests reject missing, reordered, or displaced
  leaves, and the clean-profile GUI gate proves the same graph before and
  after a real Sketch edit transition. That gate also caught and corrected
  three live-contract drifts without compatibility aliases: view actions use
  presentation transactions, Matching Fastener Hole uses its complete live
  operation name, and Geometric Fill/Extend Face use their complete live
  operation names. The focused Native/ribbon/surface/guardrail suite is
  818/818 green, and the current registered Model schema is 65,530 bytes, 6
  bytes below the unchanged 64-KiB hard limit.
- The complete Model bracket workflow now runs through the real 75-action
  provider surface and production Native session factory. A 375-line
  clean-profile GUI gate creates one physical Component and two reusable
  Sketch definitions without entering edit mode, then drives the human into
  contextual Sketch edit to draw the closed base profile and mounting-hole
  profile. Each human ribbon transition makes the old Model turn fail with
  `NATIVE_SURFACE_CHANGED` both during edit and after returning; only a newly
  frozen Model turn can continue. The resumed workflow validates both
  profiles, extrudes a 60-by-30-by-8-mm component-owned Body, cuts one exact
  6-mm through-hole, and linearly patterns that subtractive feature into three
  holes at 20-mm spacing. It verifies exact removed volume, operation order
  and source identity, one-step pattern undo/redo, concise dispatcher results,
  no residual edit/task UI, stable Body and operation IDs, Component
  containment, recompute, and FCStd save/reopen. The gate emits
  `VIBECAD_NATIVE_MODEL_BRACKET_WORKFLOW_GUI_OK` on two consecutive runs. The
  live ribbon gate, 818 focused Native/ribbon/surface/guardrail tests, and the
  full Part Design VibeScript lifecycle are green; the protected VibeScript
  result reports `"ok": true`. Source and packaged build-tree copies match,
  and the complete registered Model schema remains 65,530 bytes, 6 bytes under
  the unchanged hard limit.
- Contextual Sketch now has an exact bounded state read for the one sketch the
  human actually opened. The 733-line domain serializer reports native
  geometry indices and persistent IDs where available, typed curve parameters,
  construction/internal state, constraint slots and values, exact projected
  source objects and subelements, native external-geometry indices, attachment
  support and offset, compact native wire/face diagnostics, DoF, and bounded
  conflict/redundancy/malformed/open-vertex state. Sketch setup keeps its
  document list summary-only; detailed state is never multiplied across every
  sketch. Large state prunes explicit tail records under a 52-KiB domain budget
  while preserving exact totals and truncation flags inside the unchanged
  64-KiB host snapshot limit. The read path never calls `solve()`, opens a
  transaction, changes geometry, or changes the human-selected surface. A
  293-line clean-profile GUI gate proves a real six-geometry, ten-constraint,
  externally projected, face-attached sketch; repeated read-only equality;
  unchanged undo/redo/booked-transaction boundaries; incomplete Sketch
  provider fail-closure; and FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_STATE_GUI_OK`. The focused suite is 821/821 green,
  the 527-action live ribbon gate is green, and the complete protected Part
  Design VibeScript lifecycle again reports `"ok": true`.
- Contextual Sketch Point is the first exact `sketch.geometry` mutation. Its
  890-byte closed schema requires the internal name of the sketch, the geometry
  and constraint counts observed in current state, and one bounded 2D
  position. Preflight and postcondition both prove that this is the exact
  `Sketcher::SketchObject` the human already has open on `sketch.edit`; the
  production path contains no workbench, ribbon, edit-session, selection, or
  `runCommand` activation. One host-owned transaction appends one
  non-construction `Part::GeomPoint`, recomputes only that sketch, preserves
  every pre-existing geometry and constraint definition, returns the exact new
  geometry plus compact profile/solver state, records only the changed sketch,
  and fails closed on stale counts or target drift. The complete Sketch surface
  remains unavailable and publishes zero schemas while the rest of its live
  actions are unfinished. A 326-line real-GUI gate proves wrong-target and
  stale-state refusal without history change, exact Point creation, one undo
  entry, duplicate-call idempotency, forced-verifier rollback, undo/redo while
  the same Sketch stays open, unchanged ribbon/workbench identity, and FCStd
  save/reopen. That gate has now become the rolling contextual-geometry gate
  described below so subsequent geometry variants reuse the same real GUI
  lifecycle instead of accumulating duplicate harnesses.
- Contextual Sketch Line is the second exact `sketch.geometry` variant and maps
  only `Sketcher_CreateLine`; the interactive Line/Polyline dropdown remains a
  parent-only action. The closed contract
  requires two bounded 2D endpoints and refuses coincident or sub-nanometre
  segments before a transaction. The 151-line domain module appends one
  non-construction `Part::GeomLineSegment` using the same exact active-Sketch,
  stale-count, full pre-existing geometry/constraint fingerprint, atomic
  transaction, targeted recompute, and concise solver/profile result contract
  as Point. Exact endpoint, type, index, construction state, receipt, undo, and
  persistence postconditions are independently checked. Point and Line together
  serialize to 1,546 bytes against the unchanged 65,536-byte hard limit, while
  the incomplete production Sketch surface still advertises zero tools and
  schemas. The rolling real-GUI geometry gate proves schema-valid but
  degenerate Line refusal without history change, Point rollback/idempotency,
  exact Line creation, separate one-step Point and Line undo/redo while the
  human-opened Sketch remains in edit, unchanged ribbon/workbench identity, and
  both geometries after FCStd save/reopen. Polyline extends that same gate below
  rather than duplicating its lifecycle harness.
- Contextual Sketch Polyline is the third exact `sketch.geometry` variant and
  maps only `Sketcher_CreatePolyline`. Its closed contract accepts 2 through 65
  bounded vertices plus an explicit open/closed choice, rejects consecutive
  duplicate or sub-nanometre vertices, requires at least three vertices when
  closed, and refuses an implicitly closed open path. The 257-line domain module
  atomically appends the exact ordered `Part::GeomLineSegment` sequence and
  explicit `Coincident` constraints at every joint, including the closing joint
  for a closed path. Postcondition proves every new segment endpoint, constraint
  reference, contiguous index, construction flag, full pre-existing fingerprint,
  receipt, and compact solver/profile result before commit. Point, Line, and
  Polyline together serialize to 1,969 bytes against the unchanged 65,536-byte
  hard limit, while the incomplete production Sketch surface still advertises
  zero tools and schemas. The rolling real-GUI gate proves invalid
  Polyline refusal without history mutation, exact open-path geometry and
  constraints, one-step atomic undo/redo, unchanged active Sketch/ribbon/
  workbench identity, and Point, Line, and Polyline persistence after FCStd
  save/reopen. Center-radius Arc extends that same gate below.
- Contextual Sketch center-radius Arc is the fourth exact `sketch.geometry`
  variant and maps only `Sketcher_CreateArc`; the distinct
  `Sketcher_Create3PointArc` command remains unfinished. Its canonical closed
  contract requires a bounded 2D center, radius greater than one nanometre, a
  start angle from 0 inclusive to 360 degrees exclusive, and a counter-clockwise
  sweep strictly between 0 and 360 degrees. The 182-line domain module appends
  one non-construction `Part::GeomArcOfCircle` in one host transaction. Its
  postcondition proves exact center, +Z sketch-plane axis, radius, parameter
  range, analytic endpoints, open-curve state, index, construction state, full
  pre-existing geometry/constraint fingerprints, receipt, and compact solver/
  profile result before commit. Point, Line, Polyline, and Arc together serialize
  to 2,564 bytes against the unchanged 65,536-byte hard limit, while the
  incomplete production Sketch surface still advertises zero tools and schemas.
  The rolling real-GUI gate proves invalid-radius refusal without
  history mutation, exact Arc creation, one-step undo/redo, unchanged active
  Sketch/ribbon/workbench identity, and all four geometry variants after FCStd
  save/reopen. Three-point Arc extends that same gate below.
- Contextual Sketch three-point Arc is the fifth exact `sketch.geometry`
  variant and maps only `Sketcher_Create3PointArc`. Its closed contract names
  two bounded endpoints and one bounded rim point. The 269-line domain module
  rejects every coincident pair, collinear or sub-nanometre-height triples, and
  circumcircles whose center or radius falls outside the one-million-mm Sketch
  bound before mutation. It analytically derives the circumcenter and chooses
  the direct or angular-seam-wrapped parameter interval whose interior contains
  the rim point. The shared 65-line circular-arc proof verifies the actual
  `Part::GeomArcOfCircle` center, +Z axis, radius, parameters, stored endpoint
  order, construction/open state, and analytic rim incidence before commit.
  Point, Line, Polyline, center Arc, and three-point Arc together serialize to
  3,388 bytes against the unchanged 65,536-byte hard limit; the incomplete
  production Sketch surface still advertises zero tools and schemas. The
  rolling real-GUI gate uses the harder wrapped lower semicircle and
  proves collinear refusal without history mutation, exact creation, one-step
  undo/redo, unchanged active Sketch/ribbon/workbench identity, and all five
  variants after FCStd save/reopen. Elliptical Arc extends that same gate below.
- Contextual Sketch elliptical Arc is the sixth exact `sketch.geometry` variant
  and maps only `Sketcher_CreateArcOfEllipse`. Its canonical closed contract
  requires a bounded center, distinct positive major/minor radii, bounded major
  axis rotation, start parameter, and counter-clockwise parameter sweep. The
  371-line domain module rejects circular or inverted radii and atomically
  creates the trimmed `Part::GeomArcOfEllipse` plus the exact durable state made
  by the human command: construction major/minor diameters, both construction
  foci, and four `InternalAlignment` constraints. Postcondition proves the main
  curve's center, +Z plane, rotated major axis, radii, parameter range and
  analytic endpoints; every internal role, coordinate and construction flag;
  every alignment reference; full pre-existing fingerprints; and concise
  receipt/solver/profile state before commit. The six geometry operations
  serialize to 4,011 bytes against the unchanged 65,536-byte hard limit, while
  incomplete production Sketch still advertises zero tools and schemas. The
  The rolling real-GUI gate proves equal-radius refusal without history
  mutation, the exact five-geometry/four-constraint delta, one-step undo/redo,
  unchanged active Sketch/ribbon/workbench identity, and all durable geometry
  after FCStd save/reopen. Stable provider and argument fixtures live in a
  separate support module so the executable lifecycle gate remains below the
  1,000-line split boundary. Hyperbolic Arc extends that gate below.
- Contextual Sketch hyperbolic Arc is the seventh exact `sketch.geometry`
  variant and maps only `Sketcher_CreateArcOfHyperbola`. Its closed contract
  requires a bounded center, positive major and minor coefficients, bounded
  major-axis rotation, and ordered dimensionless start/end parameters in the
  interval from -20 through 20. It deliberately permits a major coefficient
  below the minor coefficient, rejects equal or reversed parameter bounds, and
  analytically refuses endpoints outside the one-million-mm Sketch bound before
  mutation. The 359-line domain module creates one trimmed
  `Part::GeomArcOfHyperbola`, exposes the exact durable state created by the
  human command, and proves the three construction internals
  (`HyperbolaMajor`, `HyperbolaMinor`, and `HyperbolaFocus`) plus their three
  `InternalAlignment` constraints. Its postcondition also proves the main
  curve's center, +Z plane, rotated major axis, coefficients, parameter range,
  analytic endpoints, open/non-construction state, full pre-existing
  fingerprints, targeted receipt, and concise solver/profile state before
  commit. Elliptical and hyperbolic Arc share a 106-line internal-geometry
  verifier rather than duplicating role, coordinate, construction, and
  constraint-reference checks. The seven geometry operations serialize to
  exactly 4,357 bytes against the unchanged 65,536-byte hard limit; incomplete
  production Sketch continues to advertise zero tools and schemas. The
  rolling real-GUI gate proves ordered-parameter refusal without history
  mutation, the exact four-geometry/three-constraint hyperbola delta, one-step
  undo/redo, unchanged active Sketch/ribbon/workbench identity, and all seven
  operations after FCStd save/reopen. Parabolic Arc extends that gate below.
- Contextual Sketch parabolic Arc is the eighth exact `sketch.geometry` variant
  and maps only `Sketcher_CreateArcOfParabola`. Its closed contract uses a
  bounded vertex, positive focal length, bounded focal-axis rotation, and
  ordered start/end parameters expressed explicitly in millimetres. It rejects
  coincident, reversed, or sub-nanometre parameter spans and analytically
  refuses a focus or endpoint outside the one-million-mm Sketch bound before a
  transaction. The 336-line domain module constructs the exact
  `Part::GeomArcOfParabola`, exposes the durable state created by the human
  command, and proves the construction `ParabolaFocus` point, construction
  `ParabolaFocalAxis` line from vertex to focus, and both
  `InternalAlignment` references. Its main-curve postcondition proves the
  vertex, +Z plane, rotated focal axis, focal length, parameter range, analytic
  endpoints, open/non-construction state, full pre-existing fingerprints,
  targeted receipt, and concise solver/profile state before commit. The eight
  geometry operations serialize to exactly 5,004 bytes against the unchanged
  65,536-byte hard limit; incomplete production Sketch still advertises zero
  tools and schemas. The 959-line executable real-GUI gate, backed by a
  separate 196-line provider/argument fixture, proves invalid-span refusal
  without history mutation, the exact three-geometry/two-constraint parabola
  delta, one-step undo/redo, unchanged active Sketch/ribbon/workbench identity,
  and all eight operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola`.
  The focused suite is 932/932 green, the Sketch state and 527-action live
  ribbon gates remain green, the full protected Sketcher VibeScript lifecycle
  emits `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part
  Design phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code
  zero.
- Contextual Sketch center-radius Circle is the ninth exact `sketch.geometry`
  variant and maps only `Sketcher_CreateCircle`; the dropdown parent and
  three-point Circle remain unimplemented. Its closed contract requires one
  bounded center and a radius greater than one nanometre. The 130-line domain
  module appends one non-construction `Part::GeomCircle` and reuses the shared
  88-line circular proof to verify the exact center, +Z Sketch-plane axis,
  radius, closed state, index, pre-existing fingerprints, targeted receipt,
  and concise solver/profile result before commit. The nine geometry
  operations serialize to exactly 5,127 bytes against the unchanged
  65,536-byte hard limit; incomplete production Sketch still advertises zero
  tools and schemas. Circle's 86-line lifecycle case is separate from the
  974-line rolling GUI executable and 214-line provider/argument fixture, so
  every executable test module remains below the 1,000-line split boundary.
  The real host gate proves invalid-radius refusal without history mutation,
  exact creation, one-step undo/redo, unchanged active Sketch/ribbon/workbench
  identity, and all nine operations after FCStd save/reopen. Reload validation
  deliberately compares the durable Circle contract rather than FreeCAD's
  document-local numeric geometry ID. The gate emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle`.
  The focused suite is 938/938 green, the 527-action live ribbon gate remains
  green, the protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch three-point Circle is the tenth exact `sketch.geometry`
  variant and maps only `Sketcher_Create3PointCircle`. Its closed contract
  names three bounded points and refuses every duplicate pair, collinear or
  sub-nanometre-height triple, and any derived center or radius outside the
  one-million-mm Sketch bound before mutation. The 159-line domain module
  constructs one exact non-construction `Part::GeomCircle`, proves its center,
  +Z axis, radius, closed state, and analytic incidence of all three requested
  points, then returns the same concise state/receipt contract as center-radius
  Circle. Three-point Arc and Circle now share a 70-line bounded circumcircle
  derivation; the Arc module is 225 lines after removing its duplicate math,
  and its established behavior and diagnostics remain covered. The ten
  geometry operations serialize to exactly 5,952 bytes against the unchanged
  65,536-byte hard limit; incomplete production Sketch continues to advertise
  zero tools and schemas. Both Circle variants use the 143-line lifecycle case
  beside the 986-line rolling executable and 232-line provider fixture. The
  real host gate proves collinear refusal without history mutation, exact
  three-point Circle creation, one-step undo/redo, unchanged active Sketch/
  ribbon/workbench identity, and all ten operations after FCStd save/reopen.
  It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle`.
  The focused suite is 944/944 green, the 527-action live ribbon gate remains
  green, the protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch center-based Ellipse is the eleventh exact
  `sketch.geometry` variant and maps only `Sketcher_CreateEllipseByCenter`;
  three-point Ellipse remains a separate action. Its closed contract
  requires a bounded center, distinct positive major/minor radii with the minor
  radius strictly smaller, and a bounded major-axis rotation. The 218-line
  domain module creates one closed non-construction `Part::GeomEllipse`, then
  exposes and proves the exact durable state made by the human command:
  construction major/minor diameter lines, both construction foci, and four
  `InternalAlignment` constraints. Full Ellipse and elliptical Arc now share a
  138-line analytic/internal-geometry layer; the Arc module is 291 lines after
  removing its duplicated axis, focus, endpoint, and record verification.
  Existing elliptical Arc tests remain green. The eleven geometry operations
  serialize to exactly 6,117 bytes against the unchanged 65,536-byte hard
  limit; incomplete production Sketch continues to advertise zero tools and
  schemas. A 127-line Ellipse lifecycle case keeps the rolling executable at
  985 lines, with provider/argument fixtures at 278 lines. The real host gate
  proves equal-radius refusal without history mutation, the exact five-
  geometry/four-constraint delta, one-step undo/redo, unchanged active Sketch/
  ribbon/workbench identity, and all eleven operations after FCStd save/reopen.
  It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse`.
  The focused suite is 953/953 green, the 527-action live ribbon gate remains
  green, the protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch three-point Ellipse is the twelfth exact
  `sketch.geometry` variant and maps only `Sketcher_CreateEllipseBy3Points`.
  Its contract follows the human command precisely: the first two points are
  opposite endpoints of an initial axis and the third point lies on the
  ellipse. The 297-line domain module derives the midpoint and second radius,
  promotes the derived axis when it is longer, and refuses duplicate axis
  endpoints, an axis-collinear or out-of-span rim point, and the equal-radius
  Circle result before mutation. It creates and proves the closed full
  `Part::GeomEllipse`, both construction diameter lines, both construction
  foci, all four `InternalAlignment` constraints, and analytic incidence of
  all three source points. The twelve geometry operations serialize to exactly
  6,757 bytes against the unchanged 65,536-byte hard limit; incomplete
  production Sketch continues to advertise zero tools and schemas. A 236-line
  Ellipse lifecycle case and 304-line provider/argument fixture keep the
  rolling executable at 997 lines, still below the 1,000-line split boundary.
  The real host gate proves invalid-rim refusal without history mutation, the
  exact five-geometry/four-constraint delta, one-step undo/redo, unchanged
  active Sketch/ribbon/workbench identity, and all twelve operations after
  FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse`.
  The focused suite is 961/961 green, the 527-action live ribbon gate remains
  green, the protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch corner Rectangle is the thirteenth exact
  `sketch.geometry` variant and maps only `Sketcher_CreateRectangle`; the
  dropdown parent and center Rectangle remain unimplemented. Its closed
  contract names two bounded opposite corners and refuses a zero or
  sub-nanometre width or height before mutation. The 151-line corner domain
  uses a shared 173-line human-parity rectangle boundary for either diagonal
  direction: four counter-clockwise non-construction LineSegments, four
  explicit closing `Coincident` constraints, then four ordered
  `Horizontal`/`Vertical` constraints. Its postcondition proves every endpoint,
  corner reference, alignment reference and state flag, contiguous output
  index, full pre-existing fingerprint, targeted receipt, and concise profile/
  solver result before commit. The thirteen geometry operations serialize to
  exactly 7,345 bytes against the unchanged 65,536-byte hard limit;
  incomplete production Sketch continues to advertise zero tools and schemas,
  with center Rectangle now proving fail-closure. Before adding Rectangle, the
  997-line rolling lifecycle executable was split into a 274-line basic-
  geometry case and a 754-line orchestrator without changing its real-host
  assertions; after Rectangle the orchestrator is 768 lines and its new case
  is 118 lines. The real host gate proves zero-width refusal without history
  mutation, the exact four-geometry/eight-constraint delta for the negative-
  diagonal ordering, one-step undo/redo, unchanged active Sketch/ribbon/
  workbench identity, and all thirteen operations after FCStd save/reopen. It
  emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle`.
  The focused suite is 969/969 green, the 527-action live ribbon gate remains
  green, the protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch center Rectangle is the fourteenth exact
  `sketch.geometry` variant and maps only `Sketcher_CreateRectangle_Center`;
  Oblong is now the next unfinished geometry action. Its closed contract names
  a bounded center and one bounded corner, rejects a zero or sub-nanometre half
  span, and refuses a reflected opposite corner outside the one-million-mm
  Sketch bound before mutation. The 231-line domain reuses the exact shared
  four-side/eight-constraint rectangle boundary, then appends the human
  command's construction `Part::GeomPoint` and three-reference `Symmetric`
  constraint between opposite corners and that center. Its postcondition
  proves the five geometries, all nine constraints and reference positions,
  construction/active/driving/virtual state, contiguous indices, full
  pre-existing fingerprint, targeted receipt, and concise profile/solver
  result before commit. The fourteen geometry operations serialize to exactly
  7,711 bytes against the unchanged 65,536-byte hard limit; incomplete
  production Sketch continues to advertise zero tools and schemas, with
  Oblong proving fail-closure. The two Rectangle lifecycle cases share one
  216-line module beside the 781-line rolling orchestrator and 343-line
  provider/argument fixture. The real host gate proves zero-half-width refusal
  without history mutation, the exact five-geometry/nine-constraint delta,
  one-step undo/redo, unchanged active Sketch/ribbon/workbench identity, and
  all fourteen operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle`.
  The focused suite is 977/977 green, the 527-action live ribbon gate remains
  green, the protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Oblong is the fifteenth exact `sketch.geometry` variant and
  maps only `Sketcher_CreateOblong`; Triangle is now the next unfinished
  geometry action. Its closed contract names two bounded opposite corners and
  a finite positive corner radius strictly smaller than half both spans, and
  refuses every invalid radius before mutation. The 486-line domain reuses the
  shared human-parity rectangle ordering to create four shortened
  non-construction LineSegments, four counter-clockwise quarter-circle arcs,
  and the human command's two construction corner Points. It applies and
  proves the exact nineteen-constraint topology: eight ordered `Tangent`, four
  `Horizontal`/`Vertical`, three `Equal`, and four `PointOnObject` constraints.
  The postcondition also proves canonical arc parameters, both diagonal
  orderings, construction/active/driving/virtual state, contiguous indices,
  the full pre-existing fingerprint, targeted receipt, and concise profile/
  solver result before commit. The fifteen geometry operations serialize to
  exactly 7,859 bytes against the unchanged 65,536-byte hard limit;
  incomplete production Sketch continues to advertise zero tools and schemas,
  with Triangle proving fail-closure. A 137-line Oblong lifecycle case keeps
  the rolling orchestrator at 795 lines and the provider/argument fixture at
  366 lines. The real host gate proves invalid-radius refusal without history
  mutation, the exact ten-geometry/nineteen-constraint delta, one-step undo/
  redo, unchanged active Sketch/ribbon/workbench identity, and all fifteen
  operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong`.
  The focused suite is 985/985 green, the 527-action live ribbon gate remains
  green, source and built runtime copies are byte-identical, and the touched
  Python files pass Ruff. The protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Triangle is the sixteenth exact `sketch.geometry` variant
  and maps only `Sketcher_CreateTriangle`; Square is now the next unfinished
  geometry action. Its closed contract names a bounded center and first corner,
  requires a non-degenerate radius no greater than one million mm, and refuses
  any derived vertex outside the same Sketch coordinate bound before mutation.
  A 51-line Triangle adapter fixes the side count at three while a 371-line
  regular-polygon domain owns the exact reusable human-command semantics. It
  creates three counter-clockwise non-construction LineSegments followed by
  the construction circumcircle, then applies and proves the human command's
  exact eight constraints: three closing `Coincident`, two `Equal`, and three
  endpoint-to-circle `PointOnObject` constraints. Its postcondition proves
  every generated vertex, line endpoint, circumcircle field, construction/
  active/driving/virtual state, contiguous index, full pre-existing
  fingerprint, targeted receipt, and concise profile/solver result before
  commit. The sixteen geometry operations serialize to exactly 7,910 bytes
  against the unchanged 65,536-byte hard limit; incomplete production Sketch
  continues to advertise zero tools and schemas, with Square proving
  fail-closure. A 131-line regular-polygon lifecycle case keeps the rolling
  orchestrator at 809 lines and the provider/argument fixture at 384 lines.
  The real host gate proves coincident center/corner refusal without history
  mutation, the exact four-geometry/eight-constraint delta, one-step undo/
  redo, unchanged active Sketch/ribbon/workbench identity, and all sixteen
  operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle`.
  The focused contract suite is 1,117/1,117 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Square is the seventeenth exact `sketch.geometry` variant
  and maps only `Sketcher_CreateSquare`; Pentagon is now the next unfinished
  geometry action. Its closed center/corner contract and all derived-bound
  checks reuse the proven 371-line regular-polygon domain through a separate
  51-line fixed-four-side adapter. It creates four counter-clockwise
  non-construction LineSegments and the construction circumcircle, then
  applies and proves the human command's exact eleven constraints: four
  closing `Coincident`, three `Equal`, and four endpoint-to-circle
  `PointOnObject` constraints. The postcondition proves every generated
  vertex, line endpoint, circumcircle field, durable state flag, contiguous
  index, pre-existing fingerprint, targeted receipt, and concise profile/
  solver result before commit. The seventeen geometry operations serialize
  to exactly 7,955 bytes against the unchanged 65,536-byte hard limit;
  incomplete production Sketch continues to advertise zero tools and
  schemas, with Pentagon proving fail-closure. The shared regular-polygon GUI
  case is 237 lines, the rolling orchestrator is 821 lines, and its provider/
  argument fixture is 402 lines. The real host gate proves coincident center/
  corner refusal without history mutation, the exact five-geometry/eleven-
  constraint delta, one-step undo/redo, unchanged active Sketch/ribbon/
  workbench identity, and all seventeen operations after FCStd save/reopen.
  It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square`.
  The focused contract suite is 1,121/1,121 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Pentagon is the eighteenth exact `sketch.geometry`
  variant and maps only `Sketcher_CreatePentagon`; Hexagon is now the next
  unfinished geometry action. Its 51-line fixed-five-side adapter reuses the
  unchanged 371-line regular-polygon domain and its closed, derived-bounded
  center/corner contract. It creates five counter-clockwise non-construction
  LineSegments and the construction circumcircle, then applies and proves the
  human command's exact fourteen constraints: five closing `Coincident`, four
  `Equal`, and five endpoint-to-circle `PointOnObject` constraints. The
  postcondition proves the complete analytic shape, durable state, indices,
  pre-existing fingerprint, targeted receipt, and concise profile/solver
  result before commit. The eighteen geometry operations serialize to exactly
  8,006 bytes against the unchanged 65,536-byte hard limit; incomplete
  production Sketch continues to advertise zero tools and schemas, with
  Hexagon proving fail-closure. The shared regular-polygon GUI case is 339
  lines, the rolling orchestrator is 834 lines, and its provider/argument
  fixture is 435 lines. The real host gate proves coincident center/corner
  refusal without history mutation, the exact six-geometry/fourteen-
  constraint delta, one-step undo/redo, unchanged active Sketch/ribbon/
  workbench identity, and all eighteen operations after FCStd save/reopen.
  It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon`.
  The focused contract suite is 1,125/1,125 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Hexagon is the nineteenth exact `sketch.geometry` variant
  and maps only `Sketcher_CreateHexagon`; Heptagon is now the next unfinished
  geometry action. Its 51-line fixed-six-side adapter reuses the unchanged
  371-line regular-polygon domain. It creates six counter-clockwise
  non-construction LineSegments and the construction circumcircle, then
  applies and proves six closing `Coincident`, five `Equal`, and six
  endpoint-to-circle `PointOnObject` constraints—the exact seventeen-
  constraint durable topology produced by the human command. All analytic,
  state, index, fingerprint, targeting, transaction, profile, and solver
  postconditions remain closed. The nineteen geometry operations serialize
  to exactly 8,054 bytes against the unchanged 65,536-byte hard limit;
  incomplete production Sketch continues to advertise zero tools and
  schemas, with Heptagon proving fail-closure. The shared regular-polygon GUI
  case is 436 lines, the rolling orchestrator is 846 lines, and its provider/
  argument fixture is 452 lines. The real host gate proves coincident center/
  corner refusal without history mutation, the exact seven-geometry/
  seventeen-constraint delta, one-step undo/redo, unchanged active Sketch/
  ribbon/workbench identity, and all nineteen operations after FCStd save/
  reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon`.
  The focused contract suite is 1,129/1,129 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Heptagon is the twentieth exact `sketch.geometry` variant
  and maps only `Sketcher_CreateHeptagon`; Octagon is now the next unfinished
  geometry action. Its 51-line fixed-seven-side adapter reuses the unchanged
  371-line regular-polygon domain and creates seven counter-clockwise
  non-construction LineSegments plus the construction circumcircle. It
  applies and proves the human command's exact twenty constraints: seven
  closing `Coincident`, six `Equal`, and seven endpoint-to-circle
  `PointOnObject` constraints, together with all analytic, durable-state,
  index, fingerprint, targeting, transaction, profile, and solver
  postconditions. The twenty geometry operations serialize to exactly 8,105
  bytes against the unchanged 65,536-byte hard limit; incomplete production
  Sketch continues to advertise zero tools and schemas, with Octagon proving
  fail-closure. The shared regular-polygon GUI case is 538 lines, the rolling
  orchestrator is 858 lines, and its provider/argument fixture is 469 lines.
  The real host gate proves coincident center/corner refusal without history
  mutation, the exact eight-geometry/twenty-constraint delta, one-step undo/
  redo, unchanged active Sketch/ribbon/workbench identity, and all twenty
  operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon`.
  The focused contract suite is 1,133/1,133 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Octagon is the twenty-first exact `sketch.geometry`
  variant and maps only `Sketcher_CreateOctagon`; arbitrary Regular Polygon is
  now the next unfinished geometry action. Its 51-line fixed-eight-side
  adapter reuses the unchanged 371-line regular-polygon domain. It creates
  eight counter-clockwise non-construction LineSegments and the construction
  circumcircle, then applies and proves the human command's exact twenty-three
  constraints: eight closing `Coincident`, seven `Equal`, and eight
  endpoint-to-circle `PointOnObject` constraints. All analytic, durable-state,
  index, fingerprint, targeting, transaction, profile, and solver
  postconditions remain closed. The twenty-one geometry operations serialize
  to exactly 8,153 bytes against the unchanged 65,536-byte hard limit;
  incomplete production Sketch continues to advertise zero tools and schemas,
  with arbitrary Regular Polygon proving fail-closure. The shared regular-
  polygon GUI case is 637 lines, the rolling orchestrator is 870 lines, and
  its provider/argument fixture is 486 lines. The real host gate proves
  coincident center/corner refusal without history mutation, the exact nine-
  geometry/twenty-three-constraint delta, unchanged active Sketch/ribbon/
  workbench identity, and all twenty-one operations after FCStd save/reopen.
  This twenty-first successful transaction also reaches FreeCAD's default
  twenty-entry undo retention limit; the gate proves the new named Octagon
  transaction replaces only the oldest retained entry and still undoes and
  redoes as one coherent step. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon`.
  The focused contract suite is 1,137/1,137 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch arbitrary Regular Polygon is the twenty-second exact
  `sketch.geometry` variant and maps only `Sketcher_CreateRegularPolygon`;
  straight Slot is now the next unfinished geometry action. Its 65-line
  adapter exposes the human dialog's integer side-count control and strictly
  bounds it from 3 through 9,999, rejecting booleans, fractional counts, and
  out-of-range values before mutation. It reuses the unchanged 371-line
  regular-polygon domain for exact topology. A proved nine-sided call creates
  nine counter-clockwise non-construction LineSegments and the construction
  circumcircle, with nine closing `Coincident`, eight `Equal`, and nine
  endpoint-to-circle `PointOnObject` constraints. The same closed analytic,
  durable-state, index, fingerprint, targeting, transaction, profile, and
  solver postconditions scale from the requested side count. The twenty-two
  geometry operations serialize to exactly 8,373 bytes against the unchanged
  65,536-byte hard limit; incomplete production Sketch continues to advertise
  zero tools and schemas, with straight Slot proving fail-closure. The shared
  regular-polygon GUI case is 742 lines, the rolling orchestrator is 883
  lines, and its provider/argument fixture is 507 lines. The real host gate
  proves invalid side-count refusal without history mutation, the exact ten-
  geometry/twenty-six-constraint nine-sided delta, retention of the newest
  named transaction at the twenty-entry undo limit, one-step undo/redo,
  unchanged active Sketch/ribbon/workbench identity, and all twenty-two
  operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon`.
  The focused contract suite is 1,144/1,144 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch straight Slot is the twenty-third exact `sketch.geometry`
  variant and maps only `Sketcher_CreateSlot`; arc Slot is now the next
  unfinished geometry action. Its 359-line domain follows
  `DrawSketchHandlerSlot` exactly: two non-construction semicircular arcs in
  human-command order, two connecting LineSegments, four endpoint-specific
  `Tangent` constraints, and one arc `Equal` constraint. The strict contract
  accepts the two arc centers and radius, refuses coincident centers,
  non-positive radii, unexpected fields, stale target counts, and any derived
  boundary outside the fixed coordinate envelope before mutation. It proves
  exact geometry and constraint indices, parameters, centers, endpoints,
  topology, fingerprints, active-Sketch identity, and the four-geometry/five-
  constraint append in one document transaction. The twenty-three geometry
  operations serialize to exactly 8,946 bytes against the unchanged 65,536-
  byte hard limit; incomplete production Sketch continues to advertise zero
  tools and schemas, with arc Slot proving fail-closure. The dedicated GUI
  case is 145 lines, the rolling orchestrator is 897 lines, and its provider/
  argument fixture is 527 lines, keeping each module below the split boundary.
  The real host gate proves schema-level invalid-radius refusal without
  history mutation, the exact durable topology, retention of the newest named
  transaction at FreeCAD's twenty-entry undo limit, one-step undo/redo,
  unchanged active Sketch/ribbon/workbench identity, and all twenty-three
  operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot`.
  The focused contract suite is 1,151/1,151 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Arc Slot is the twenty-fourth exact `sketch.geometry`
  variant and maps only `Sketcher_CreateArcSlot`; non-periodic B-spline is now
  the next unfinished geometry action. Its 458-line domain follows the shipped
  action's default rounded-end `DrawSketchHandlerArcSlot` construction for
  both positive and negative sweeps. It preserves the human geometry order:
  outer boundary, initial semicircular end, terminal semicircular end, and—
  when the slot radius is smaller than the centerline radius—inner boundary.
  It applies and proves the handler's exact center `Coincident` and four
  direction-specific `Tangent` constraints. The valid human boundary case in
  which slot radius equals centerline radius is separately proved as three
  arcs, two `Coincident` constraints, and two `Tangent` constraints. The
  strict contract refuses zero/full-turn sweeps, an oversized slot radius,
  the Open CASCADE inner-boundary confusion interval, stale target counts,
  unexpected fields, and an unbounded outer envelope before mutation. Exact
  arc roles, canonical parameters, centers, endpoints, radii, direction,
  geometry/constraint indices, fingerprints, and one-transaction append
  counts are postconditions. The twenty-four geometry operations serialize
  to exactly 9,444 bytes against the unchanged 65,536-byte hard limit;
  incomplete production Sketch continues to advertise zero tools and schemas,
  with non-periodic B-spline proving fail-closure. The combined Slot GUI case
  is 283 lines, the rolling orchestrator is 909 lines, and its provider/
  argument fixture is 551 lines. The real host gate proves an invalid radius
  fails without history mutation, a clockwise four-arc/five-constraint call
  retains its newest named transaction at the twenty-entry undo limit, exact
  one-step undo/redo, unchanged active Sketch/ribbon/workbench identity, and
  all twenty-four operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot`.
  The focused contract suite is 1,161/1,161 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual non-periodic control-point B-spline is the twenty-fifth exact
  `sketch.geometry` variant and maps only `Sketcher_CreateBSpline`; periodic
  B-spline is now the next unfinished geometry action and continues to keep
  production Sketch fail-closed. The 523-line shared control-point B-spline
  core and 63-line non-periodic adapter reproduce the shipped human handler's
  durable construction in its exact order: one construction circle per pole,
  one `Weight` constraint followed by `Equal` constraints, the non-construction
  spline, one `InternalAlignment:BSplineControlPoint` constraint per pole, and
  the exposed construction knot points with their
  `InternalAlignment:BSplineKnotPoint` constraints. The contract accepts two
  through twenty-four exact control points and a requested degree from one
  through twenty-five, clamps the effective degree to the human tool's
  non-periodic limit, generates the exact uniform knots and clamped endpoint
  multiplicities, and refuses adjacent duplicate poles, malformed fields,
  invalid degrees, stale target counts, and out-of-bounds coordinates before
  mutation. Postconditions prove every durable geometry and constraint index,
  role, pole, knot, multiplicity, weight, parameter, internal type, reference,
  append count, fingerprint, and active-Sketch identity. The twenty-five
  geometry operations serialize to exactly 9,904 bytes against the unchanged
  65,536-byte hard limit. The dedicated B-spline GUI case is 184 lines, the
  rolling orchestrator is 923 lines, and its provider/argument fixture is 571
  lines. The real host gate proves duplicate-pole refusal without history
  mutation, the exact seven-geometry/ten-constraint durable append, retention
  of the newest named transaction at FreeCAD's twenty-entry undo limit,
  one-step undo/redo, unchanged active Sketch/ribbon/workbench identity, and
  all twenty-five operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline`.
  The focused contract suite is 1,171/1,171 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual periodic control-point B-spline is the twenty-sixth exact
  `sketch.geometry` variant and maps only `Sketcher_CreatePeriodicBSpline`;
  interpolated B-spline is now the next unfinished geometry action and keeps
  production Sketch fail-closed. Its 63-line adapter reuses the now 537-line
  shared control-point core while preserving the human handler's periodic
  differences: two poles are sufficient, effective degree clamps to the pole
  count, the closing click does not add a duplicate terminal pole, every knot
  has multiplicity one, and all N+1 periodic knot points are exposed even
  though the first and terminal points coincide. The exact durable order
  remains construction pole circles with one `Weight` and subsequent `Equal`
  constraints, the non-construction periodic spline, control-point alignment
  constraints, then exposed knot points and knot-alignment constraints. The
  strict contract refuses an explicit final pole equal to the first because
  that is not a state the human handler creates, as well as adjacent duplicate
  poles, malformed fields, invalid degrees, stale counts, and out-of-bounds
  coordinates before mutation. Postconditions additionally prove periodic and
  closed state, equal curve endpoints, uniform knots, N+1 knot internals, and
  all exact defining data, indices, references, fingerprints, append counts,
  and active-Sketch identity. The twenty-six geometry operations serialize to
  exactly 9,982 bytes against the unchanged 65,536-byte hard limit. The
  combined B-spline GUI case is 356 lines, the rolling orchestrator is 936
  lines, and its provider/argument fixture is 590 lines. The real host gate
  proves duplicate-closure refusal without history mutation, the exact
  twelve-geometry/sixteen-constraint durable append, retention of the newest
  named transaction at FreeCAD's twenty-entry undo limit, one-step undo/redo,
  unchanged active Sketch/ribbon/workbench identity, and all twenty-six
  operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline`.
  The focused contract suite is 1,180/1,180 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual non-periodic interpolated B-spline is the twenty-seventh exact
  `sketch.geometry` variant and maps only
  `Sketcher_CreateBSplineByInterpolation`; periodic interpolated B-spline is
  now the next unfinished geometry action and keeps production Sketch
  fail-closed. Its 487-line domain follows the shipped handler's knot-based
  workflow rather than reusing control-point topology: it first appends the
  exact construction interpolation points, creates and degree-raises the
  Open CASCADE interpolation result to the handler's fixed cubic degree,
  aligns the input points as B-spline knots, and then calls Sketcher's internal
  exposure to create the generated construction control circles. The exposure
  constraints deliberately preserve their different human order—control
  `InternalAlignment` followed by `Weight` or reversed-reference `Equal` for
  each pole. The special three-input handler behavior is separately proved:
  the middle interpolation point is a `PointOnObject` rather than a generated
  knot, while the two endpoints remain knot alignments. The strict contract
  accepts two through twenty-four exact input points and refuses adjacent
  duplicates, malformed fields, stale target counts, and out-of-bounds
  coordinates before mutation. An independently reconstructed reference curve
  proves the exact generated poles, weights, knots, multiplicities, degree,
  parameters, endpoints, periodic/closed state, durable control handles,
  semantic weight radii, every constraint/index/reference, append counts,
  fingerprints, and active-Sketch identity. The twenty-seven geometry
  operations serialize to exactly 10,445 bytes against the unchanged
  65,536-byte hard limit. The combined B-spline GUI case is 525 lines, the
  rolling orchestrator is 948 lines, and its provider/argument fixture is 608
  lines. The real host gate proves adjacent-duplicate refusal without history
  mutation, the exact eleven-geometry/sixteen-constraint durable append,
  retention of the newest named transaction at FreeCAD's twenty-entry undo
  limit, one-step undo/redo, unchanged active Sketch/ribbon/workbench identity,
  and all twenty-seven operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation`.
  The focused contract suite is 1,186/1,186 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime
  copies are byte-identical, and the touched Python files pass Ruff. The
  protected Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual periodic interpolated B-spline is the twenty-eighth exact
  `sketch.geometry` variant and maps only
  `Sketcher_CreatePeriodicBSplineByInterpolation`; Sketch text is now the next
  unfinished geometry action and production Sketch remains fail-closed. Its
  62-line adapter reuses the 606-line interpolation domain while preserving
  the shipped human handler's distinct periodic topology: two through
  twenty-four construction interpolation points, a periodic Open CASCADE
  interpolation raised to cubic degree, one knot alignment for every input,
  generated construction control circles, and exposure of the one terminal
  periodic knot not represented by an input handle. The strict contract
  refuses adjacent duplicates and an explicit final input equal to the first,
  because the human closing click adds neither duplicate geometry nor a second
  input constraint. Postconditions reconstruct an independent periodic
  reference curve and prove its exact poles, weights, knots, multiplicities,
  degree, closed endpoints, every generated internal handle, every constraint
  and reference, append counts, fingerprints, and active-Sketch identity. The
  two-point host special case is separately proved with six poles, three knots,
  and multiplicities `[3, 3, 3]`. The twenty-eight geometry operations
  serialize to exactly 10,574 bytes against the unchanged 65,536-byte hard
  limit. The combined B-spline GUI case is 715 lines, the rolling orchestrator
  is 966 lines, and its provider/argument fixture is 625 lines. The real host
  gate proves duplicate-closure refusal without history mutation, the exact
  eleven-geometry/fifteen-constraint durable append for four input points,
  retention of the newest named transaction at FreeCAD's twenty-entry undo
  limit, one-step undo/redo, unchanged active Sketch/ribbon/workbench identity,
  and all twenty-eight operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation`.
  The focused contract suite is 1,193/1,193 green with four intentional skips,
  the 527-action live ribbon gate remains green, source and built runtime copies
  are byte-identical, and the touched Python files pass Ruff. The protected
  Sketcher VibeScript lifecycle emits
  `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17 protected Part Design
  phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both with exit code zero.
- Contextual Sketch Text is the twenty-ninth exact `sketch.geometry` variant
  and maps only `Sketcher_CreateText`; at that checkpoint Construction-state
  changes remained the next unfinished Sketch action and production Sketch
  remained fail-closed.
  The shipped handler was traced through its C++ creation and persistence
  paths: it appends one construction handle line, generates the font outlines
  as non-construction Sketch curves, and owns the complete group through one
  `Text` constraint. The existing Python wrapper could only expose the first
  three references and none of the durable Text metadata, so it now adds four
  read-only properties—`Elements`, `Text`, `Font`, and `IsTextHeight`—without
  changing any existing API. The bounded 465-line domain accepts exact handle
  endpoints, width/height sizing, one through sixty-four visible single-line
  characters, and either the `default` sentinel or an installed font basename.
  Font discovery follows the same bundled and platform font roots as the human
  dialog, resolves a canonical name without exposing a machine path, and
  revalidates the selected file identity immediately before mutation. It caps
  generated glyph topology at 512 curves and returns only the handle, Text
  constraint summary, curve count/range, kind counts, and a SHA-256 geometry
  fingerprint instead of dumping every glyph edge. Postconditions prove the
  exact construction handle, contiguous generated curves, all constraint
  elements, text, persisted font basename, sizing mode, active/driving state,
  pre-existing fingerprints, append counts, and active-Sketch identity. The
  twenty-nine geometry operations serialize to exactly 11,336 bytes against
  the unchanged 65,536-byte hard limit. The isolated Text GUI case is 168
  lines, the provider fixture is 649 lines, and the rolling orchestrator is
  883 lines after all save/reopen verification was moved behind a dedicated
  160-line boundary module before the next lifecycle case. The real host gate
  proves multiline refusal without history mutation,
  then creates `AI` with the bundled `osifont-lgpl3fe` as one handle plus 22
  exact glyph curves (13 B-splines and nine lines) owned by a 23-element Text
  constraint. It proves the newest named transaction at FreeCAD's twenty-entry
  undo limit, one-step undo/redo, unchanged active Sketch/ribbon/workbench
  identity, the new read-only constraint properties, and all twenty-nine
  operations after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text`.
  The rebuilt Sketcher core and VibeCAD scripts are green, the focused contract
  suite is 1,208/1,208 green with four intentional skips, the 527-action live
  ribbon gate remains green, source and built runtime copies are byte-identical,
  and the touched Python files pass Ruff. The protected Sketcher VibeScript
  lifecycle emits `VIBECAD_SKETCHER_VIBESCRIPT_FINAL_OK_TRUE`, and all 17
  protected Part Design phases emit `VIBECAD_VIBESCRIPT_FINAL_OK_TRUE`, both
  with exit code zero.
- Sketch Construction is the thirtieth exact `sketch.geometry` variant and
  maps only `Sketcher_ToggleConstruction`; at that checkpoint,
  automatic/general Dimension was the next unfinished Sketch action and the
  production surface remained fail-closed there. The shipped handler was
  traced before implementation:
  without a selection it changes only the human's ephemeral creation mode,
  which Native does not expose; with a selection it durably toggles ordinary
  internal geometry, redirects grouped members through their group handle,
  refuses internal-alignment geometry, and uses the external geometry
  extension's `Defining` flag rather than the generic Construction bit.
  Standalone point geometry is supported, while a vertex selection on another
  curve is not misrepresented as a geometry target. The bounded 359-line
  domain therefore accepts only the active Sketch identity, exact internal,
  constraint, and external counts, and one through sixty-four distinct exact
  geometry indices with their expected current states. Its preflight freezes
  every internal geometry, constraint, and external-geometry record; it
  refuses stale states, axes, grouped members instead of their handle,
  internal-alignment geometry, missing external `Defining` support, count
  drift, duplicate targets, and any unrelated topology or metadata change.
  It calls the same `toggleConstruction` primitive as the human command and
  reports only the changed indices, state kinds, previous/current states,
  solver/profile summary, and transaction receipt. Active Sketch state now
  includes bounded exact external-geometry records and omits FreeCAD's noisy
  literal `InternalType == "None"`. The thirty-operation provider schema is
  exactly 11,917 bytes against the unchanged 65,536-byte hard limit. The real
  GUI case is 169 lines, the rolling orchestrator is 905 lines, and save/reopen
  verification remains isolated in its 164-line boundary module. The GUI gate
  proves stale-state, grouped-member, and internal-alignment refusal without
  history mutation; then it toggles an internal line's Construction state and
  an external edge's Defining state in one named transaction while preserving
  the human selection. It proves the twenty-entry undo cap, one-step undo/redo,
  exact concise response and receipt, unchanged active Sketch/ribbon/workbench,
  and both durable states plus all thirty operations after FCStd save/reopen.
  It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction`.
  Construction/schema/state tests are 49/49 green, and the full current
  `vibecad_tests` sweep is 1,561/1,561 green with four intentional skips. The
  rebuilt runtime passed the real GUI lifecycle, and the previously completed
  527-action live ribbon and protected Sketcher/Part Design VibeScript gates
  remain green.
- Sketch automatic/general Dimension is the first exact `sketch.constraint`
  variant and maps only `Sketcher_Dimension`; at that checkpoint, horizontal
  Distance was the next unfinished Sketch action and the production surface
  remained fail-closed there. The shipped interactive command was traced
  before implementation.
  Its cursor location, selection shape, and mode cycling can select unrelated
  dimensional and geometric constraint families, so Native exposes only four
  deterministic outcomes: `distance_x`, `distance_y`, `distance`, and `angle`.
  It explicitly refuses diagonal single-line and two-point projection cases,
  radius-versus-diameter cases, non-dimensional mode cycling, more than two
  selected elements, unsupported conics and whole B-splines, and degenerate
  zero/coincident/tangent/collinear cases instead of guessing the human's
  cursor or preference state. The exact request names one or two geometry
  elements and their whole/start/end/center positions, the expected inferred
  kind, exact internal/constraint/external counts, an explicit driving state,
  and a unit-bearing dimension. Stale or surprising inference is rejected
  before mutation. Reference dimensions treat the supplied value as an
  expected current measurement and refuse measurement drift.
  The bounded 674-line domain uses the same point/curve constructor forms and
  line-angle endpoint orientation as the human command. It supports root axes
  only where their semantics are defined, refuses grouped members,
  internal-alignment geometry and unavailable external geometry, preserves the
  human selection, and executes one named document transaction. Its
  postcondition freezes all pre-existing topology, metadata, external geometry
  and unrelated constraints while allowing the solver to move existing
  coordinates and update pre-existing reference measurements. It then proves
  the exact new constraint, requested driving state, solver health, counts,
  profile state, and concise transaction receipt. Exact-state helpers and
  target parsing and append proof are isolated in 124-, 328-, and 257-line
  modules shared with later constraints; the real GUI case is 222 lines,
  save/reopen remains behind a 170-line boundary, and the rolling orchestrator
  was split to 690 lines before adding the next lifecycle case. The current
  two-operation constraint schema is exactly 2,082 bytes; both Sketch
  tool-family schemas together are 13,998 bytes against the unchanged
  65,536-byte hard limit.
  The GUI gate proves ambiguous-inference, stale-inference, and unit-mismatch
  refusal without history mutation; then it creates an exact driving
  horizontal distance, proves the solver changes a 10 mm line to 20 mm,
  preserves the human selection, reaches FreeCAD's twenty-entry undo cap,
  proves one-step undo/redo, and verifies all thirty geometry operations plus
  the inferred dimension after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension`.
  Dimension/schema tests are 34/34 green, and the full current
  `vibecad_tests` sweep is 1,595/1,595 green with four intentional skips. The
  rebuilt runtime passed the real GUI lifecycle, the 527-action live ribbon
  gate remains green, source and built runtime copies are byte-identical, and
  touched Python passes Ruff. The protected Sketcher VibeScript lifecycle and
  all 17 protected Part Design phases both complete with exit code zero; the
  Part Design result reports `"ok": true`.
- Sketch horizontal Distance is the second exact `sketch.constraint` variant
  and maps only `Sketcher_ConstrainDistanceX`; vertical Distance is now the
  next unfinished Sketch action and the production surface remains fail-closed
  there. The shipped human command was traced before implementation. Native
  preserves its three durable forms: one exact point receives a signed X
  coordinate relative to the origin, one whole line is converted to its two
  endpoints, and two exact points receive a positive horizontal separation
  after the same negative-sign endpoint normalization as the human command.
  Signed and zero point coordinates are valid. Zero two-point projection,
  axes selected as whole lines, the origin selected alone, unsupported whole
  curves, whole elements in a two-point request, duplicate targets, grouped
  members, internal-alignment geometry, missing external geometry, and stale
  counts or reference measurements are refused before mutation.
  The exact request names the active Sketch, internal/constraint/external
  counts, one or two whole/start/end/center elements, a millimetre value, and
  an explicit driving state. Reference mode requires the supplied value to
  match the current signed coordinate or normalized separation. Driving mode
  is never silently converted to reference mode. A 257-line shared constraint
  append module constructs the exact active/driving constraint and calls
  Sketcher's non-mutating `diagnoseAdditionalConstraints` before a document
  transaction opens. Conflicting, redundant, partially redundant, malformed,
  inconsistent, or unavailable feasibility results fail closed without an
  append. The feasibility call must preserve every geometry, constraint,
  external-geometry, and solver-diagnostic record. The 356-line operation
  domain then opens one named transaction, adds exactly one `DistanceX`, and
  proves the exact references, signed value, driving/active/virtual state,
  unchanged topology and metadata, stable unrelated constraints, solver
  health, and the measured solved result. Its concise response contains only
  operation, target form, exact constraint, before/after measurement, Sketch
  counts/profile summary, and receipt.
  Compact multi-operation provider schemas are now revalidated in the
  dispatcher against the selected variant's original closed schema before a
  ticket or runtime call is created. This preserves the 64-KiB surface cap
  without allowing a compact union to weaken operation-specific nested values
  or required fields. The constraint schema remains 2,082 bytes and both
  Sketch schemas remain 13,998 bytes. Constraint dispatch is 408 lines, the
  schema/runtime/binding files are 190/91/44 lines, the focused GUI case is 200
  lines, its ordered catalog helper is 95 lines, the rolling orchestrator is
  690 lines, and save/reopen is 170 lines.
  The real GUI gate refuses an axis target, an already constrained line through
  the solver-feasibility path, a stale reference measurement, and a wrong unit
  without history mutation. It then creates a point at X=-12 mm and applies an
  exact driving X coordinate of -30 mm while preserving the human selection.
  It proves the measured solver result, FreeCAD's twenty-entry undo cap,
  one-step undo/redo restoring both constraint and solved point position, exact
  receipt, unchanged active Sketch/ribbon/workbench, final 173-geometry and
  244-constraint counts, and all prior operations plus horizontal Distance
  after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension,constrain_distance_x`.
  The focused constraint/schema/dispatcher suite is 75/75 green, and the full
  current `vibecad_tests` sweep is 1,626/1,626 green with four intentional
  skips. The rebuilt runtime passes the rolling Sketch lifecycle; the
  representative completed Model bracket workflow and 527-action live ribbon
  gate remain green. Source and built runtime copies are byte-identical,
  touched Python passes Ruff, and `git diff --check` is clean. The protected
  Sketcher VibeScript lifecycle and all 17 protected Part Design phases both
  complete with exit code zero; the Part Design result reports `"ok": true`.
- Sketch vertical Distance is the third exact `sketch.constraint` variant and
  maps only `Sketcher_ConstrainDistanceY`; general Distance is now the next
  unfinished Sketch action and the production surface remains fail-closed
  there. The shipped human command was traced before implementation. Native
  preserves its three durable forms: one exact point receives a signed Y
  coordinate relative to the origin, one whole line is converted to its two
  endpoints, and two exact points receive a positive vertical separation
  after negative-sign endpoint normalization. Signed and zero point
  coordinates are valid. Equal-Y two-point targets are refused with guidance
  to use a Horizontal geometric constraint. Axes selected as whole lines, the
  origin selected alone, unsupported whole curves, whole elements in a
  two-point request, non-positive two-point values, grouped members,
  internal-alignment geometry, unavailable external geometry, stale counts,
  and stale reference measurements are all rejected before mutation.
  Reference mode requires the supplied signed coordinate or normalized
  separation to match the current measurement. Driving mode remains driving
  and is never silently converted to reference mode.
  Horizontal and vertical implementations now share one 413-line exact
  axis-distance core behind separate 63-line bindings. The definition accepts
  only the two internally consistent X/`DistanceX` and Y/`DistanceY`
  identities, and every preflight, creation, and verification boundary rejects
  cross-routed X/Y state. The shared core uses Sketcher's non-mutating solver
  feasibility diagnostic before opening a transaction, freezes every
  pre-existing geometry, constraint, external-geometry, and solver record,
  then adds and verifies exactly one active constraint with its exact
  references, value, driving state, solved measurement, and concise receipt.
  Constraint schema/runtime/binding files are 203/117/44 lines. The three
  constraint variants serialize to exactly 2,169 bytes, and the thirty
  geometry plus three constraint variants serialize together to exactly
  14,085 bytes against the unchanged 65,536-byte hard limit.
  The focused real-GUI case is 200 lines, the rolling orchestrator is 708
  lines, and save/reopen remains behind a 174-line boundary module. The gate
  refuses a whole-axis target, stale reference measurement, wrong unit, and a
  duplicate constraint without changing history. It then creates a point at
  Y=-14 mm, applies an exact driving Y coordinate of -35 mm, and proves the
  resulting `DistanceY`, measured solver result, preserved human selection,
  newest named transaction at FreeCAD's twenty-entry undo limit, exact receipt,
  one-step undo/redo, unchanged active Sketch/ribbon/workbench, final
  174-geometry and 245-constraint counts, and every prior operation plus
  vertical Distance after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension,constrain_distance_x,constrain_distance_y`.
  The focused constraint/schema/dispatcher suite is 105/105 green, and the
  full current `vibecad_tests` sweep is 1,653/1,653 green with four intentional
  skips. The rebuilt runtime passes the rolling Sketch lifecycle; the
  representative completed Model bracket workflow and 527-action live ribbon
  gate remain green. All packaged touched source/build copies are
  byte-identical, touched Python passes Ruff, and `git diff --check` is clean.
  The protected Sketcher VibeScript lifecycle and all 17 protected Part Design
  phases complete with exit code zero; the Part Design result reports
  `"ok": true`.
- Sketch general Distance is the fourth exact `sketch.constraint` variant and
  maps only `Sketcher_ConstrainDistance`; combined Radius/Diameter is now the
  next unfinished Sketch action and the production surface remains fail-closed
  there. The shipped command and its solver tests were traced before
  implementation. Native exposes all stable durable forms: signed
  horizontal-axis-to-point `DistanceY`, signed vertical-axis-to-point
  `DistanceX`, direct point-to-point `Distance`, whole-line length,
  whole-circular-arc length, point-to-line, point-to-circle or circular arc,
  circle or circular-arc to line, and circle or circular-arc to another circle
  or circular arc. Point/curve and circle/line selections are normalized to
  the exact constructor order used by Sketcher. Direct point distances and all
  non-axis lengths are positive; signed and zero axis coordinates remain
  valid.
  One point alone, one whole circle without a second curve, an axis length,
  two whole lines, unsupported whole curves, coincident points, zero-length
  lines, points already on curves, tangent curve pairs, stale counts, stale
  reference measurements, grouped members, internal-alignment geometry, and
  unavailable external geometry are refused before mutation. Sketcher's own
  solver suite marks negative driving circle/line secant distances as
  unsupported, so intersecting circle/line and circle/circle targets fail
  closed instead of silently converting intersection depth into a positive
  clearance and moving geometry to another branch. Explicit reference mode
  requires the supplied value to equal the current measurement; explicit
  driving mode is never silently converted to reference mode.
  The bounded 613-line domain freezes the exact active Sketch and all
  geometry, constraint, external-geometry, and solver records, constructs only
  the resolved Sketcher constraint form, calls the non-mutating solver
  feasibility diagnostic before a transaction opens, then proves the exact
  references, type, value, driving/active/virtual state, solved measurement,
  stable unrelated state, solver health, and concise receipt. Constraint
  schema/runtime/binding files are 216/143/44 lines. The four constraint
  variants serialize to exactly 2,248 bytes, and the thirty geometry plus four
  constraint variants serialize together to exactly 14,164 bytes against the
  unchanged 65,536-byte hard limit.
  The focused real-GUI case is 220 lines, the rolling orchestrator is 720
  lines, and save/reopen remains behind a 176-line boundary module. The gate
  refuses an incomplete point target, stale reference value, wrong unit, two
  whole lines, and a duplicate exact length without history mutation. It then
  creates an isolated 5 mm line and applies an exact driving 13 mm whole-line
  `Distance` while preserving the human selection. It proves the measured
  solver result, newest named transaction at FreeCAD's twenty-entry undo
  limit, one-step undo/redo restoring the 5/13 mm states, exact receipt,
  unchanged active Sketch/ribbon/workbench, final 175-geometry and
  246-constraint counts, and every prior operation plus general Distance after
  FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension,constrain_distance_x,constrain_distance_y,constrain_distance`.
  The focused constraint/schema/dispatcher suite is 137/137 green, and the
  full current `vibecad_tests` sweep is 1,685/1,685 green with four intentional
  skips. The rebuilt runtime passes the clean rolling Sketch lifecycle; the
  representative completed Model bracket workflow and 527-action live ribbon
  gate remain green. All packaged touched source/build copies are
  byte-identical, touched Python passes Ruff, and `git diff --check` is clean.
  The protected Sketcher VibeScript lifecycle and all 17 protected Part Design
  phases complete with exit code zero; the Part Design result reports
  `"ok": true`.
- Sketch combined Radius/Diameter is the fifth exact `sketch.constraint`
  variant and maps only `Sketcher_ConstrainRadiam`; explicit Radius is now the
  next unfinished Sketch action and the production surface remains fail-closed
  there. The shipped command was traced before implementation. For normal
  geometry it deterministically creates `Diameter` on a whole circle and
  `Radius` on a whole circular arc. Its human multi-selection convenience adds
  `Equal` constraints before one size constraint, while a B-spline control
  handle is actually a `Weight` target. Native keeps those semantics explicit:
  this operation accepts one exact whole normal curve and requires the caller
  to state the expected `radius` or `diameter` result. Multi-curve sizing is
  refused with guidance to use the separately scoped Equal operation, and
  B-spline-owned internal geometry remains unavailable here because pole
  Weight is its own shipped action at step 10.82.
  The exact request includes the active Sketch, internal/constraint/external
  counts, one whole geometry index, expected constraint kind, positive
  millimetre value, and explicit driving state. Exact target-kind mismatch,
  point or axis positions, lines, standalone points, ellipses, grouped members,
  internal-alignment geometry, unavailable external geometry, stale counts,
  and stale reference values are rejected before mutation. Explicit reference
  mode requires the supplied diameter or radius to match the current
  measurement; explicit driving mode is never silently converted to reference
  mode.
  The bounded 320-line domain freezes every pre-existing geometry, constraint,
  external-geometry, and solver record, constructs exactly one `Diameter` or
  `Radius`, calls the non-mutating solver feasibility diagnostic before a
  transaction opens, then proves its exact reference, value,
  driving/active/virtual state, solved measurement, stable unrelated state,
  solver health, and concise receipt. Constraint schema/runtime/binding files
  are 287/170/44 lines. The five constraint variants serialize to exactly
  2,563 bytes, and the thirty geometry plus five constraint variants serialize
  together to exactly 14,479 bytes against the unchanged 65,536-byte hard
  limit.
  The focused real-GUI case is 229 lines, the rolling orchestrator is 732
  lines, and save/reopen remains behind a 178-line boundary module. The gate
  refuses an incorrect expected kind, stale reference value, wrong unit, line
  target, multi-target request, and duplicate exact constraint without history
  mutation. It then creates an isolated radius-5 mm circle and applies an exact
  driving 16 mm `Diameter`, proving the solved radius is 8 mm, preserving the
  human selection, reaching FreeCAD's twenty-entry undo limit, and retaining
  exact receipt, one-step undo/redo, active Sketch/ribbon/workbench identity,
  final 176-geometry and 247-constraint counts, and every prior operation plus
  combined Radius/Diameter after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension,constrain_distance_x,constrain_distance_y,constrain_distance,constrain_radius_diameter`.
  The focused constraint/schema/dispatcher suite is 164/164 green, and the
  full current `vibecad_tests` sweep is 1,712/1,712 green with four intentional
  skips. The rebuilt runtime passes the rolling Sketch lifecycle; the
  representative completed Model bracket workflow and 527-action live ribbon
  gate remain green. All packaged touched source/build copies are
  byte-identical, touched Python passes Ruff, and `git diff --check` is clean.
  The protected Sketcher VibeScript lifecycle and all 17 protected Part Design
  phases complete with exit code zero; the Part Design result reports
  `"ok": true`.
- Sketch Radius is the sixth exact `sketch.constraint` variant and maps only
  `Sketcher_ConstrainRadius`; explicit Diameter is the next unfinished Sketch
  action, so the production Sketch surface remains fail-closed there. The
  shipped Radius command was traced before implementation: it creates a
  `Radius` constraint for either a whole circle or whole circular arc, its
  multi-selection driving shortcut adds `Equal` constraints before one Radius,
  and a B-spline control handle is actually a `Weight` target. Native keeps
  those distinct operations explicit. This Radius operation accepts one exact
  whole normal circle or circular arc, requires Equal to be called separately
  for a size group, and leaves pole Weight to its own shipped action at step
  10.82. It never uses the combined command's circle-to-Diameter inference.
  The exact request contains the active Sketch identity, current
  geometry/constraint/external-geometry counts, one whole geometry index, a
  positive millimetre radius, and explicit driving state. Point and axis
  positions, lines, points, ellipses, multi-target requests, grouped members,
  internal-alignment geometry, unavailable external geometry, stale counts,
  stale reference values, extra combined-command fields, solver rejection,
  preflight drift, and postcondition drift are rejected without mutation.
  Reference mode requires the supplied radius to equal the current
  measurement; driving mode is never silently converted to reference mode.
  The completed combined and explicit commands now share one 379-line exact
  circular-size lifecycle instead of duplicating two domain implementations.
  The combined and Radius bindings are only 55 and 51 lines. The shared core
  freezes every pre-existing geometry, constraint, external-geometry, and
  solver record; resolves the command-specific `Radius`/`Diameter` form; calls
  the non-mutating solver diagnostic before a transaction opens; and proves
  the exact type, reference, value, driving/active/virtual state, solved
  measurement, unchanged unrelated state, solver health, and concise receipt.
  Constraint schema/runtime/binding files are 310/196/44 lines. The six
  constraint variants serialize to exactly 2,634 bytes, and the thirty
  geometry plus six constraint variants total 14,551 bytes against the
  unchanged 65,536-byte hard limit.
  The focused real-GUI Radius case is 232 lines, the rolling orchestrator is
  745 lines, and save/reopen remains isolated in a 180-line module. The gate
  refuses a center target, stale reference value, wrong unit, line target,
  multi-target request, unexpected combined-command field, and duplicate
  constraint without history mutation. It then creates an isolated radius-4
  mm circle and applies an exact driving 7.5 mm `Radius`, which specifically
  proves the explicit command does not produce `Diameter` for circles. It
  preserves the human selection and active Sketch/ribbon/workbench, reaches
  FreeCAD's twenty-entry undo limit, and proves the exact receipt, named
  transaction, one-step undo/redo restoring 4/7.5 mm states, final
  177-geometry and 248-constraint counts, and every prior operation plus
  explicit Radius after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension,constrain_distance_x,constrain_distance_y,constrain_distance,constrain_radius_diameter,constrain_radius`.
  The focused constraint/schema/dispatcher suite is 189/189 green, and the
  full current `vibecad_tests` sweep is 1,737/1,737 green with four intentional
  skips. The rebuilt runtime passes the clean rolling Sketch lifecycle; the
  representative completed Model bracket workflow and 527-action live ribbon
  gate remain green. All packaged touched source/build copies are
  byte-identical, touched Python passes Ruff, and `git diff --check` is clean.
  The protected Sketcher VibeScript lifecycle and all 17 protected Part Design
  phases complete with exit code zero; the Part Design result reports
  `"ok": true`.
- Sketch Diameter is the seventh exact `sketch.constraint` variant and maps
  only `Sketcher_ConstrainDiameter`; Angle is the next unfinished Sketch
  action, so the production surface remains fail-closed there. The shipped
  command was traced before implementation: it creates `Diameter` for both
  whole circles and whole circular arcs, rejects B-spline Weight handles, and
  uses the same human multi-selection Equal shortcut as the other size
  commands. Native accepts one exact whole normal circle or circular arc,
  requires Equal to be invoked separately for multi-curve sizing, and rejects
  internal B-spline control geometry. It never aliases the combined command,
  whose arc behavior would incorrectly create `Radius`.
  The request carries the active Sketch identity, exact current
  geometry/constraint/external-geometry counts, one whole geometry index, a
  positive millimetre diameter, and explicit driving state. Point and axis
  positions, lines, points, ellipses, multi-target requests, grouped members,
  internal-alignment geometry, unavailable external geometry, stale counts,
  stale reference measurements, extra combined-command fields, solver
  rejection, preflight drift, and postcondition drift all fail without
  mutation. Reference mode requires an exact current diameter; driving mode is
  never silently converted to reference mode.
  Combined Radius/Diameter, explicit Radius, and explicit Diameter now share a
  385-line circular-size lifecycle, with respective 55/51/51-line bindings.
  The core selects only the command-specific constraint form, freezes all
  pre-existing geometry/constraint/external and solver state, performs the
  non-mutating feasibility diagnostic before the transaction, and proves the
  exact reference, type, value, driving/active/virtual state, solved
  measurement, stable unrelated state, solver health, and receipt after the
  append. Constraint schema/runtime/binding files are 326/222/44 lines. The
  seven constraint variants serialize to 2,713 bytes, and the thirty geometry
  plus seven constraint variants total 14,630 bytes against the unchanged
  65,536-byte limit.
  The focused real-GUI Diameter case is 237 lines, the rolling orchestrator is
  757 lines, and save/reopen remains isolated in 182 lines. The gate refuses a
  center target, stale reference value, wrong unit, line target, multi-target
  request, unexpected combined-command field, and duplicate constraint with
  no history mutation. It then creates an isolated radius-3 mm circular arc
  and applies an exact driving 10 mm `Diameter`, proving the solved radius is 5
  mm and, critically, that the explicit action does not use combined
  Radius-on-arc behavior. It preserves the human selection and active
  Sketch/ribbon/workbench, reaches the twenty-entry undo limit, and proves the
  exact receipt, named transaction, one-step undo/redo restoring 3/5 mm
  radius states, final 178-geometry and 249-constraint counts, and all earlier
  operations plus Diameter after FCStd save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension,constrain_distance_x,constrain_distance_y,constrain_distance,constrain_radius_diameter,constrain_radius,constrain_diameter`.
  The focused constraint/schema/dispatcher suite is 214/214 green, and the
  full current `vibecad_tests` sweep is 1,762/1,762 green with four intentional
  skips. The rebuilt runtime passes the clean rolling Sketch lifecycle; the
  representative Model bracket workflow and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript lifecycle and all 17 protected Part Design phases complete with
  exit code zero; the Part Design result reports `"ok": true`.
- Sketch Angle is the eighth exact `sketch.constraint` variant and maps only
  `Sketcher_ConstrainAngle`; Lock is now the next unfinished Sketch action, so
  the production surface remains fail-closed there. The shipped command and
  its `calculateAngle` endpoint-selection logic were traced before the
  implementation was accepted. Native exposes four explicit durable forms:
  signed orientation for one whole non-axis line, positive span for one whole
  circular arc, a positive internal angle between two exact directed line or
  axis rays, and `AngleViaPoint` for two whole curves through one exact curve
  point. Negative line-line and via-point measurements normalize by swapping
  the two curve references, exactly preserving the positive Sketcher
  constraint branch.
  Unlike the human convenience command, Native does not silently append one
  or more hidden `PointOnObject` constraints for an angle-via-point request.
  The selected point must already lie geometrically on both curves; otherwise
  the request fails with guidance to constrain that topology first. This
  preserves the one-call/one-exact-append contract and leaves Coincident and
  point-on-object behavior to their own explicit actions. Line-line requests
  require `start` or `end` for each normal line ray, while axes are named as
  whole inputs and normalized to their positive root rays. Axis-only
  orientation, whole line-line inputs, non-lines, parallel or collinear rays,
  zero-length lines, non-circular arcs, degenerate arc spans, unsupported
  via-point curves, inexact/off-curve points, internal B-spline geometry,
  duplicate elements, stale counts, stale reference measurements, invalid
  units, form/count mismatches, form-specific out-of-range values, unavailable
  host queries, solver rejection, preflight drift, and postcondition drift are
  all refused without mutation. Reference mode requires the requested value
  to match the exact current measurement; driving mode is never silently
  converted to reference mode.
  The bounded 600-line Angle domain freezes all pre-existing geometry,
  constraint, external-geometry, and solver records, resolves only the named
  form, constructs exactly one `Angle` or `AngleViaPoint`, calls Sketcher's
  non-mutating feasibility diagnostic before opening a transaction, and then
  proves the exact type, ordered references, branch, radians value,
  driving/active/virtual state, solved measurement, stable unrelated state,
  solver health, and concise receipt. Constraint schema/runtime/target files
  are 402/249/328 lines. The eight constraint variants serialize to exactly
  3,019 bytes, and the thirty geometry plus eight constraint variants serialize
  together to exactly 14,935 bytes against the unchanged 65,536-byte limit.
  The fake-host proof is isolated behind a 168-line Angle mixin, and 46 focused
  Angle tests cover every supported form and the refusal boundaries above.
  The real-GUI case is 365 lines, the rolling orchestrator is 769 lines, and
  save/reopen remains isolated in 184 lines. The gate creates two exact lines
  at 60 degrees, refuses a whole ray, stale reference value, wrong unit,
  wrong form, parallel axis ray, duplicate element, and redundant finished
  constraint without history mutation, then applies one exact driving
  45-degree line-line `Angle`. It proves the exact ordered references and
  radians value, preserved human selection and active Sketch/ribbon/workbench,
  newest named transaction at FreeCAD's twenty-entry undo limit, exact receipt,
  one-step undo/redo restoring the 60/45-degree states, final 180-geometry and
  250-constraint counts, and every earlier operation plus Angle after FCStd
  save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension,constrain_distance_x,constrain_distance_y,constrain_distance,constrain_radius_diameter,constrain_radius,constrain_diameter,constrain_angle`.
  The focused constraint/schema/dispatcher suite is 262/262 green, and the
  full current `vibecad_tests` sweep is 1,810/1,810 green with four intentional
  skips. The rebuilt runtime passes the clean rolling Sketch lifecycle; the
  representative Model bracket workflow and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript lifecycle finishes fully constrained, and all 17 protected Part
  Design phases complete with exit code zero and `"ok": true`.
- Sketch Lock is the ninth exact `sketch.constraint` variant and maps only
  `Sketcher_ConstrainLock`; the live unified Coincident action is now the next
  unfinished Sketch action, so production remains fail-closed at
  `Sketcher_ConstrainCoincidentUnified`. The shipped command was traced before
  implementation. It does not create a constraint named Lock: one selected
  point receives an absolute `DistanceX` plus `DistanceY`, while multiple
  selected vertices use the last point as a reference and add a relative
  `DistanceX`/`DistanceY` pair for every earlier point.
  Native preserves those intrinsic two-constraint semantics without exposing
  the human command's unbounded fan-out. One call explicitly chooses either
  one absolute point or one ordered target/reference point pair. Additional
  relative targets are separate semantic calls. The two forms are a closed
  nested schema union, so absolute position fields cannot appear in a relative
  request and relative reference/offset fields cannot appear in an absolute
  request. Both forms require the expected current signed X/Y position or
  reference-minus-target offset in millimetres. The expectation must already
  match the live geometry for driving and reference modes: Lock freezes the
  current relationship and is never repurposed as a move operation.
  Exact line endpoints, curve centers/endpoints, standalone points, external
  points, signed and zero values, and the Sketch origin as a relative reference
  are supported. The origin as the target, whole geometry, duplicate target
  and reference points, mixed or incomplete forms, stale measurements, invalid
  or unbounded coordinates, grouped members, internal-alignment geometry,
  unavailable point lookup, stale topology, solver rejection, an inexact
  two-proposal feasibility result, preflight drift, and postcondition drift all
  fail without mutation. Driving mode remains driving and reference mode
  remains reference; fixed/external state is never used to silently change the
  requested mode.
  The bounded 371-line Lock domain freezes all pre-existing geometry,
  constraint, external-geometry, and solver records, constructs exactly the
  ordered X/Y pair, diagnoses both proposals together before a transaction
  opens, appends both through one host call, and proves both exact indices,
  types, ordered references, values, driving/active/virtual state, unchanged
  measurement, stable unrelated state, solver health, and one concise receipt.
  The shared constraint-append module is now a 357-line sequence-capable core:
  existing one-constraint operations retain their exact wrapper while Lock
  uses the same diagnostic, append, and verification invariants for two
  constraints. Constraint schema/runtime files are 487/274 lines. The nine
  constraint variants serialize to exactly 5,123 bytes, and the thirty
  geometry plus nine constraint variants serialize together to exactly 17,039
  bytes against the unchanged 65,536-byte limit.
  Thirty-one focused Lock tests cover absolute/relative driving and reference
  pairs, origin and external references, signed offsets, schema discrimination,
  exact pair diagnostics, and every refusal boundary above. The real-GUI case
  is 257 lines, the rolling orchestrator is 781 lines, and save/reopen remains
  isolated in 186 lines. The gate creates one exact point, refuses a stale
  position, whole target, origin target, mixed form, out-of-range coordinate,
  and redundant finished Lock without history mutation, then appends exact
  driving `DistanceX` and `DistanceY` constraints in one named semantic
  transaction. It proves the point remains at 290/160 mm, exact constraint
  values and references, preserved human selection and active
  Sketch/ribbon/workbench, FreeCAD's twenty-entry undo cap, one-step undo
  removing both constraints, one-step redo restoring both, final 181-geometry
  and 252-constraint counts, and every earlier operation plus Lock after FCStd
  save/reopen. It emits
  `VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK operations=create_point,create_line,create_polyline,create_arc,create3_point_arc,create_arc_of_ellipse,create_arc_of_hyperbola,create_arc_of_parabola,create_circle,create3_point_circle,create_ellipse,create3_point_ellipse,create_rectangle,create_center_rectangle,create_oblong,create_triangle,create_square,create_pentagon,create_hexagon,create_heptagon,create_octagon,create_regular_polygon,create_slot,create_arc_slot,create_b_spline,create_periodic_b_spline,create_b_spline_by_interpolation,create_periodic_b_spline_by_interpolation,create_text,toggle_construction,infer_dimension,constrain_distance_x,constrain_distance_y,constrain_distance,constrain_radius_diameter,constrain_radius,constrain_diameter,constrain_angle,constrain_lock`.
  The focused constraint/schema/dispatcher suite is 295/295 green, and the
  full current `vibecad_tests` sweep is 1,843/1,843 green with four intentional
  skips. The rebuilt rolling Sketch lifecycle, representative Model bracket
  workflow, and 527-action live ribbon gate are green. All packaged touched
  source/build copies are byte-identical, touched Python passes Ruff, and
  `git diff --check` is clean. The protected Sketcher VibeScript lifecycle
  finishes fully constrained, and all 17 protected Part Design phases complete
  with exit code zero and `"ok": true`.
- Sketch Coincident is the tenth exact `sketch.constraint` variant and maps
  only the live `Sketcher_ConstrainCoincidentUnified` action. The shipped
  command was traced through every selection branch before implementation.
  Native exposes its three durable semantics as a closed nested union:
  `point_point` creates one exact `Coincident` between two explicit points,
  `point_on_object` creates one exact `PointOnObject` from an explicit point to
  one whole curve or axis, and `concentric` normalizes two explicit whole
  circles, circular arcs, ellipses, or elliptical arcs to exact center-point
  `Coincident` references. One call always names one ordered pair. The human
  command's unbounded selection fan-out is split into separate semantic calls,
  and its destructive Tangent-replacement branch is refused with direction to
  use the future explicit Tangent operation rather than silently deleting an
  existing constraint. Production remains fail-closed at the next live action,
  `Sketcher_ConstrainHorVer`.
  The 461-line domain freezes all geometry, constraints, external geometry,
  and solver diagnostics before mutation, diagnoses the exact one-constraint
  proposal outside a transaction, revalidates the target, appends exactly one
  constraint, and proves its index, type, ordered references, driving/active/
  virtual state, absent dimensional value, unchanged unrelated metadata, and
  solved geometric postcondition. Already coincident points, already-on-curve
  points, already-concentric conics, same-geometry non-B-spline endpoints,
  wrong point/whole positions, unsupported conics, point geometry used as a
  curve, own-curve targets, mixed or incomplete forms, hidden Tangent
  substitution, grouped members, internal-alignment geometry, missing or
  detached external geometry, unavailable host queries, stale topology,
  solver rejection, feasibility side effects, and preflight/postcondition
  drift all fail without opening or retaining a mutation.
  Thirty-five focused domain tests plus exact schema tests cover all three
  forms, all four concentric conic types, the origin, axes, external geometry,
  B-spline endpoints, schema discrimination, and the refusal boundaries above.
  The isolated real-GUI case is 460 lines, the rolling orchestrator remains 794
  lines, and save/reopen remains isolated in 190 lines. The gate creates two
  distinct points and makes them coincident, places another point on the fixed
  horizontal axis, and makes two new circles concentric. It proves exact
  serialized `Coincident` and `PointOnObject` constructors, pre/post geometric
  measurements, preserved human selection and active Sketch/ribbon/workbench,
  named transactions and receipts, one-step undo/redo for every form, and all
  three constraints after FCStd save/reopen at final 186-geometry and
  255-constraint counts. It emits the complete rolling marker ending in
  `constrain_angle,constrain_lock,constrain_coincident`.
  The focused constraint/schema/dispatcher suite is 332/332 green, and the
  full current `vibecad_tests` sweep is 1,880/1,880 green with four intentional
  skips. The ten constraint variants serialize to exactly 8,056 bytes; the
  thirty geometry plus ten constraint variants total 19,973 bytes against the
  unchanged 65,536-byte cap. The rebuilt rolling Sketch lifecycle,
  representative Model bracket workflow, and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript lifecycle finishes fully constrained at DoF zero, and all 17
  protected Part Design phases complete with exit code zero and `"ok": true`.
- Automatic Horizontal/Vertical is the eleventh exact `sketch.constraint`
  variant and maps only the live `Sketcher_ConstrainHorVer` action. The
  shipped command was traced through its complete selection and inference
  logic before implementation. Native exposes the two durable one-target
  forms: one exact whole straight line, or one exact ordered pair of points.
  It measures the target delta and infers Horizontal when `abs(dx) > abs(dy)`
  and Vertical when `abs(dy) > abs(dx)`. The request must state that expected
  inference explicitly, so stale or misunderstood geometry is refused before
  mutation. Exactly diagonal and numerically ambiguous targets are refused
  instead of inheriting the human command's arbitrary Vertical tie-break. The
  human command's multi-edge fan-out and multi-point chain construction are
  split into separate semantic calls. Production remains fail-closed at the
  next live action, `Sketcher_ConstrainHorizontal`.
  The 391-line domain freezes geometry, constraints, external geometry, and
  solver diagnostics before mutation, diagnoses one exact proposal outside a
  transaction, revalidates the target, appends exactly one `Horizontal` or
  `Vertical` constraint, and proves its index, type, ordered references,
  driving/active/virtual state, absent dimensional value, unchanged unrelated
  metadata, and solved geometric postcondition. Zero-length lines, coincident
  point pairs, exact or near-diagonal ambiguity, stale expected inference,
  non-line whole targets, wrong point/whole positions, origin or axis used as
  a whole target, redundant Horizontal/Vertical/Block constraints, grouped
  members, internal-alignment geometry, missing host queries, stale topology,
  solver rejection, feasibility side effects, and preflight/postcondition
  drift all fail without opening or retaining a mutation.
  Thirty-one focused domain tests plus exact schema tests cover both forms,
  both inference directions, signed deltas, the origin, axes, external
  geometry, center points, and the refusal boundaries above. The isolated
  real-GUI case is 393 lines, the rolling orchestrator is 808 lines, and
  save/reopen remains isolated in 194 lines. The gate first refuses a truly
  diagonal line, then proves stale-inference, nonwhole-target, and stale-count
  refusals on a near-horizontal line before appending one exact Horizontal
  constraint. It then appends one exact Vertical constraint between two
  points. It proves pre/post deltas, exact serialized constructors, preserved
  human selection and active Sketch/ribbon/workbench, named transactions and
  receipts, one-step undo/redo for each form, and both constraints after FCStd
  save/reopen at final 190-geometry and 257-constraint counts. It emits the
  complete rolling marker ending in
  `constrain_coincident,constrain_horizontal_vertical`.
  The focused constraint/schema/dispatcher suite is 365/365 green, and the
  full current `vibecad_tests` sweep is 1,913/1,913 green with four intentional
  skips. The individual Horizontal/Vertical schema is exactly 1,772 bytes;
  all eleven constraint variants serialize to exactly 10,030 bytes, and the
  thirty geometry plus eleven constraint variants total 21,947 bytes against
  the unchanged 65,536-byte cap. The rebuilt rolling Sketch lifecycle,
  representative Model bracket workflow, and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript lifecycle finishes fully constrained at DoF zero, and all 17
  protected Part Design phases complete with exit code zero and `"ok": true`.
- Explicit Horizontal is the twelfth exact `sketch.constraint` variant and
  maps only the live `Sketcher_ConstrainHorizontal` action. The shared shipped
  `horVerActivated` and `horVerApplyConstraint` paths were traced through both
  explicit-Horizontal selection sequences before implementation. Native keeps
  the two durable one-target forms: one exact whole internal straight line
  creates `Sketcher.Constraint('Horizontal', geometry_index)`, and one exact
  ordered point pair creates the five-reference Horizontal constructor. One
  call always creates one constraint. The human command's multi-edge fan-out
  and adjacent-pair construction across an arbitrary point sequence are split
  into separate semantic calls. The closed explicit contract deliberately has
  no inference or expected-inference field: a line or point pair that currently
  appears more vertical is still made Horizontal exactly as requested.
  Production remains fail-closed at the next live action,
  `Sketcher_ConstrainVertical`.
  Automatic and explicit axis alignment now share one 502-line lifecycle
  domain, while their operation-specific type guards and bindings remain in
  63- and 65-line modules. The common domain freezes geometry, constraints,
  external geometry, and solver diagnostics before mutation, diagnoses the
  exact one-constraint proposal outside a transaction, revalidates the target,
  appends exactly one constraint, and proves its index, type, ordered
  references, driving/active/virtual state, absent dimensional value,
  unchanged unrelated metadata, and solved zero-Y-delta postcondition. This
  factoring preserves the already completed automatic action without copying
  its mutation or verification machinery into Horizontal and the upcoming
  Vertical action. Zero-length lines, coincident point pairs, non-line whole
  targets, point/whole form mistakes, fixed axes used as whole targets,
  existing Horizontal/Vertical/Block constraints, grouped members,
  internal-alignment geometry, unavailable point or constraint queries, stale
  topology, solver rejection, feasibility side effects, and preflight or
  postcondition drift all fail without opening or retaining a mutation.
  Twenty-six focused Horizontal domain tests plus exact schema tests cover both
  constructor forms, signed direction, a deliberately vertical-looking line
  and point pair, the origin, external geometry, conic centers, the closed
  no-inference contract, and the refusal boundaries above. All thirty-one
  automatic Horizontal/Vertical tests remain green after the common-domain
  extraction. The isolated real-GUI Horizontal case is 337 lines, the rolling
  orchestrator is 823 lines, and save/reopen remains isolated in 198 lines.
  The gate creates a 2-by-8-mm line, proves axis, nonwhole, stale-count, and
  unexpected-inference refusals, then appends the exact line Horizontal
  constraint. It creates a 4-by-8-mm ordered point pair and appends the exact
  point-pair Horizontal constraint. It proves pre/post deltas, exact serialized
  constructors, preserved human selection and active Sketch/ribbon/workbench,
  named transactions and receipts, one-step undo/redo for each form, and both
  constraints after FCStd save/reopen at final 193-geometry and 259-constraint
  counts. It emits the complete rolling marker ending in
  `constrain_horizontal_vertical,constrain_horizontal`.
  The focused constraint/schema/dispatcher suite is 407/407 green, and the
  full current `vibecad_tests` sweep is 1,941/1,941 green with four intentional
  skips. The individual Horizontal schema is exactly 1,670 bytes; all twelve
  constraint variants serialize to exactly 10,197 bytes, and the thirty
  geometry plus twelve constraint variants total 22,114 bytes against the
  unchanged 65,536-byte cap. The rebuilt rolling Sketch lifecycle,
  representative Model bracket workflow, and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript lifecycle finishes fully constrained at DoF zero, and all 17
  protected Part Design phases complete with exit code zero and `"ok": true`.
- Explicit Vertical is the thirteenth exact `sketch.constraint` variant and
  maps only the live `Sketcher_ConstrainVertical` action. The shipped command
  delegates its one-edge and two-point selection sequences to the same traced
  handler as automatic alignment and explicit Horizontal. Native preserves
  those two durable one-target forms: one exact whole internal straight line
  creates `Sketcher.Constraint('Vertical', geometry_index)`, and one exact
  ordered point pair creates the five-reference Vertical constructor. One call
  always creates one constraint; arbitrary selected-edge fan-out and adjacent
  pair generation across a point sequence remain separate semantic calls. The
  explicit closed contract has no inference field, so a currently
  horizontal-looking target is still made Vertical exactly as requested.
  Production remains fail-closed at the next live action,
  `Sketcher_ConstrainParallel`.
  Vertical is a 60-line guarded binding over the same 502-line alignment
  lifecycle proven by automatic Horizontal/Vertical and explicit Horizontal.
  It therefore shares their frozen preflight state, side-effect-free exact
  solver diagnosis, one-constraint append, exact serialized-reference proof,
  solver-state comparison, and postcondition measurement without duplicating
  mutation logic. It proves a zero-X-delta result while retaining the same
  refusal policy for zero-length lines, coincident point pairs, non-line whole
  targets, point/whole form mistakes, fixed axes used as whole targets,
  existing Horizontal/Vertical/Block constraints, grouped members,
  internal-alignment geometry, unavailable host queries, stale topology,
  solver rejection, feasibility side effects, and preflight/postcondition
  drift. Every refusal occurs before a retained mutation.
  Twenty-six focused Vertical domain tests plus exact schema tests cover both
  constructors, signed direction, a deliberately horizontal-looking line and
  point pair, the origin, external geometry, conic centers, the closed
  no-inference contract, and all shared refusal boundaries. The isolated
  real-GUI Vertical case is 337 lines, the rolling orchestrator is 835 lines,
  and save/reopen remains isolated in 200 lines. The gate creates an 8-by-2-mm
  line, proves axis, nonwhole, stale-count, and unexpected-inference refusals,
  then appends the exact line Vertical constraint. It creates an 8-by-4-mm
  ordered point pair and appends the exact point-pair Vertical constraint. It
  proves pre/post deltas, exact serialized constructors, preserved human
  selection and active Sketch/ribbon/workbench, named transactions and
  receipts, one-step undo/redo for both forms, and both constraints after
  FCStd save/reopen at final 196-geometry and 261-constraint counts. It emits
  the complete rolling marker ending in
  `constrain_horizontal,constrain_vertical`.
  The focused constraint/schema/dispatcher suite is 435/435 green, and the
  full current `vibecad_tests` sweep is 1,969/1,969 green with four intentional
  skips. The individual Vertical schema is exactly 1,668 bytes; all thirteen
  constraint variants serialize to exactly 10,257 bytes, and the thirty
  geometry plus thirteen constraint variants total 22,174 bytes against the
  unchanged 65,536-byte cap. The rebuilt rolling Sketch lifecycle,
  representative Model bracket workflow, and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript lifecycle finishes fully constrained at DoF zero, and all 17
  protected Part Design phases complete with exit code zero and `"ok": true`.
- Parallel is the fourteenth exact `sketch.constraint` variant and maps only
  the live `Sketcher_ConstrainParallel` action. The shipped command was traced
  through its selection-mode and preselection paths: it accepts internal line
  pairs, one internal line with either axis, and one internal line with
  external line geometry, then creates
  `Sketcher.Constraint('Parallel', first_index, second_index)`. Native exposes
  exactly one ordered pair of distinct whole straight lines per call and
  requires at least one editable internal line. The human command's arbitrary
  selected-line chain is split into separate semantic calls rather than
  silently producing adjacent constraints. Input ordering is preserved in the
  exact constructor and result. Production remains fail-closed at the next
  live action, `Sketcher_ConstrainPerpendicular`.
  The 302-line domain freezes geometry, constraints, external geometry, and
  solver diagnostics before mutation; rejects invalid geometry before solver
  work; diagnoses one exact proposal without opening a transaction; rechecks
  the complete target; appends exactly one constraint; and proves index, type,
  ordered references, driving/active/virtual state, absent dimensional value,
  unchanged unrelated metadata, and the solved angular postcondition. Its
  concise result reports only the angular error to the nearest parallel
  direction before and after. Same-line targets, fewer or more than two
  targets, point rather than whole selections, non-line curves and points,
  zero-length lines, two fixed axes/external lines, an existing Parallel in
  either order, grouped members, internal-alignment geometry, unavailable host
  queries, stale or missing external geometry, stale topology, solver
  rejection, feasibility side effects, and preflight/postcondition drift all
  fail without a retained mutation.
  Twenty-seven focused domain tests plus exact schema tests cover internal
  pairs, both orderings with axes and external lines, already anti-parallel
  geometry without a constraint, exact serialized references, all closed
  contract failures, and the refusal boundaries above. Fake-host solver
  behavior is isolated in 41 lines rather than added to the shared test host.
  The isolated real-GUI Parallel case is 380 lines, the rolling orchestrator is
  847 lines, and save/reopen remains isolated in 202 lines. The gate first
  proves same-line, two-axis, nonwhole, and stale-count refusals. It then
  creates and independently proves an internal/internal pair, an internal line
  against the fixed horizontal axis, and an external/internal pair against the
  live imported external line. Every form proves pre/post angular error,
  exact ordered constructors, preserved human selection and active
  Sketch/ribbon/workbench, named transactions and receipts, independent
  one-step undo/redo, and all three constraints after FCStd save/reopen at
  final 200-geometry and 264-constraint counts. It emits the complete rolling
  marker ending in `constrain_vertical,constrain_parallel`.
  The focused constraint/schema/dispatcher suite is 464/464 green, and the
  full current `vibecad_tests` sweep is 1,998/1,998 green with four intentional
  skips. The individual Parallel schema is exactly 1,228 bytes; all fourteen
  constraint variants serialize to exactly 10,730 bytes, and the thirty
  geometry plus fourteen constraint variants total 22,647 bytes against the
  unchanged 65,536-byte cap. The rebuilt rolling Sketch lifecycle,
  representative Model bracket workflow, and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript lifecycle finishes fully constrained at DoF zero, and all 17
  protected Part Design phases complete with exit code zero and `"ok": true`.
- Perpendicular is the fifteenth exact `sketch.constraint` variant and maps
  only the live `Sketcher_ConstrainPerpendicular` action. The shipped command
  was traced through every selection branch in
  `Sketcher/Gui/CommandConstraints.cpp`: two whole curves, one endpoint and
  one curve, two endpoints, two points and one line, and two curves through
  one point. Native represents those meanings as five explicit discriminated
  forms; it never reads the human selection or invents hidden construction
  geometry. The explicit via-point form adds and reports only the required
  durable PointOnObject support constraints in the same atomic transaction.
  Production remains fail-closed at the next live action,
  `Sketcher_ConstrainTangent`.
  Real-host probing exposed an unsafe FreeCAD five-reference constructor: its
  feasibility diagnosis segfaults in `GCS::ConstraintPerpendicular::rescale`,
  and direct append alternatives either segfault or can retain a partially
  redundant constraint. Native therefore accepts the point-pair/line form
  only when the two points are the explicit start and end of one straight
  line, compiling it to the stable two-line constructor. Arbitrary point pairs
  refuse with a precise instruction to create the line first. Likewise, the
  human command's special two-conic branch is not imitated because it silently
  creates a construction point and supporting constraints; Native requires an
  explicit existing point and the via-point form instead.
  The 192-line domain, 462-line target validator, and 281-line measurement
  module freeze geometry, constraints, external geometry, and solver state;
  validate the exact form before diagnosis; recheck topology immediately
  before mutation; apply the complete constructor set atomically; and prove
  exact serialized references, constraint state, allowed orientation datum,
  supporting constraints, unchanged unrelated metadata, and the solved
  perpendicular postcondition. Invalid positions or curve classes, duplicate
  or stale targets, fixed-only targets, implicit conic helpers, unsupported
  arbitrary point pairs, solver rejection, feasibility side effects, and
  preflight or postcondition drift all refuse without a retained mutation.
  Twenty-four focused domain tests plus exact schema and fail-closed surface
  tests cover all five forms, constructor ordering, support-constraint
  reporting, state counts, atomic rollback, and every refusal boundary above.
  The real-GUI case independently proves line/line, line/circle,
  endpoint/curve, endpoint/endpoint, the safe point-pair/line form, and an
  ellipse/line via-point form. It also proves preserved human selection and
  active Sketch/ribbon/workbench, named transactions and receipts, exact
  one-step undo/redo, no mutation of earlier rolling geometry, and every
  result after FCStd save/reopen at final 216-geometry and 275-constraint
  counts. The rolling marker now ends in
  `constrain_parallel,constrain_perpendicular`.
  The focused constraint/schema/dispatcher suite is 473/473 green, and the
  full current `vibecad_tests` sweep is 2,024/2,024 green with four intentional
  skips. The individual Perpendicular schema is exactly 6,190 bytes; all
  fifteen constraint variants serialize to exactly 16,181 bytes, and the
  thirty geometry plus fifteen constraint variants total 28,097 bytes against
  the unchanged 65,536-byte cap. The rebuilt rolling Sketch lifecycle,
  representative Model bracket workflow, and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript lifecycle finishes fully constrained at DoF zero, and all 17
  protected Part Design phases complete with exit code zero and `"ok": true`.
- Tangent is the sixteenth exact `sketch.constraint` variant and maps only the
  live `Sketcher_ConstrainTangent` action. The shipped command was traced
  through every selection and substitution branch in
  `Sketcher/Gui/CommandConstraints.cpp`: two whole curves, one endpoint and
  one curve, two endpoints, two curves through one explicit point, and the
  hidden Coincident/PointOnObject/whole-Tangent substitutions performed by
  the human command family. Native exposes the four constructive meanings as
  `curve_curve`, `endpoint_curve`, `endpoint_endpoint`, and
  `curves_via_point`; destructive substitutions are separate
  `replace_with_endpoint_curve` and `replace_with_endpoint_endpoint` forms
  that require the exact current constraint index. Direct forms refuse and
  identify the replacement form and index instead of silently deleting a
  support constraint. Production remains fail-closed at the next live action,
  `Sketcher_ConstrainEqual`.
  Native never copies the human command's implicit conic helper-point branch.
  Whole-curve Tangent is limited to lines, circles, and circular arcs;
  ellipses, hyperbolas, and parabolas require an explicit existing point, and
  B-splines require an explicit endpoint or via-point form. Required
  PointOnObject support for a via-point target is appended and reported in the
  same transaction, while an exact existing support is reused. Every form
  requires an editable internal target and preserves caller ordering.
  Replacement preflight is non-mutating: Sketcher now provides the additive
  `diagnoseConstraintReplacement(index, constraints)` API, which evaluates a
  hypothetical remove-one/add-one solver set, returns the same complete
  diagnostics as `diagnoseAdditionalConstraints`, and restores the live
  solver state. The Sketcher target builds cleanly, its generated Python
  binding is present, and a serial real-host probe proves accepted endpoint
  Tangent replacement while geometry, constraint topology/type, and live
  diagnostics remain unchanged. Mutation then deletes only the named index,
  appends exactly one replacement, and verifies every surviving constraint
  after deterministic reindexing; no broad point-based deletion is used.
  The 270-line domain, 614-line target validator, 222-line measurement module,
  and 201-line shared curve-differential helper freeze geometry, constraints,
  external geometry, and solver state; diagnose the exact complete proposal;
  recheck the target immediately before mutation; and prove exact serialized
  references, driving/active/virtual state, allowed pointwise orientation
  datum, support constraints, unchanged unrelated topology and metadata,
  physical contact, point-on-curve support, and the solved tangent
  postcondition. Existing constraints in either order, wrong replacement
  indices or references, inactive/reference/virtual replacements, fixed-only
  targets, implicit helper geometry, stale state, solver rejection,
  feasibility side effects, and preflight or postcondition drift all refuse
  without a retained mutation.
  Fifty-one focused Tangent tests plus the exact schema and dispatcher gates
  cover all six forms, line/line, line/circle, circle/circle, explicit
  B-spline endpoints, support append/reuse, all four allowed replacement
  source/destination paths, preservation and reindexing of an unrelated
  same-point constraint, closed contracts, and every refusal boundary above.
  The complete exact-constraint suite is 515/515 green. Fake Tangent solving
  is isolated in a 265-line mixin and recognizes already-satisfied legacy
  Slot, Oblong, and Arc Slot tangencies without moving their geometry.
  The 685-line real-GUI case independently proves all constructive forms and
  all replacement source types. It proves failed direct substitutions create
  no undo entry; preserved human selection and active Sketch/ribbon/workbench;
  the named single transaction; exact replacement of a non-final constraint
  while its following Horizontal constraint survives and reindexes; atomic
  one-step undo/redo restoring both topology and geometry; and every result
  after FCStd save/reopen at final 240-geometry and 291-constraint counts. The
  rolling marker now ends in
  `constrain_perpendicular,constrain_tangent`.
  The full current `vibecad_tests` sweep is 2,077/2,077 green with four
  intentional skips. The individual Tangent schema is exactly 6,920 bytes;
  all sixteen constraint variants serialize to exactly 22,350 bytes, and the
  thirty geometry plus sixteen constraint variants total 34,266 bytes against
  the unchanged 65,536-byte cap. The rebuilt rolling Sketch lifecycle,
  representative Model bracket workflow, and 527-action live ribbon gate are
  green. All packaged touched source/build copies are byte-identical, touched
  Python passes Ruff, and `git diff --check` is clean. The protected Sketcher
  VibeScript integration returns zero, and all 17 protected Part Design phases
  report `PHASE_OK`, final exit code zero, and `"ok": true`.
- Equal is the seventeenth exact `sketch.constraint` variant and maps only the
  live `Sketcher_ConstrainEqual` action. The shipped command and solver paths
  were traced before implementation: line segments share length; circles and
  circular arcs share radius; ellipses/elliptical arcs and
  hyperbolas/hyperbolic arcs share both major and minor radii; parabolic arcs
  share focal length; and B-spline control-point handles share their owning
  pole weight. Whole B-spline curves, axes, unsupported or mixed families,
  group/internal geometry other than exact B-spline control-point handles, and
  selections containing more than one fixed or external target are refused.
  Native accepts an ordered chain of two through seventeen whole compatible
  edges and atomically appends adjacent Equal constraints. Direct and
  transitive membership in the existing Equal graph are both rejected before
  mutation rather than relying on the host solver's permissive redundant-call
  diagnosis.
  `VibeCADNativeSketchEqual.py` is a 175-line transaction domain,
  `VibeCADNativeSketchEqualMeasure.py` is a 196-line family/postcondition
  module, and `VibeCADNativeSketchEqualTarget.py` is a 305-line exact target
  validator. They freeze geometry, constraints, external geometry, solver
  state, owning B-spline alignment, and family measurements; diagnose the
  complete proposed constraint chain; recheck every target immediately before
  mutation; and prove exact serialized references, constraint flags, unchanged
  unrelated topology, and the family-specific solved postcondition afterward.
  Sketcher now exposes the existing C++
  `Constraint::InternalAlignmentIndex` as an additive read-only Python property
  so B-spline pole identity is inspected exactly rather than inferred from
  constraint order. The rebuilt Sketcher target and serial real-host probes
  prove all six supported families, including true B-spline pole-weight
  synchronization.
  Sixty-two focused Equal/schema tests cover all families, ordered chains,
  direct and transitive duplicate refusal, axes, wrong positions and counts,
  mixed and unsupported families, whole B-splines, malformed or mismatched
  pole owners, fixed/external limits, stale state, solver rejection,
  feasibility side effects, and preflight/postcondition drift without retained
  mutation. The complete Native Sketch suite is 804/804 green, and the full
  current `vibecad_tests` sweep is 2,106/2,106 green with four intentional
  skips. The individual Equal schema is exactly 1,226 bytes; all seventeen
  constraint variants serialize to exactly 22,815 bytes; and the thirty
  geometry plus seventeen constraint variants total 34,731 bytes against the
  unchanged 65,536-byte cap. To keep the next constraint from creating a
  monolith, the 993-line constraint schema was split without changing a single
  serialized byte: the capability module is now 743 lines, curve-relation
  schemas are 240 lines, and shared exact-element fragments are 33 lines.
  The focused real-GUI lifecycle proves all six families, atomic chain
  creation, named one-step undo/redo, preserved human selection and edit
  boundary, and FCStd save/reopen at 16 geometry and 11 constraints. The
  accumulated GUI lifecycle also proves the existing 240-geometry,
  291-constraint Sketch, then performs a human switch to a separate Equal
  Sketch and starts a newly frozen provider turn for that human-selected edit
  target; a stale turn is never reused across the context change. The final
  rolling marker now ends in `constrain_tangent,constrain_equal`. The rebuilt
  rolling Sketch lifecycle, representative Model bracket workflow, and
  527-action live ribbon gate are green. All twelve packaged touched
  source/build files are byte-identical, touched Python passes Ruff formatting
  and lint, and `git diff --check` is clean. The protected Sketcher VibeScript
  lifecycle finishes with four geometry, eleven constraints, and DoF zero;
  all 17 protected Part Design phases report `PHASE_OK`, final exit code zero,
  and `"ok": true`.
- Symmetric is the eighteenth exact `sketch.constraint` variant and maps only
  the live `Sketcher_ConstrainSymmetric` action. The shipped GUI command,
  Python constructors, and solver paths were traced before implementation.
  Native exposes four explicit forms rather than selection inference: two
  exact points about a whole straight line or Sketch axis, two exact points
  about an exact point or root, one open curve's endpoints about a whole
  straight line or axis, and one open curve's endpoints about an exact point.
  The line-reference form emits the exact five-reference host constructor and
  the point-reference form emits the exact six-reference constructor. Subject
  point order is canonical, and the curve forms expand to exact start/end
  references so an equivalent existing endpoint-pair Symmetric constraint is
  also refused as a duplicate.
  `VibeCADNativeSketchSymmetric.py` is a 180-line transaction domain,
  `VibeCADNativeSketchSymmetricMeasure.py` is a 148-line reflection and
  midpoint postcondition module, and
  `VibeCADNativeSketchSymmetricTarget.py` is a 334-line exact target validator.
  They support line segments, circular, elliptical, hyperbolic, and parabolic
  arcs, and non-periodic B-splines; full conics, periodic B-splines,
  non-straight symmetry lines, self-reference, own-endpoint references,
  group/internal geometry, duplicate definitions, all-fixed targets, and
  stale geometry, constraint, or external-reference counts are refused before
  mutation. Preflight freezes geometry, constraints, external geometry, and
  solver diagnostics; diagnoses the exact proposed constraint; proves that
  feasibility analysis had no side effect; rechecks the target immediately
  before mutation; appends exactly one constraint; and verifies exact
  serialized references, flags, unchanged unrelated topology and metadata,
  no new solver issues, reflection error, and midpoint error afterward.
  Seventy-seven focused Symmetric/schema tests cover all four forms, every
  supported curve family, root, both axes, internal and external line/point
  references, editable references with fixed subjects, order-independent and
  curve/endpoint duplicate detection, exact constructors, closed schemas,
  wrong fields, positions, counts, target types, degenerate lines, blocked,
  group, and internal geometry, solver rejection, incomplete diagnostics,
  feasibility side effects, preflight drift, postcondition drift, and exact
  runtime routing. The complete Native Sketch suite is 846/846 green, and the
  full current `vibecad_tests` sweep is 2,148/2,148 green with four intentional
  skips. The individual Symmetric schema is exactly 5,352 bytes; all eighteen
  constraint variants serialize to exactly 27,420 bytes; and the thirty
  geometry plus eighteen constraint variants total 39,336 bytes against the
  unchanged 65,536-byte cap. The next incomplete Block action remains the
  fail-closed surface boundary.
  The focused real-GUI lifecycle proves eleven independent Symmetric
  constraints across every form and supported open-curve family, root, axes,
  and external references. It also proves invalid and duplicate paths create
  no undo entry, human selection and the active
  Sketch/ribbon/workbench remain unchanged, the named transaction is one
  semantic undo/redo step, and all 21 geometry and 11 constraints survive
  exact FCStd save/reopen. The accumulated Sketch lifecycle performs another
  human edit-target switch to a separate Symmetric Sketch, freezes a new
  provider turn, replays the same case after the existing 240-geometry,
  291-constraint and Equal sketches, and reopens all three successfully. Its
  final marker now ends in
  `constrain_tangent,constrain_equal,constrain_symmetric`.
  The rebuilt Sketcher and VibeCAD script targets, representative Model bracket
  workflow, and 527-action live ribbon gate are green. All ten packaged
  touched source/build copies are byte-identical, touched Python passes Ruff,
  and `git diff --check` is clean. The protected Sketcher VibeScript lifecycle
  finishes with four geometry, eleven constraints, and DoF zero; all 17
  protected Part Design phases report `PHASE_OK`, final exit code zero, and
  `"ok": true`.
- Block is the nineteenth exact `sketch.constraint` variant and maps only the
  live `Sketcher_ConstrainBlock` action. The shipped GUI command, exact
  `Sketcher.Constraint("Block", index)` constructor, geometry-facade state,
  and solver behavior were traced before implementation. Native accepts a
  bounded ordered set of one through sixteen distinct, exact, whole, internal
  edges across every shipped primary edge family: line, circle, circular arc,
  ellipse, elliptical arc, hyperbolic arc, parabolic arc, and B-spline. It also
  accepts exact human-selectable ellipse major/minor, hyperbola major/minor,
  parabola focal-axis, and B-spline control-point internal handles. Axes,
  external geometry, vertices, point-like internal alignment geometry, group
  members, duplicate selections, existing Block constraints, and malformed
  blocked facades without matching constraints are refused before mutation.
  An exact group handle remains a valid whole-edge target; creation and
  behavior of Constraint Groups remains the next unfinished action.
  `VibeCADNativeSketchBlock.py` is a 204-line atomic transaction domain and
  `VibeCADNativeSketchBlockTarget.py` is a 144-line exact target validator.
  Preflight freezes all geometry, constraints, external geometry, and solver
  issues; proves counts and exact targets are current; diagnoses the complete
  proposed Block set on copied geometry; proves diagnosis did not alter live
  state; appends one exact Block per selected edge; and verifies exact
  constraint indices, references, facade flags, unchanged unrelated topology
  and metadata, unchanged canonical geometry records except the selected
  `blocked` transitions, and no new solver issues before commit. The additive
  Sketcher `diagnoseBlockConstraints` API deep-copies complete geometry with
  extensions, applies proposed Block facade state only on those copies, solves
  current plus proposed constraints, and returns full diagnostics while the
  existing generic diagnostic API remains unchanged.
  Thirty-eight Block domain tests and 77 focused Block/schema tests cover all
  eight edge families, all six internal-handle kinds, the full sixteen-target
  batch, closed and bounded schemas, exact constructors and routing, axes,
  external geometry, points, positions, group members and handles, duplicate,
  existing, and malformed targets, stale counts, solver rejection, incomplete
  or inconsistent diagnostics, diagnostic side effects, preflight drift, and
  postcondition movement or missing Blocked state. The complete Native Sketch
  suite is 886/886 green, and the full current `vibecad_tests` sweep is
  2,188/2,188 green with four intentional skips. The individual Block schema
  is exactly 1,226 bytes; all nineteen constraint variants serialize to exactly
  27,885 bytes; the thirty geometry variants serialize to 11,917 bytes; and
  both surfaces total 39,801 bytes against the unchanged 65,536-byte cap.
  Constraint Group remains the fail-closed surface boundary.
  The focused real-GUI lifecycle proves 16 Block targets over 23 real geometry
  fixtures and all supported edge and internal-handle families, with 32 total
  fixture and Block constraints. It directly proves copied diagnosis accepts
  the exact proposal without altering live geometry, facade flags, or
  constraints; invalid and duplicate calls create no undo entry; human
  selection and active context remain unchanged; the named transaction is one
  semantic undo/redo step; and exact geometry, constraint references, and
  Blocked flags survive FCStd save/reopen. The accumulated Sketch lifecycle
  switches to a separate human-selected Block Sketch, freezes a fresh provider
  turn, replays the case after the existing geometry, Equal, and Symmetric
  cases, and reopens all results successfully. Its final marker now ends in
  `constrain_tangent,constrain_equal,constrain_symmetric,constrain_block`.
  The rebuilt Sketcher and VibeCAD script targets, representative Model bracket
  workflow, and 527-action live ribbon gate are green. All eleven packaged
  touched source/build copies are byte-identical, touched Python passes Ruff,
  `python -m compileall` and `git diff --check` are clean, and the largest
  rolling integration module remains 991 lines. The protected Sketcher
  VibeScript lifecycle finishes with four geometry, eleven constraints, and
  DoF zero; all 17 protected Part Design phases report `PHASE_OK`, final exit
  code zero, and `"ok": true`.
- Constraint Group is the twentieth exact `sketch.constraint` variant and maps
  only the live `Sketcher_ConstrainGroup` action. The shipped GUI command,
  `Sketcher.Constraint("Group", elements)` constructor, persistent geometry-tag
  behavior, solver semantics, and internal-geometry cleanup were traced before
  implementation. Native accepts an ordered set of two through sixteen
  distinct, exact, whole, internal primary geometries, including standalone
  points, construction geometry, every shipped curve family, and an already
  Blocked member. Axes, external geometry, point positions, internal-alignment
  geometry, duplicate targets, existing Group or Text handles and members,
  nested groups, stale counts, existing solver issues, unavailable or duplicate
  persistent tags, and invalid, infinite, or zero-height combined bounds are
  refused before mutation.
  `VibeCADNativeSketchGroup.py` is a 163-line atomic transaction domain,
  `VibeCADNativeSketchGroupTarget.py` is a 259-line exact target validator, and
  `VibeCADNativeSketchGroupState.py` is a 328-line exact postcondition verifier.
  Preflight freezes all geometry, constraints, external geometry, solver state,
  persistent identities, and the finite OCC bounding box. Creation follows the
  human command exactly: it removes only unused exposed internal geometry for
  selected conic or B-spline parents, adds one construction-line handle from
  `(minX,minY)` to `(minX,maxY)`, and appends one Group whose full ordered
  element list starts with that handle. Verification proves the precise allowed
  cleanup, unchanged surviving tagged geometry and unrelated constraints, the
  exact new handle and Group, and no solver issues. General Sketch state remains
  capped at eight Group/Text elements; Group additionally verifies the complete
  raw host element list, including the maximum seventeen handle-plus-member
  entries, so no global return cap or 65,536-byte schema limit was raised.
  Thirty-six focused Group domain tests and 77 focused Group/schema tests cover
  all supported families, construction and Blocked members, the full
  sixteen-member boundary, exact constructor and runtime routing, allowed
  internal cleanup and index rewrites, every target refusal above, stale state,
  preflight drift, and exact postcondition failures. The complete Native Sketch
  suite is 924/924 green, and the full current `vibecad_tests` sweep is
  2,226/2,226 green with four intentional skips. The individual Group schema is
  exactly 1,226 bytes; all twenty constraint variants serialize to exactly
  28,350 bytes; the thirty geometry variants serialize to 11,917 bytes; and the
  combined surfaces total 40,266 bytes against the unchanged 65,536-byte cap.
  Driving/Reference Toggle is now the fail-closed surface boundary.
  The focused real-GUI lifecycle creates a ten-member Group across 12 real
  primary fixtures covering a point, construction geometry, every curve
  family, and a Blocked member. It proves the exact three-constraint final
  state, deletion of only 18 exposed internal geometries and their 19 dependent
  constraints, existing member constraints being ignored by Group semantics,
  invalid-call no-ops, unchanged selection and active edit context, one named
  undo/redo step, nested-Group refusal, and exact FCStd save/reopen. The rolling
  lifecycle switches to a separate human-selected Group Sketch, freezes a fresh
  provider turn, replays and reopens it after every earlier geometry and
  constraint case, and reports all 51 implemented operations ending in
  `constrain_equal,constrain_symmetric,constrain_block,constrain_group`.
  The rebuilt Sketcher and VibeCAD script targets, representative Model bracket
  workflow, and exact 527-action live ribbon gate are green. All twelve packaged
  touched source/build copies are byte-identical, touched Python passes Ruff and
  `python -m compileall`, `git diff --check` is clean, and the rolling modules
  remain split at 988 and 53 lines. The protected Sketcher VibeScript integration
  returns zero; all 17 protected Part Design phases report `PHASE_OK`, the final
  result contains `"ok": true`, and the wrapper returns zero.
- Driving/Reference Toggle is the twenty-first exact `sketch.constraint`
  variant and maps only the live `Sketcher_ToggleDrivingConstraint` action.
  It does not expose or alter Sketcher's human-controlled global
  driving/reference creation mode. Native accepts a bounded ordered set of one
  through sixteen distinct exact constraint indices, the exact expected state
  of each selected constraint, and all three current Sketch counts. It supports
  every dimensional constraint type handled by the host command: Distance,
  DistanceX, DistanceY, Radius, Diameter, Angle, SnellsLaw, and Weight,
  including inactive and virtual dimensional constraints. Nondimensional
  constraints, stale or duplicate targets, malformed or unbounded arguments,
  external-only references becoming driving, existing solver issues, and
  incomplete, inconsistent, mutating, or refusing diagnostics are rejected
  before mutation.
  `VibeCADNativeSketchDriving.py` is a 272-line atomic transaction domain,
  `VibeCADNativeSketchDrivingState.py` is a 285-line exact state and
  postcondition verifier, and `VibeCADNativeSketchDrivingTarget.py` is a
  101-line exact target parser. The additive Sketcher
  `diagnoseDrivingChanges` API evaluates the complete proposed batch against a
  cloned constraint list, reports exact solver diagnostics, and restores the
  live solver without changing document state. Preflight proves all geometry,
  constraint, external-geometry, expression, and solver state is still exact
  after diagnosis. Mutation uses one named transaction, toggles only the exact
  selected indices, and removes only the selected constraint expression when a
  driving constraint becomes reference. Postconditions prove exact counts,
  geometry topology and metadata, external references, unrelated constraints
  and expressions, each requested driving-state transition, and a clean
  solver. Solver-driven coordinate changes are intentionally accepted because
  making a measured dimensional constraint driving may legitimately solve the
  whole Sketch to new coordinates; exact topology, persistent metadata, and
  constraint state remain protected.
  Forty-three focused Driving/Reference domain tests cover all eight
  dimensional types, mixed batches, inactive, virtual, named, and expressed
  constraints, the sixteen-target bound, every refusal above, exact
  diagnostics, diagnostic side effects, preflight drift, expression record
  shape and path resolution, postcondition drift, solver motion, and exact
  transaction routing. Driving plus the complete geometry and constraint
  schema suites are 118/118 green. The complete Native Sketch suite is
  969/969 green, and the full current `vibecad_tests` sweep is 2,271/2,271
  green with four intentional skips. The individual Driving schema is exactly
  1,104 bytes; all twenty-one constraint variants serialize to exactly 28,824
  bytes; the thirty geometry plus twenty-one constraint surfaces total 40,742
  bytes against the unchanged 65,536-byte cap. Active/Inactive Toggle is now
  the fail-closed surface boundary.
  The focused real-GUI lifecycle toggles eight exact targets spanning every
  host dimensional type in a thirteen-constraint, ten-geometry Sketch. It
  proves direct diagnosis has no live side effects; stale, nondimensional,
  external-only, redundant-batch, and duplicate calls create no undo entry;
  selection and active edit context remain unchanged; a named expression is
  removed only when its exact constraint becomes reference; the complete
  batch is one semantic undo/redo step; and exact states, metadata,
  expressions, constraints, solver health, and selection survive FCStd
  save/reopen. The accumulated Sketch lifecycle switches to a separate
  human-selected Driving Sketch, freezes a fresh provider turn, replays and
  reopens it after every earlier geometry and constraint case, and reports all
  52 implemented operations ending in
  `constrain_symmetric,constrain_block,constrain_group,toggle_driving_reference`.
  The rebuilt Sketcher and VibeCAD script targets, representative Model bracket
  workflow, and exact 527-action live ribbon gate are green. All twelve checked
  packaged source/build copies are byte-identical, touched Python passes Ruff
  and `python -m compileall`, `git diff --check` is clean, and the rolling
  modules remain split at 995 and 54 lines. The protected Sketcher VibeScript
  integration returns zero; all 17 protected Part Design phases complete, the
  final structured result contains `"ok": true`, and the forced process marker
  is `VIBECAD_PARTDESIGN_VIBESCRIPT_FINAL_EXIT 0`.
- Active/Inactive Toggle is the twenty-second exact `sketch.constraint`
  variant and maps only the live `Sketcher_ToggleActiveConstraint` action.
  It accepts the exact human-opened Sketch, all three observed Sketch counts,
  and a bounded ordered batch of one through sixteen distinct exact constraint
  indices with each constraint's expected current active state. The desired
  state is the exact inverse because the human command itself is a toggle.
  Native does not impose a stale constraint-type whitelist: the contract and
  runtime support every current or future host constraint category that the
  human command can toggle, while still freezing each selected constraint's
  exact index, type, state, and complete serialized record.
  `SketchObject::diagnoseActiveChanges` validates the whole batch, clones only
  the selected constraints, applies their hypothetical active states and
  orientations to a copied constraint vector, and diagnoses the complete
  hypothetical solver state without mutating the live Sketch. The Python
  binding strictly accepts only one through sixteen integer/boolean pairs,
  returns the exact ordered indices and active states plus bounded solver
  diagnostics, and restores the live solver diagnostics before returning.
  Native refuses stale counts, types, or states; duplicate or unbounded
  targets; existing solver issues; incomplete, inconsistent, or rejected host
  diagnostics; diagnostic side effects; preflight drift; and unrelated
  postcondition changes before committing any result. Mutation uses one named
  host transaction and calls `toggleActive` only for the exact selected
  indices. Postconditions prove exact geometry topology and metadata, external
  references, constraint records, expressions, requested state transitions,
  and clean solver diagnostics. Solver-driven coordinate changes remain
  permitted because either activation or deactivation can legitimately solve
  the Sketch differently without changing its protected topology or metadata.
  Thirty-seven focused Active/Inactive tests cover mixed active/inactive
  batches, the full sixteen-target bound, exact runtime routing, expressions,
  Driving and Virtual flags, solver movement, and Distance, Horizontal,
  Coincident, Block, Group, Text, and InternalAlignment semantic categories,
  along with every refusal and drift condition above. The complete Native
  Sketch suite is 1,008/1,008 green, and the full current `vibecad_tests` sweep
  is 2,310/2,310 green with four intentional skips. The individual Active
  schema is exactly 1,100 bytes; all twenty-two constraint variants serialize
  to exactly 28,914 bytes; the thirty geometry plus twenty-two constraint
  surfaces total 40,832 bytes against the unchanged 65,536-byte cap. Sketch
  Fillet is now the fail-closed surface boundary.
  The focused real-GUI lifecycle proves side-effect-free accepted and rejected
  host diagnoses, redundant-activation refusal, stale and duplicate no-ops,
  unchanged human selection and edit context, exact mixed activation and
  deactivation, a single named undo/redo step, preserved expressions plus
  Driving, Virtual, and Block state, and exact FCStd save/reopen at 91 geometry
  and nine constraints. Its Text fixture follows the complete production
  constructor plus `setTextAndFont` initialization path, so both active and
  inactive Text state are durable. The accumulated lifecycle switches to a
  separate human-selected Active Sketch, freezes a fresh provider turn,
  replays and reopens it after every earlier geometry and constraint case, and
  reports all 52 implemented Sketch mutations ending in
  `constrain_group,toggle_driving_reference,toggle_active_inactive`.
  The sequential Sketcher and VibeCAD script builds, representative Model
  bracket workflow, and exact 527-action live ribbon gate are green. All 18
  checked packaged source/build copies are byte-identical; touched Python
  passes Ruff formatting and lint plus `python -m compileall`;
  `git diff --check` is clean; and the rolling lifecycle is split across
  964-, 75-, and 85-line modules. The protected Sketcher VibeScript integration
  exits zero. All 17 protected Part Design phases report `PHASE_OK`, its final
  structured result contains `"ok": true`, and the forced process marker is
  `VIBECAD_PARTDESIGN_VIBESCRIPT_FINAL_EXIT 0`.
- Sketch Fillet is the thirty-first exact `sketch.geometry` variant and maps
  only the live `Sketcher_CreateFillet` action. It accepts the exact
  human-opened Sketch, all three observed Sketch counts, `preserve_corner`,
  and exactly one of the two target forms exposed by the human command: a
  corner point on one geometry or a bounded pair of geometry indices. It does
  not expose an arbitrary radius. For a corner target, the host derives the
  same initial radius as the task-panel path,
  `min(length1, length2) * 0.2 * sin(angle / 2)`. For a two-curve line/line
  target it uses `Part::suggestFilletRadius`; the other supported bounded
  curve pairs pass zero so the existing Sketcher fillet kernel derives the
  radius. Native uses the human command's `trim=true`, `chamfer=false`
  behavior, preserves the host's untrimmed result for blocked targets, and
  creates the result as construction geometry only when both source
  geometries are construction geometry. It never changes human selection.
  Stale counts, malformed or ambiguous target forms, duplicate or out-of-range
  indices, unsupported geometry, invalid corner points, an already-unhealthy
  solver, incomplete or inconsistent host diagnostics, diagnostic side
  effects, preflight drift, and any postcondition mismatch are refused before
  a result can be retained.
  `VibeCADNativeSketchFillet.py` is a 558-line atomic transaction domain,
  `VibeCADNativeSketchFilletDiagnostic.py` is a 288-line strict diagnostic
  validator, and `VibeCADNativeSketchFilletTarget.py` is a 165-line exact
  target parser. The additive Sketcher `diagnoseFillet` overloads execute the
  existing production fillet implementation against a detached diagnostic
  clone, with a narrowly scoped detached `PropertyConstraintList` mode that
  skips only live ObjectIdentifier rename/removal paths. The diagnostic
  binding returns bounded solver state, exact normalized target and radius,
  trim/construction decisions, and complete detached geometry, metadata, and
  constraint state without opening a document transaction or mutating the
  live Sketch. The final mutation uses one named transaction and the same host
  fillet path. Postconditions prove exact geometry, constraint, and external
  counts; exact retained topology, metadata, expressions, external references,
  and solver health; identity mapping for every pre-existing geometry; unique
  tags for every new geometry; and a complete mutation receipt whose before,
  after, retained, and created index partitions are mutually consistent.
  Malformed geometry-group metadata also fails closed.
  Twenty-seven focused Fillet tests cover both target forms, human radius
  derivation, `preserve_corner`, trimmed and blocked results, construction
  inheritance, exact runtime routing, diagnostic purity, every bounded-input
  refusal, preflight drift, malformed diagnostics and group data, receipt
  integrity, topology changes, solver state, and exact transaction behavior.
  The complete Native Sketch suite is 1,036/1,036 green, and the full current
  `vibecad_tests` sweep is 2,338 passed with four intentional skips. The
  individual Fillet schema is exactly 1,732 bytes in the provider's wrapped
  measurement; all thirty-one geometry variants serialize to exactly 13,000
  bytes; all twenty-two constraint variants serialize to 28,914 bytes; and
  the combined geometry and constraint surface is exactly 41,915 bytes
  against the unchanged 65,536-byte cap. Sketch Chamfer is now the
  fail-closed surface boundary.
  The focused real-GUI lifecycle proves four resulting geometries, four
  constraints, both exact target forms, side-effect-free diagnostics,
  refusals with no undo entry, unchanged selection and edit context, and one
  semantic undo/redo transaction. The accumulated real-GUI lifecycle now
  covers 53 Sketch operations, inserts `create_fillet` immediately after
  `toggle_construction`, and saves and reopens the shared FCStd document after
  every operation through
  `constrain_group,toggle_driving_reference,toggle_active_inactive`. FreeCAD
  regenerates geometry UUID tags when an FCStd document reopens, so the reopen
  contract correctly proves exact persisted geometry and constraints plus
  nonempty, unique regenerated tags rather than claiming UUID equality across
  serialization. The sequential VibeCADScripts and Sketcher builds, exact
  527-action live ribbon gate, representative Model bracket workflow, and all
  82 host Sketcher tests are green with one intentional host skip. The
  protected Sketcher VibeScript integration exits zero; all 17 protected Part
  Design VibeScript phases complete, its final structured result contains
  `"ok": true`, and its forced process marker is
  `VIBECAD_PARTDESIGN_VIBESCRIPT_FINAL_EXIT 0`. All fourteen checked
  source/build Python copies are byte-identical; touched Python passes Ruff
  formatting and lint plus `python -m compileall`; `git diff --check` is
  clean. Fillet production modules remain split at 558, 288, and 165 lines,
  its focused tests at 505, 262, and 145 lines, and the shared rolling modules
  remain bounded at 982, 56, and 77 lines.
- Sketch Chamfer is the thirty-second exact `sketch.geometry` variant and maps
  only the live `Sketcher_CreateChamfer` action. Its closed contract reuses the
  two exact target forms proved for the human Fillet/Chamfer handler: one
  endpoint corner or two bounded curves with exact reference points. It also
  requires the exact human-opened Sketch, all three observed Sketch counts,
  and `preserve_corner`; it does not expose an arbitrary radius. The detached
  host diagnostic executes the existing Sketcher fillet kernel with
  `trim=true,chamfer=true`. It derives the corner and line/line radii through
  the same human paths, leaves the remaining supported bounded curve pairs to
  the kernel, proves the support arc, visible chamfer line, optional preserved
  corner, exact construction state, full detached geometry and constraint
  state, and solver health, and makes no live-document change. The final
  mutation uses one named transaction and preserves the human command's exact
  construction-index behavior, including its preserved-corner edge case,
  rather than silently changing human semantics. Selection and edit context
  remain untouched.
  Stale counts, malformed or ambiguous targets, duplicate or out-of-range
  indices, unsupported or unbounded geometry, invalid corner points, blocked
  source trimming, existing solver problems, incomplete or inconsistent host
  diagnostics, diagnostic side effects, preflight drift, malformed geometry
  groups, receipt corruption, and every postcondition mismatch fail before a
  result is retained. The shared exact-target and exact-state infrastructure
  is isolated in 173- and 466-line modules; the Fillet and Chamfer target
  wrappers are 37 lines each; Fillet production is reduced to 179 lines; and
  Chamfer production and diagnostic validation are split into 205- and
  337-line modules. The rolling GUI orchestration was split before further
  growth and now uses 873- and 65-line runners; the focused Chamfer GUI gate is
  382 lines.
  Thirty-one focused Chamfer tests cover both target forms, radius and
  construction semantics, preserved and consumed corners, exact routing,
  diagnostic purity, stale and malformed inputs, drift, grouping, receipt
  integrity, rollback, solver state, and exact transaction behavior. The
  combined Fillet/Chamfer/schema focused run is 92/92 green, the complete
  Native Sketch suite is 1,068/1,068 green, and the full current
  `vibecad_tests` sweep is 2,370 passed with four intentional skips. The
  individual Chamfer schema is exactly 1,733 bytes, all thirty-two geometry
  variants serialize to 13,048 bytes, all twenty-two constraint variants to
  28,916 bytes, and both Sketch schemas total 41,964 bytes against the
  unchanged 65,536-byte cap.
  The focused real-GUI lifecycle proves both actual mutation target forms,
  five geometries and six constraints for the corner case, four geometries
  and four constraints for the curve-pair case, side-effect-free diagnostics,
  stale refusal with no undo entry, unchanged selection and edit context, one
  transaction per mutation, undo/redo, and FCStd save/reopen. The accumulated
  real-GUI lifecycle now covers 54 Sketch operations and saves and reopens the
  shared document after each operation. The sequential VibeCADScripts and
  Sketcher builds, refactored Fillet GUI gate, exact 527-action live ribbon
  census, representative Model bracket workflow, and all 84 host Sketcher
  tests are green with one intentional host skip. The protected Sketcher and
  Part Design VibeScript integrations exit zero, and the latter's structured
  result contains `"ok": true`. All 17 applicable source/build copies are
  byte-identical; the 19 touched Python files pass Ruff formatting and lint
  plus `python -m compileall`; and `git diff --check` is clean. Trim is now the
  deliberate fail-closed `sketch.geometry` surface boundary.
- Sketch Trim is the thirty-third exact `sketch.geometry` variant and maps
  only the live `Sketcher_Trimming` action. Its closed contract requires the
  exact human-opened Sketch, all three observed Sketch counts, one exact
  trim-eligible geometry index, the exact picked point, and the complete
  expected post-mutation state returned by a detached host diagnostic. The
  diagnostic follows the existing human Trim handler's curve and internal-
  geometry eligibility rules, runs the real Sketcher trim kernel on an
  isolated clone, and reports whether the operation deletes, shortens, or
  splits the source curve. It returns the complete resulting geometry,
  construction, constraint, external-geometry, expression, solver, identity,
  and mutation-receipt state without opening a document transaction or
  changing the live Sketch. The final mutation uses one named transaction and
  proves that the original target is replaced or deleted exactly as diagnosed,
  with no guessed topology or relaxed postcondition.
  During real-kernel verification, the split case exposed an important
  discrepancy: a raw detached recompute retained replacement line parameter
  intervals of `[15,20]`, while the human document recompute normalized them
  to `[0,5]`. The implementation was corrected to run the detached clone's
  full `solve(true)` path before publishing expected state. The verifier was
  not weakened; the diagnostic and committed operation now agree on the exact
  normalized result. Stale counts, malformed or ineligible targets, group or
  internal geometry, diagnostic side effects, incomplete diagnostics,
  preflight drift, identity or expression drift, receipt corruption, solver
  failure, and every exact-state mismatch fail before a result is retained.
  Shared mutation receipts, identity, expression, and exact-state helpers were
  extracted into a dedicated module rather than duplicated, and Trim target,
  diagnostic, state, and transaction responsibilities remain split across
  focused modules.
  Thirty-five focused Trim tests cover delete, shorten, split, closed-curve,
  target validation, diagnostic distrust and purity, all eligibility gates,
  stale-state refusal, exact state, receipts, identity, expressions, rollback,
  and runtime routing. The individual Trim schema is exactly 1,190 bytes; all
  thirty-three geometry variants serialize to 13,576 bytes against the
  unchanged 65,536-byte cap. The complete current `vibecad_tests` sweep is
  2,406 passed with four intentional skips.
  Three real-kernel Trim host tests prove delete, shorten, split, and the exact
  normalized split intervals. The complete Sketcher host suite is 87/87 green
  with one intentional skip. The focused real-GUI gate proves all three
  outcomes, exact targets and receipts, side-effect-free diagnostics, stale
  refusal without an undo entry, unchanged selection and edit context, one
  transaction, undo/redo, and FCStd save/reopen. The accumulated real-GUI
  lifecycle now covers 55 Sketch operations and saves and reopens the shared
  document after every operation. Sequential VibeCADScripts, Sketcher, and
  SketcherScripts builds, the exact 527-action live ribbon census, and the
  representative Model bracket workflow are green. The protected Sketcher and
  Part Design VibeScript integrations both exit zero, and the latter's final
  structured result contains `"ok": true`. Split is now the deliberate
  fail-closed `sketch.geometry` surface boundary.
- Sketch Split is the thirty-fourth exact `sketch.geometry` variant and maps
  only the live `Sketcher_Split` action. Its closed contract requires the exact
  human-opened Sketch, all three observed Sketch counts, one exact eligible
  geometry index, the exact picked point, and the complete expected
  post-mutation state returned by an isolated host diagnostic. The diagnostic
  matches the human selection gate for line segments, circles, ellipses, arcs
  of conics, and B-splines; the human B-spline-knot path is represented by its
  resolved parent curve. It runs the real Sketcher split kernel and full
  `solve(true)` path on a detached clone without changing the live document.
  The committed operation then uses one named transaction and must reproduce
  that exact diagnosed geometry, construction, constraint, external-geometry,
  expression, solver, durable-identity, and mutation-receipt state.
  Open curves must become exactly two connected, nondegenerate replacements
  of the correct kind while preserving the source endpoints and construction
  state. Closed and periodic curves must become exactly one open,
  non-periodic replacement. Only the selected curve and any aligned internal
  helpers owned by that curve may be deleted, which preserves the real
  B-spline cleanup behavior without permitting unrelated topology changes.
  Malformed or stale counts, ineligible or internal targets, invalid points,
  diagnostic side effects, incomplete or inconsistent detached results,
  unrelated deletions, wrong replacement kinds or counts, disconnected
  pieces, expression or identity drift, corrupt receipts, solver failure, and
  every exact-state mismatch fail before a result is retained. Shared exact
  curve-point targeting, detached-result parsing, and state verification were
  extracted for Trim and Split instead of duplicating either operation.
  Thirty-one focused Split tests cover open and closed outcomes, every human
  curve family, target bounds, diagnostic distrust and purity, eligibility,
  stale-state refusal, exact geometry and constraint state, expressions,
  identities, receipts, rollback, transaction behavior, and runtime routing.
  The individual Split schema is exactly 1,191 bytes; all thirty-four geometry
  variants serialize to 13,597 bytes against the unchanged 65,536-byte cap.
  The complete current `vibecad_tests` sweep is 2,438 passed with four
  intentional skips.
  Four real-kernel Split host tests prove a line's two normalized connected
  pieces and coincident constraint, closed-circle and closed-ellipse opening,
  every supported open conic-arc and B-spline kind, construction preservation,
  exact receipts, diagnostic purity, and rejection outside the human gate.
  The complete Sketcher host suite is 91/91 green with one intentional skip.
  The focused real-GUI gate proves line splitting and circle opening, exact
  targets and receipts, side-effect-free diagnostics, stale refusal without
  an undo entry, unchanged selection and edit context, one transaction,
  undo/redo, and FCStd save/reopen. The accumulated real-GUI lifecycle now
  covers 56 Sketch operations and saves and reopens the shared document after
  every operation. The exact 527-action live ribbon census and representative
  Model bracket workflow remain green. Both protected VibeScript integrations
  exit zero and the Part Design result contains `"ok": true`. The final
  sequential VibeCADScripts and Sketcher builds are green; all 18 applicable
  source/build copies are byte-identical; the 20 touched Python files pass Ruff
  lint, Ruff formatting, and `python -m compileall`; and `git diff --check` is
  clean. Extend is now the deliberate fail-closed `sketch.geometry` surface
  boundary.
- Sketch Extend is the thirty-fifth exact `sketch.geometry` variant and maps
  only the live `Sketcher_Extend` action. Its closed contract requires the
  exact human-opened Sketch, all three observed Sketch counts, one exact
  eligible geometry index, an explicit `start | end` endpoint, and the exact
  picked target point. It exposes no inferred endpoint, curve search, raw
  increment, solver control, command dispatch, or workbench activation. The
  eligibility gate matches the human command exactly: only a line segment or
  circular arc may be extended, internal geometry is excluded, the target must
  move the selected endpoint, and the line handler's human endpoint-switch
  behavior is refused instead of silently changing the caller's exact role.
  A shared native calculation now drives both the live drawing preview and a
  detached `diagnoseExtend` path. The diagnostic clones the Sketch, runs the
  real `extend` kernel and `solve(true)`, and returns exact geometry,
  construction, constraint, solver, identity, expression, and mutation-receipt
  state without opening a document transaction or changing the live Sketch.
  The committed operation accepts only that frozen result, rechecks the exact
  live state before mutation, performs one named transaction, and retains a
  concise result containing the exact target, selected endpoint, extended or
  shortened outcome, new endpoint, changed geometry indices, and final counts.
  Malformed or stale counts, ineligible geometry, endpoint switching, no-op or
  arc-center targets, incomplete or malicious diagnostics, diagnostic side
  effects, unrelated constrained-geometry changes, collection-identity drift,
  changed constraints or expressions, post-preflight drift, receipt
  corruption, and every exact postcondition mismatch fail before a result is
  retained.
  Real-GUI verification exposed a pre-existing host defect in circular-arc
  Extend: the arc range was mutated through a raw geometry pointer, so the
  named document transaction existed but Undo did not restore the arc. The
  shared Sketcher kernel now clones the arc, changes the clone's range, and
  replaces the `Geometry` property value. This records the mutation through
  the document property system for both the human command and Native mode,
  while preserving geometry identity and the historical endpoint calculation.
  A permanent host regression proves exact circular-arc undo and redo.
  Forty-five focused Extend tests cover the closed target, all four line/arc
  extension and shortening outcomes, untrusted diagnostics and purity,
  eligibility, stale-state refusal, exact geometry and constraint state,
  identities, expressions, receipts, rollback, and runtime routing. The
  individual Extend schema is exactly 1,249 bytes; all thirty-five geometry
  variants serialize to 14,078 bytes against the unchanged 65,536-byte cap.
  The complete current `vibecad_tests` sweep is 2,484 passed with four
  intentional skips. Five focused real-kernel Extend host tests prove line and
  circular-arc endpoints and directions, construction preservation, exact
  receipts, diagnostic purity, endpoint-switch refusal, unsupported-target
  refusal, and document undo/redo. The complete Sketcher host suite is 96/96
  green with one intentional skip.
  The focused real-GUI gate proves line extension and arc shortening, exact
  targets and receipts, side-effect-free diagnostics, stale refusal without an
  undo entry, unchanged selection and edit context, one transaction, exact
  undo/redo, and FCStd save/reopen. The accumulated real-GUI lifecycle now
  covers 57 Sketch operations in one shared editable document and verifies the
  durable state after save/reopen. The exact 527-action live ribbon census and
  representative Model bracket workflow remain green. Both protected
  VibeScript integrations exit zero and the Part Design result contains
  `"ok": true`. Final sequential VibeCADScripts and Sketcher builds are green;
  all 15 applicable source/build copies are byte-identical; the 16 focused
  Python files pass Ruff lint, Ruff formatting, and `python -m compileall`; and
  `git diff --check` is clean. External-geometry Projection is now the
  deliberate fail-closed `sketch.geometry` surface boundary.
- Sketch external-geometry Projection is the thirty-sixth exact
  `sketch.geometry` variant and maps only the live `Sketcher_Projection`
  action. Its closed contract requires the exact human-opened Sketch, all
  three observed Sketch counts, the exact source object and optional exact
  `Face`, `Edge`, or `Vertex` subelement, an explicit defining/reference role,
  and the complete expected external-reference state. It also supports the
  same whole-object Datum, Plane, Line, and Point sources as the human command.
  It exposes no object search, inferred role, UI-preference inference, raw
  external index, command dispatch, or workbench activation.
  Sketcher now has an additive, side-effect-free `diagnoseExternal` host API.
  Diagnosis and committed external-geometry rebuild both use one shared
  projection evaluator, so preflight does not approximate the kernel or clone
  a live document graph. The diagnostic resolves stable mapped-topology keys
  through the same topological-naming path as the committed
  `PropertyLinkSubList`, and reports the projected geometry, durable reference,
  external type, reference index, add/upgrade outcome, and defining role.
  Native mode performs that diagnosis before mutation and repeats it against
  the exact frozen state immediately before one named transaction. It then
  calls the real `addExternal` path and verifies exact geometry, constraints,
  external links and types, solver state, durable identities, source geometry
  and placement, and mutation receipt before retaining the result.
  Duplicate Projection/Both links, invalid or self targets, stale counts,
  source geometry/configuration drift, diagnostic side effects or inconsistent
  metadata, unexpected external-type padding, wrong mapped references,
  collection-identity drift, solver failure, receipt corruption, and every
  exact postcondition mismatch fail without a retained mutation. An existing
  Intersection link may be upgraded to Both only while preserving its explicit
  defining/reference role. The source fingerprint deliberately covers
  projection-relevant geometry and placement rather than reverse-link metadata,
  allowing the expected Sketch backlink while still rejecting source changes.
  The aligned external-state decoder also distinguishes real link/type records
  from Sketcher's historical blank-Sketch `ExternalTypes == [0]` padding; a
  permanent host regression freezes that behavior.
  The focused Projection/schema suite has 83 passing tests. The individual
  Projection schema is exactly 1,460 bytes; all thirty-six geometry variants
  serialize to 14,911 bytes against the unchanged 65,536-byte cap. The complete
  current `vibecad_tests` sweep is 2,530 passed with four intentional skips.
  Nine focused real-kernel host tests prove defining and reference projections,
  mapped compound keys, Edge/Vertex/Face targets, duplicate and invalid-state
  refusal, role-preserving Intersection-to-Both upgrade, diagnostic purity,
  exact commit agreement, and document undo/redo.
  The focused real-GUI gate proves exact selection and edit context,
  side-effect-free diagnosis, stale and duplicate refusal, one transaction,
  exact undo/redo, and FCStd save/reopen. It also proves that both Projection
  and Intersection remain present on the human ribbon while the production
  Native surface returns no schemas because the next Intersection variant is
  incomplete. The accumulated real-GUI lifecycle now covers 58 Sketch
  operations in one shared editable document and verifies the durable state
  after save/reopen. Both protected Sketcher and Part Design VibeScript
  integrations exit zero; the final sequential VibeCADScripts and Sketcher
  builds, focused Ruff check, and `git diff --check` are green. External-
  geometry Intersection is now the deliberate fail-closed `sketch.geometry`
  surface boundary.
- Sketch external-geometry Intersection is the thirty-seventh exact
  `sketch.geometry` variant and maps only the live `Sketcher_Intersection`
  action. It uses the same exact human-opened Sketch, source object,
  `Face`/`Edge`/`Vertex` subelement, explicit defining/reference role, observed
  Sketch counts, and complete expected external-reference state as Projection;
  it exposes no object search, inferred role, raw external index, command
  dispatch, workbench activation, or UI-preference inference. Projection and
  Intersection now share one small production transaction core while retaining
  separate operation contracts and wrappers. Both paths diagnose twice around
  preflight, perform exactly one real `addExternal` call in one named document
  transaction, and verify exact source state, projected geometry, constraints,
  external links and types, solver state, durable identities, receipt, and
  operation outcome before retaining a mutation.
  A new Intersection creates type 1; applying Intersection to an existing
  Projection upgrades it to Both/type 2 while preserving the user's explicit
  defining/reference role. Applying Projection to an existing Intersection
  remains the complementary role-preserving upgrade. Exact duplicates,
  mismatched expected state, stale source or Sketch counts, impure, untrusted,
  incomplete, or drifting diagnostics, postcondition drift, and receipt
  corruption all fail closed without retaining a result.
  Real-GUI verification exposed a host wrapper defect for crossing-edge
  Intersection points: `Part::GeomPoint::getPyObject()` reconstructed a bare
  point and discarded its `ExternalGeometryExtension`. It now returns a clone,
  matching the curve wrappers and preserving the durable reference, external
  type, and defining/reference role. Permanent Projection and Intersection
  host regressions freeze the point-wrapper behavior.
  The focused schema/Projection/Intersection suite has 104 passing tests. The
  individual Intersection schema is exactly 1,462 bytes; all thirty-seven
  geometry variants serialize to 14,998 bytes against the unchanged
  65,536-byte cap. The complete current `vibecad_tests` sweep is 2,551 passed
  with four intentional skips. Five focused Intersection host tests and the
  complete 110-test Sketcher host suite pass with one intentional skip. The
  focused real-GUI gate proves crossing-edge point creation, explicit reference
  role, diagnostic purity, stale and duplicate refusal, exact receipt and
  result, selection/edit-context preservation, one transaction, exact
  undo/redo, and FCStd save/reopen. The accumulated real-GUI lifecycle now
  covers 59 Sketch operations in one shared editable document and verifies the
  durable state after save/reopen. It also proves Projection and Intersection
  remain present on the human ribbon while Native fails closed at Carbon Copy.
  Both protected Sketcher and Part Design VibeScript integrations exit zero.
  Final sequential VibeCADScripts and Sketcher builds, focused Ruff lint and
  formatting, and `git diff --check` are green, and no GUI process remains.
  Carbon Copy is now the deliberate fail-closed `sketch.geometry` surface
  boundary.
- Sketch Carbon Copy is the thirty-eighth exact `sketch.geometry` variant and
  maps only the live `Sketcher_CarbonCopy` action. Its closed request names the
  exact active target Sketch and exact source Sketch, freezes their geometry,
  constraint, external-geometry, expression, placement, container, and solver
  state, and requires the observed source and target counts. The request makes
  the geometry role explicit as `regular` or `construction` and makes the
  source relationship explicit as same-body aligned, cross-body aligned, or
  unaligned. Those three modes map directly to the host's two independent
  cross-body and alignment permission flags; Native does not infer them from
  GUI preferences, selection, or document layout.
  Sketcher now exposes an additive exact Carbon Copy host API and a detached,
  side-effect-free diagnostic path that shares the real transformation,
  constraint, solver, external-reference, and expression construction logic
  with the commit. The human command retains its existing preference-driven
  behavior. Native diagnoses the exact operation twice around a complete
  frozen-state comparison, commits once in one named transaction, and proves
  exact created indices and tags, copied geometry and construction roles,
  constraints, projected external references, source-linked expressions,
  alignment flips, solver health, stable unrelated state, and the concise
  mutation receipt. Generated geometry tags are normalized only in the
  repeatability and save/reopen comparisons because detached diagnoses and
  reopened documents legitimately allocate fresh tags; the actual committed
  receipt still proves every real created tag exactly.
  Missing or detached sources, source/target identity drift, stale counts,
  duplicate or circular copies, unavailable or synchronizing external
  geometry, forbidden cross-body or unaligned relationships, impure,
  incomplete, untrusted, or drifting diagnostics, malformed expression paths,
  postcondition drift, and receipt corruption all fail without a retained
  mutation. Source geometry, constraints, external references, expressions,
  placement, and solver state are proved unchanged after commit, undo/redo,
  and save/reopen.
  Seven focused real-kernel host tests cover pure diagnosis and exact commit,
  regular and construction geometry, external references, expressions,
  explicit unaligned transforms and flips, explicit cross-body permission,
  invalid/duplicate/circular refusal, and undo/redo; the independent reverse-
  mapping host regression also passes. The focused Native Carbon Copy suite is
  15/15 green, its schema/Carbon/snapshot group is 59/59 green, all Native
  Sketch tests are 1,266/1,266 green, all Native tests are 1,889/1,889 green,
  and the complete current `vibecad_tests` sweep is 2,568 passed with four
  intentional skips. The individual Carbon Copy schema is exactly 1,818 bytes
  and all thirty-eight geometry variants serialize to 16,066 bytes against the
  unchanged 65,536-byte cap.
  The focused real-GUI gate proves diagnostic purity, stale and duplicate
  refusal, exact external-reference and expression copying, one transaction,
  exact undo/redo, source preservation, selection preservation, and FCStd
  save/reopen. The accumulated real-GUI lifecycle now covers 60 Sketch
  operations in one shared editable document and verifies durable state after
  save/reopen. It also proves Carbon Copy and Translate remain on the human
  ribbon while production Native fails closed at the unfinished Translate
  action. Both protected Sketcher and Part Design VibeScript integrations exit
  zero. Final sequential VibeCADScripts and Sketcher builds, focused Ruff lint
  and formatting, and `git diff --check` are green. The read-only 5-axis crash
  fixture remains byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Translate is now the deliberate fail-closed `sketch.geometry` surface
  boundary.
- Sketch Translate is the thirty-ninth exact `sketch.geometry` variant and maps
  only the live `Sketcher_Translate` action. Its closed request names the exact
  active Sketch, ordered unique internal or external geometry indices, observed
  geometry, constraint, and external-geometry counts, the exact first vector,
  copy count, optional exact second vector and row count, and the explicit
  `Preserve` or `Equal` dimensional-constraint mode. It rejects ambiguous row
  configuration, a zero first vector, unsupported axes or incomplete internal
  geometry, `Equal` in move mode, and requests that would create more than
  4,096 geometry elements.
  Sketcher now exposes one additive exact Translate host API and a detached,
  side-effect-free diagnostic path. The existing human preview remains intact,
  while its final commit and Native share the same exact mutation
  implementation. Move mode preserves expression-driven dimensional
  constraints. Copy and two-vector array modes preserve construction state,
  supported curve geometry, external-geometry semantics, copied constraints,
  internal alignment, and the ribbon's exact ordering; `Equal` mode constrains
  copied dimensional constraints to their originals. The host diagnostic and
  receipt now include exact geometry and constraint tags, making stable object
  identity directly verifiable rather than inferred from array position.
  Native freezes exact geometry, constraint, external-reference, expression,
  configuration, solver, and durable-tag state; diagnoses twice around
  preflight; proves diagnosis purity and repeatability; commits exactly once in
  one named transaction; and verifies the complete final state and concise
  receipt. Stale counts or identities, untrusted, incomplete, impure, or
  drifting diagnostics, malformed receipt echoes, and any geometry,
  constraint, expression, solver, or tag drift fail closed without retaining a
  mutation.
  Seven focused real-kernel host tests cover pure move diagnosis, expression
  preservation, construction and supported curves, one- and two-vector arrays,
  dimensional equality, external geometry, invalid or incomplete input, and
  exact undo/redo. The focused Native Translate suite is 21/21 green; its
  schema and adjacent operation group is 62/62 green; all Native Sketch tests
  are 1,288/1,288 green; all Native tests are 1,911/1,911 green; and the
  complete current `vibecad_tests` sweep is 2,590 passed with four intentional
  skips. The individual Translate schema is exactly 1,793 bytes and all
  thirty-nine geometry variants serialize to 17,107 bytes against the
  unchanged 65,536-byte cap.
  The focused real-GUI gate proves pure exact host diagnosis, stale refusal,
  expression-preserving move, arbitrary two-vector two-dimensional array,
  exact generated geometry and constraints, stable identities, one
  transaction, exact undo/redo, and durable FCStd save/reopen state. The
  accumulated real-GUI lifecycle now covers 61 Sketch operations in one shared
  editable document and verifies durable state after save/reopen. It also
  proves Translate and Rotate remain on the human ribbon while production
  Native fails closed at the unfinished Rotate action. Both protected Sketcher
  and Part Design VibeScript integrations exit zero. Final sequential
  VibeCADScripts and Sketcher builds, focused Ruff lint and formatting, the
  seven-test real-host rerun, and `git diff --check` are green; no FreeCAD
  process remains. The read-only 5-axis crash fixture was never modified and
  remains byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Rotate is now the deliberate fail-closed `sketch.geometry` surface boundary.
- Sketch Rotate is the fortieth exact `sketch.geometry` variant and maps only
  the live `Sketcher_Rotate` action. Its closed request names the exact active
  Sketch, ordered unique internal or external geometry indices, observed
  geometry, constraint, and external-geometry counts, an explicit center,
  degree-valued total angle, copy count, and `Preserve` or `Equal`
  dimensional-constraint mode. It rejects a zero or out-of-range angle,
  `Equal` in move mode, unsupported axes or incomplete internal geometry, and
  requests that would create more than 4,096 geometry elements.
  Sketcher now exposes one additive exact Rotate host API and a detached,
  side-effect-free diagnostic path. The existing human preview remains intact,
  while its final commit and Native share the same exact mutation
  implementation. Move mode replaces the selected geometry and preserves
  expression-driven dimensional constraints. Copy mode retains the originals,
  distributes copies across the requested total angle, preserves construction
  state, supported curve geometry, external-geometry semantics, copied
  constraints, and internal alignment, and implements the ribbon's exact
  `Preserve` and `Equal` behavior. Axis-dependent horizontal, vertical,
  distance-X, and distance-Y constraints are intentionally not copied onto
  rotated geometry, matching the human command.
  Native freezes exact geometry, constraint, external-reference, expression,
  configuration, solver, and durable-tag state; diagnoses twice around
  preflight; proves diagnosis purity and repeatability; commits exactly once in
  one named transaction; and verifies the complete final state and concise
  receipt. Stale counts or identities, untrusted, incomplete, impure, or
  drifting diagnostics, malformed receipt echoes, and any geometry,
  constraint, expression, solver, or tag drift fail closed without retaining a
  mutation.
  Seven focused real-kernel host tests cover expression-preserving move,
  angular copy distribution, dimensional equality, axis-dependent constraint
  semantics, external geometry, invalid-input purity, and exact undo/redo. The
  focused Native Rotate suite is 21/21 green; its schema and adjacent operation
  group is 84/84 green; all Native Sketch tests are 1,310/1,310 green; all
  Native tests are 1,933/1,933 green; and the complete current `vibecad_tests`
  sweep is 2,612 passed with four intentional skips. The individual Rotate
  schema is exactly 1,680 bytes and all forty geometry variants serialize to
  17,539 bytes against the unchanged 65,536-byte cap.
  The focused real-GUI gate proves pure exact host diagnosis, stale refusal,
  expression-preserving move, exact polar-array geometry and constraints,
  stable identities, one transaction, exact undo/redo, and durable FCStd
  save/reopen state. The accumulated real-GUI lifecycle now covers 62 Sketch
  operations in one shared editable document and verifies durable state after
  save/reopen. It also proves Translate, Rotate, and Scale remain on the human
  ribbon while production Native fails closed at the unfinished Scale action.
  Both protected Sketcher and Part Design VibeScript integrations exit zero.
  Final sequential VibeCADScripts and Sketcher builds, an explicit SketcherGui
  build, focused Ruff lint and formatting, the seven-test real-host rerun, and
  `git diff --check` are green; no FreeCAD process remains. The implementation
  is split across bounded modules (the shared host transform file is 672 lines
  and the shared Native transform-state module is 515 lines). The read-only
  5-axis crash fixture was never modified and remains byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Scale is now the deliberate fail-closed `sketch.geometry` surface boundary.
- Sketch Scale is the forty-first exact `sketch.geometry` variant and maps only
  the live `Sketcher_Scale` action. Its closed request names the exact active
  Sketch, ordered unique internal or external geometry indices, observed
  geometry, constraint, and external-geometry counts, an explicit center, a
  finite positive bounded scale factor, and explicit copy-versus-replace
  intent. It rejects duplicate or stale targets, axes, incomplete aligned
  internal geometry, and factors outside the bounded schema. Native does not
  expose the human command's special whole-sketch origin mode.
  Sketcher now exposes one additive exact Scale host API and one detached,
  side-effect-free diagnostic path. The existing human preview remains intact,
  while its final commit and Native share the same exact mutation
  implementation. Uniform scaling covers points, lines, circles, circular
  arcs, ellipses, hyperbolas, parabolas, and B-splines; preserves construction
  state, external-geometry semantics, internal alignment, dimensional
  constraints, constraint-label locations, and the source facade ID during
  replacement; and follows the human command's expression semantics. Copy mode
  retains the source state and creates the scaled geometry and constraints in
  the ribbon's exact ordering.
  Native freezes exact geometry, constraint, external-reference, expression,
  configuration, solver, and durable-identity state; diagnoses twice around
  preflight; proves diagnosis purity and repeatability; commits exactly once in
  one named transaction; and verifies the complete final state and concise
  receipt. Stale state, untrusted, incomplete, impure, or drifting diagnostics,
  malformed receipt echoes, and any postcondition drift fail closed without
  retaining a mutation.
  Eight focused real-kernel host tests cover replacement and copy semantics,
  dimensional and orientation constraints, all supported curve families,
  external geometry, invalid-input purity, guarded whole-sketch origin mode,
  and exact undo/redo. The focused Native Scale suite is 21/21 green; its
  Translate/Rotate/Scale/schema group is 106/106 green; all Native Sketch tests
  are 1,332/1,332 green; all Native tests are 1,955/1,955 green; and the
  complete current `vibecad_tests` sweep is 2,634 passed with four intentional
  skips. The individual Scale schema is exactly 1,428 bytes and all forty-one
  geometry variants serialize to 17,852 bytes against the unchanged
  65,536-byte cap.
  The focused real-GUI gate proves pure diagnosis, stale refusal, exact circle
  replacement and line copying, constraint and expression semantics, stable
  identities, one transaction, exact undo/redo, selection preservation, and
  durable FCStd save/reopen state. The accumulated real-GUI lifecycle now
  covers 63 Sketch operations in one shared editable document and verifies
  durable state after save/reopen. It also proves Translate, Rotate, Scale, and
  Offset remain on the human ribbon while production Native fails closed at
  unfinished Offset. Both protected Sketcher and Part Design VibeScript
  integrations exit zero. Final sequential VibeCADScripts and Sketcher builds,
  an explicit SketcherGui build, focused Ruff lint, the eight-test real-host
  rerun, and `git diff --check` are green; no FreeCAD process remains. The
  Scale implementation is split across bounded host, target, state, runtime,
  GUI-case, integration, and test modules, each below 1,000 lines.
  The VibeCAD nested-link preselection fix was also verified against a
  byte-identical disposable copy of the real 5-axis machine by scanning its
  viewport with mouse preselection and resolving nested App::Link subelements;
  the regression exits normally, the upstream `TestViewProviderLink` suite is
  5/5 green, and the original read-only fixture was never saved or modified and
  remains byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Offset is now the deliberate fail-closed `sketch.geometry` surface boundary.
- Sketch Offset is the forty-second exact `sketch.geometry` variant and maps
  only the live `Sketcher_Offset` action. Its closed request names the exact
  active Sketch, ordered unique internal or external geometry indices,
  observed geometry, constraint, and external-geometry counts, a signed,
  finite, nonzero bounded offset distance in millimetres, the exact Arc or
  Intersection join type, and explicit Keep, Delete, or Constrain source
  behavior. Duplicate or stale targets, unsupported geometry, incomplete
  aligned internal geometry, mixed or invalid topology, invalid enum values,
  and distances outside the bounded schema fail closed before mutation.
  Sketcher now exposes one additive exact Offset host API and one detached,
  side-effect-free diagnostic path. The human command retains its interactive
  preview, while its final commit and Native share the same exact mutation
  implementation. Internal and external lines, circles, and circular arcs are
  supported in the Sketch working plane; Arc and Intersection joins, source
  retention or deletion, construction connectors, topology constraints, and
  one shared driving offset dimension follow the human ribbon semantics.
  Native freezes exact geometry, constraint, external-reference, expression,
  configuration, solver, and durable-identity state; diagnoses twice around
  preflight; proves diagnosis purity and repeatability; commits exactly once in
  one named transaction; and verifies the complete final state, identities,
  and concise receipt. Only the presentation-computed label distance and label
  position of newly created dimensions are normalized between detached and
  live state; all semantic constraint fields remain exact and a changed
  dimension value is proven to fail verification. Stale state, untrusted,
  incomplete, impure, or drifting diagnostics, malformed receipt echoes, and
  any semantic postcondition drift fail closed without retaining a mutation.
  Ten focused real-kernel host tests cover pure diagnosis/commit parity,
  positive and negative signed offsets, Arc and Intersection joins, Keep,
  Delete, and Constrain modes, polygon and circle constraints, existing
  constraints, external geometry, invalid-input purity, and exact undo/redo.
  The focused Native Offset suite is 17/17 green; its adjacent
  Translate/Rotate/Scale/Offset/schema group is 123/123 green; all Native
  Sketch tests are 1,350/1,350 green; all Native tests are 1,973/1,973 green;
  and the complete current `vibecad_tests` sweep is 2,652 passed with four
  intentional skips. The individual Offset schema is exactly 1,530 bytes and
  all forty-two geometry variants serialize to 18,482 bytes against the
  unchanged 65,536-byte cap.
  The focused real-GUI gate proves signed circle offset, both join modes, all
  three source modes, pure diagnosis, stale refusal, stable identities, one
  transaction, exact undo/redo, selection preservation, and durable FCStd
  save/reopen state. The accumulated real-GUI lifecycle now covers 64 Sketch
  operations in one shared editable document and verifies durable state after
  save/reopen. At the rolling document's bounded undo capacity, the gate
  verifies the exact latest transaction name and actual undo/redo state while
  permitting eviction of the oldest entry. It also proves Symmetry remains on
  the human ribbon while production Native fails closed at unfinished
  `Sketcher_Symmetry`. Both protected Sketcher and Part Design VibeScript
  integrations exit zero. Final sequential VibeCADScripts and Sketcher builds,
  an explicit SketcherGui build, focused Ruff lint and formatting, the
  ten-test real-host rerun, and `git diff --check` are green; no FreeCAD process
  remains. The Offset implementation is split across bounded host, constraint,
  target, state, runtime, GUI-case, integration, and test modules, each below
  1,000 lines. The original read-only 5-axis fixture was never saved or
  modified and remains byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Symmetry is now the deliberate fail-closed `sketch.geometry` surface
  boundary.
- Sketch Symmetry is the forty-third exact `sketch.geometry` variant and maps
  only the live `Sketcher_Symmetry` action. Its closed request names the exact
  active Sketch, ordered unique internal or external source geometry indices,
  observed geometry, constraint, external-reference, and external-geometry
  counts, one exact line, axis, origin, or geometry-point reference, and
  explicit Keep, Delete, or Constrain source behavior. Duplicate or stale
  targets, axes or the origin as sources, unsupported geometry, incomplete
  aligned internal geometry, invalid reference positions, non-line whole
  references, invalid enum values, and drifting exact state fail closed before
  mutation.
  Sketcher now exposes one additive exact Symmetry host API and one detached,
  side-effect-free diagnostic path. The human command retains its interactive
  preview, while its final commit and Native share the same exact mutation
  implementation. Points, lines, circles, circular arcs, ellipses, elliptical,
  hyperbolic, and parabolic arcs, and B-splines can be mirrored about an exact
  internal or external line, either Sketch axis, the origin, or an exact
  supported geometry point. Construction state, external-geometry semantics,
  internal alignment, copied constraints, curve orientation, source deletion,
  and optional editable human Symmetric constraints follow the human ribbon
  semantics.
  Native freezes exact geometry, constraint, external-reference, expression,
  configuration, solver, and durable-identity state; diagnoses twice around
  preflight; proves diagnosis purity and repeatability; commits exactly once in
  one named transaction; and verifies the complete final state, identities,
  exact reference and mode echoes, and concise receipt. Stale state, untrusted,
  incomplete, impure, or drifting diagnostics, malformed receipt echoes, and
  any postcondition drift fail closed without retaining a mutation.
  Nine focused real-kernel host tests cover pure diagnosis/commit parity,
  horizontal and vertical axes, internal and external line references, origin
  and exact geometry-point references, all three source modes, every human
  curve family, copied constraints, expression removal, curve orientation,
  invalid-input purity, strict integer parsing, and exact undo/redo. The
  focused Native Symmetry suite is 17/17 green; its adjacent
  Translate/Rotate/Scale/Offset/Symmetry/schema group is 142/142 green; all
  Native Sketch tests are 1,368/1,368 green; all Native tests are 1,991/1,991
  green; and the complete current `vibecad_tests` sweep is 2,670 passed with
  four intentional skips. The individual Symmetry schema is exactly 1,435
  bytes and all forty-three geometry variants serialize to 18,954 bytes
  against the unchanged 65,536-byte cap.
  The focused real-GUI gate proves internal-line, vertical-axis, origin-point,
  and external-line references; Keep, Delete, and Constrain behavior; pure
  diagnosis; stale refusal; copied constraints and expression semantics; one
  transaction; exact undo/redo; selection and edit-context preservation; and
  durable FCStd save/reopen state. The preceding focused Offset gate remains
  green, and the accumulated real-GUI lifecycle now covers all 65 implemented
  Sketch operations in one shared editable document and verifies durable state
  after save/reopen. It also proves removal of axis alignment remains on the
  human ribbon while production Native fails closed at unfinished
  `Sketcher_RemoveAxesAlignment`. Both protected Sketcher and Part Design
  VibeScript integrations exit zero. Final sequential VibeCADScripts and
  Sketcher builds, an explicit SketcherGui build, focused Ruff lint and
  formatting, the nine-test real-host rerun, and `git diff --check` are green;
  no FreeCAD process remains. The Symmetry implementation is split across
  bounded host, target, state, runtime, GUI-case, integration, and test modules,
  each no longer than 551 lines. The original read-only 5-axis fixture was
  never saved or modified and remains byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Removal of axis alignment is now the deliberate fail-closed
  `sketch.geometry` surface boundary.
- Remove Axes Alignment is the forty-fourth exact `sketch.geometry` variant
  and maps only the live `Sketcher_RemoveAxesAlignment` action. Its closed
  request names the exact active Sketch, 1–256 ordered unique current internal
  geometry indices, and observed geometry, constraint, external-reference,
  and external-geometry counts. Axes, external geometry, duplicates, stale
  indices or counts, empty selections, and selections with no applicable
  rewrite fail closed before mutation; no raw selection path or command
  dispatch is exposed.
  Sketcher now exposes additive exact and detached-diagnostic host APIs over
  the same rewrite used by the existing human command. Whole-line Horizontal
  and Vertical constraints are removed while additional selected constraints
  of each orientation become Parallel to the first; selected axis-based
  Symmetric and PointOnObject relationships are removed; selected DistanceX
  and DistanceY constraints become general Distance constraints without
  changing their durable tag, name, expression, or value; point-specific
  Horizontal/Vertical constraints, non-axis PointOnObject relations, and
  unselected alignment remain unchanged. The existing permissive human API
  retains its public no-op behavior, while the exact Native path rejects a
  no-op and strict Python target parsing rejects booleans.
  Native freezes complete geometry, constraint, external-reference,
  expression, configuration, solver, and durable-identity state. It derives
  the only valid rewrite independently from the frozen records, validates all
  six diagnostic counts and the complete expected final constraint sequence,
  proves detached-diagnosis purity, diagnoses again immediately before
  commit, commits once in one named transaction, and verifies complete final
  state plus the host mutation receipt. An incomplete, untrusted, impure, or
  drifting diagnostic, unrelated geometry or external-state change, wrong
  constraint rewrite, stale target, or semantic postcondition drift fails
  closed without retaining a mutation.
  Seven focused real-host tests cover Horizontal/Vertical-to-Parallel
  rewriting, axis Symmetric and PointOnObject removal, distance conversion
  with expression/tag preservation, point-specific and non-axis preservation,
  unselected preservation, strict invalid/no-op purity, legacy no-op behavior,
  and exact undo/redo. The focused Native suite is 14/14 green; the adjacent
  Translate/Rotate/Scale/Offset/Symmetry/Remove-Axes-Alignment/schema group is
  157/157 green; all Native Sketch tests are 1,383/1,383 green; all Native
  tests are 2,006/2,006 green; and the complete current `vibecad_tests` sweep
  is 2,685 passed with four intentional skips. The individual schema is
  exactly 1,068 bytes, all forty-four geometry variants serialize to 19,165
  bytes against the unchanged 65,536-byte cap, and the preceding forty-three
  variants remain exactly 18,954 bytes.
  The focused real-GUI gate proves every rewrite family, pure diagnosis,
  stale and no-op refusal with no transaction, stable distance identity/name/
  expression, one successful transaction, exact undo/redo, selection and edit
  preservation, and durable FCStd save/reopen state. The preceding focused
  Symmetry gate remains green, and the accumulated real-GUI lifecycle now
  covers all 66 implemented Sketch operations in one shared editable document
  and verifies every separate operation Sketch after save/reopen. Production
  Native still exposes zero tools and now fails closed at unfinished
  `Sketcher_BSplineConvertToNURBS`.
  The nested-link selection fix was additionally rerun directly against the
  original read-only 5-axis machine: all eligible nested linked-Body faces
  resolve through `getDetailPath`, a real mouse-move sweep exercises the
  `SoFCUnifiedSelection` preselection path from the reported crash, and the GUI
  exits cleanly. The file was never saved or repaired and remains exactly
  SHA-256 `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Both protected Sketcher and Part Design VibeScript integrations exit zero.
  Final sequential VibeCADScripts and Sketcher builds, an explicit SketcherGui
  build, focused Ruff lint/formatting, `git diff --check`, line-size checks,
  and process cleanup are green. The new host, target, state, runtime,
  GUI-case, integration, and test modules are each below 500 lines. B-spline
  conversion to NURBS is now the deliberate fail-closed `sketch.geometry`
  surface boundary.
- B-spline conversion to NURBS is the forty-fifth exact `sketch.geometry`
  variant and maps only the live `Sketcher_BSplineConvertToNURBS` action. Its
  closed request names the exact active Sketch, 1–256 ordered unique current
  internal or external edge indices, and observed geometry, constraint,
  external-reference, and external-geometry counts. Empty selections, axes,
  points, duplicates, stale indices or counts, grouped/internal-alignment
  geometry, unhealthy external state, and resource-excessive B-spline helper
  expansion fail closed before mutation; no raw selection path or command
  dispatch is exposed.
  Sketcher now exposes additive `diagnoseConvertToNURBS(list[int])` and
  `convertToNURBSExact(list[int])` host APIs while retaining the existing
  public `convertToNURBS(int)` behavior. Detached clone preflight validates the
  complete ordered mixed internal/external conversion before live mutation,
  closing the human helper's legacy partial-mutation failure path. Commit
  preserves the command's two-pass behavior: all requested roots convert in
  selection order, then only converted internal roots expose control points and
  knots; external targets become internal B-spline copies without helper
  exposure. Placement, tolerance, tags, expressions, external references and
  maps, solver state, and exact constraint deletion/remapping semantics are
  covered by the diagnostic and mutation receipt.
  Native independently freezes and verifies complete geometry, constraint,
  external, expression, solver, configuration, and durable-identity state. It
  proves exact root ordering and B-spline types, exact helper geometry and
  InternalAlignment/Weight/Equal constraints, expected endpoint-Coincident
  survival and midpoint/non-Coincident removal, external-copy behavior,
  detached-diagnosis purity, immediate pre-commit freshness, one named
  transaction, and the complete postcondition. Untrusted or drifting
  diagnostics, unexpected helper/resource growth, unrelated state changes, or
  wrong host receipts fail closed without retaining a mutation.
  Seven focused real-host tests are registered through Sketcher's aggregate
  test entry point and cover internal, external, mixed-order, constraint,
  expression, invalid-target purity, legacy API, and exact undo/redo behavior.
  The focused Native/schema suite is 18/18 green; all Native Sketch tests are
  1,401/1,401 green; all Native tests are 2,024/2,024 green; and the complete
  current `vibecad_tests` sweep is 2,703 passed with four intentional skips.
  The individual schema is exactly 1,093 bytes and all forty-five geometry
  variants serialize to 19,357 bytes against the unchanged 65,536-byte cap.
  The focused real-GUI gate proves mixed internal/external conversion, controls,
  knots, constraints, expression removal, stale/point refusal, one undo/redo,
  save/reopen, selection, and edit-context preservation. The accumulated
  real-GUI lifecycle covers all 67 implemented Sketch operations in one shared
  editable document and verifies durable state after save/reopen. Both
  protected Sketcher and Part Design VibeScript lifecycles exit zero. Final
  sequential VibeCADScripts and Sketcher builds and the explicit SketcherGui
  build are green. All row modules are below 500 lines. The original read-only
  5-axis fixture was never saved or modified and remains byte-identical at
  SHA-256 `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  B-spline degree increase is now the deliberate fail-closed
  `sketch.geometry` surface boundary.
- B-spline degree increase is the forty-sixth exact `sketch.geometry` variant
  and maps only the live `Sketcher_BSplineIncreaseDegree` action. Its closed
  request names the exact active Sketch, 1–256 ordered unique current internal
  B-spline geometry indices, and observed geometry, constraint,
  external-reference, and external-geometry counts. Empty selections, axes,
  points, external or grouped geometry, duplicate or stale indices/counts,
  non-B-splines, degree-25 curves, unhealthy solver/external state, excessive
  helper growth, and any ambiguous or non-applicable target fail closed before
  mutation; no raw selection path or command dispatch is exposed.
  Sketcher now exposes additive `diagnoseIncreaseBSplineDegree(list[int])` and
  `increaseBSplineDegreeExact(list[int])` host APIs while retaining the
  existing public API and human-command behavior. The exact path clones each
  complete Part geometry object, elevates its degree exactly once in detached
  preflight, and then replaces the live root and exposes only missing control
  points and knots. This avoids the legacy raw-OCC reconstruction path and
  preserves construction state, durable tags, expressions, placement,
  tolerance, external state, and unaffected geometry/constraint identity and
  order.
  Native freezes the complete geometry, constraint, external, expression,
  solver, configuration, and durable-identity state; computes an independent
  curve proof containing degree, poles, weights, knots, multiplicities,
  periodic/rational/closed flags, parameter range, and nine shape samples;
  proves detached-diagnosis purity; diagnoses again immediately before
  commit; commits once in one named transaction; and verifies the complete
  host receipt and postcondition. Degree must increase by exactly one, knot
  multiplicities by exactly one, knots/range/flags and sampled shape must stay
  invariant, pole growth is bounded, pre-existing helpers may move only to the
  exact elevated control/knot positions without changing identity or metadata,
  and every newly appended helper/constraint must have the exact construction
  and InternalAlignment/Weight/Equal semantics. Untrusted, impure, stale, or
  drifting diagnostics and unrelated-state changes fail closed without
  retaining a mutation.
  Seven focused real-host tests are registered through Sketcher's aggregate
  entry point and cover unexposed and already-exposed B-splines, exact degree
  elevation and shape preservation, helper/constraint behavior, metadata and
  expression identity, invalid/maximum-degree purity, legacy behavior, and
  exact undo/redo. The focused Native/schema suite is 21/21 green; the
  complete geometry-schema group is 50/50 green; all Native Sketch tests are
  1,420/1,420 green; all Native tests are 2,043/2,043 green; and the complete
  current `vibecad_tests` sweep is 2,722 passed with four intentional skips.
  The individual schema is exactly 1,070 bytes and all forty-six geometry
  variants serialize to 19,432 bytes against the unchanged 65,536-byte cap.
  The focused real-GUI gate proves degree/shape/identity preservation,
  existing and created helper semantics, constraints and expressions,
  maximum-degree and stale-target refusal, one undo/redo, selection and edit
  preservation, and durable FCStd save/reopen state. The accumulated real-GUI
  lifecycle covers all 68 implemented Sketch operations in one shared editable
  document and verifies every separate operation Sketch after save/reopen.
  Both protected Sketcher and Part Design VibeScript lifecycles exit zero.
  Final sequential VibeCADScripts, SketcherScripts, Sketcher, and explicit
  SketcherGui builds are green; focused Ruff lint/format checks and
  `git diff --check` are green. The installed clang-format 18.1.3 cannot parse
  the repository configuration's `BreakTemplateDeclarations` key, so no false
  C++ formatting-pass claim is recorded. Every new row module is below 500
  lines. No VibeCAD/FreeCAD process remains, the retired generated
  `VibeCADWorkbenchTools.py` artifact is absent, and the original read-only
  5-axis fixture was never saved or modified and remains byte-identical at
  SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  B-spline degree decrease is now the deliberate fail-closed
  `sketch.geometry` surface boundary.
- B-spline degree decrease is the forty-seventh exact `sketch.geometry`
  variant and maps only the live `Sketcher_BSplineDecreaseDegree` action. Its
  closed request names one exact current internal B-spline geometry index, the
  exact active Sketch, all observed geometry, constraint, external-reference,
  and external-geometry counts, and an explicit finite
  `maximum_deviation_mm`. Lists, booleans, axes, external/grouped geometry,
  non-B-splines, degree-one curves, stale counts or indices, unhealthy
  solver/external state, malformed helper alignment, custom constraints or
  expressions on disposable helpers, excessive helper growth, and any
  approximation above the requested loss limit fail closed before mutation.
  The normal result is concise: the exact root index, old and new degrees,
  measured deviation, retained-helper count, and created/deleted geometry and
  constraint counts.
  Sketcher now exposes additive `diagnoseDecreaseBSplineDegree(int)` and
  `decreaseBSplineDegreeExact(int)` host APIs while retaining the existing
  public `decreaseBSplineDegree(int, int)` behavior. Detached diagnosis clones
  the complete Part geometry, applies the human command's one-degree-lower OCC
  approximation, preserves the root object and its metadata, matches existing
  control/knot helpers by exact position, remaps retained InternalAlignment
  indices, deletes only obsolete helpers and their generated constraints, and
  exposes the complete reduced helper set. The exact commit preserves the root
  index, root tag, construction/layer metadata, unrelated geometry,
  constraints, names, expressions, placement, tolerance, and external state.
  Seven focused real-host tests cover pure diagnosis, the actual lossy
  cubic-to-quadratic approximation, exposed-helper reconciliation, unrelated
  expression identity, malformed alignment, invalid targets, the legacy API,
  and exact undo/redo.
  Native freezes and verifies the complete transform state and independently
  proves degree, poles, weights, knots, multiplicities, periodic/rational/
  closed flags, parameter range, and 129 sampled positions. It computes a
  parameterization-independent symmetric sampled-polyline deviation, diagnoses
  again immediately before commit, runs one named transaction, and verifies
  the complete mutation receipt, reduced representation, deviation, helper
  positions and roles, alignment indices, solver state, and all unrelated
  records. UUIDs for newly created diagnostic-clone helpers are correctly
  treated as commit-time identities and canonicalized only for created entries;
  every pre-existing geometry and constraint identity remains exact. This
  closes real clone-to-clone UUID nondeterminism without weakening final-state
  or receipt verification.
  The focused Native/schema suite is 21/21 green, the complete geometry-schema
  group is 52/52 green, all Native Sketch tests are 1,441/1,441 green, all
  Native tests are 2,064/2,064 green, and the complete current
  `vibecad_tests` sweep is 2,743 passed with four intentional skips. The
  individual schema is exactly 1,088 bytes and all forty-seven geometry
  variants serialize to 19,804 bytes against the unchanged 65,536-byte cap.
  The focused real-GUI gate proves explicit loss-limit refusal, degree and root
  identity, helper deletion/creation and alignment, unrelated named-expression
  preservation, stale/non-spline/linear-spline refusal, one undo/redo,
  selection and edit-context preservation, and durable FCStd save/reopen state.
  The accumulated real-GUI lifecycle covers all 69 implemented Sketch
  operations in one shared editable document and verifies every separate
  operation Sketch after save/reopen. Both protected Sketcher and Part Design
  VibeScript lifecycles exit zero. Final sequential VibeCADScripts,
  SketcherScripts, Sketcher, and explicit SketcherGui builds are green; focused
  Ruff lint/format checks and `git diff --check` are green. Every row module is
  below 500 lines. No VibeCAD/FreeCAD process remains, the retired generated
  `VibeCADWorkbenchTools.py` artifact is absent, and the original 5-axis
  fixture passed a final read-only GUI hover/preselection regression without
  being saved or modified; its SHA-256 remains
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  B-spline knot-multiplicity increase is now the deliberate fail-closed
  `sketch.geometry` surface boundary.
- B-spline knot-multiplicity increase is the forty-eighth exact
  `sketch.geometry` variant and maps only the live
  `Sketcher_BSplineIncreaseKnotMultiplicity` child action. Its closed request
  names the exact active Sketch, all observed geometry, constraint,
  external-reference, and external-geometry counts, one exact current internal
  B-spline geometry index, and one exact zero-based knot index. Lists,
  booleans, axes, external/grouped geometry, non-splines, stale counts or
  indices, end knots already at maximum multiplicity, unhealthy solver or
  external state, malformed/duplicate helper alignment, custom helper
  constraints, excessive helper growth, and any sampled shape displacement
  above 0.001 mm fail closed before mutation. The normal result is concise:
  the root geometry index, knot index and parameter, degree, old and new
  multiplicities, and retained, deleted, and exposed helper counts.
  Sketcher now exposes additive
  `diagnoseIncreaseBSplineKnotMultiplicity(int, int)` and
  `increaseBSplineKnotMultiplicityExact(int, int)` host APIs while retaining
  the existing public OCC-indexed `modifyBSplineKnotMultiplicity` behavior.
  Detached diagnosis clones the complete Sketch state, increases exactly one
  internally converted one-based OCC knot multiplicity by one, preserves the
  root geometry object, durable identity, and metadata, maps existing control
  and knot helpers by exact position, removes only obsolete generated helpers,
  and exposes the complete resulting helper set. The exact commit preserves
  unrelated geometry, constraints, names, expressions, placement, tolerance,
  external state, and root index, identity, construction, and layer metadata.
  Seven focused real-host tests cover diagnosis purity, exact commit,
  root/construction identity, complete helper reconciliation, unrelated
  expression and constraint preservation, invalid geometry and knot targets,
  maximum multiplicity, malformed duplicate alignment, the legacy API, and
  one-step undo/redo.
  Native freezes and verifies the complete transform state and independently
  proves degree, poles, weights, knots, multiplicities, periodic/rational/
  closed flags, parameter range, and 129 pointwise shape samples. It diagnoses
  again immediately before commit, runs one named transaction, and verifies
  the complete mutation receipt, exactly one multiplicity increment, exactly
  one added pole, unchanged knot vector/domain/representation flags, the hard
  0.001 mm sampled-displacement ceiling, helper positions and roles, alignment
  indices, solver state, and every unrelated record. UUID canonicalization is
  limited to helpers created by detached diagnosis; all pre-existing geometry
  and constraint identities remain exact. The focused operation/schema plus
  degree-decrease and geometry-schema regression set is 82/82 green; all
  Native Sketch tests are 1,456/1,456 green; all Native tests are
  2,079/2,079 green; and the complete current `vibecad_tests` sweep is 2,758
  passed with four intentional skips. The individual schema is exactly 1,079
  bytes and all forty-eight geometry variants serialize to 20,124 bytes
  against the unchanged 65,536-byte cap.
  The focused real-GUI gate proves stale-count, wrong-geometry, out-of-range,
  and maximum-endpoint refusal; detached-diagnosis purity; exact
  multiplicity, root identity, sampled shape, helper, constraint, and
  expression state; selection and edit-context preservation; one undo/redo;
  and durable FCStd save/reopen state. The accumulated real-GUI lifecycle
  covers all 70 implemented Sketch operations in one shared editable document
  and verifies every separate operation Sketch after save/reopen. Both
  protected Sketcher and Part Design VibeScript lifecycles exit zero. Final
  sequential VibeCADScripts, SketcherScripts, Sketcher, and explicit
  SketcherGui builds are green; focused Ruff lint/format checks and
  `git diff --check` are green. Every row module is below 500 lines. The
  degree-decrease helper state was cleanly generalized for both B-spline
  mutations, with no stale source reference or generated build copy. No
  VibeCAD/FreeCAD process remains, the retired generated
  `VibeCADWorkbenchTools.py` artifact is absent, and the original read-only
  5-axis fixture was never saved or modified and remains byte-identical at
  SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  B-spline knot-multiplicity decrease is now the deliberate fail-closed
  `sketch.geometry` surface boundary.
- B-spline knot-multiplicity decrease is the forty-ninth exact
  `sketch.geometry` variant and maps only the live
  `Sketcher_BSplineDecreaseKnotMultiplicity` child action. Its closed request
  names the exact active Sketch, all observed geometry, constraint,
  external-reference, and external-geometry counts, one exact current internal
  B-spline geometry index, one exact zero-based knot index, and an explicit
  finite `maximum_deviation_mm`. Lists, booleans, axes, external/grouped
  geometry, non-splines, stale counts or indices, out-of-range knots,
  unhealthy solver or external state, malformed/duplicate helper alignment,
  custom constraints or expressions on disposable helpers, excessive helper
  growth, kernel-refused endpoint removal, and any approximation above the
  requested loss limit fail closed before mutation. The normal result is
  concise: the root geometry index, knot index and parameter, degree, old and
  new multiplicities, measured deviation, and retained, deleted, and exposed
  helper counts.
  Sketcher now exposes additive
  `diagnoseDecreaseBSplineKnotMultiplicity(int, int)` and
  `decreaseBSplineKnotMultiplicityExact(int, int)` host APIs while retaining
  the existing public OCC-indexed `modifyBSplineKnotMultiplicity` behavior.
  Increase and decrease share one exact host kernel without changing the human
  command path. Detached diagnosis clones the complete Sketch state and applies
  exactly one decrement: a higher-multiplicity knot remains with multiplicity
  reduced by one, while a multiplicity-one interior knot is removed using the
  human command's OCC tolerance. The root geometry object, durable identity,
  and metadata are preserved; existing control and knot helpers are matched by
  exact position, only obsolete generated helpers are removed, retained
  InternalAlignment indices are remapped, and every missing helper in the
  reduced representation is exposed. Unrelated geometry, constraints, names,
  expressions, placement, tolerance, external state, and root index,
  construction, layer, and durable identity remain exact. Eight focused
  real-host tests cover detached-diagnosis purity, actual positive-loss
  interior-knot removal, retained higher-multiplicity knots, complete helper
  reconciliation, root/construction identity, unrelated constraint and
  expression identity, invalid types and targets, malformed duplicate
  alignment, kernel-refused endpoint purity, the legacy API, and exact
  undo/redo.
  Native freezes and verifies the complete transform state and independently
  proves degree, poles, weights, knots, multiplicities, periodic/rational/
  closed flags, parameter range, and 129 sampled positions. Loss is computed
  with a parameterization-independent symmetric sampled-polyline deviation.
  Native diagnoses again immediately before commit, runs one named
  transaction, and verifies the complete host receipt, retained-knot or
  removed-knot representation semantics, the explicit loss limit, helper
  positions and roles, alignment indices, solver state, and every unrelated
  record. Detached-clone UUID canonicalization remains limited to newly created
  helpers; every pre-existing geometry and constraint identity stays exact.
  Shared representation-proof, mutation-state, and helper-reconciliation
  modules serve both multiplicity directions, avoiding a duplicated state
  monolith; every row module is below 500 lines.
  The focused increase/decrease/schema plus degree-decrease and
  geometry-schema regression set is 97/97 green; all Native Sketch tests are
  1,471/1,471 green; all Native tests are 2,094/2,094 green; and the complete
  current `vibecad_tests` sweep is 2,773 passed with four intentional skips.
  The individual schema is exactly 1,173 bytes and all forty-nine geometry
  variants serialize to 20,404 bytes against the unchanged 65,536-byte cap.
  The focused real-GUI gate proves stale-count, zero-loss-limit,
  kernel-refused endpoint, non-spline, and out-of-range refusal;
  detached-diagnosis purity; actual positive-loss knot removal within the
  explicit limit; root, helper, constraint, expression, selection, and
  edit-context identity; one undo/redo; and durable FCStd save/reopen state.
  The accumulated real-GUI lifecycle covers all 71 implemented Sketch
  operations in one shared editable document and verifies every separate
  operation Sketch after save/reopen. Both protected Sketcher and Part Design
  VibeScript lifecycles exit zero. Final sequential VibeCADScripts,
  SketcherScripts, Sketcher, and explicit SketcherGui builds are green; focused
  Ruff lint/format checks and `git diff --check` are green. No VibeCAD/FreeCAD
  process remains; the old multiplicity-increase source filenames, their stale
  generated build copy, the retired generated `VibeCADWorkbenchTools.py`, and
  the earlier degree-decrease helper artifact are absent. The protected 5-axis
  fixture was never saved or modified and remains byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  B-spline knot insertion is now the deliberate fail-closed
  `sketch.geometry` surface boundary.
- B-spline knot insertion is the fiftieth exact `sketch.geometry` variant and
  maps only the live `Sketcher_BSplineInsertKnot` child action. Its closed
  request names the exact active Sketch, all observed geometry, constraint,
  external-reference, and external-geometry counts, one exact current internal
  B-spline geometry index, and one explicit finite parameter. Booleans,
  non-finite or billion-scale values, stale counts, external or grouped
  geometry, non-splines, parameters outside the actual curve domain, knots
  already at maximum multiplicity, unhealthy solver or external state,
  malformed or duplicate helper alignment, custom constraints or expressions
  on disposable helpers, and excessive helper growth all fail closed before
  mutation. The concise result reports the root geometry index, requested and
  resolved knot parameters, degree, old and new multiplicities, sampled
  displacement, and retained, deleted, and exposed helper counts.
  Sketcher now exposes additive `diagnoseInsertBSplineKnot(int, double)` and
  `insertBSplineKnotExact(int, double)` host APIs while retaining the existing
  public `insertBSplineKnot(int, double, int)` API and human command path.
  Detached diagnosis clones the complete Sketch and external state. Exact
  commit applies the same OCC insertion primitive as the human action: a new
  parameter creates exactly one knot with multiplicity one, while an existing
  knot gains exactly one multiplicity. Both cases add exactly one control pole,
  preserve degree and curve shape, retain the root geometry object, durable
  identity, construction state, and metadata, and use the shared host helper
  reconciler to retain exact-position helpers, delete only obsolete generated
  helpers, remap InternalAlignment indices, and expose only missing helpers.
  Unrelated geometry, constraints, names, expressions, placement, tolerance,
  and external state remain exact. Seven real-host tests cover new and existing
  knot semantics, detached-diagnosis purity, helper and expression identity,
  invalid finite/domain/type/geometry targets, maximum endpoint refusal,
  duplicate alignment, the legacy API, and exact undo/redo.
  Native freezes and verifies the complete transformation state, diagnoses
  again immediately before commit, performs one named transaction, and proves
  the exact host receipt, new-knot or existing-knot representation, unchanged
  degree, parameter domain and representation flags, exactly one added pole,
  root and unrelated-record identity, helper reconciliation, solver health,
  and 129 pointwise curve samples under the hard 0.001 mm displacement ceiling.
  Detached-clone UUID canonicalization remains limited to newly created
  helpers. The individual schema is exactly 1,075 bytes and all fifty geometry
  variants serialize to 20,690 bytes against the unchanged 65,536-byte cap.
  The focused B-spline/schema regression set is 112/112 green; all Native
  Sketch tests are 1,486/1,486 green; all Native tests are 2,109/2,109 green;
  and the complete current `vibecad_tests` sweep is 2,788 passed with four
  intentional skips.
  The focused real-GUI gate proves stale-count, before-domain, after-domain,
  non-spline, and maximum-endpoint refusal; detached-diagnosis purity; exact
  insertion shape, root, helper, constraint, expression, selection, and edit
  state; one undo/redo; and durable FCStd save/reopen state. The accumulated
  real-GUI lifecycle covers all 72 implemented Sketch operations and verifies
  every separate operation Sketch after one shared save/reopen. Both protected
  Sketcher and Part Design VibeScript lifecycles exit zero. Final sequential
  VibeCADScripts, SketcherScripts, Sketcher, and SketcherGui builds are green;
  focused Ruff checks and `git diff --check` are clean. Every row module is
  below 500 lines. The old multiplicity-increase source/build artifacts, the
  earlier degree-decrease helper artifact, and retired generated
  `VibeCADWorkbenchTools.py` remain absent; no VibeCAD/FreeCAD process remains.
  The protected 5-axis fixture was never saved or modified and remains
  byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Curve joining is now the deliberate fail-closed `sketch.geometry` surface
  boundary.
- Curve joining is the fifty-first exact `sketch.geometry` variant and maps
  only the live `Sketcher_JoinCurves` child action. Its closed request names
  the exact active Sketch, all observed geometry, constraint,
  external-reference, and external-geometry counts, and two distinct exact
  internal curve endpoints expressed only as `start` or `end`. The assistant
  cannot request a continuity level: Native and the host derive C0 or C1 from
  the same exact endpoint Tangent constraint inspected by the human command.
  Stale counts, axes or external geometry, duplicate curves, whole-curve or
  non-endpoint targets, points, closed or periodic curves, internal helper
  geometry, grouped/Text members, mixed construction state, unhealthy solver
  or external state, excessive helper growth, malformed receipts, duplicate
  or missing helper roles, and any detached/live result disagreement fail
  closed. The concise result reports both requested endpoints, derived C0/C1
  continuity, the joined B-spline index and representation, exact deleted and
  created geometry indices, and the generated helper count.
  Sketcher now exposes additive
  `diagnoseJoinCurves(int, PointPos, int, PointPos)` and
  `joinCurvesExact(int, PointPos, int, PointPos)` host APIs while retaining the
  existing public `join(...)` API and human command. Diagnosis clones the
  complete Sketch state, runs the same join primitive on the clone, bounds the
  resulting geometry, and solves it without touching the live document. Exact
  commit repeats that preflight before applying the shared primitive. The
  shared C1 path now safely elevates degree-one inputs to degree two before
  forming the interior join knot and rejects a zero endpoint derivative,
  fixing the prior invalid zero-multiplicity tangent join without changing C0
  behavior. Seven real-host tests prove pure detached C0/C1 diagnosis, endpoint
  reversal, valid joining when both source B-splines already expose their
  generated helpers, invalid-target refusal, unrelated named expression and
  durable identity preservation, legacy API availability, and one exact
  undo/redo transaction.
  Native freezes the complete geometry, constraint, expression, external,
  solver, placement, and tolerance state; diagnoses again immediately before
  commit; and performs one named document transaction. Its independent proof
  requires both selected roots and only their aligned helpers to be deleted,
  every unrelated geometry and constraint to retain its identity, exactly one
  open non-periodic B-spline root, its endpoints to equal the two unselected
  source endpoints in the requested orientation, and exactly one unique
  `InternalAlignmentIndex` for every control pole and knot helper. Final
  verification compares the complete canonical host state, exact mutation
  receipt, remapped expressions, external records, solver degrees of freedom,
  and configuration token to the frozen diagnostic plan.
  The focused Native/schema set is 20/20 green; all Native Sketch tests are
  1,506/1,506 green; all Native tests are 2,129/2,129 green; and the complete
  current `vibecad_tests` sweep is 2,808 passed with four intentional skips.
  The individual schema is exactly 1,374 bytes and all fifty-one geometry
  variants serialize to 21,324 bytes against the unchanged 65,536-byte cap.
  The focused compiled real-GUI gate proves stale-count, duplicate-curve, and
  invalid-endpoint refusal; detached-diagnosis purity; exact C0 B-spline and
  complete helper topology; unrelated named expression and identity
  preservation; one undo/redo; unchanged selection/edit boundary; and durable
  FCStd save/reopen state. The accumulated real-GUI lifecycle covers all 73
  implemented Sketch operations in one long-lived document and verifies every
  separate operation Sketch after the shared save/reopen cycle. Both protected
  Sketcher and Part Design VibeScript lifecycles exit zero. Final sequential
  VibeCADScripts, SketcherScripts, Sketcher, and SketcherGui builds are green;
  focused Ruff format/lint checks and `git diff --check` are clean. The row's
  host and Native implementation modules range from 100 to 366 lines. No
  VibeCAD/FreeCAD process remains, and the retired generated
  `VibeCADWorkbenchTools.py` stays absent from source and build output. The
  protected 5-axis fixture was never opened for mutation, saved, or modified
  and remains byte-identical at SHA-256
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Constraint-based element selection is now the deliberate fail-closed Sketch
  surface boundary.
- Constraint-based element selection is the first exact `sketch.inspect`
  variant and maps only the live `Sketcher_SelectConstraints` action. It is a
  primary read with `transaction_behavior="none"`: the assistant supplies one
  exact active Sketch, observed geometry/constraint/external-geometry counts,
  and 1–32 distinct internal, external, axis, whole-geometry, or exact-point
  selections. The operation does not call the GUI selection API and does not
  reproduce the human command's selection side effect. Instead it returns the
  current matching constraint indices, types, optional names, per-constraint
  matched-selection indices, bounded counts, and canonical geometry and
  constraint state hashes.
  The implementation freezes complete geometry, constraint, expression,
  external-geometry, solver, and full `Constraint.Elements` relationship
  state before the read and repeats the freeze afterward. It mirrors the
  human command's `involvesGeoId` semantics for whole geometry and
  `involvesGeoIdAndPosId` semantics for exact points, including Group members
  beyond the first three legacy slots, internal helpers, axes, and external
  geometry. It recognizes only the host's exact `(-2000, 0)` undefined-slot
  padding and rejects malformed references, unavailable points, duplicate or
  stale selections, missing/detached/unsynchronized external geometry,
  non-unique live constraint identities, relationship/resource overflow, or
  any state drift during the read.
  Twenty-four focused domain/schema tests are green. The focused compiled GUI
  gate proves whole, endpoint, and multi-element reads; stale-count refusal;
  exact parity with the human `Sketcher_SelectConstraints` result; unchanged
  human selection, edit/ribbon/workbench boundary, transaction state, undo
  history, and document state; and durable relationship state after FCStd
  save/reopen. The accumulated real-GUI lifecycle is green for all 74
  implemented Sketch operations and verifies every separate operation Sketch
  after one shared save/reopen. The individual provider schema is 1,291 bytes
  and the complete three-tool rolling Sketch schema is 51,529 bytes against
  the unchanged 65,536-byte limit. The complete current `vibecad_tests` sweep
  is 2,832 passed with four intentional skips. Both protected Sketcher and
  Part Design VibeScript lifecycles exit zero with the final Part Design
  result reporting `"ok": true`. Final sequential VibeCADScripts,
  SketcherScripts, Sketcher, and SketcherGui builds are green; the row's nine
  implementation/test modules range from 42 to 360 lines. The upstream
  `TestViewProviderLink` suite is 5/5 green. The original 5-axis machine was
  opened only as a read-only regression fixture: nested linked-Body detail
  resolution and a real mouse-move preselection sweep both exit cleanly, and
  its before/after SHA-256 remains exactly
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  Element-associated constraint selection
  (`Sketcher_SelectElementsAssociatedWithConstraints`) is now the deliberate
  fail-closed Sketch surface boundary.
- Element-associated constraint selection is the second and completing
  `sketch.inspect` variant and maps only the live
  `Sketcher_SelectElementsAssociatedWithConstraints` action through the
  explicit `select_elements` operation. Its closed request names the exact
  active Sketch, observed geometry/constraint/external-geometry counts, and
  1–32 distinct constraints by current index, exact type, and exact name. It
  returns the selected constraint summaries, every ordered unique associated
  whole geometry or exact point, per-element matching input indices, bounded
  counts, and canonical geometry and constraint state hashes. It is a primary
  read with no document transaction and never changes the human GUI selection.
  The operation shares bounded exact element and full relationship-state
  modules with `select_constraints`, rejects stale indices/types/names/counts,
  malformed or overflowing relationships, unavailable points or axes,
  missing/detached/unsynchronized external geometry, non-unique live
  constraint identities, and any state drift during the read. It reads the
  complete bounded `Constraint.Elements` relationship rather than truncating
  to legacy `First`/`Second`/`Third` fields. The real host proves why this
  matters: for a Group with a handle and three members, the stock human
  command selects only the handle, while Native reports the handle and all
  three members without mutating the document or GUI selection.
  Twenty-three new focused domain/schema cases are green, bringing the
  combined `sketch.inspect` set to 47/47. The focused compiled GUI gate proves
  both relationship directions, ordinary whole/point/multi-selection parity,
  exact stale refusal, full Group relationships, unchanged
  selection/edit/ribbon/workbench/transaction/undo state, and durable FCStd
  save/reopen state. The accumulated rolling GUI lifecycle passes all 75
  implemented Sketch operations, including both inspect variants, and
  verifies every separate operation Sketch after the shared save/reopen. The
  `select_elements` provider schema is 1,213 bytes, the combined inspect schema
  is 2,002 bytes, and the complete three-tool rolling Sketch schema is 52,240
  bytes against the unchanged 65,536-byte cap. The complete current
  `vibecad_tests` sweep is 2,855 passed with four intentional skips. Both
  protected Sketcher and Part Design VibeScript lifecycles exit zero, with the
  final Part Design result reporting `"ok": true`. Final sequential
  VibeCADScripts, SketcherScripts, Sketcher, and SketcherGui builds are green;
  focused Ruff lint/format checks and `git diff --check` are clean. Shared and
  reverse inspect implementation modules are split from 61 to 242 lines, and
  the real-GUI case remains 344 lines. The upstream `TestViewProviderLink`
  suite is 5/5 green. The original 5-axis machine was used only as an immutable
  crash-regression fixture: nested linked-Body detail resolution and a real
  mouse-move preselection sweep exit cleanly, and its before/after SHA-256
  remains exactly
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  `Sketcher_ArcOverlay` in the unfinished `sketch.presentation` family is now
  the deliberate fail-closed Sketch surface boundary.
- Circular arc helper visibility is now the first `sketch.presentation`
  variant and maps only `Sketcher_ArcOverlay` through the explicit
  `arc_overlay` operation. Its closed request names the exact human-opened
  Sketch, observed geometry/constraint/external-geometry counts, the expected
  current visibility, and the desired visibility. The operation is an
  idempotent primary view/presentation action: it changes the global Sketcher
  `ArcCircleHelperVisible` presentation preference without opening a document
  transaction, creating an undo entry, changing selection, or mutating FCStd
  state. It follows the renderer's actual absent-key default of hidden instead
  of the stock toggle command's inconsistent absent-key assumption.
  The runtime freezes complete canonical internal geometry, constraints, and
  external geometry before the preference write and verifies the identical
  state afterward. It rejects a stale preference or count, wrong active
  document/surface/edit target, malformed request, failed host write, or any
  model drift. A no-op performs no preference write. A failed verification
  restores the prior value only while the preference still equals the value
  Native wrote, so an intervening human change is not overwritten; a host
  write that changes the value and then raises is covered by the same
  rollback rule. Success returns only the exact Sketch reference, prior and
  current visibility, changed status, internal/external arc counts, bounded
  Sketch counts, and canonical geometry/constraint/external-state hashes.
  Sixteen focused domain/schema cases are green. The focused compiled GUI
  gate creates two real circular arcs and proves the actual
  `InformationGroup` `SoSwitch` nodes transition hidden to visible under
  Native, the human `Sketcher_ArcOverlay` command produces the matching inverse
  state, stale and no-op behavior is exact, edit/ribbon/workbench/selection/
  transaction/undo/document state is unchanged, and FCStd save/reopen retains
  the model. The gate snapshots and restores the user's exact global
  preference state, including key absence. The accumulated rolling GUI
  lifecycle passes all 76 implemented Sketch operations and verifies every
  separate operation Sketch after the shared save/reopen. The arc-overlay
  schema is 882 bytes and the complete four-tool rolling Sketch schema is
  53,121 bytes against the unchanged 65,536-byte cap. Production remains
  deliberately unavailable because later `sketch.presentation` actions are
  still incomplete; the family is now reported as incomplete rather than
  missing.
  The complete current `vibecad_tests` sweep is 2,871 passed with four
  intentional skips. Both protected Sketcher and Part Design VibeScript
  lifecycles exit zero, with the final Part Design result reporting
  `"ok": true`. Final sequential VibeCADScripts, SketcherScripts, Sketcher,
  and SketcherGui builds are green; focused Ruff lint/format checks and
  `git diff --check` are clean. New production modules range from 44 to 190
  lines and the focused GUI modules from 175 to 215 lines. The upstream
  `TestViewProviderLink` suite is 5/5 green. The original 5-axis machine was
  opened only as an immutable crash-regression fixture: nested linked-Body
  detail resolution and a real mouse-move preselection sweep exit cleanly,
  and its before/after SHA-256 remains exactly
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  B-spline degree-information visibility in row 10.78 is now the deliberate
  fail-closed Sketch surface boundary.
- B-spline degree-information visibility is now the second
  `sketch.presentation` variant and maps only the live
  `Sketcher_BSplineDegree` action through the explicit `bspline_degree`
  operation. Its closed request names the exact human-opened Sketch, observed
  geometry/constraint/external-geometry counts, expected current visibility,
  and desired visibility. The operation uses Sketcher's exact
  `BSplineDegreeVisible` presentation preference and the renderer's actual
  absent-key default of visible. It is an idempotent primary view action: it
  opens no document transaction, creates no undo entry, changes no selection,
  and mutates no FCStd state.
  A shared bounded preference engine now freezes canonical internal geometry,
  constraints, and external geometry before any write, rejects stale counts or
  visibility, verifies the identical Sketch state afterward, and returns only
  the exact Sketch reference, prior/current visibility, changed status,
  internal/external B-spline counts, bounded Sketch counts, and three canonical
  state hashes. A no-op performs no write. Failed verification restores only a
  value still owned by the Native operation; a concurrent human preference
  change is never overwritten, and a host write that mutates then raises is
  rolled back through the same rule.
  Sixteen new focused domain/schema cases are green, bringing the combined
  presentation set to 32/32. The focused compiled GUI gate creates a real cubic
  B-spline and proves the actual degree-label `InformationGroup` `SoSwitch`
  transitions visible to hidden under Native, the human
  `Sketcher_BSplineDegree` command produces the matching inverse state, stale
  and no-op behavior is exact, edit/ribbon/workbench/selection/transaction/undo
  state remains unchanged, and FCStd save/reopen retains the model. The gate
  snapshots and restores the user's exact global preference state, including
  key absence. The accumulated rolling GUI lifecycle passes all 77 implemented
  Sketch operations and verifies every separate operation Sketch after the
  shared save/reopen. The dedicated provider schema is 885 bytes and the
  complete four-tool rolling Sketch schema is 53,348 bytes against the
  unchanged 65,536-byte cap. Production remains deliberately unavailable
  because later `sketch.presentation` actions are incomplete; B-spline
  control-polygon visibility in row 10.79 is now the fail-closed boundary.
  The complete current `vibecad_tests` sweep is 2,887 passed with four
  intentional skips. Both protected Sketcher and Part Design VibeScript
  lifecycles exit zero, with the final Part Design result reporting
  `"ok": true`. Final sequential VibeCADScripts, SketcherScripts, Sketcher,
  and SketcherGui builds are green; focused Ruff lint/format checks and
  `git diff --check` are clean. New production modules remain between 73 and
  204 lines and the focused GUI modules between 98 and 175 lines.
- B-spline control-polygon visibility is now the third
  `sketch.presentation` variant and maps only the live
  `Sketcher_BSplinePolygon` action through the explicit
  `bspline_control_polygon` operation. Its closed request retains the exact
  active-Sketch identity, bounded geometry/constraint/external-geometry
  counts, expected current visibility, and explicit desired visibility. The
  operation uses Sketcher's exact `BSplineControlPolygonVisible` preference
  and the renderer's real absent-key default of visible. It changes no FCStd
  state, document transaction, undo record, edit boundary, or selection.
  A new 61-line shared B-spline presentation adapter now owns the concise
  B-spline result contract for degree and control-polygon state without
  changing the existing degree entry points. The control-polygon operation is
  50 lines and delegates the already-proven stale detection, no-op behavior,
  owned-value rollback, concurrent-human-change protection, and canonical
  Sketch verification to the common preference engine.
  Thirteen new focused domain/schema cases are green, bringing the combined
  presentation set to 45/45. The focused compiled GUI gate creates the real
  cubic B-spline and identifies the control-polygon layer by its actual four
  pole coordinates, one four-vertex `SoLineSet`, and the renderer's exact
  `zInfo = 0.004` placement. It proves only that layer's `SoSwitch` changes,
  matches the human `Sketcher_BSplinePolygon` command, rejects stale state,
  verifies the no-op path, restores the user's exact global preference
  state—including key absence—and preserves model/edit/ribbon/workbench/
  selection/transaction/undo state through FCStd save/reopen. The prior
  B-spline degree GUI lifecycle remains green after the shared-adapter
  extraction.
  The logged accumulated GUI lifecycle passes all 78 implemented Sketch
  operations and verifies every separate operation Sketch after the shared
  save/reopen. The dedicated control-polygon schema is 894 bytes, the combined
  three-operation presentation schema is 1,184 bytes, and the complete
  four-tool rolling Sketch schema is 53,423 bytes against the unchanged
  65,536-byte cap. Production remains deliberately unavailable because the
  later `sketch.presentation` actions are incomplete; B-spline
  curvature-comb visibility in row 10.80 is now the fail-closed boundary.
  The complete current `vibecad_tests` sweep is 2,900 passed with four
  intentional skips. Both protected Sketcher and Part Design VibeScript
  lifecycles exit zero. Final sequential VibeCADScripts, SketcherScripts,
  Sketcher, and SketcherGui builds are green; focused Ruff lint/format checks
  and `git diff --check` are clean. The upstream `TestViewProviderLink` suite
  remains 5/5 green. The original 5-axis file was never saved or repaired and
  its SHA-256 remains exactly
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  A fresh post-build GUI regression opened only a byte-identical disposable
  copy, found 29 links, exercised four representative linked objects through
  eight explicit `Face1`/`Edge1` preselection calls and 35 real viewport mouse
  moves, then closed normally without a SIGSEGV. Both original and disposable
  copy retained the exact hash, and no VibeCAD process remains.
- B-spline curvature-comb visibility is now the fourth
  `sketch.presentation` variant and maps only the live
  `Sketcher_BSplineComb` action through the explicit
  `bspline_curvature_comb` operation. Its closed request retains the exact
  active-Sketch identity, bounded geometry/constraint/external-geometry
  counts, expected current visibility, and explicit desired visibility. The
  operation uses Sketcher's exact `BSplineCombVisible` preference and the
  renderer's real absent-key default of visible. It delegates stale-state
  detection, idempotent no-op behavior, owned-value rollback,
  concurrent-human-change protection, and canonical Sketch verification to
  the shared presentation engine and concise B-spline adapter. It changes no
  FCStd state, document transaction, undo record, edit boundary, or
  selection.
  Thirteen new focused domain/schema cases are green, bringing the combined
  presentation set to 58/58. The focused compiled GUI gate creates a real
  one-piece cubic B-spline and validates the renderer's actual curvature-comb
  topology: 64 radial two-point line records plus one 64-point spine, 192
  coordinates total, radial endpoints identical to the spine coordinates,
  nonzero curvature scale, and exact `zInfo = 0.004` placement. It proves only
  that layer's `SoSwitch` changes, matches the human `Sketcher_BSplineComb`
  command, rejects stale state, verifies the no-op path, restores the user's
  exact global preference state—including key absence—and preserves model/
  edit/ribbon/workbench/selection/transaction/undo state through FCStd
  save/reopen. The prior control-polygon and degree-information GUI lifecycles
  remain green.
  The logged accumulated GUI lifecycle passes all 79 implemented Sketch
  operations and verifies every separate operation Sketch after the shared
  save/reopen. The dedicated curvature-comb schema is 893 bytes, the combined
  four-operation presentation schema is 1,256 bytes, and the complete
  four-tool rolling Sketch schema is 53,495 bytes against the unchanged
  65,536-byte cap. Production remains deliberately unavailable because later
  `sketch.presentation` actions are incomplete; B-spline knot-multiplicity
  visibility in row 10.81 is now the fail-closed boundary.
  The complete current `vibecad_tests` sweep is 2,913 passed with four
  intentional skips. Both protected Sketcher and Part Design VibeScript
  lifecycles exit zero, with the final Part Design result reporting
  `"ok": true`. Final sequential VibeCADScripts, SketcherScripts, Sketcher,
  and SketcherGui builds are green; the upstream `TestViewProviderLink` suite
  is 5/5 green. The original 5-axis file was never saved or repaired and its
  SHA-256 remains exactly
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  A fresh post-build GUI regression opened only a byte-identical disposable
  copy, found 29 links, exercised four representative linked objects through
  eight explicit `Face1`/`Edge1` preselection calls and 35 real viewport mouse
  moves, then closed normally without a SIGSEGV. Independent before/after
  hashes for the original and disposable copy remained exact, and no VibeCAD
  process remains.
- B-spline knot-multiplicity visibility is now the fifth
  `sketch.presentation` variant and maps only the live
  `Sketcher_BSplineKnotMultiplicity` action through the explicit
  `bspline_knot_multiplicity` operation. Its closed request retains the exact
  active-Sketch identity, bounded geometry/constraint/external-geometry
  counts, expected current visibility, and explicit desired visibility. The
  operation uses Sketcher's exact `BSplineKnotMultiplicityVisible` preference
  and the renderer's real absent-key default of visible. It delegates
  stale-state detection, idempotent no-op behavior, owned-value rollback,
  concurrent-human-change protection, and canonical Sketch verification to
  the shared presentation engine and concise B-spline adapter. It changes no
  FCStd state, document transaction, undo record, edit boundary, or
  selection.
  Thirteen new focused domain/schema cases are green, bringing the combined
  presentation set to 71/71. A pre-implementation live-host probe and the
  focused compiled GUI gate both establish the renderer's exact topology for
  the one-piece cubic fixture: two independent text switches, each containing
  `Material`, `Font`, `Translation`, and `Text2`; exact labels `(4)` and `(4)`;
  endpoint translations `(-12, -3, 0.004)` and `(14, 2, 0.004)`; knots
  `(0.0, 1.0)`; and multiplicities `(4, 4)`. The focused gate proves only
  those switches change, matches the human `Sketcher_BSplineKnotMultiplicity`
  command, rejects stale state, verifies the no-op path, restores the user's
  exact global preference state—including key absence—and preserves model/
  edit/ribbon/workbench/selection/transaction/undo state through FCStd
  save/reopen. The prior curvature-comb, control-polygon, and
  degree-information GUI lifecycles remain green.
  The logged accumulated GUI lifecycle passes all 80 implemented Sketch
  operations and verifies every separate operation Sketch after the shared
  save/reopen. The dedicated knot-label schema is 896 bytes, the combined
  five-operation presentation schema is 1,337 bytes, and the complete
  four-tool rolling Sketch schema is 53,576 bytes against the unchanged
  65,536-byte cap. Production remains deliberately unavailable because later
  `sketch.presentation` actions are incomplete; B-spline pole-weight
  visibility in row 10.82 is now the fail-closed boundary.
  The complete current `vibecad_tests` sweep is 2,926 passed with four
  intentional skips. Both protected Sketcher and Part Design VibeScript
  lifecycles exit zero, with the final Part Design result reporting
  `"ok": true`. Final sequential VibeCADScripts, SketcherScripts, Sketcher,
  and SketcherGui builds are green; the upstream `TestViewProviderLink` suite
  is 5/5 green. The original 5-axis file was never saved or repaired and its
  SHA-256 remains exactly
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  A fresh post-build GUI regression opened only a byte-identical disposable
  copy, found 29 links, exercised four representative linked objects through
  eight explicit `Face1`/`Edge1` preselection calls and 35 real viewport mouse
  moves, then closed normally without a SIGSEGV. Independent before/after
  hashes for the original and disposable copy remained exact, the temporary
  copy was moved to Trash, and no VibeCAD process remains.
- B-spline pole-weight visibility is now the sixth `sketch.presentation`
  variant and maps only the live `Sketcher_BSplinePoleWeight` action through
  the explicit `bspline_pole_weight` operation. Its closed request retains the
  exact active-Sketch identity, bounded geometry/constraint/external-geometry
  counts, expected current visibility, and explicit desired visibility. The
  operation uses Sketcher's exact `BSplinePoleWeightVisible` preference and
  the renderer's real absent-key default of visible. It delegates stale-state
  detection, idempotent no-op behavior, owned-value rollback,
  concurrent-human-change protection, and canonical Sketch verification to
  the shared presentation engine and concise B-spline adapter. It changes no
  FCStd state, document transaction, undo record, edit boundary, or selection.
  Thirteen new focused domain/schema cases are green, bringing the combined
  presentation set to 84/84. A pre-implementation live-host probe established
  the renderer's exact per-pole topology: one text switch for each pole, each
  containing `Material`, `Font`, `Translation`, and `Text2`; the first text
  line is empty, the second is the current weight enclosed in brackets, and
  each label is translated to its exact pole coordinate at `zInfo = 0.004`.
  The focused compiled GUI gate uses nonuniform cubic weights and verifies the
  displayed values against Sketcher's canonical current weights at the
  renderer's active display precision. It proves only those switches change,
  matches the human `Sketcher_BSplinePoleWeight` command, rejects stale state,
  verifies the no-op path, restores the user's exact global preference
  state—including key absence—and preserves model/edit/ribbon/workbench/
  selection/transaction/undo state through FCStd save/reopen. The prior
  knot-multiplicity, curvature-comb, control-polygon, and degree-information
  GUI lifecycles remain green.
  The logged accumulated GUI lifecycle passes all 81 implemented Sketch
  operations and verifies every separate operation Sketch after the shared
  save/reopen. The dedicated pole-weight schema is 890 bytes, the combined
  six-operation presentation schema is 1,400 bytes, and the complete
  four-tool rolling Sketch schema is 53,639 bytes against the unchanged
  65,536-byte cap. Production remains deliberately unavailable because later
  `sketch.presentation` actions are incomplete; internal-alignment geometry
  restoration in row 10.83 is now the fail-closed boundary.
  The complete current `vibecad_tests` sweep is 2,939 passed with four
  intentional skips. Both protected Sketcher and Part Design VibeScript
  lifecycles exit zero, with the final Part Design result reporting
  `"ok": true`. Final sequential VibeCADScripts, SketcherScripts, Sketcher,
  and SketcherGui builds are green; the upstream `TestViewProviderLink` suite
  is 5/5 green. The original 5-axis file was never saved or repaired and its
  SHA-256 remains exactly
  `f896d1c44bcf3249ac3c5b32e343dfe36210af3b0d4683527b1b3623612c7f37`.
  A fresh post-build GUI regression opened only a byte-identical disposable
  copy, found 29 links, exercised four representative linked objects through
  eight explicit `Face1`/`Edge1` preselection calls and 35 real viewport mouse
  moves, then closed normally without a SIGSEGV. Independent before/after
  hashes for the original and disposable copy remained exact, the temporary
  copy was moved to Trash, and no VibeCAD process remains.
- Internal-alignment restoration in row 10.83 is implemented as the explicit
  `restore_internal_alignment_geometry` variant of `sketch.geometry`. The
  request names the exact active Sketch, exact geometry, expected internal
  alignment state, and requested restoration type; the runtime supports the
  five live Sketcher alignment families, rejects stale or partial targets
  before mutation, uses one transaction, rolls back on verification failure,
  preserves selection/edit/ribbon/workbench state, and survives FCStd
  save/reopen. Its focused compiled-host gate passes exact human-command
  parity, atomicity, stale-target rejection, partial-target refusal, rollback,
  selection preservation, and reopen verification.
- Virtual-space switching in row 10.84 follows Sketcher's two distinct live
  semantics through one `set_virtual_space` constraint operation. With no
  selected constraints it changes only the active Sketch view's ephemeral
  real/virtual visibility and creates no transaction or undo record. With
  exact selected constraints it changes the durable
  `Constraint.InVirtualSpace` state atomically, verifies every requested
  target, and rejects stale state. The missing live `ViewSketch` and
  `ViewSection` actions are also implemented as `align_view_to_sketch` and
  `section_view` presentation operations. Camera alignment temporarily
  disables only the active view's animation so orientation can be applied and
  verified synchronously, then restores that view's exact prior animation
  setting; section view verifies and rolls back the Sketch's canonical view
  state.
- The production `sketch.edit` Native surface is now complete and available:
  all 85 shipped Sketch operations resolve into four concise tools, with Leave
  Sketch and Cancel Sketch remaining human-only. A real fresh-Sketch GUI gate
  captures the production turn-start context, freezes its nonempty Sketch-only
  surface, builds the actual Codex declarations, and executes a real
  `sketch.geometry/create_line` provider call. It then proves the required
  lifecycle: the frozen surface is stable during that turn; a human exit to
  Model causes the next turn to resolve Model tools; and human re-entry into
  Sketch causes the following turn to resolve the complete Sketch tools again.
  Exact singleton schema branches are normalized only at the provider adapter
  boundary, leaving the frozen schema and digest unchanged while satisfying
  the provider's object-root requirement.
- The Native Codex launch crash after rebasing onto `origin/main` was traced to
  the provider adapter re-resolving the live modeling surface on its worker
  thread after the turn had already been frozen. The adapter now validates the
  Codex declarations only against the frozen turn-start tool and modeling
  surfaces, so the human-selected surface is read on Qt before launch and is
  never queried again from the provider or nested tool-callback threads. A
  compiled GUI regression exercises the full provider-worker, Codex-callback,
  Native Sketch dispatch path and creates a real line successfully. The
  FreeCAD Python GUI binding also converts every off-main-thread guard failure
  into a normal catchable Python `RuntimeError` instead of allowing a C++
  exception to escape the Python callback boundary and abort the process.
- The post-split rolling compiled GUI gate passes all 85 Sketch operations,
  including internal alignment, both view actions, and both virtual-space
  modes. The focused provider/unit slice passes 60/60, and the full current
  `vibecad_tests` sweep passes 2,966 tests with four intentional skips. Both
  protected Sketcher and Part Design VibeScript integration lifecycles exit
  zero. Sequential VibeCADScripts, SketcherScripts, Sketcher, and SketcherGui
  builds are green, and the GUI-hosted `TestViewProviderLink` suite is 5/5
  green. The shared GUI support module was split at 1,076 lines into focused
  950-line support and 133-line provider-turn modules without changing its
  existing imports.
- The `sketch.edit` surface now excludes `document.save` and continues to
  exclude `document.open`. It exposes one `sketch.control/leave` operation that
  exact-targets the active document UID, Sketch object, and expected geometry
  and constraint counts. The shared C++ exit path validates the exact edit task
  before accepting it, and its Python boundary converts all native failures to
  catchable Python exceptions. A compiled GUI gate proves unavailable Save and
  Open calls do not mutate or leave the Sketch, stale identity and state are
  rejected without leaving, the exact Leave preserves the Sketch and active
  workbench, the current turn is invalidated, and a fresh turn resolves the
  post-edit surface where Save is available. This supersedes the earlier
  human-only Leave Sketch boundary.
  Final verification passes the sequential VibeCADScripts, SketcherScripts,
  Sketcher, and SketcherGui builds; the complete `vibecad_tests` sweep; both
  protected VibeScript lifecycles; the exact Leave GUI lifecycle; and both
  provider-worker dispatch regressions. The existing human Leave Sketch test
  also accepts a provisional Sketch as one global history operation. Broader
  GUI suites remain sensitive to the fixed 1440-pixel test display: the ribbon
  theme gate stops at its 1854-pixel width assertion, and three Sketcher GUI
  cases reject projected click points outside the viewport before reaching the
  operation under test.
- The only remaining production Native raw GUI command, Sketch section-view
  toggling, now uses an exact active-document, document-UID, Sketch-object C++
  API. An architecture gate rejects Native domain use of workbench activation,
  raw commands, edit-mode entry/exit, full-registry imports, hidden
  implementation lookup, and nested binding access. The dispatcher
  reauthorizes the frozen surface and exact document after every handler, while
  the mutation runner reauthorizes immediately before commit and aborts its
  transaction if authority changes. The exact `sketch.control/leave` operation
  is the sole controlled surface-transition exception and must prove both the
  expected `NATIVE_SURFACE_CHANGED` result and its exact next surface. Model
  sketch creation remains outside edit mode and returns
  `next_step.human_action = open_created_sketch`. Focused tests cover switches
  before a call, during mutation, and between calls; the complete pure-Python
  suite, exact compiled GUI lifecycles, required sequential builds, and both
  protected VibeScript integrations are green.
- The 5-axis Documents fixture was changed outside the immutable regression
  after the earlier recorded `f896d1...` baseline: at the final check it had a
  new 08:33 timestamp, a new FreeCAD backup, and SHA-256
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  It was not saved, repaired, or modified during this verification. A
  byte-identical disposable copy of the current file opened and closed in the
  compiled GUI without a SIGSEGV after exercising 29 links, eight explicit
  face/edge preselection calls, and 35 real viewport mouse moves; the current
  original hash remained exact afterward and the disposable test directory
  was moved to Trash.
- Ribbon authority now includes the exact compiled and preference environment,
  not only the currently materialized action graph. A generated GUI header
  records 21 ribbon-relevant CMake feature flags. The controller separately
  publishes the three CAM and two Drawing preferences that can change the
  graph, observes only those five keys, and advances the same monotonic surface
  revision when either the manifest or this environment changes. The strict
  Python reader rejects missing, extra, mistyped, or surface-inappropriate
  environment fields; its canonical environment digest is part of both the
  authorization token and provider modeling-surface ID. Session creation now
  compares that exact frozen ID, closing the provider-launch-to-dispatcher gap
  even when a human leaves and returns to the same ribbon or a relevant setting
  changes between turns.
  Pure contracts prove both compiled-feature and preference drift invalidate a
  frozen turn without mutation. The compiled Drawing GUI lifecycle toggles a
  graph-shaping preference and proves revision, manifest digest, and
  environment digest all change, then restores the user's exact prior value.
  Default live counts remain Model 75, Assemble 53, Mesh 60, Analyze 103,
  Manufacture 54, Drawing 107, Parameters 24, Sketch setup 15, and Sketch edit
  105. The real Model provider workflow, fresh-Sketch inter-turn swap, and
  exact Leave Sketch lifecycle all pass. The complete current Python suite,
  final sequential VibeCADScripts/SketcherScripts/Sketcher/SketcherGui builds,
  and both protected VibeScript integrations are green. The Drawing preference
  is restored to `false`, and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Native turn assembly now resolves one live ribbon manifest and derives both
  modeling identity and schemas from that exact frozen object. Conditional
  Analyze, Manufacture, and all four Drawing preference graphs no longer
  inherit phantom actions from a default graph. MCP forwards the same frozen
  surface and exposes no provider workbench-switch command; only the GUI can
  change ribbons between turns. The retired direct layer was reduced by 121
  implementation modules and six obsolete test suites. Durable Assembly, FEM,
  Drawing, Manufacture, and Part regeneration rebinding now lives in five
  focused modules backed by one exact-selection module, without old provider
  schemas or mutation entry points. The service package now contains only 20
  registered VibeScript/shared tools and one shared runtime. Milestone
  verification is 2,926
  passed with four intentional skips, all four required build targets green,
  both protected VibeScript lifecycles green, and the 5-axis fixture hash
  unchanged.
- The contextual Sketch surface now includes one bounded atomic `sketch.batch`
  create operation alongside the exact single-operation tools. One request can
  add 1–32 points, lines, circles, or circular arcs and 1–16 supported
  constraints using client-local references. All references, geometry kinds,
  point positions, dimensions, active-Sketch identity, and expected counts are
  resolved before a transaction. Sketcher's additional-constraint diagnosis
  rejects redundancy or conflicts before durable constraint insertion, and
  any later degeneracy or postcondition failure rolls back the entire batch.
  Success returns stable host geometry IDs and exact constraint indices plus a
  concise closed-profile and solver summary. The exact production Sketch
  surface serializes to 63,246 bytes under the unchanged 65,536-byte cap.
  A dispatcher-backed compiled GUI gate proves a fully constrained four-line
  profile with 11 constraints in one mutating call, invalid-reference no-op,
  redundant-batch rollback, exact duplicate-call replay, one undo step, exact
  undo/redo, and FCStd save/reopen. A second rolled-back catalog batch exercises
  point, circle, arc, Parallel, Perpendicular, Equal, Angle, Radius, Diameter,
  and Distance construction paths. It reports
  `VIBECAD_NATIVE_SKETCH_BATCH_GUI_OK schema_bytes=63246 profile_mutations=1
  catalog_mutations=1 geometry=4 constraints=11`. The complete pure suite is
  2,934 passed with four intentional skips, Ruff is green, both protected
  VibeScript integrations exit zero, and the 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- The Assemble structure family implements the live
  `Assembly_CreateAssembly`, `Assembly_InsertLink`, and
  `Assembly_InsertNewPart` actions. Assembly creation rechecks the expected
  Assembly count and exact human-active parent both before and inside its
  transaction, honors the one-root preference, and creates one native
  `Assembly::AssemblyObject` with one `Assembly::JointGroup`. Existing-source
  insertion requires the exact open source document UID/name and source object
  name/ID from bounded Assemble state, rejects stale objects, unsaved external
  documents, non-component sources, and dependency cycles, and creates the
  correct `App::Link` or `Assembly::AssemblyLink`. Placement, rigid/flexible
  state, native managed-clone ownership, and the placement transform that a
  flexible AssemblyLink distributes into its cloned components are verified
  after recompute. Automatic grounding is deliberately absent because Ground
  is its own later operation.
- New Part creates one current-document `App::Part`, its empty
  `PartDesign::Body`, and one Assembly occurrence as a single timeline
  operation. Native does not activate the Body, change Assembly edit mode,
  change selection, or open either human task dialog. `Assembly_ActivateAssembly`
  remains human-only. A dispatcher-backed compiled GUI gate proves invalid and
  stale no-ops, root/nested creation, same-document component insertion,
  non-identity flexible external-subassembly insertion, exact native clone
  resources, new-Part timeline ownership, duplicate replay, five independent
  undo/redo entries, unchanged human activation, and two-document FCStd
  save/reopen. It reports `VIBECAD_NATIVE_ASSEMBLY_STRUCTURE_GUI_OK
  assemblies=2 components=4 transactions=5 active_read=true`. The complete
  pure suite is 2,948 passed with four intentional skips; Ruff and sequential
  VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui builds are green.
  The protected Sketcher, Part Design, and Assembly VibeScript integrations all
  exit zero; the Part Design result reports `"ok": true`.
- Assemble Ground and Unground are one desired-state `assembly.joint`
  operation mapped from the live `Assembly_ToggleGrounded` action. A bounded
  request names the exact human-active Assembly and each exact component,
  supplies the expected component and grounded-joint counts, and states every
  component's expected current grounding state. Preflight runs both before and
  inside the transaction and rejects stale, duplicate, malformed, foreign,
  inactive-timeline, or no-op targets before mutation. Human and Native paths
  share the same Assembly ownership predicate; Native creates the real
  `GroundedJoint` and `ViewProviderGroundedJoint`, verifies timeline ownership
  and placement-property locking, and removes the exact joint when ungrounding.
  It leaves human selection and Assembly activation unchanged and returns exact
  created, deleted, and changed-object receipts. A dispatcher-backed compiled
  GUI gate proves two-component atomic ground, duplicate replay, one-step
  undo/redo, partial unground, a second undo/redo cycle, and FCStd save/reopen;
  it reports `VIBECAD_NATIVE_ASSEMBLY_GROUNDING_GUI_OK components=2
  ground_batch=2 unground=1 transactions=2 reopen=true`. The complete suite is
  2,953 passed with four intentional skips; Ruff and sequential VibeCADScripts,
  AssemblyScripts, Assembly, and AssemblyGui builds are green. The protected
  Sketcher, Part Design, and Assembly VibeScript integrations all exit zero,
  no FreeCAD process remains, and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Fixed Joint is an exact `assembly.joint/create_fixed` operation
  mapped from the live `Assembly_CreateJointFixed` action. Its bounded request
  names two distinct active movable components using normalized
  component-relative object/Face/Edge/Vertex paths, exact compatible anchor
  paths, complete connector offsets, expected component placements, expected
  component/grounded/regular-joint counts, the expected solve-on-creation
  preference, and explicit label/reverse state. Preflight runs before and
  inside the transaction and rejects stale placements or counts, malformed or
  foreign joint graphs, unsupported anchors, same-component targets, and an
  existing Fixed pair without mutation. Native creates one real Assembly
  `Joint` with `JointObject.Joint` and `ViewProviderJoint`, uses the same
  connector and reverse operations as the human task path, preserves selection
  and human Assembly activation, and verifies exact topology references,
  offsets, timeline ownership, object graph, moved placements, and bounded
  solver diagnostics. A conflicting, redundant, malformed, or otherwise
  rejected solve rolls back the transaction; the human-equivalent ungrounded
  deferred-solve status remains representable. Assemble state now exposes
  exact component placements and shape counts, regular joints separately from
  grounded joints, the solve preference, and the last bounded solver result.
  A dispatcher-backed compiled GUI gate proves a stale-count no-op, two solved
  Fixed joints with full offsets, reverse behavior, exact duplicate replay,
  undo/redo after each transaction, and FCStd save/close/reopen with both model
  and view proxies restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_FIXED_JOINT_GUI_OK components=3 joints=2
  reverse=true transactions=2 reopen=true`. The complete suite is 2,969
  passed with four intentional skips; Ruff, compileall, diff checks, and the
  VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui build targets are
  green. The protected Sketcher, Part Design, and Assembly VibeScript
  integrations all exit zero, no VibeCAD test process remains, and the
  immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Revolute Joint is an exact `assembly.joint/create_revolute`
  operation mapped from the live `Assembly_CreateJointRevolute` action. It
  reuses a shared regular-joint engine extracted from the proven Fixed path;
  the public Fixed contract remains intact while connector, graph, solver,
  receipt, activation, selection, and lifecycle verification logic has one
  implementation for later joint families. The Revolute request adds explicit
  minimum and maximum enabled states and degree values matching the human
  task UI's `-180` through `180` range. Enabled inverted bounds, non-finite or
  out-of-range angles, stale counts/placements/preferences, duplicate
  Revolute pairs, and malformed graphs all fail before mutation. Native sets
  the real `EnableAngleMin`, `AngleMin`, `EnableAngleMax`, and `AngleMax`
  properties before connector solve, applies full offsets and reverse state,
  and performs a final solve so returned diagnostics describe the final
  configured pose. Bounded Assemble state now returns complete connector
  offsets and angular limits for regular joints. A dispatcher-backed compiled
  GUI gate proves stale-count no-op, exact offsets, both enabled limits,
  reverse behavior, final solver success, duplicate replay, one-step
  undo/redo, and FCStd save/close/reopen with native model and view proxies,
  references, offsets, and limits restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_REVOLUTE_JOINT_GUI_OK components=2 joints=1
  limits=true reverse=true transactions=1 reopen=true`; the original Fixed
  lifecycle gate remains green after extraction. The complete suite is 2,978
  passed with four intentional skips; Ruff, compileall, diff checks, and the
  VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui build targets are
  green. The protected Sketcher, Part Design, and Assembly VibeScript
  integrations all exit zero, no VibeCAD test process remains, and the
  immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Cylindrical Joint is an exact
  `assembly.joint/create_cylindrical` operation mapped from the live
  `Assembly_CreateJointCylindrical` action. It uses the proven shared
  regular-joint engine and names two exact component-rooted connectors with
  complete offsets, expected component placements, label, reverse state,
  expected component/grounded/regular-joint counts, and the expected
  solve-on-creation preference. Its request carries independent enabled states
  and values for minimum/maximum linear travel and minimum/maximum angular
  travel. Angles match the human task UI's `-180` through `180` degree range;
  lengths must be finite and remain inside the existing Native coordinate
  envelope of `-1,000,000` through `1,000,000` mm because the human length
  controls impose no narrower range. Enabled inverted pairs, non-finite or
  out-of-envelope values, stale state, duplicate Cylindrical pairs, invalid
  connectors, and malformed graphs fail without mutation. Native writes all
  eight real Assembly limit properties before connector solve and performs a
  final solve after reverse configuration. Concise Assemble state now exposes
  both linear and angular limits for Cylindrical joints. A dispatcher-backed
  compiled GUI gate proves stale-count no-op, full offsets, all four enabled
  bounds, reverse behavior, final solver success, duplicate replay, one-step
  undo/redo, and FCStd save/close/reopen with model/view proxies, references,
  offsets, and both limit families restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_CYLINDRICAL_JOINT_GUI_OK components=2 joints=1
  length_limits=true angle_limits=true reverse=true transactions=1
  reopen=true`; the Fixed and Revolute lifecycle gates remain green against
  the expanded shared engine. The complete suite is 2,995 passed with four
  intentional skips; Ruff, compileall, diff checks, and the VibeCADScripts,
  AssemblyScripts, Assembly, and AssemblyGui build targets are green. The
  protected Sketcher, Part Design, and Assembly VibeScript integrations all
  exit zero, no VibeCAD test process remains, and the immutable 5-axis fixture
  remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Slider Joint is an exact `assembly.joint/create_slider` operation
  mapped from the live `Assembly_CreateJointSlider` action. It uses the proven
  shared regular-joint engine with Assembly type index 3 and names two exact
  component-rooted connectors with complete offsets, expected component
  placements, label, reverse state, expected component/grounded/regular-joint
  counts, and the expected solve-on-creation preference. The full connector
  offsets preserve the human task UI's Advanced offsets and simplified
  second-connector yaw; no separate rotation field is invented. Slider permits
  translation along the connector axis while restricting rotation, and its
  only real limit properties are `EnableLengthMin`, `LengthMin`,
  `EnableLengthMax`, and `LengthMax`. Independent enabled states and finite
  values use the existing Native coordinate envelope of `-1,000,000` through
  `1,000,000` mm because the human length controls impose no narrower range.
  Enabled inverted bounds, non-finite or out-of-envelope values, stale state,
  duplicate Slider pairs, invalid connectors, and malformed graphs fail
  before mutation. Native writes the four exact properties before connector
  solve, performs a final solve after reverse configuration, and preserves
  activation and human selection. Concise Assemble state exposes linear
  limits for Slider without fabricating angular limits. A dispatcher-backed
  compiled GUI gate proves stale-count no-op, full offsets, both enabled
  linear bounds, reverse behavior, final solver success, duplicate replay,
  one-step undo/redo, and FCStd save/close/reopen with model/view proxies,
  references, offsets, limits, and bounded state restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_SLIDER_JOINT_GUI_OK components=2 joints=1
  limits=true reverse=true transactions=1 reopen=true`; the Fixed, Revolute,
  and Cylindrical lifecycle gates remain green against the expanded shared
  engine. The complete suite is 3,009 passed with four intentional skips;
  Ruff, compileall, diff checks, and the VibeCADScripts, AssemblyScripts,
  Assembly, and AssemblyGui build targets are green. The protected Sketcher,
  Part Design, and Assembly VibeScript integrations all exit zero, no VibeCAD
  test process remains, and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Ball Joint is an exact `assembly.joint/create_ball` operation mapped
  from the live `Assembly_CreateJointBall` action. It uses Assembly type index
  4 and the shared regular-joint engine, which reaches the compiled spherical
  solver used by the human command. Its two exact component-rooted connectors
  retain complete advanced attachment offsets and expected component
  placements, while the joint holds the connector points coincident and leaves
  rotation unrestricted. The provider schema deliberately omits reverse,
  simplified rotation, distance, and limit fields because the human Ball task
  exposes none of those controls; the concise result likewise omits the shared
  engine's internal false reverse state and empty property map. Expected
  component, grounded, and regular-joint counts plus the solve-on-creation
  preference guard stale requests before mutation. Invalid connectors,
  duplicate Ball pairs, malformed graphs, changed placements, and inapplicable
  fields fail closed. Native performs the human pre-solve and a final solve,
  then proves exact object identity, proxies, point references, full offsets,
  solver status, activation, and selection. Concise Assemble state returns the
  Ball connectors without fabricating linear or angular limits. A
  dispatcher-backed compiled GUI gate proves stale-count no-op, two real
  vertex connectors, independent full offsets, final solver success,
  idempotent replay, one-step undo/redo, and FCStd save/close/reopen with
  model/view proxies, references, offsets, and bounded state restored. It
  reports `VIBECAD_NATIVE_ASSEMBLY_BALL_JOINT_GUI_OK components=2 joints=1
  point_connectors=true offsets=true transactions=1 reopen=true`; the Fixed,
  Revolute, Cylindrical, and Slider lifecycle gates remain green against the
  expanded shared engine. The complete suite is 3,016 passed with four
  intentional skips; Ruff, compileall, diff checks, and the VibeCADScripts,
  AssemblyScripts, Assembly, and AssemblyGui build targets are green. The
  protected Sketcher, Part Design, and Assembly VibeScript integrations all
  exit zero, no VibeCAD test process remains, and the immutable 5-axis fixture
  remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Distance Joint is an exact `assembly.joint/create_distance`
  operation mapped from the live `Assembly_CreateJointDistance` action. It
  uses Assembly type index 5, writes the human joint's exact signed `Distance`
  property, supports the human Reverse control, and retains the complete
  independent Offset1 and Offset2 placements from the Advanced controls. It
  deliberately exposes no simplified offset/rotation or linear/angular limit
  fields because the human Distance task has none. Finite distance values use
  the Native coordinate envelope of `-1,000,000` through `1,000,000` mm; bool,
  non-finite, and out-of-envelope values fail before mutation. The contract
  derives the same 37 point, line, circle, curve, plane, cylinder, cone, torus,
  sphere, and fallback geometry modes as the compiled Distance implementation,
  and requires `expected_distance_mode` so topology drift cannot silently
  change solver semantics. Native mirrors the compiled implementation's
  canonical connector priority before creating the joint. This is essential
  because the compiled canonicalizer swaps Reference1/Reference2 and
  Placement1/Placement2 for mixed geometry but does not swap Offset1/Offset2;
  canonicalizing the connector specifications first keeps each complete offset
  attached to its intended component. Expected component, grounded, and
  regular-joint counts plus the solve-on-creation preference guard stale
  requests before mutation. Invalid connectors, changed placements, duplicate
  Distance pairs, malformed graphs, changed geometry modes, and inapplicable
  fields fail closed. Concise Assembly state reports the exact signed distance
  and derived mode without invoking the mutating compiled canonicalizer or
  fabricating limit fields. A dispatcher-backed compiled GUI gate proves a
  deliberately reversed point/plane request is canonicalized with offset
  ownership intact, then verifies stale-count no-op, Reverse, the 18 mm signed
  value, final solver success, idempotent replay, one-step undo/redo, and FCStd
  save/close/reopen with model/view proxies, references, offsets, mode, and
  bounded state restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_DISTANCE_JOINT_GUI_OK components=2 joints=1
  mode=point_plane canonicalized=true distance_mm=18 reverse=true
  transactions=1 reopen=true`; the Fixed, Revolute, Cylindrical, Slider, and
  Ball lifecycle gates remain green. The complete suite is 3,042 passed with
  four intentional skips; Ruff, compileall, diff checks, and the
  VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui build targets are
  green. The protected Sketcher, Part Design, and Assembly VibeScript
  integrations all exit zero, no VibeCAD test process remains, and the
  immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Parallel Joint is an exact `assembly.joint/create_parallel`
  operation mapped from the live `Assembly_CreateJointParallel` action. It
  uses Assembly type index 6 and reaches the compiled
  `ASMTParallelAxesJoint` used by the human command. The two exact
  component-rooted connectors preserve independent complete Offset1 and
  Offset2 placements, and the operation supports the human Reverse control.
  No distance, angle, simplified rotation, or limit properties are invented;
  the human task's Rotate 90 control merely changes the second full attachment
  offset, which Native already represents exactly. Expected component,
  grounded, and regular-joint counts plus the solve-on-creation preference
  guard stale requests. Invalid connectors, duplicate Parallel pairs,
  malformed graphs, changed component placements, and inapplicable fields
  fail before mutation. In addition to proving exact type, proxies,
  references, offsets, reverse state, activation, selection, and solver
  status, Native verifies the live global connector axes satisfy the actual
  Parallel postcondition. Both equal and anti-parallel directions are valid,
  matching the human command's cross-product semantics. Concise Assembly
  state reports `axes_parallel` without fabricating joint properties. A
  dispatcher-backed compiled GUI gate starts with both the arm and its second
  connector deliberately misaligned, then proves the solver establishes the
  semantic axis relationship, stale-count no-op, independent full offsets,
  Reverse, idempotent replay, one-step undo/redo, and FCStd
  save/close/reopen with model/view proxies, references, offsets, and bounded
  state restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_PARALLEL_JOINT_GUI_OK components=2 joints=1
  axes_parallel=true reverse=true offsets=true transactions=1 reopen=true`;
  the Fixed, Revolute, Cylindrical, Slider, Ball, and Distance lifecycle gates
  remain green. The complete suite is 3,052 passed with four intentional
  skips; Ruff, compileall, diff checks, and the VibeCADScripts,
  AssemblyScripts, Assembly, and AssemblyGui build targets are green. The
  protected Sketcher, Part Design, and Assembly VibeScript integrations all
  exit zero, no VibeCAD test process remains, and the immutable 5-axis fixture
  remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Perpendicular Joint is an exact
  `assembly.joint/create_perpendicular` operation mapped from the live
  `Assembly_CreateJointPerpendicular` action. It uses Assembly type index 7
  and reaches the compiled `ASMTPerpendicularJoint` used by the human command,
  whose real constraint makes the two connector coordinate systems' global Z
  axes orthogonal. The two exact component-rooted connectors preserve complete,
  independent Offset1 and Offset2 placements. Reverse, angle, distance,
  simplified rotation, and limit fields are deliberately absent because the
  human Perpendicular task exposes none of them. Expected component, grounded,
  and regular-joint counts plus the solve-on-creation preference guard stale
  requests. Invalid connectors, duplicate Perpendicular pairs, malformed
  graphs, changed component placements, and inapplicable fields fail before
  mutation. Native follows the human command's `preventParallel` connector
  path, including its 10-degree X-axis perturbation of the moving component
  when the initial connector axes are parallel, so the solver does not begin
  from that singular orientation. It then verifies the live global connector
  Z-axis dot product as an exact `axes_perpendicular` semantic postcondition,
  in addition to exact type, proxies, references, offsets, solver status,
  activation, and selection. Concise Assembly state reports that semantic
  relationship without fabricating joint properties. A dispatcher-backed
  compiled GUI gate deliberately starts with the exact connector axes parallel
  by using yaw-only full offsets, then proves the perturbation-and-solve path,
  stale-count no-op, independent offsets, moved-component reporting,
  idempotent replay, one-step undo/redo, and FCStd save/close/reopen with model
  and view proxies, references, offsets, semantic state, and bounded summary
  restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_PERPENDICULAR_JOINT_GUI_OK components=2 joints=1
  initial_parallel=true axes_perpendicular=true offsets=true transactions=1
  reopen=true`; the Fixed, Revolute, Cylindrical, Slider, Ball, Distance, and
  Parallel lifecycle gates remain green. The complete suite is 3,064 passed
  with four intentional skips; Ruff, compileall, diff checks, source/build-tree
  byte comparison, and the VibeCADScripts, AssemblyScripts, Assembly, and
  AssemblyGui build targets are green. The protected Sketcher, Part Design,
  and Assembly VibeScript integrations all exit zero, no VibeCAD test process
  remains, and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Angle Joint is an exact `assembly.joint/create_angle` operation
  mapped from the live `Assembly_CreateJointAngle` action. It uses Assembly
  type index 8, persists the human task's real `Angle` property, and reaches
  the compiled `ASMTAngleJoint`, whose constraint is the global connector-Z
  dot product `cos(abs(Angle))`. Native deliberately accepts the one canonical
  geometric range from 0 through 180 degrees instead of exposing the raw
  property's negative and periodic aliases as false choices. At zero, it
  mirrors the compiled `AssemblyObject` special case that substitutes
  `ASMTParallelAxesJoint`; concise results and state name that relation
  `parallel_unsigned` so equal and anti-parallel zero-angle outcomes cannot be
  misread. All other canonical values report `axis_dot_cosine`. Two exact
  component-rooted connectors retain complete, independent Offset1 and Offset2
  placements. Reverse, distance, simplified rotation, and limit fields are
  absent because the human Angle task exposes none of them. Expected component,
  grounded, and regular-joint counts plus the solve-on-creation preference
  guard stale requests. Invalid connectors, duplicate Angle pairs, malformed
  graphs, changed component placements, non-finite or non-canonical angles,
  and inapplicable fields fail before mutation. Native follows the human
  `preventParallel` connector path, including its 10-degree global connector-X
  perturbation when the initial axes are parallel. It then verifies the live
  global connector-axis dot product against the exact compiled relation and
  reports the stored canonical angle, measured principal axis angle, relation,
  and satisfaction without leaking the shared engine's internal false Reverse
  state or property map. A dispatcher-backed compiled GUI gate deliberately
  starts with exact connector axes parallel through yaw-only full offsets and
  proves the real 60-degree cosine solver, stale-count no-op, property and
  offset persistence, moved-component reporting, idempotent replay, one-step
  undo/redo, and FCStd save/close/reopen with model and view proxies,
  references, semantic state, and bounded summary restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_ANGLE_JOINT_GUI_OK components=2 joints=1
  initial_parallel=true angle_degrees=60 angle_satisfied=true offsets=true
  transactions=1 reopen=true`; all eight previously completed compiled joint
  lifecycle gates remain green. The complete suite is 3,086 passed with four
  intentional skips; Ruff, compileall, diff checks, source/build-tree byte
  comparison, and the VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui
  build targets are green. The protected Sketcher, Part Design, and Assembly
  VibeScript integrations all exit zero, no VibeCAD test process remains, and
  the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Rack-and-Pinion Joint is an exact
  `assembly.joint/create_rack_pinion` operation mapped from the live
  `Assembly_CreateJointRackPinion` action. It uses Assembly type index 9,
  persists the human task's real signed `Distance` pitch-radius property, and
  reaches the compiled `ASMTRackPinionJoint`, whose exact coupling relation is
  `rack travel mm per pinion radian = -pitch radius`. The contract requires the
  exact active Slider joint for the rack and exact active Revolute joint for
  the pinion instead of relying on the compiled engine's ambiguous scan for any
  matching Slider. It also requires semantically named rack and pinion
  connectors that exactly reuse the corresponding prerequisite joint side,
  including component, element and anchor paths, and complete attachment
  offset. Preflight proves both prerequisites are active in the human-active
  Assembly, have the required joint types, constrain distinct rack and pinion
  components, leave those components ungrounded, and expose perpendicular live
  Slider and Revolute axes. Native creates the rack as connector one and the
  pinion as connector two, then verifies that the compiled solve did not swap
  or drift that exact persisted dependency graph. The pitch radius accepts the
  human task's complete signed direction semantics while rejecting Boolean,
  zero, non-finite, sub-tolerance, and unbounded values. Reverse, angle,
  simplified offset/rotation, limits, and a second radius are absent because
  the human Rack-and-Pinion task exposes none of them. Expected component,
  grounded, and regular-joint counts plus the solve-on-creation preference
  guard stale requests before mutation. Concise results report the two
  semantic connectors, both exact prerequisite identities, pitch radius,
  signed travel ratio, and perpendicular-axis proof without leaking the shared
  engine's false Reverse value or raw property map. Concise state reconstructs
  the prerequisites from persisted connector equality after save/reopen and
  explicitly reports whether that graph still resolves. A dispatcher-backed
  compiled GUI gate builds a real grounded base, global-X Slider rack, and
  global-Z Revolute pinion, then proves stale-count no-op, real solver status
  zero, exact dependency reuse, canonical side order, idempotent replay,
  one-step undo/redo that preserves both prerequisites, and FCStd
  save/close/reopen with model and view proxies, references, offsets, ratio,
  axis semantics, and bounded state restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_RACK_PINION_JOINT_GUI_OK components=3 joints=3
  prerequisites=true pitch_radius_mm=20 ratio=-20 axes_perpendicular=true
  transactions=1 reopen=true`; all nine previously completed compiled joint
  lifecycle gates remain green. The complete suite is 3,109 passed with four
  intentional skips; Ruff, compileall, diff checks, source/build-tree byte
  comparison, and the VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui
  build targets are green. The protected Sketcher, all 17 Part Design phases,
  and Assembly VibeScript integrations exit zero, no VibeCAD test process
  remains, and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Screw Joint is an exact `assembly.joint/create_screw` operation
  mapped from the live `Assembly_CreateJointScrew` action. It uses Assembly
  type index 10, persists the human task's real signed `Distance` thread-pitch
  property, and reaches the compiled `ASMTScrewJoint`, whose constraint is
  `2*pi*z - pitch*theta - constant = 0`. Results therefore distinguish the
  persisted relative axial advance per relative revolution from the slider's
  travel per screw revolution; for the canonical fixed-base arrangement the
  latter is the negative of the signed pitch. The contract requires an exact
  active Slider prerequisite for the translating component and an exact active
  Revolute prerequisite for the screw instead of relying on the compiled
  engine's ambiguous scan for a matching Slider. Semantically named slider and
  screw connectors must exactly reuse the corresponding prerequisite side,
  including component, element and anchor paths, and complete attachment
  offset. Preflight proves both prerequisites are active in the human-active
  Assembly, have the required joint types, constrain distinct ungrounded
  components, and place their live connector Z axes on one directed collinear
  line rather than merely parallel lines. Native persists the slider as
  connector one and screw as connector two, then verifies that the compiled
  solve did not swap or drift the exact dependency graph. Signed pitch accepts
  the human task's complete direction semantics while rejecting Boolean, zero,
  non-finite, sub-tolerance, and unbounded values. Reverse, angle, simplified
  offset/rotation, limits, and secondary-distance fields are absent because
  the human Screw task exposes none of them. Expected component, grounded, and
  regular-joint counts plus the solve-on-creation preference guard stale
  requests before mutation. Concise results return semantic connectors, both
  exact prerequisite identities, signed pitch, both motion-rate conventions,
  and the collinearity proof without leaking the shared engine's false Reverse
  value or raw property map. Concise state reconstructs both prerequisites from
  persisted connector equality after save/reopen and explicitly reports
  whether the graph still resolves. A dispatcher-backed compiled GUI gate
  builds a real grounded base, Slider component, and Revolute screw component,
  then proves stale-count no-op, real solver status zero, exact dependency
  reuse, canonical side order, idempotent replay, one-step undo/redo preserving
  both prerequisites, and FCStd save/close/reopen with model and view proxies,
  references, offsets, rates, axis semantics, and bounded state restored. It
  reports `VIBECAD_NATIVE_ASSEMBLY_SCREW_JOINT_GUI_OK components=3 joints=3
  prerequisites=true thread_pitch_mm=-2 slider_travel_mm_per_revolution=2
  axes_collinear=true transactions=1 reopen=true`; all ten previously completed
  compiled joint lifecycle gates remain green. The complete suite is 3,133
  passed with four intentional skips; Ruff lint, new-file Ruff formatting,
  compileall, diff checks, ten applicable source/build-tree byte comparisons,
  and the VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui build
  targets are green. The protected Sketcher, all 17 Part Design phases, and
  Assembly VibeScript integrations all exit zero, no VibeCAD test process
  remains, and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Gears Joint is an exact `assembly.joint/create_gears` operation
  mapped from the live plural `Assembly_CreateJointGears` action and persisted
  `Gears` joint type rather than inventing a renamed command or compatibility
  alias. It uses Assembly type index 11 and persists the human task's two real
  `Distance` and `Distance2` properties as `radius1_mm` and `radius2_mm`. Both
  radii follow the task's strictly positive range and reject Boolean, zero,
  negative, non-finite, sub-tolerance, and unbounded values before mutation.
  The compiled `ASMTGearJoint` relation is
  `theta2 + (radius1 / radius2) * theta1 = constant`, so concise output names
  the second rotation per first rotation as `-(radius1 / radius2)` and reports
  the opposite direction explicitly. The contract requires two distinct exact
  active Revolute prerequisites and semantically named gear connectors that
  exactly reuse their corresponding prerequisite side, including component,
  element and anchor paths, and complete attachment offset. Preflight proves
  the two Revolute joints constrain distinct ungrounded rotating components;
  it deliberately does not invent an axis-parallelism rule absent from the
  human task and compiled rotational coupling. Native persists the first and
  second gear in radius order and verifies that the compiled solve did not
  swap or drift that dependency graph. Reverse, angle, simplified
  offset/rotation, and limit fields are absent: the human task's direction
  checkbox switches between the separate Gears and Belt joint types instead
  of setting a Gears property. Expected component, grounded, and regular-joint
  counts plus the solve-on-creation preference guard stale requests before
  mutation. Concise state reconstructs both Revolute prerequisites from
  persisted connector equality after save/reopen and reports whether the
  exact graph still resolves. A dispatcher-backed compiled GUI gate uses two
  distinct real shaft axes, proves stale-count no-op, real solver status zero,
  exact dependency reuse, radius order, ratio and direction, idempotent replay,
  one-step undo/redo preserving both prerequisites, and FCStd
  save/close/reopen with model and view proxies, references, offsets, semantic
  state, and bounded summary restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_GEARS_JOINT_GUI_OK components=3 joints=3
  prerequisites=true radius1_mm=20 radius2_mm=40 ratio=-0.5
  direction=opposite transactions=1 reopen=true`; all eleven previously
  completed compiled joint lifecycle gates remain green. The complete suite is
  3,158 passed with four intentional skips; Ruff lint, new-file Ruff formatting,
  compileall, diff checks, eight applicable source/build-tree byte comparisons,
  and the VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui build
  targets are green. The protected Sketcher, all 17 Part Design phases, and
  Assembly VibeScript integrations all exit zero, no VibeCAD test process or
  test-created crash lock remains, and the immutable 5-axis fixture remains
  exactly `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Assemble Belt Joint is an exact `assembly.joint/create_belt` operation mapped
  from the live `Assembly_CreateJointBelt` action and persisted `Belt` joint
  type. It uses the human command's type index 12 and stores the task panel's
  positive Radius 1 and Radius 2 values in the real `Distance` and `Distance2`
  properties. Boolean, zero, negative, non-finite, sub-tolerance, and unbounded
  radii fail before mutation. The compiled Belt path uses `ASMTGearJoint` with
  a negated second solver radius, yielding
  `theta2 - (radius1 / radius2) * theta1 = constant`; concise output therefore
  reports `+(radius1 / radius2)` as the second rotation per first rotation and
  names the direction `same`. The contract accepts only two distinct exact
  active Revolute prerequisites and semantically named pulley connectors that
  reuse their corresponding prerequisite sides completely: component,
  element path, anchor path, and attachment offset. Both pulley components
  must remain ungrounded and the prerequisites may not cross-constrain the
  other pulley. Native deliberately does not invent an axis-parallelism rule
  absent from the human task and compiled relation. Reverse, angle, simplified
  offset/rotation, and limit fields are absent because the task checkbox
  selects between the separate Gears and Belt types rather than persisting a
  direction property. Expected component, grounded, regular-joint, and
  solve-on-creation state rejects stale requests without a transaction. A new
  shared 373-line two-Revolute rotation-coupling engine now owns the common
  prerequisite, exact-side, regular-joint, verification, and concise-result
  mechanics; the Gears and Belt contracts are each 170-line semantic wrappers
  with opposite and same direction fixed by their respective persisted joint
  types. Concise Assembly state restores both prerequisite identities and the
  signed ratio after reopen. The dispatcher-backed compiled GUI gate proves a
  stale-count no-op, real solver status zero, exact dependency reuse, radius
  order, `+0.5` same-direction output, idempotent replay, one-step undo/redo,
  and FCStd save/close/reopen with model/view proxies, references, offsets, and
  state restored. It reports
  `VIBECAD_NATIVE_ASSEMBLY_BELT_JOINT_GUI_OK components=3 joints=3
  prerequisites=true radius1_mm=20 radius2_mm=40 ratio=0.5 direction=same
  transactions=1 reopen=true`. Completing Belt makes the entire
  `assembly.joint` capability definition and implementation complete while
  Native remains globally unavailable until the rest of this plan is done.
  All thirteen compiled joint lifecycle gates pass. The complete VibeCAD suite
  is 3,184 passed with four intentional skips; Ruff, compileall, diff checks,
  source/build parity, and the VibeCADScripts, AssemblyScripts, Assembly, and
  AssemblyGui targets are green. The protected Sketcher gate exits zero, all
  17 Part Design phases report `VIBECAD_VIBESCRIPT_PHASE_OK`, and the Assembly
  VibeScript gate returns `VIBECAD_ASSEMBLY_VIBESCRIPT_GATE_EXIT 0` with every
  published joint solver code zero. No VibeScript source changed.
  No FreeCAD or FreeCADCmd process remains, the preserved pre-existing recovery
  snapshot and lock are untouched, the prior test-created crash lock remains
  absent, and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Solve Assembly is an exact `assembly.structure/solve_assembly` operation
  mapped only from the live `Assembly_SolveAssembly` action. It accepts only
  the exact human-active Assembly plus the current component, grounded-joint,
  and regular-joint counts and a lowercase SHA-256 of its bounded solver state.
  That state fingerprints at most 128 native solver placement objects by
  document identity, object identity and type, exact placement, and both
  `Placement` and `LinkPlacement` read-only state. Stale counts, stale state,
  a different active Assembly, inactive timeline objects, or an oversized
  placement graph all fail before a transaction opens. The implementation
  follows the human command's native lifecycle with `assembly.solve(False)`
  followed by one full document recompute inside one named transaction. A
  solver exception, nonzero status, invalid Assembly, recompute failure,
  deleted object, changed placement-object graph, unexpected created object,
  post-solve drift, active-Assembly drift, selection drift, or moved grounded
  component aborts the transaction. The only allowed solver-created objects
  are exact native grounding repairs for components whose placement was
  already locked; those repairs, their targets, and the JointGroup membership
  are verified before commit. Success returns only before/after state hashes,
  bounded exact placement changes, lock changes, grounding repairs, counts,
  and concise solver health instead of the raw diagnostic graph. The focused
  solve and state modules are 473 and 230 lines, and the dispatcher-backed
  compiled GUI gate proves free-motion solving, native grounding repair, stale
  state no-op, exact constrained movement, unchanged grounded placement,
  preserved selection and active Assembly, idempotent replay, one-step
  undo/redo, and FCStd save/close/reopen. It reports
  `VIBECAD_NATIVE_ASSEMBLY_SOLVE_GUI_OK components=2 joints=1 grounded=1
  moved=1 free_motion=true grounding_repair=true stale_noop=true
  selection=true transactions=2 undo_redo=true reopen=true`. All thirteen
  compiled joint lifecycle gates plus the structure, grounding, and solve gates
  pass. The complete VibeCAD suite is 3,193 passed with four intentional skips;
  Ruff, compileall, diff checks, source/build parity, and the VibeCADScripts,
  AssemblyScripts, Assembly, and AssemblyGui targets are green. The protected
  Sketcher gate exits zero, all 17 Part Design phases report
  `VIBECAD_VIBESCRIPT_PHASE_OK`, and the Assembly VibeScript gate returns
  explicit `"ok": true`, every published joint solver code zero, and
  `VIBECAD_ASSEMBLY_VIBESCRIPT_GATE_EXIT 0`. No VibeScript source changed. No
  FreeCAD or FreeCADCmd process remains, the preserved recovery snapshot and
  lock are untouched, the prior test-created crash lock remains absent, and
  the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
- Conflicting-constraint diagnosis is an exact read-only
  `assembly.diagnose/select_conflicting_constraints` operation mapped only from
  the live `Assembly_SelectConflictingConstraints` action. It reads the same
  most-recent compiled solver diagnosis used by the human command without
  invoking another solve, changing selection, opening a transaction, or
  mutating the document. Its bounded state capture cross-checks the exact
  human-active Assembly, native JointGroup, components, grounded and regular
  joints, solver placement hash, status, message, remaining degrees of freedom,
  category flags and names, per-joint constraint counts, redundancy flags,
  removed degrees of freedom, signed residuals, absolute residuals, and maximum
  residuals. Duplicate, unknown, inconsistent, oversized, non-finite, stale,
  malformed, or selection-drifting diagnostics fail closed. The provider
  request supplies the exact Assembly, diagnosis SHA-256, all relevant counts,
  and a bounded page; success returns only exact conflicting-joint references,
  labels/types, both semantic connectors and offsets, constraint and violating
  counts, maximum residuals, solver status/DoF/tolerance, and pagination state.
  Assemble snapshots classify unavailable or incomplete diagnostic graphs as a
  bounded unavailable summary rather than breaking state capture. A
  dispatcher-backed compiled GUI gate creates an impossible 3-4-7.1 distance
  loop, proves native solve status `-1`, compares Native's exact three-joint set
  with the real human selection command, verifies residual evidence, two-page
  pagination, stale-state no-ops, unchanged selection/objects/placements/undo
  and transaction state, idempotent replay, and FCStd save/close/reopen followed
  by another compiled solve. It reports
  `VIBECAD_NATIVE_ASSEMBLY_CONFLICT_DIAGNOSIS_GUI_OK components=3 joints=3
  conflicts=3 solver_status=-1 human_match=true pagination=true
  stale_noop=true selection=true transactions=0 reopen=true`. The focused
  diagnosis suite has 13 tests, all 17 Assembly GUI lifecycle gates pass, and
  the complete VibeCAD suite is 3,206 passed with four intentional skips. Ruff,
  compileall, diff checks, source/build parity, and the VibeCADScripts,
  AssemblyScripts, Assembly, and AssemblyGui targets are green. The protected
  Sketcher gate exits zero, all 17 Part Design phases pass, and the Assembly
  VibeScript gate returns explicit `"ok": true` with every ordinary and coupled
  joint solver code zero. No VibeScript source changed; no FreeCAD process
  remains; the preserved recovery snapshot and lock are untouched; the prior
  test-created crash lock remains absent; and the immutable 5-axis fixture
  remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  At that checkpoint, `assembly.diagnose` remained intentionally incomplete
  pending row 11.24; Native mode remains globally unavailable until this
  entire plan is complete.
- Redundant-constraint diagnosis is an exact read-only
  `assembly.diagnose/select_redundant_constraints` operation mapped only from
  the live `Assembly_SelectRedundantConstraints` action. It consumes the same
  most-recent `getLastRedundant()` solver result selected by the human command;
  it does not invoke a solve or recompute, change selection, open a transaction,
  or mutate the document. Conflict and redundancy reads now share one bounded
  preflight/drift guard that verifies the exact human-active Assembly, timeline
  state, component and joint identities, diagnosis SHA-256 and counts, page,
  frozen turn, and human selection before returning. Category membership is
  independently reconstructed from the same native constraint specifications,
  residuals, and redundant flags, including the producer's intentional overlap
  between redundant and partially redundant sets. Success
  returns only exact joint references, labels/types, both semantic connectors
  and offsets, aggregate constraint/DoF evidence, concise solver health, and
  pagination state. A dispatcher-backed compiled GUI gate creates two identical
  Fixed joints between one grounded and one moving component, proves native
  solver status `0`, proves only `FixedTwo` is redundant with all six of its six
  constraints redundant and zero degrees of freedom removed, and matches the
  exact human selection command. It also proves stale hash/count no-ops,
  unchanged selection/objects/placements/undo/transaction/edit state,
  idempotent replay, and FCStd save/close/reopen followed by a fresh compiled
  solve and read. It reports
  `VIBECAD_NATIVE_ASSEMBLY_REDUNDANT_DIAGNOSIS_GUI_OK components=2 joints=2
  redundant=1 solver_status=0 human_match=true complete_redundancy=true
  stale_noop=true selection=true transactions=0 reopen=true`. The combined
  conflict/redundancy diagnosis suite has 19 tests, all 18 Assembly GUI
  lifecycle gates pass, and the complete VibeCAD suite is 3,212 passed with
  four intentional skips. Ruff, compileall, diff checks, source/build parity,
  and the VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui targets are
  green. The protected Sketcher gate exits zero, all 17 Part Design phases pass
  with final `"ok": true`, and the Assembly VibeScript gate exits zero with
  top-level `"ok": true` and every ordinary and coupled joint solver code zero.
  No VibeScript source changed; no FreeCAD process remains; the preserved
  recovery snapshot and lock are untouched; the prior test-created crash lock
  remains absent; and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  At that checkpoint, `assembly.diagnose` remained intentionally incomplete
  pending row 11.24; Native mode remains globally unavailable until this
  entire plan is complete.
- Partially redundant-constraint diagnosis is an exact read-only
  `assembly.diagnose/select_partially_redundant_constraints` operation mapped
  only from the live `Assembly_SelectPartiallyRedundantConstraints` action. It
  reads the same most-recent `getLastPartiallyRedundant()` set used by the human
  command without solving, recomputing, selecting, opening a transaction, or
  mutating the document. The shared exact diagnosis guard freezes and rechecks
  the active Assembly, timeline, object identities, diagnosis SHA-256 and
  counts, bounded page, turn, and human selection. Each returned joint proves
  that its redundant-constraint count is strictly between zero and its total
  constraint count, returns both semantic connectors and offsets plus aggregate
  constraint/DoF evidence, and explicitly reports whether it also belongs to
  the independently produced redundant set. This preserves the compiled
  producer's intentional category overlap and status priority instead of
  pretending the categories are disjoint. A dispatcher-backed compiled GUI
  gate creates coincident Cylindrical and Slider joints between one grounded
  and one moving component. Cylindrical removes four DoF with zero redundant
  constraints; Slider then has five constraints, four redundant and one
  effective, appears in both redundancy sets, leaves one DoF, and is the exact
  sole selection of the human partial-redundancy command. The gate also proves
  stale hash/count no-ops, unchanged selection/objects/placements/undo/
  transaction/edit state, idempotent replay, and FCStd save/close/reopen
  followed by a fresh compiled solve and read. It reports
  `VIBECAD_NATIVE_ASSEMBLY_PARTIAL_REDUNDANCY_DIAGNOSIS_GUI_OK components=2
  joints=2 partial=1 redundant_overlap=1 solver_status=0 human_match=true
  aggregate=4_of_5 stale_noop=true selection=true transactions=0 reopen=true`.
  The combined diagnosis suite has 25 tests, all 19 Assembly GUI lifecycle
  gates pass, and the complete VibeCAD suite is 3,218 passed with four
  intentional skips. Ruff, compileall, diff checks, source/build parity, and
  the VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui targets are
  green. The protected Sketcher gate exits zero, all 17 Part Design phases pass
  with final `"ok": true`, and the Assembly VibeScript gate exits zero with
  top-level `"ok": true` and every ordinary and coupled joint solver code zero.
  No VibeScript source changed; no FreeCAD process remains; the preserved
  recovery snapshot and lock are untouched; the prior test-created crash lock
  remains absent; and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  At that checkpoint, `assembly.diagnose` remained intentionally incomplete
  pending row 11.24; Native mode remains globally unavailable until this
  entire plan is complete.
- Malformed-constraint diagnosis is an exact read-only
  `assembly.diagnose/select_malformed_constraints` operation mapped only from
  the live `Assembly_SelectMalformedConstraints` action. It consumes the same
  most-recent `getLastMalformed()` result selected by the human command and
  does not solve, recompute, select, open a transaction, or mutate the
  document. The shared diagnosis guard freezes and rechecks the exact
  human-active Assembly, timeline, object identities, solver-placement and
  diagnosis SHA-256 values, category and graph counts, bounded page, turn, and
  human selection. Malformed joints must be absent from the native constraint
  rows because the compiled producer excludes them from the MbD graph; any
  overlapping, unknown, stale, duplicate, unbounded, connector-drifting, or
  selection-drifting state fails closed. Each result contains only the exact
  joint reference, label/type, both semantic connectors and offsets, the
  `same_solver_part_in_fixed_drag_bundle` reason, whether the joint is the
  Fixed bundle constraint itself or an extra intra-bundle constraint, and a
  role-specific corrective action. A dispatcher-backed compiled GUI gate
  creates three components with a grounded-to-moving Cylindrical joint, a
  Fixed joint between the two moving components, and a Slider across that same
  pair. A normal solve is clean; a real direct component drag then enters the
  compiled Fixed-bundle pre-drag solve, which safely omits the Fixed and Slider
  joints, leaves only the Cylindrical solver row, reports status `0`, and
  produces the exact two-joint malformed set. The gate cancels that real move
  through the supported selection-clear lifecycle, proves exact placement
  restoration and transaction abort while retaining the diagnosis, and
  matches Native's two bounded pages to the real human selection command. It
  also proves stale hash/count no-ops, unchanged selection/objects/placements/
  undo/transaction/edit state, idempotent replay, and FCStd save/close/reopen
  followed by another compiled drag diagnosis. It reports
  `VIBECAD_NATIVE_ASSEMBLY_MALFORMED_DIAGNOSIS_GUI_OK components=3 joints=3
  malformed=2 fixed_member=1 intra_bundle=1 solver_status=0 human_match=true
  pagination=true stale_noop=true selection=true transactions=0 reopen=true`.
  The combined diagnosis suite has 31 tests, all 20 Assembly GUI lifecycle
  gates pass, and the complete VibeCAD suite is 3,224 passed with four
  intentional skips. Ruff, compileall, diff checks, source/build parity, and
  the VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui targets are
  green. The protected Sketcher gate exits zero, all 17 Part Design phases
  pass with final `"ok": true`, and the Assembly VibeScript gate exits zero
  with top-level `"ok": true`, all 13 ordinary joint solver codes zero, and
  both coupled-joint solver codes zero. No VibeScript source changed; no
  FreeCAD or FreeCADCmd process remains; the preserved recovery snapshot and
  lock are untouched; the prior test-created crash lock remains absent; and
  the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  At that checkpoint, `assembly.diagnose` remained intentionally incomplete
  pending the remaining human Diagnose action in row 11.24; Native mode
  remains globally unavailable until this entire plan is complete.
- Joints-of-component reading is an exact read-only
  `assembly.diagnose/select_joints_of_component` operation mapped only from
  the live `Assembly_SelectJointsOfComponent` action. It targets one exact
  active movable component rather than changing or depending on human
  selection. The 568-line execution domain consumes the generated
  `AssemblyObject.Joints` Python property, which invokes the compiled
  `AssemblyObject::getJoints()` implementation used by
  `getJointsOfPart()`. Native therefore preserves compiled joint order and
  filtering for inactive, errored, suppressed, incomplete, self-referencing,
  and invalid-proxy joints instead of reconstructing that behavior in Python.
  Component eligibility is independently cross-checked through
  `UtilsAssembly.getMovablePartsWithin()` and
  `isMovableAssemblyComponent()`. A new concise Assemble
  `component_joint_state` exposes only availability, a SHA-256 digest, and
  component/joint counts. The digest covers exact Assembly, movable-component,
  and compiled-joint identities and order plus returned labels, types,
  component endpoints, connector paths, and offsets. Preflight and final
  verification freeze the active Assembly, target identity, counts, graph
  digest, bounded page, turn, and human selection. Stale, duplicate,
  cross-document, inactive, non-movable, unbounded, malformed-connector, or
  drifting state fails closed without a transaction. Success returns the
  target component, total graph and matching counts, pagination state, and
  for each matching joint its exact reference, label/type, whether the target
  occupies the first or second connector, the other component, and both exact
  connectors and offsets.
  A dispatcher-backed compiled GUI gate creates five components, three active
  joints attached to one target, and a fourth suppressed joint between that
  target and an otherwise unconnected component. It proves the exact Native
  order and set match the real human command invoked from an Assembly-rooted
  component subpath, proves the suppressed joint is excluded, checks both
  connector sides, two-page pagination, a zero-joint result, stale hash/count
  and wrong-target no-ops, unchanged selection/objects/placements/undo/
  transaction/edit state, idempotent replay, and FCStd save/close/reopen. It
  reports `VIBECAD_NATIVE_ASSEMBLY_COMPONENT_JOINTS_GUI_OK components=5
  joints=3 attached=3 suppressed_excluded=true human_match=true
  exact_sides=true pagination=true empty=true stale_noop=true selection=true
  transactions=0 reopen=true diagnose_complete=true`. The combined diagnosis
  suite has 38 tests, all 21 Assembly GUI lifecycle gates pass, and the
  complete VibeCAD suite is 3,231 passed with four intentional skips. Ruff,
  compileall, diff checks, source/build parity for touched files, and the
  VibeCADScripts, AssemblyScripts, Assembly, and AssemblyGui targets are green.
  The protected Sketcher gate exits zero, all 17 Part Design phases pass with
  exit zero, and the current-source Assembly VibeScript gate exits zero with
  top-level `"ok": true`, all 13 ordinary joint solver codes zero, and both
  coupled-joint solver codes zero. No VibeScript source changed. This fifth
  variant completes the shipped Assembly Diagnose ribbon family without
  exposing selection mutation, command dispatch, or workbench/ribbon control.
  No FreeCAD or FreeCADCmd process remains; the preserved recovery snapshot
  and lock are untouched; the prior test-created crash lock remains absent;
  and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  Native mode remains globally unavailable until this entire plan is complete.
- Assembly view creation is one exact mutating
  `assembly.structure/create_view` operation mapped only from the live
  `Assembly_CreateView` action on the human-selected Assemble ribbon. A new
  bounded view-state reader freezes the exact active Assembly, component
  count, finite Assembly bounds, both native movable-target inventories from
  `UtilsAssembly.getMovablePartsWithin()` (`individual_objects` and
  `parts_as_single_solid`), canonical Assembly-rooted selection paths, target
  placements and centers, the existing view/move graph, and a SHA-256 digest.
  Provider state exposes concise identities, counts, bounds, target-mode
  membership, and a bounded view preview; internal selection paths and the
  full durable graph are not echoed to the provider. The closed request schema
  requires that digest and every relevant count, one exact Assembly, one
  scope, and an ordered bounded sequence of normal placement transforms or
  positive radial distances against exact target references. Preflight
  rechecks the human-active Assembly and presentation, requires the human
  command's minimum of two components, resolves every identity into the
  requested native target inventory, rejects duplicate, stale, zero-effect,
  cross-scope, cross-root, non-finite, or unbounded requests, and preserves
  selection without opening the human task panel.
  Mutation uses the real `CommandCreateView` operation and step factories plus
  their `ExplodedView` and `ExplodedViewStep` view-provider proxies. It creates
  or reuses the native `Assembly::ViewGroup`, publishes every step as a
  resource of one operation, assigns the exact native `References`,
  `MoveType`, and `MovementTransform` properties, and finalizes the canonical
  resource-first/owner-last History block through
  `finalizeProvisionalTimelineOperationBlock()`. Before commit, every move is
  exercised through its real `applyStep()` implementation; every exact target
  must move and produce one native explosion line, after which all original
  Assembly placements are restored exactly and every move resource is hidden.
  Postcondition verification rechecks graph identity/order, proxies, History
  role/owner/editor metadata, view-group membership, accepted visibility,
  target inventories, bounds, prior views, human selection, active Assembly,
  movement presentation, and restored placements. The concise result contains
  only exact object identities, label/scope, counts, explosion-line count, the
  new view-state digest, and explicit preservation facts. One transaction,
  assistant-local undo, stale-state no-op behavior, and idempotent call replay
  remain owned by the shared Native mutation runtime.
  Repeated compiled save/reopen testing exposed a separate core lifecycle
  defect: `DocumentTimeline::normalizeAfterRestore()` preserved the correct
  `VisibilityAtEnd` bits but did not reapply them after Python-backed operation
  proxies completed their restore callbacks, so an arbitrary move could reopen
  visible. History now shares one end-state presentation reconciliation path
  between restore and undo/redo, applied after the complete object graph is
  reconstructed and while restore capture is suppressed. A deterministic
  Assembly core regression intentionally makes a Python resource visible in
  `onDocumentRestored()` and proves the accepted hidden state wins. The Native
  GUI gate asserts live and accepted visibility after each create, each redo,
  immediately before and after save, and after reopen. The unfixed build failed
  four of five repeated runs; the repaired build passed five of five and the
  final formatted build passed again. The gate also proves nested individual
  targets, single-solid scope, normal and radial moves, malformed and stale
  no-ops, exact task/edit/selection/presentation preservation, one-step
  undo/redo, view-group reuse, proxy/owner restoration, and baseline placements,
  reporting `VIBECAD_NATIVE_ASSEMBLY_VIEW_GUI_OK views=2 normal_moves=2
  radial_moves=1 nested_target=true stale_noop=true undo_redo=true reopen=true
  placements_restored=true`.
  All 22 compiled Native Assembly lifecycle gates pass against the rebuilt
  core, the focused view/structure/component suite has 19 passing tests, and
  the complete VibeCAD suite has 3,236 passing tests with four intentional
  skips. The new deterministic Assembly core test passes; the broader legacy
  `AssemblyTests.TestCore` module retains an independently reproducible,
  unrelated flexible-occurrence provisional-proof failure and was not hidden
  by changing production behavior or assertions. Ruff, formatting of every
  new file, compilation, diff checks, declared source/build parity, and the
  VibeCADScripts, AssemblyScripts, FreeCADApp, FreeCADGui, Assembly, and
  AssemblyGui targets are green. The protected Sketcher VibeScript lifecycle
  passes, all 17 Part Design VibeScript phases pass, and the current-source
  Assembly VibeScript integration exits zero. No VibeScript source changed.
  No FreeCAD or FreeCADCmd process remains; the preserved recovery snapshot
  and lock are untouched; the prior test-created crash lock remains absent;
  and the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  Native mode remains globally unavailable until this entire plan is complete.
- Assembly simulation creation is one exact mutating
  `assembly.structure/create_simulation` operation mapped only from the live
  `Assembly_CreateSimulation` action on the human-selected Assemble ribbon.
  It authors the durable Simulation/Motion History graph only; solver-frame
  generation, playback, cancellation, and restoration remain the separate
  row 11.27 lifecycle. This boundary matches the human command and keeps
  document and GUI access on the main thread instead of pretending FreeCAD
  objects are safe to mutate in a worker.
  A bounded simulation-state reader freezes the exact human-active Assembly,
  complete component and grounding graph, active regular-joint connector and
  parameter records, solver-placement digest, eligible driveable joints,
  existing Simulation/Motion graph, and one canonical SHA-256 digest. It
  advertises only valid unsuppressed Revolute, Slider, and Cylindrical joints
  with exact live connectors, mapping them respectively to angular, linear,
  or both motion types. Provider state returns concise identities, labels,
  supported motion types, counts, and bounded previews rather than the full
  internal graph.
  The closed schema requires one exact Assembly, finite bounded simulation
  parameters, 1 through 256 ordered exact-joint motions, single-line formulas,
  and the frozen digest and relevant counts. Preflight re-resolves every
  target, rejects stale active state before mutation, requires two components,
  a valid ground, and at least one driveable joint, enforces native joint/type
  compatibility, permits the intentional angular-plus-linear pair on one
  Cylindrical joint, rejects duplicate joint/type pairs, and bounds planned
  output work to 10,000 intervals.
  Mutation uses the shipped `CommandCreateSimulation.Simulation`, `Motion`,
  `ViewProviderSimulation`, and `ViewProviderMotion` factories and
  `UtilsAssembly.getSimulationGroup()`. It sets the exact native property
  types and values, preserves requested motion order, marks every motion as a
  resource owned by its Simulation, and finalizes one canonical
  resource-first/owner-last History block. Postcondition verification proves
  object types and proxies, ownership, group order, accepted visibility,
  timeline editor metadata, prior-graph preservation, exact parameters,
  unchanged selection and active Assembly, unchanged component placements,
  and absence of stray objects. The concise result contains exact identities,
  counts, parameters, motion-to-joint mappings, the new state digest, and an
  explicit `kinematics_generated: false`. The shared mutation runtime provides
  one semantic undo step and idempotent call replay.
  The focused and complete VibeCAD suites pass, with 3,242 tests passing and
  four intentional skips. The compiled GUI lifecycle gate passed repeatedly
  and on the final source/build pair, reporting
  `VIBECAD_NATIVE_ASSEMBLY_SIMULATION_GUI_OK simulations=2 motions=3
  cylindrical_dual_motion=true kinematics_not_generated=true stale_noop=true
  idempotent=true undo_redo=true reopen=true placements_unchanged=true`.
  VibeCADScripts and AssemblyScripts build cleanly; Ruff, Python compilation,
  diff checks, source/build parity, the protected Sketcher and Part Design
  VibeScript lifecycles, and the current-source Assembly VibeScript integration
  are green. No VibeScript source changed. The preserved recovery cache and
  lock are untouched, the forbidden crash lock remains absent, and the
  immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  Native mode remains globally unavailable until this entire plan is complete.
- Assembly simulation playback is one exact presentation-only
  `assembly.simulation` capability with the complete `open`, `seek`, `step`,
  `play`, `pause`, and `close` lifecycle. The open variant maps only from the
  shipped Simulation History context action on the human-selected Assemble
  surface; the remaining variants map the controls of the player task panel.
  Every control requires the exact 32-character Native playback identifier and
  exact Simulation object, while open additionally requires the frozen
  Assembly simulation-state digest and one exact generated output-grid time.
  There is no command dispatcher, workbench switch, or legacy playback path.
  Open validates the active Assembly, native Simulation ownership, complete
  durable simulation graph, finite bounded grid, document size, recompute and
  transaction state, and absence of another task before routing through the
  shipped read-only `CommandCreateSimulation.openSimulation()` player. It
  captures the exact document graph, solver placements and locks, component
  and joint visibility, selection, camera pose/projection, and GUI dirty state
  before generation. Launch postconditions prove that at least two frames were
  generated without a durable graph, transaction, selection, or active-
  Assembly change. A cryptographically random session ID is retained only for
  the exact document and stable Qt form identity because the native task-dialog
  binding returns a fresh Python wrapper on each query.
  Seek and step pause before displaying one exact generated solver frame;
  forward and backward play use the shipped timer controls; pause stops that
  exact timer. Common state, view, and inspection reads remain available only
  while the exact Native-owned player is live. Mutations, save, undo, another
  task, a Sketch edit, unresolved edit state, and any non-Assembly edit remain
  blocked by runtime guards. Normal Assembly edit mode is intentionally not
  mistaken for a task panel, so the human-selected Assemble surface can start
  a turn. Close routes the shipped rejection lifecycle and then verifies the
  task is gone and the document graph, simulation records, placements and
  locks, visibility, selection, camera, and dirty state are restored. Human
  close and document teardown remove the exact registry entry immediately;
  an old form callback cannot remove a replacement session. Saving from the
  shipped player establishes a new clean close baseline even when playback
  opened over a dirty GUI document.
  The compiled GUI gate exercises the real Assembly solver and player and
  reports `VIBECAD_NATIVE_ASSEMBLY_PLAYBACK_GUI_OK generated=true seek=true
  step=true bidirectional=true pause=true mutation_blocked=true
  save_baseline=true dirty_save_clean=true manual_close=true idempotent=true
  restored=true selection_preserved=true`. All six pre-existing compiled
  saved-simulation/player lifecycle tests pass. The focused contract suite has
  27 passing tests, and the complete VibeCAD suite has 3,250 passing tests with
  four intentional skips. Strict Ruff checks, formatting, Python compilation,
  diff checks, source/build parity, and the VibeCADScripts and AssemblyScripts
  targets are green. The protected current-source Sketcher, Part Design, and
  Assembly VibeScript gates all exit zero; no VibeScript source changed. No
  FreeCAD, FreeCADCmd, pytest, or build process remains. The preserved recovery
  cache and lock are untouched, the forbidden crash lock remains absent, and
  the immutable 5-axis fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  Native mode remains globally unavailable until this entire plan is complete.
- Assembly BOM creation is one exact `assembly.structure/create_bom` mutation
  mapped only from the shipped `Assembly_CreateBom` action on the
  human-selected Assemble ribbon. A bounded BOM-state reader freezes the exact
  active Assembly, direct-component count, native `OutList` source traversal,
  link and link-element targets, occurrence ordering, mirroring and scale,
  supported scalar and quantity properties, existing BOM operations and
  tables, and one canonical SHA-256 digest. Provider state exposes concise
  counts, supported built-in columns, bounded `.PropertyName` choices, existing
  BOM settings, and table sizes without returning the complete source graph or
  cell contents.
  The closed schema requires one exact Assembly, 1 through 32 unique ordered
  printable columns, the three native traversal flags, and the frozen digest,
  component count, and BOM count. Preflight re-resolves the human-active
  Assembly, rejects stale state before mutation, requires at least one active
  component, and enforces explicit source, property, operation, row, and cell
  bounds. Mutation uses the shipped `CommandCreateBom.createBomFeature()`
  factory and the real `Assembly::BomObject` and `Assembly::BomGroup` types,
  applies the exact native columns and settings, and generates the table in one
  immediate document transaction without opening a task panel or spreadsheet
  view. It preserves the human command's History behavior: the core enrolls
  the BOM in History, and Native does not invent a `VibeCADTimelineRole` that
  the human factory does not create.
  Postconditions prove the exact Assembly owner and BOM-group order, accepted
  and active History state, ordered table headers, bounded table digest and
  preview, unchanged active Assembly and selection, unchanged source graph and
  solver placements, prior-BOM preservation, and absence of stray document
  objects. The shared mutation runtime provides one-step undo/redo and
  idempotent same-call replay. The compiled lifecycle gate exercises property
  columns, quantity aggregation across duplicate links, nested detail,
  parts-only filtering, group reuse, stale no-op, replay, undo/redo, save and
  reopen, ownership, and unchanged placements, reporting
  `VIBECAD_NATIVE_ASSEMBLY_BOM_GUI_OK boms=2 rows=4 properties=true
  quantity_aggregation=true parts_filter=true stale_noop=true idempotent=true
  undo_redo=true reopen=true owner=true no_sheet_opened=true
  placements_unchanged=true`.
  The complete VibeCAD suite has 3,256 passing tests and four intentional
  skips; the focused contract suite, targeted core BOM test, VibeCADScripts,
  AssemblyScripts, FreeCADApp, FreeCADGui, Assembly, and AssemblyGui targets,
  Ruff, formatting, Python compilation, diff checks, and declared source/build
  parity are green. The protected current-source Sketcher, Part Design, and
  Assembly VibeScript gates all exit zero, and no VibeScript source changed.
  The preserved recovery cache and lock are untouched, the forbidden crash
  lock remains absent, no FreeCAD process remains, and the immutable 5-axis
  fixture remains exactly
  `19a445d49a18b6cd997e51eadd2c0c8f89eca29533281e2015601874c0f58cbe`.
  Native mode remains globally unavailable until this entire plan is complete.
- The Assembly joint runtime has been split by responsibility without changing
  its public provider contract: the dispatcher is 228 lines, shared argument
  decoding is 158 lines, motion-joint execution is 440 lines, and
  relation-joint execution is 598 lines. The Angle contract is 229 lines, the
  Rack-and-Pinion contract is 350 lines, the shared coupled-joint geometry
  layer is 194 lines, the Screw contract is 345 lines, the shared rotational
  coupling engine is 373 lines, and the Gears and Belt contracts are 170 lines
  each. Every execution module remains below the 1,000-line ceiling.
- The 1,405-line action inventory remains declarative rather than accumulating
  domain execution logic. New domain modules are split before they approach
  1,000 lines.

## Non-negotiable product rules

- The human alone changes ribbons and workbenches.
- The assistant has no workbench activation, ribbon activation, command-search,
  or arbitrary `runCommand` capability.
- The active provider surface is derived from the visible VibeCAD ribbon and
  contextual edit state, not from historical FreeCAD workbench-pack names.
- A human ribbon change invalidates the current assistant turn before another
  mutation can run.
- VibeCAD does not automatically continue an assistant turn after a surface
  change. The human resumes from the new surface.
- Tools cannot invoke a different surface as a hidden side effect.
- Human selection may provide exact targets, but labels and selection order are
  never silently guessed.
- Every mutation is one closed transaction and one coherent undo step.
- Expensive operations never block the UI thread.
- Normal provider results are concise and contain only information useful for
  deciding or verifying the next operation.
- Full diagnostics belong in debug logging, not normal tool results.
- VibeScript source remains authoritative for VibeScript-owned documents.
- Visibility, selection, tree expansion, and camera changes are presentation
  state and never count as VibeScript source overrides.
- Native mode does not attempt to backpropagate mutations into VibeScript.
- No partial Native mode is presented as finished to users.
- Runtime implementation must stay split by responsibility and ribbon/domain.
  Shared registries may describe contracts, but they may not accumulate domain
  execution logic; capability implementations and tests must be split before a
  source file becomes a multi-thousand-line monolith.

## Fixed surface scope

The current maximum ribbon inventory is:

| Human surface | Actions, including dropdown children and shared actions |
|---|---:|
| Model | 75 |
| Assemble | 53 |
| Mesh | 60 |
| Analyze | 103 |
| Manufacture | 61 |
| Drawing | 113 |
| Parameters | 24 |
| Contextual Sketch setup and edit | 117 |

There are 540 unique command IDs after shared actions are deduplicated. These
numbers are a baseline, not a manually maintained source of truth. The live
ribbon manifest must remain authoritative as conditional build features and
preferences change.

Conditional counts are union counts, not a claim that mutually exclusive
dropdown parents are visible simultaneously. For example, the separated
Drawing layout replaces `TechDraw_CompDimensionTools` while adding six IDs, so
that live layout contains 112 actions and the two-layout union contains 113.
The 19 context actions are tracked separately and are not included in the
ribbon totals.

Legacy FreeCAD commands available only from command search or legacy menus are
not part of Native assistant authority. Current VibeCAD context actions that
complete a ribbon workflow are in scope and must be classified separately.

## Provider result contract

### Successful mutation

A normal success contains only:

- `result`: the meaningful created or changed semantic object references and
  operation-specific verification;
- `state`: the smallest domain snapshot needed to continue;
- `next`: included only when a prerequisite or a particularly useful next
  action cannot be inferred from `state`.

The host must not echo the request, normalized arguments, empty arrays, empty
candidate lists, unchanged-state booleans, transaction internals, stack traces,
or duplicated object summaries.

### Successful read

A read returns the requested domain information directly. It does not wrap the
same information in document, observed, normalized, and diagnostic copies.
Pagination or explicit detail levels are required for potentially large data.

### Failure

A normal failure contains only:

- a stable error code;
- one clear human-readable message;
- the exact failing target when one was resolved;
- one repair action when deterministic;
- candidates only when the failure is genuinely ambiguous and the candidates
  are bounded and actionable.

Full native diagnostics, exception information, transaction traces, and timing
data go to the debug record and are referenced by a diagnostic ID only when
needed.

### Host-injected bookkeeping

The assistant should not manually pass revision numbers or retry tokens on
every call. The session adapter freezes the expected document revision and
injects an idempotency token into each mutation. The service verifies both
before mutation. Revision conflicts and duplicate retry results are returned
concisely.

## Execution ledger

### 0. Freeze scope and authority

- [x] 0.1 Record this plan as the sole Native-mode implementation ledger.
- [ ] 0.2 Record the owner approval for breaking old Native tool contracts in
  the first implementation PR.
- [x] 0.3 State explicitly that the approval does not cover VibeScript APIs.
- [x] 0.4 Define `native` and `vibescript` as the only authoring modes.
- [x] 0.5 Keep VibeScript as the default for new VibeScript projects.
- [x] 0.6 Define ordinary non-VibeScript documents as eligible for Native mode.
- [x] 0.7 Define the explicit one-way “Take manual control” transition for a
  VibeScript-owned document.
- [x] 0.8 Define the conditions that prevent taking manual control: active run,
  open transaction, edit task, regeneration, or unresolved candidate.
- [x] 0.9 Define how Native-authored changes prevent silent return to source
  authority without discard or a new source program.
- [x] 0.10 Define presentation-only changes that never alter authoring authority.

### 1. Build the live ribbon capability manifest

- [x] 1.1 Add one machine-readable manifest format for surfaces, groups,
  command IDs, dropdown parents, and leaf actions.
- [x] 1.2 Add fields for read, mutation, view, export, interactive, parent-only,
  and human-only classification.
- [x] 1.3 Add fields for native capability family and operation variant.
- [x] 1.4 Add fields for prerequisites, exact-target type, transaction behavior,
  postcondition checker, and background-operation requirement.
- [x] 1.5 Extract the live Model action graph.
- [x] 1.6 Extract the live Assemble action graph.
- [x] 1.7 Extract the live Mesh action graph, including Points and Reverse
  Engineering composition.
- [x] 1.8 Extract the live Analyze action graph for every compiled FEM/VTK
  feature combination.
- [x] 1.9 Extract the live Manufacture action graph for every supported
  preference and optional command combination.
- [x] 1.10 Extract the live Drawing action graph for both supported dimension
  layouts and all dropdown children.
- [x] 1.11 Extract the live Parameters action graph.
- [x] 1.12 Extract the Sketch setup action graph.
- [x] 1.13 Extract the in-edit Sketch action graph and dropdown children.
- [x] 1.14 Extract the shared View action graph.
- [x] 1.15 Extract the shared Inspect action graph.
- [x] 1.16 Extract the Assembly, CAM, Drawing, Fastener, and Inspection context
  actions that complete ribbon workflows.
- [x] 1.17 Classify application-strip New, Open, Save, Undo, Redo, and document
  tab actions separately from ribbon authoring.
- [x] 1.18 Classify command search, theme, assistant chrome, preferences, and
  debugger controls as human-only UI.
- [x] 1.19 Add a test that fails on every unclassified live action.
- [x] 1.20 Add a test that fails on stale manifest actions no longer shipped.
- [x] 1.21 Add a test that reports exact per-surface and unique-action counts.
- [x] 1.22 Make the manifest the only source used to assemble Native provider
  surfaces.

### 2. Remove the retired Native architecture

- [x] 2.1 Inventory every import and caller of `VibeCADWorkbenchTools`.
- [x] 2.2 Delete `WorkbenchToolPack` and the workbench-keyed pack table.
- [x] 2.3 Delete compatibility-only Part and Part Design provider-name lists.
- [x] 2.4 Delete the Draft, Surface, Points, Reverse Engineering, Robot,
  Material, Inspection, MeshPart, and other standalone workbench packs.
- [x] 2.5 Delete workbench command-prefix discovery from provider context.
- [x] 2.6 Delete workbench object-template discovery from provider context.
- [x] 2.7 Delete workbench pack summaries from the service.
- [x] 2.8 Delete the old native surface resolver path that reads a tool pack by
  `Gui.activeWorkbench().name()`.
- [x] 2.9 Delete provider logic that distinguishes canonical native tools from
  compatibility native tools.
- [x] 2.10 Delete old direct native tool schemas from the provider registry.
- [x] 2.11 Delete old direct native public dispatch names.
- [x] 2.12 Delete implementations used only by removed public wrappers.
- [x] 2.13 Move only proven domain algorithms into clean new capability modules
  when they already satisfy the new exact-target, transaction, result, and
  state contracts; delete their old wrapper modules and compatibility branches.
- [x] 2.14 Delete old workbench-pack contract tests.
- [x] 2.15 Delete old native tool-name compatibility tests.
- [ ] 2.16 Delete tests expecting old verbose result shapes.
- [ ] 2.17 Delete prompt prose that teaches the assistant old tool sequences.
- [x] 2.18 Delete provider-accessible workbench-switch registration.
- [x] 2.19 Delete arbitrary command enumeration from normal model context.
- [x] 2.20 Prove no removed native name remains registered or advertised.
- [x] 2.21 Prove VibeScript registrations and VibeScript tests are unchanged.

### 3. Restore authoring-mode selection cleanly

- [x] 3.1 Replace the hardcoded VibeScript engine result with a typed
  `native | vibescript` mode value.
- [x] 3.2 Add one authoritative mode store per project/document.
- [x] 3.3 Add a session-only Native choice for an unsaved ordinary document.
- [x] 3.4 Persist that session choice when the document is first saved.
- [x] 3.5 Restore a two-choice assistant-header selector.
- [x] 3.6 Remove historical Build123d and OpenSCAD choices completely.
- [x] 3.7 Disable the selector while a provider turn is running.
- [x] 3.8 Disable the selector while a document transaction is open.
- [x] 3.9 Disable the selector while a task or contextual edit is open.
- [x] 3.10 Require explicit confirmation for “Take manual control.”
- [x] 3.11 Show the authoring authority without implying that presentation
  changes modify source.
- [x] 3.12 Make a mode change affect only the next assistant turn.
- [x] 3.13 Do not mutate the document simply because the selector changed.
- [x] 3.14 Test new, saved, reopened, and multi-document mode behavior.
- [x] 3.15 Test refusal during runs, transactions, and edit tasks.

### 4. Make the human-selected ribbon authoritative

- [x] 4.1 Expose a stable VibeCAD ribbon surface ID from the ribbon controller.
- [x] 4.2 Represent Model, Assemble, Mesh, Analyze, Manufacture, Drawing, and
  Parameters as distinct permanent surface IDs.
- [x] 4.3 Represent Sketch setup and Sketch edit as distinct contextual states.
- [x] 4.4 Include compiled feature flags and relevant preferences in the
  surface revision.
- [x] 4.5 Resolve the provider surface from the ribbon controller, not legacy
  workbench-pack names.
- [x] 4.6 Verify the underlying workbench matches the ribbon controller without
  treating it as the capability source.
- [x] 4.7 Freeze the surface ID and schema digest at human turn start.
- [x] 4.8 Detect a human ribbon change before every tool call.
- [x] 4.9 Reject the call without mutation when the frozen surface changed.
- [x] 4.10 Return one concise “surface changed; resume from the current ribbon”
  result.
- [x] 4.11 Do not automatically start a continuation turn.
- [x] 4.12 Do not expose a tool that activates a ribbon or workbench.
- [x] 4.13 Do not let a domain tool call `Gui.activateWorkbench` indirectly.
- [x] 4.14 Prevent a Model tool from secretly entering Sketch edit mode.
- [x] 4.15 Allow only the explicit exact-target Leave Sketch control to close
  the current Sketch task; every other Sketch tool must preserve edit mode.
- [x] 4.16 Have create-sketch operations return the created sketch and a human
  instruction to open it when editing is required.
- [x] 4.17 Require the human to open contextual Sketch editing; allow only the
  explicit Native Leave control to finish it, then require a new turn.
- [x] 4.18 Test human ribbon switches before a call, during a long operation,
  and between two calls.
- [x] 4.19 Test that the assistant cannot reach a hidden surface through a raw
  command, registry lookup, or nested domain call.

### 5. Build the new capability registry and schema rules

- [x] 5.1 Create one ribbon-capability registry sourced from the manifest.
- [x] 5.2 Separate provider-facing capability definitions from domain execution
  implementations.
- [x] 5.3 Require `domain.operation` names with no aliases.
- [x] 5.4 Ban generic `execute`, `run_command`, and arbitrary command-ID inputs.
- [x] 5.5 Use discriminated unions for operation variants.
- [x] 5.6 Make irrelevant variant fields impossible in JSON Schema.
- [ ] 5.7 Use exact document/object/subelement references.
- [ ] 5.8 Use explicit unit-bearing fields or typed quantities.
- [x] 5.9 Bound arrays, text, object lists, and file result sizes.
- [x] 5.10 Give every tool one short intent-focused description.
- [ ] 5.11 Put prerequisites in schemas and descriptions rather than relying on
  prompt folklore.
- [x] 5.12 Define a hard per-surface provider tool-count budget.
- [x] 5.13 Define a hard per-surface serialized-schema byte budget.
- [ ] 5.14 Fail CI when either budget is exceeded.
- [ ] 5.15 Prove each advertised tool has one implementation and each
  implementation is advertised on at least one intended surface.

### 6. Implement host-owned state and operation memory

- [x] 6.1 Define one monotonic structural document revision.
- [x] 6.2 Exclude camera, selection, visibility, tree expansion, and UI chrome
  from structural revision changes.
- [x] 6.3 Detect human structural changes between provider calls.
- [x] 6.4 Freeze the expected revision at call dispatch.
- [x] 6.5 Generate an idempotency token at call dispatch.
- [x] 6.6 Reject stale mutations before opening a transaction.
- [x] 6.7 Return the prior verified result for a duplicate retry token.
- [x] 6.8 Record exact created, changed, deleted, and replaced object identities.
- [x] 6.9 Rebuild the working set from live document objects after every call.
- [x] 6.10 Keep only a bounded list of recent semantic operation receipts.
- [x] 6.11 Reconstruct state after save/reopen without relying on the chat
  transcript.
- [x] 6.12 Build the Model state snapshot.
- [x] 6.13 Build the Sketch state snapshot.
- [x] 6.14 Build the Assembly state snapshot.
- [x] 6.15 Build the Mesh state snapshot.
- [x] 6.16 Build the Analyze state snapshot.
- [x] 6.17 Build the Manufacture state snapshot.
- [x] 6.18 Build the Drawing state snapshot.
- [x] 6.19 Build the Parameters state snapshot.
- [x] 6.20 Include only the active domain snapshot in normal provider context.
- [x] 6.21 Include the exact current user selection as optional target context.
- [ ] 6.22 Keep full cross-domain state available only through explicit reads.
- [x] 6.23 Add a test that deletes prior tool transcript and continues from live
  state alone.
- [x] 6.24 Add a test that manual visibility changes do not create authoring
  conflicts.
- [x] 6.25 Add a test that manual geometry changes do create a revision conflict.

### 7. Implement transaction, verification, and background execution

- [x] 7.1 Add one common mutation transaction runner.
- [x] 7.2 Refuse to nest a provider mutation inside an existing transaction.
- [x] 7.3 Abort exactly on preflight or execution failure.
- [x] 7.4 Recompute exactly the affected document graph.
- [x] 7.5 Run an operation-specific postcondition before commit.
- [x] 7.6 Commit one semantic history operation and one undo step.
- [x] 7.7 Verify undo restores the exact pre-call state.
- [x] 7.8 Verify redo restores the exact committed state.
- [x] 7.9 Keep debug diagnostics outside the normal result.
- [x] 7.10 Add one background-operation manager for Mesh, Analyze, Manufacture,
  and expensive Drawing work.
- [x] 7.11 Report bounded phase and progress information.
- [x] 7.12 Support cooperative cancellation.
- [x] 7.13 Abort background transactions on cancellation.
- [x] 7.14 Keep the GUI event loop responsive during background execution.
- [x] 7.15 Test close-document and ribbon-switch behavior during long work.

### 8. Implement common document, View, and Inspect capabilities

- [x] 8.1 Implement concise active-domain state reading.
- [x] 8.2 Implement exact current-selection reading.
- [x] 8.3 Implement Fit All.
- [x] 8.4 Implement Isometric View.
- [x] 8.5 Implement Grid visibility without structural revision changes.
- [x] 8.6 Implement bounded screenshot capture for visual verification.
- [x] 8.7 Implement exact distance/angle/radius measurement.
- [x] 8.8 Implement mass and physical-property measurement.
- [x] 8.9 Implement visual-inspection result reading.
- [x] 8.10 Implement exact element inspection.
- [x] 8.11 Implement geometry validity checking.
- [x] 8.12 Implement guarded save.
- [x] 8.13 Implement assistant-run-local undo without touching unrelated human
  history.
- [x] 8.14 Classify New and Open as user-authorized document operations rather
  than ordinary modeling tools.
- [ ] 8.15 Verify common tools appear identically on every eligible surface and
  no mutation-only tool leaks through them.
- [x] 8.16 Keep Inspection Annotation and Leave Info Mode context controls
  human-only while direct inspection reads remain provider-callable.

### 9. Implement the Model surface

- [x] 9.1 Implement Component creation.
- [x] 9.2 Implement Body creation.
- [x] 9.3 Implement Sketch object creation without entering Sketch edit mode.
- [x] 9.4 Implement Sketch readiness/validation reading from Model.
- [x] 9.5 Implement SubShapeBinder creation.
- [x] 9.6 Implement Clone creation.
- [x] 9.7 Implement design extrusion with its current profile, termination,
  taper, symmetric/reversed direction, and New Body/Join/Cut/Intersect result
  semantics.
- [x] 9.8 Implement design revolution with its current profile, exact axis,
  angle, symmetric/reversed direction, and New Body/Join/Cut/Intersect result
  semantics.
- [x] 9.9 Implement design loft with ordered exact sections and its current New
  Body/Join/Cut/Intersect result semantics.
- [x] 9.10 Implement design sweep with exact profile/path references and its
  current New Body/Join/Cut/Intersect result semantics.
- [x] 9.11 Implement design helix with its current profile, axis, pitch/height/
  turns/angle controls, handedness, and New Body/Join/Cut/Intersect result
  semantics.
- [x] 9.12 Implement design Box primitive.
- [x] 9.13 Implement design Cylinder primitive.
- [x] 9.14 Implement design Sphere primitive.
- [x] 9.15 Implement design Cone primitive.
- [x] 9.16 Implement design Ellipsoid primitive.
- [x] 9.17 Implement design Torus primitive.
- [x] 9.18 Implement design Prism primitive.
- [x] 9.19 Implement design Wedge primitive.
- [x] 9.20 Implement design Tube primitive.
- [x] 9.21 Implement Hole with typed counterbore, countersink, thread, depth,
  and termination options.
- [x] 9.22 Implement Fillet.
- [x] 9.23 Implement Chamfer.
- [x] 9.24 Implement Draft.
- [x] 9.25 Implement Thickness.
- [x] 9.26 Implement design Mirror.
- [x] 9.27 Implement design Linear Pattern.
- [x] 9.28 Implement design Circular Pattern.
- [x] 9.29 Implement Multi-transform when present in the live command manifest.
- [x] 9.30 Implement standalone Part primitive creation.
- [x] 9.31 Implement standalone Part Builder operations.
- [x] 9.32 Implement standalone Part Extrude.
- [x] 9.33 Implement standalone Part Revolve.
- [x] 9.34 Implement standalone Part Mirror.
- [x] 9.35 Implement Body-aware Design Scale.
- [x] 9.36 Implement Make Face.
- [x] 9.37 Implement Ruled Surface.
- [x] 9.38 Implement Part Loft.
- [x] 9.39 Implement Part Sweep.
- [x] 9.40 Implement Section.
- [x] 9.41 Implement Cross Sections.
- [x] 9.42 Implement 3D Offset.
- [x] 9.43 Implement 2D Offset.
- [x] 9.44 Implement Projection on Surface.
- [x] 9.45 Implement Compound creation.
- [x] 9.46 Implement Compound explosion/separation.
- [x] 9.47 Implement Compound filtering.
- [x] 9.48 Implement boolean union/combine.
- [x] 9.49 Implement boolean cut.
- [x] 9.50 Implement boolean common/intersection.
- [x] 9.51 Implement boolean fragments/XOR variants that remain in the live
  Model manifest.
- [x] 9.52 Implement Join Connect.
- [x] 9.53 Implement Join Embed.
- [x] 9.54 Implement Join Cutout.
- [x] 9.55 Implement Split and Slice variants retained by the live manifest.
- [x] 9.56 Implement Defeaturing.
- [x] 9.57 Implement Surface Filling.
- [x] 9.58 Implement Geometric Fill Surface.
- [x] 9.59 Implement Surface Sections.
- [x] 9.60 Implement Extend Face.
- [x] 9.61 Implement Curve on Mesh from the Model surface.
- [x] 9.62 Implement Blend Curve.
- [x] 9.63 Implement standard fastener insertion.
- [x] 9.64 Implement standard fastener editing.
- [x] 9.65 Implement matching fastener hole creation.
- [x] 9.66 Implement fastener attachment.
- [x] 9.67 Implement component-interface publication.
- [x] 9.68 Verify Model never advertises Sketch-edit geometry tools.
- [x] 9.69 Verify every Model dropdown parent maps to all and only its live leaf
  variants.
- [x] 9.70 Complete the Model bracket workflow: create structure, create sketch
  object, human opens Sketch, human returns to Model, extrude, hole, pattern,
  and finish.

### 10. Implement the contextual Sketch surface

- [x] 10.1 Implement reading geometry, constraints, external references,
  attachment, profile status, and degrees of freedom.
- [x] 10.2 Implement Point geometry.
- [x] 10.3 Implement Line geometry.
- [x] 10.4 Implement Polyline geometry.
- [x] 10.5 Implement center-radius Arc geometry.
- [x] 10.6 Implement three-point Arc geometry.
- [x] 10.7 Implement elliptical Arc geometry.
- [x] 10.8 Implement hyperbolic Arc geometry.
- [x] 10.9 Implement parabolic Arc geometry.
- [x] 10.10 Implement center-radius Circle geometry.
- [x] 10.11 Implement three-point Circle geometry.
- [x] 10.12 Implement center-based Ellipse geometry.
- [x] 10.13 Implement three-point Ellipse geometry.
- [x] 10.14 Implement corner Rectangle geometry.
- [x] 10.15 Implement center Rectangle geometry.
- [x] 10.16 Implement Oblong geometry.
- [x] 10.17 Implement Triangle geometry.
- [x] 10.18 Implement Square geometry.
- [x] 10.19 Implement Pentagon geometry.
- [x] 10.20 Implement Hexagon geometry.
- [x] 10.21 Implement Heptagon geometry.
- [x] 10.22 Implement Octagon geometry.
- [x] 10.23 Implement arbitrary Regular Polygon geometry.
- [x] 10.24 Implement straight Slot geometry.
- [x] 10.25 Implement arc Slot geometry.
- [x] 10.26 Implement non-periodic B-spline geometry.
- [x] 10.27 Implement periodic B-spline geometry.
- [x] 10.28 Implement interpolated B-spline geometry.
- [x] 10.29 Implement periodic interpolated B-spline geometry.
- [x] 10.30 Implement Sketch text geometry.
- [x] 10.31 Implement Construction-state changes.
- [x] 10.32 Implement automatic/general Dimension inference with explicit
  ambiguity refusal.
- [x] 10.33 Implement horizontal Distance constraint.
- [x] 10.34 Implement vertical Distance constraint.
- [x] 10.35 Implement general Distance constraint.
- [x] 10.36 Implement combined Radius/Diameter constraint behavior.
- [x] 10.37 Implement Radius constraint.
- [x] 10.38 Implement Diameter constraint.
- [x] 10.39 Implement Angle constraint.
- [x] 10.40 Implement Lock constraint.
- [x] 10.41 Implement Coincident constraint.
- [x] 10.42 Implement automatic Horizontal/Vertical constraint with explicit
  ambiguity refusal.
- [x] 10.43 Implement Horizontal constraint.
- [x] 10.44 Implement Vertical constraint.
- [x] 10.45 Implement Parallel constraint.
- [x] 10.46 Implement Perpendicular constraint.
- [x] 10.47 Implement Tangent constraint.
- [x] 10.48 Implement Equal constraint.
- [x] 10.49 Implement Symmetric constraint.
- [x] 10.50 Implement Block constraint.
- [x] 10.51 Implement Constraint Group behavior.
- [x] 10.52 Implement Driving/Reference toggle.
- [x] 10.53 Implement Active/Inactive toggle.
- [x] 10.54 Implement Sketch Fillet.
- [x] 10.55 Implement Sketch Chamfer.
- [x] 10.56 Implement Trim.
- [x] 10.57 Implement Split.
- [x] 10.58 Implement Extend.
- [x] 10.59 Implement external geometry Projection.
- [x] 10.60 Implement external geometry Intersection.
- [x] 10.61 Implement Carbon Copy.
- [x] 10.62 Implement Translate.
- [x] 10.63 Implement Rotate.
- [x] 10.64 Implement Scale.
- [x] 10.65 Implement Offset.
- [x] 10.66 Implement Symmetry.
- [x] 10.67 Implement removal of axis alignment.
- [x] 10.68 Implement B-spline conversion to NURBS.
- [x] 10.69 Implement B-spline degree increase.
- [x] 10.70 Implement B-spline degree decrease.
- [x] 10.71 Implement B-spline knot-multiplicity increase.
- [x] 10.72 Implement B-spline knot-multiplicity decrease.
- [x] 10.73 Implement B-spline knot insertion.
- [x] 10.74 Implement curve joining.
- [x] 10.75 Implement constraint-based element selection as a read operation.
- [x] 10.76 Implement element-associated constraint selection as a read
  operation.
- [x] 10.77 Implement arc overlay as presentation state.
- [x] 10.78 Implement B-spline degree-information visibility.
- [x] 10.79 Implement B-spline control-polygon visibility.
- [x] 10.80 Implement B-spline curvature-comb visibility.
- [x] 10.81 Implement B-spline knot-multiplicity visibility.
- [x] 10.82 Implement B-spline pole-weight visibility.
- [x] 10.83 Implement internal-alignment restoration.
- [x] 10.84 Implement virtual-space switching as presentation state.
- [x] 10.85 Implement a bounded batch call that creates geometry and constraints
  using client-local references.
- [x] 10.86 Make the batch call atomic and reject invalid references before
  mutation.
- [x] 10.87 Return stable geometry and constraint references from the batch.
- [x] 10.88 Return closed-profile status, degrees of freedom, redundancy,
  conflicts, and degenerate geometry without dumping all solver internals.
- [x] 10.89 Expose exact Leave Sketch as provider edit control and keep Cancel
  Sketch human-controlled.
- [x] 10.90 Verify Leave Sketch does not activate Model, another workbench, or
  another ribbon, and that the resulting contextual change invalidates the
  current turn.
- [x] 10.91 Complete a constrained profile from a fresh transcript using no more
  than two mutating calls.

### 11. Implement the Assemble surface

- [x] 11.1 Implement Assembly creation.
- [x] 11.2 Implement active-assembly reading without changing activation.
- [x] 11.3 Implement insertion of an existing component link.
- [x] 11.4 Implement creation/insertion of a new part.
- [x] 11.5 Implement Ground and Unground.
- [x] 11.6 Implement Fixed joint.
- [x] 11.7 Implement Revolute joint.
- [x] 11.8 Implement Cylindrical joint.
- [x] 11.9 Implement Slider joint.
- [x] 11.10 Implement Ball joint.
- [x] 11.11 Implement Distance joint.
- [x] 11.12 Implement Parallel joint.
- [x] 11.13 Implement Perpendicular joint.
- [x] 11.14 Implement Angle joint.
- [x] 11.15 Implement Rack-and-Pinion joint.
- [x] 11.16 Implement Screw joint.
- [x] 11.17 Implement Gear joint.
- [x] 11.18 Implement Belt joint.
- [x] 11.19 Implement solver execution and exact placement verification.
- [x] 11.20 Implement conflicting-constraint diagnosis.
- [x] 11.21 Implement redundant-constraint diagnosis.
- [x] 11.22 Implement partially redundant-constraint diagnosis.
- [x] 11.23 Implement malformed-constraint diagnosis.
- [x] 11.24 Implement joints-of-component reading.
- [x] 11.25 Implement assembly view creation.
- [x] 11.26 Implement simulation creation.
- [x] 11.27 Implement simulation playback and restoration.
- [x] 11.28 Implement BOM creation.
- [ ] 11.29 Implement linked-source selection reading.
- [ ] 11.30 Implement ASMT export with explicit path authorization.
- [ ] 11.31 Implement Assemble-ribbon fastener insertion.
- [ ] 11.32 Implement Assemble-ribbon fastener editing.
- [ ] 11.33 Implement Assemble-ribbon matching-hole action when present.
- [ ] 11.34 Implement Assemble-ribbon fastener attachment when present.
- [ ] 11.35 Implement Robot creation and setup.
- [ ] 11.36 Implement Robot tool shape, orientation, and default values.
- [ ] 11.37 Implement Robot trajectory and waypoint operations.
- [ ] 11.38 Implement Robot edge, dress-up, and compound trajectories.
- [ ] 11.39 Implement Robot home, restore, and simulation.
- [ ] 11.40 Implement component-interface publication from Assemble.
- [ ] 11.41 Return components, exact instance IDs, grounding, joints, remaining
  degrees of freedom, residuals, and conflicts concisely.
- [ ] 11.42 Complete insert, ground, joint, solve, BOM, and simulation workflows.
- [ ] 11.43 Implement exact-target conversion of an AssemblyLink to flexible.
- [ ] 11.44 Implement exact-target conversion of an AssemblyLink to rigid.
- [ ] 11.45 Keep the Assembly “Active object” context control human-only.

### 12. Implement the Mesh surface

- [ ] 12.1 Implement mesh inventory reading.
- [ ] 12.2 Implement mesh import with explicit file authorization.
- [ ] 12.3 Implement mesh export with explicit file authorization.
- [ ] 12.4 Implement regular-solid mesh creation.
- [ ] 12.5 Implement shape-to-mesh conversion.
- [ ] 12.6 Implement mesh-to-shape conversion.
- [ ] 12.7 Implement curve-on-mesh.
- [ ] 12.8 Implement normal harmonization.
- [ ] 12.9 Implement normal flipping.
- [ ] 12.10 Implement automatic hole filling.
- [ ] 12.11 Implement exact-boundary interactive-hole equivalent.
- [ ] 12.12 Implement facet addition.
- [ ] 12.13 Implement component removal.
- [ ] 12.14 Implement explicit component-by-hand equivalent using stable
  component IDs.
- [ ] 12.15 Implement smoothing.
- [ ] 12.16 Implement Gmsh remeshing in the background.
- [ ] 12.17 Implement decimation.
- [ ] 12.18 Implement scaling.
- [ ] 12.19 Implement mesh union.
- [ ] 12.20 Implement mesh intersection.
- [ ] 12.21 Implement mesh difference.
- [ ] 12.22 Implement polygon cut.
- [ ] 12.23 Implement polygon trim.
- [ ] 12.24 Implement trim by plane.
- [ ] 12.25 Implement section by plane.
- [ ] 12.26 Implement cross sections.
- [ ] 12.27 Implement component merge.
- [ ] 12.28 Implement component split.
- [ ] 12.29 Implement segmentation.
- [ ] 12.30 Implement best-fit segmentation.
- [ ] 12.31 Implement mesh evaluation.
- [ ] 12.32 Implement facet evaluation.
- [ ] 12.33 Implement vertex-curvature calculation.
- [ ] 12.34 Implement curvature information reading.
- [ ] 12.35 Implement solid/watertight evaluation.
- [ ] 12.36 Implement bounding-box reading.
- [ ] 12.37 Implement point-cloud import.
- [ ] 12.38 Implement point-cloud export.
- [ ] 12.39 Implement point-cloud conversion.
- [ ] 12.40 Implement point-cloud structure/edit operation.
- [ ] 12.41 Implement point-cloud merge.
- [ ] 12.42 Implement point-cloud polygon cutting.
- [ ] 12.43 Implement Poisson reconstruction in the background.
- [ ] 12.44 Implement triangulation viewing/reading.
- [ ] 12.45 Implement manual segmentation with structured regions.
- [ ] 12.46 Implement segmentation from components.
- [ ] 12.47 Implement mesh-boundary extraction.
- [ ] 12.48 Implement plane approximation.
- [ ] 12.49 Implement cylinder approximation.
- [ ] 12.50 Implement sphere approximation.
- [ ] 12.51 Implement polynomial approximation.
- [ ] 12.52 Implement surface approximation.
- [ ] 12.53 Implement curve approximation.
- [ ] 12.54 Implement optional flat-mesh and flat-face conversions when compiled.
- [ ] 12.55 Return counts, bounds, components, manifold status, defects, and
  changed topology concisely.
- [ ] 12.56 Complete repair, convert, Points, and Reverse Engineering workflows.

### 13. Implement the Analyze surface

- [ ] 13.1 Implement Analysis container creation and reading.
- [ ] 13.2 Implement solid material assignment.
- [ ] 13.3 Implement fluid material assignment.
- [ ] 13.4 Implement nonlinear mechanical material assignment.
- [ ] 13.5 Implement reinforced material assignment.
- [ ] 13.6 Implement material-property reading/editing without a modal editor.
- [ ] 13.7 Implement 1D element geometry.
- [ ] 13.8 Implement 1D element rotation.
- [ ] 13.9 Implement 2D element geometry.
- [ ] 13.10 Implement 1D fluid element setup.
- [ ] 13.11 Implement vacuum permittivity.
- [ ] 13.12 Implement electromagnetic constraint.
- [ ] 13.13 Implement current-density constraint.
- [ ] 13.14 Implement magnetization constraint.
- [ ] 13.15 Implement electric-charge-density constraint.
- [ ] 13.16 Implement initial-flow-velocity constraint.
- [ ] 13.17 Implement initial-pressure constraint.
- [ ] 13.18 Implement flow-velocity constraint.
- [ ] 13.19 Implement plane-rotation constraint.
- [ ] 13.20 Implement section-print constraint.
- [ ] 13.21 Implement transform constraint.
- [ ] 13.22 Implement fixed constraint.
- [ ] 13.23 Implement rigid-body constraint.
- [ ] 13.24 Implement displacement constraint.
- [ ] 13.25 Implement contact constraint.
- [ ] 13.26 Implement tie constraint.
- [ ] 13.27 Implement spring constraint.
- [ ] 13.28 Implement force constraint.
- [ ] 13.29 Implement pressure constraint.
- [ ] 13.30 Implement centrifugal constraint.
- [ ] 13.31 Implement self-weight constraint.
- [ ] 13.32 Implement initial-temperature constraint.
- [ ] 13.33 Implement heat-flux constraint.
- [ ] 13.34 Implement temperature constraint.
- [ ] 13.35 Implement body-heat-source constraint.
- [ ] 13.36 Implement Netgen mesh generation in the background.
- [ ] 13.37 Implement Gmsh mesh generation in the background.
- [ ] 13.38 Implement mesh region.
- [ ] 13.39 Implement mesh group.
- [ ] 13.40 Implement mesh-distance refinement.
- [ ] 13.41 Implement boundary-layer refinement.
- [ ] 13.42 Implement mesh-shape refinement.
- [ ] 13.43 Implement mesh manipulation.
- [ ] 13.44 Implement advanced mesh settings.
- [ ] 13.45 Implement transfinite curve settings.
- [ ] 13.46 Implement transfinite surface settings.
- [ ] 13.47 Implement transfinite volume settings.
- [ ] 13.48 Implement element-set creation.
- [ ] 13.49 Implement FEM-mesh to Mesh conversion.
- [ ] 13.50 Implement CalculiX solver creation.
- [ ] 13.51 Implement Elmer solver creation.
- [ ] 13.52 Implement Mystran solver creation.
- [ ] 13.53 Implement Z88 solver creation.
- [ ] 13.54 Implement elasticity equation.
- [ ] 13.55 Implement deformation equation.
- [ ] 13.56 Implement electrostatic equation.
- [ ] 13.57 Implement electric-force equation.
- [ ] 13.58 Implement magnetodynamic equation.
- [ ] 13.59 Implement 2D magnetodynamic equation.
- [ ] 13.60 Implement static-current equation.
- [ ] 13.61 Implement flow equation.
- [ ] 13.62 Implement flux equation.
- [ ] 13.63 Implement heat equation.
- [ ] 13.64 Implement solver-control editing.
- [ ] 13.65 Implement background solver execution and cancellation.
- [ ] 13.66 Implement clipping-plane add and remove as presentation state.
- [ ] 13.67 Classify Examples as human-only instructional UI.
- [ ] 13.68 Implement result purge.
- [ ] 13.69 Implement result display selection.
- [ ] 13.70 Implement post-pipeline creation.
- [ ] 13.71 Implement branch filter.
- [ ] 13.72 Implement warp filter.
- [ ] 13.73 Implement scalar clip filter.
- [ ] 13.74 Implement cut-function filter.
- [ ] 13.75 Implement region clip filter.
- [ ] 13.76 Implement contour filter.
- [ ] 13.77 Implement data-along-line extraction.
- [ ] 13.78 Implement linearized-stress reading.
- [ ] 13.79 Implement data-at-point extraction.
- [ ] 13.80 Implement calculator filter.
- [ ] 13.81 Implement plane post function.
- [ ] 13.82 Implement sphere post function.
- [ ] 13.83 Implement cylinder post function.
- [ ] 13.84 Implement box post function.
- [ ] 13.85 Implement glyph filter when compiled.
- [ ] 13.86 Implement table visualization when compiled.
- [ ] 13.87 Implement histogram visualization when compiled.
- [ ] 13.88 Implement line-plot visualization when compiled.
- [ ] 13.89 Return analysis graph, readiness, mesh, solver, run status, and result
  summaries without dumping native result arrays.
- [ ] 13.90 Complete structural, thermal, fluid, electromagnetic, and
  post-processing workflows.

### 14. Implement the Manufacture surface

- [ ] 14.1 Implement Job creation and replacement.
- [ ] 14.2 Implement Job sanity checking.
- [ ] 14.3 Implement tool-controller creation.
- [ ] 14.4 Implement tool-bit selection and properties.
- [ ] 14.5 Implement tool-bit save/export with explicit path authorization.
- [ ] 14.6 Implement Profile operation.
- [ ] 14.7 Implement Pocket Shape operation.
- [ ] 14.8 Implement Mill Facing operation.
- [ ] 14.9 Implement Helix operation.
- [ ] 14.10 Implement Adaptive operation.
- [ ] 14.11 Implement Slot operation.
- [ ] 14.12 Implement Drilling operation.
- [ ] 14.13 Implement Thread Milling operation.
- [ ] 14.14 Implement Engrave operation.
- [ ] 14.15 Implement Deburr operation.
- [ ] 14.16 Implement V-carve operation.
- [ ] 14.17 Implement Pocket 3D operation.
- [ ] 14.18 Implement optional Surface operation.
- [ ] 14.19 Implement optional Waterline operation.
- [ ] 14.20 Implement optional Rotary Surface operation.
- [ ] 14.21 Implement operation active/inactive toggle.
- [ ] 14.22 Implement loop-selection reading.
- [ ] 14.23 Implement toolpath inspection.
- [ ] 14.24 Implement operation copy.
- [ ] 14.25 Implement operation array.
- [ ] 14.26 Implement simple copy.
- [ ] 14.27 Implement Array dress-up.
- [ ] 14.28 Implement Axis Map dress-up.
- [ ] 14.29 Implement Path Boundary dress-up.
- [ ] 14.30 Implement Dogbone dress-up.
- [ ] 14.31 Implement Drag Knife dress-up.
- [ ] 14.32 Implement Lead In/Out dress-up.
- [ ] 14.33 Implement Mirror dress-up.
- [ ] 14.34 Implement Ramp Entry dress-up.
- [ ] 14.35 Implement Tag dress-up.
- [ ] 14.36 Implement Z Correct dress-up.
- [ ] 14.37 Implement Comment operation.
- [ ] 14.38 Implement Stop operation.
- [ ] 14.39 Implement Custom operation.
- [ ] 14.40 Implement Probe operation.
- [ ] 14.41 Implement Property Bag operation.
- [ ] 14.42 Implement Area and Area Workplane helpers when enabled.
- [ ] 14.43 Implement Start Point editing.
- [ ] 14.44 Implement GL simulation in the background.
- [ ] 14.45 Implement native simulation in the background.
- [ ] 14.46 Implement CAMotics launch/result reading when available.
- [ ] 14.47 Implement postprocessing of the complete job.
- [ ] 14.48 Implement postprocessing of selected operations.
- [ ] 14.49 Implement template export.
- [ ] 14.50 Implement Manufacture-ribbon Robot trajectory operations.
- [ ] 14.51 Implement KUKA compact export.
- [ ] 14.52 Implement KUKA full export.
- [ ] 14.53 Return active job, stock, machine, tools, ordered operations,
  toolpath validity, and simulation/post readiness concisely.
- [ ] 14.54 Complete job, tool, operation, dress-up, simulation, and post
  workflows without blocking the UI.
- [ ] 14.55 Implement distinct tool-bit Save and Save As output variants with
  explicit path authorization.

### 15. Implement the Drawing surface

- [ ] 15.1 Implement default page creation.
- [ ] 15.2 Implement template-based page creation.
- [ ] 15.3 Implement template-field editing.
- [ ] 15.4 Implement page redraw.
- [ ] 15.5 Implement standard projected view.
- [ ] 15.6 Implement broken view.
- [ ] 15.7 Implement active view.
- [ ] 15.8 Implement section view.
- [ ] 15.9 Implement complex section view.
- [ ] 15.10 Implement detail view.
- [ ] 15.11 Implement Draft-source view.
- [ ] 15.12 Implement clipping view/group behavior.
- [ ] 15.13 Implement stack top.
- [ ] 15.14 Implement stack bottom.
- [ ] 15.15 Implement stack up.
- [ ] 15.16 Implement stack down.
- [ ] 15.17 Implement general Dimension inference with ambiguity refusal.
- [ ] 15.18 Implement Length dimension.
- [ ] 15.19 Implement Horizontal dimension.
- [ ] 15.20 Implement Vertical dimension.
- [ ] 15.21 Implement Radius dimension.
- [ ] 15.22 Implement Diameter dimension.
- [ ] 15.23 Implement Angle dimension.
- [ ] 15.24 Implement three-point Angle dimension.
- [ ] 15.25 Implement Area dimension.
- [ ] 15.26 Implement horizontal Extent dimension.
- [ ] 15.27 Implement vertical Extent dimension.
- [ ] 15.28 Implement Axonometric Length dimension.
- [ ] 15.29 Implement horizontal Chain dimensions.
- [ ] 15.30 Implement vertical Chain dimensions.
- [ ] 15.31 Implement oblique Chain dimensions.
- [ ] 15.32 Implement horizontal Coordinate dimensions.
- [ ] 15.33 Implement vertical Coordinate dimensions.
- [ ] 15.34 Implement oblique Coordinate dimensions.
- [ ] 15.35 Implement horizontal Chamfer dimension.
- [ ] 15.36 Implement vertical Chamfer dimension.
- [ ] 15.37 Implement Arc Length dimension.
- [ ] 15.38 Implement Balloon creation.
- [ ] 15.39 Implement Balloon editing.
- [ ] 15.40 Implement Dimension repair.
- [ ] 15.41 Implement line-attribute selection/readback.
- [ ] 15.42 Implement line-attribute change.
- [ ] 15.43 Implement line extension.
- [ ] 15.44 Implement line shortening.
- [ ] 15.45 Implement view lock/unlock.
- [ ] 15.46 Implement section-view positioning.
- [ ] 15.47 Implement area annotation.
- [ ] 15.48 Implement arc-length annotation.
- [ ] 15.49 Implement format customization.
- [ ] 15.50 Implement circle center lines.
- [ ] 15.51 Implement hole-circle centers.
- [ ] 15.52 Implement thread-hole side representation.
- [ ] 15.53 Implement thread-hole bottom representation.
- [ ] 15.54 Implement thread-bolt side representation.
- [ ] 15.55 Implement thread-bolt bottom representation.
- [ ] 15.56 Implement vertex at intersection.
- [ ] 15.57 Implement offset vertex.
- [ ] 15.58 Implement cosmetic circle.
- [ ] 15.59 Implement center-radius cosmetic circle.
- [ ] 15.60 Implement three-point cosmetic circle.
- [ ] 15.61 Implement cosmetic arc.
- [ ] 15.62 Implement parallel cosmetic line.
- [ ] 15.63 Implement perpendicular cosmetic line.
- [ ] 15.64 Implement diameter-prefix insertion.
- [ ] 15.65 Implement square-prefix insertion.
- [ ] 15.66 Implement repetition-prefix insertion.
- [ ] 15.67 Implement prefix removal.
- [ ] 15.68 Implement decimal increase.
- [ ] 15.69 Implement decimal decrease.
- [ ] 15.70 Implement frame visibility.
- [ ] 15.71 Implement standard hatch.
- [ ] 15.72 Implement geometric hatch.
- [ ] 15.73 Implement rich-text annotation.
- [ ] 15.74 Implement leader line.
- [ ] 15.75 Implement cosmetic vertex.
- [ ] 15.76 Implement midpoint vertices.
- [ ] 15.77 Implement quadrant vertices.
- [ ] 15.78 Implement face centerline.
- [ ] 15.79 Implement two-line centerline.
- [ ] 15.80 Implement two-point centerline.
- [ ] 15.81 Implement two-point cosmetic line.
- [ ] 15.82 Implement line decoration.
- [ ] 15.83 Implement Show All presentation action.
- [ ] 15.84 Implement weld symbol.
- [ ] 15.85 Implement surface-finish symbol.
- [ ] 15.86 Implement hole/shaft fit.
- [ ] 15.87 Implement Keep Updated editing.
- [ ] 15.88 Implement Drawing grid and frame context actions as presentation.
- [ ] 15.89 Implement SVG export.
- [ ] 15.90 Implement DXF export.
- [ ] 15.91 Implement PDF export.
- [ ] 15.92 Implement Print All as explicit user-authorized output.
- [ ] 15.93 Resolve source geometry by stable semantic reference, never screenshot
  position or unverified edge number.
- [ ] 15.94 Return pages, views, placement, dimensions, unresolved references,
  update status, and export readiness concisely.
- [ ] 15.95 Complete page, orthographic/section/detail, dimension, annotation,
  and export workflows.
- [ ] 15.96 Implement exact-target Dimension editing without opening the human
  task dialog.
- [ ] 15.97 Implement Show Drawing as a presentation action.
- [ ] 15.98 Keep Edit Balloon and Edit Dimension context entry points
  human-controlled while provider operations use exact targets directly.

### 16. Implement the Parameters surface

- [ ] 16.1 Implement Sheet creation.
- [ ] 16.2 Implement Spreadsheet import with explicit file authorization.
- [ ] 16.3 Implement Spreadsheet export with explicit file authorization.
- [ ] 16.4 Implement bounded sheet/range reading.
- [ ] 16.5 Implement bounded batch value writing.
- [ ] 16.6 Implement formula writing and dependency reporting.
- [ ] 16.7 Implement alias creation and validation.
- [ ] 16.8 Implement cell merge.
- [ ] 16.9 Implement cell split.
- [ ] 16.10 Implement cell-property editing.
- [ ] 16.11 Implement left alignment.
- [ ] 16.12 Implement center alignment.
- [ ] 16.13 Implement right alignment.
- [ ] 16.14 Implement top alignment.
- [ ] 16.15 Implement vertical-center alignment.
- [ ] 16.16 Implement bottom alignment.
- [ ] 16.17 Implement bold style.
- [ ] 16.18 Implement italic style.
- [ ] 16.19 Implement underline style.
- [ ] 16.20 Return changed ranges, aliases, formula errors, and recompute effects
  concisely.
- [ ] 16.21 Complete parameter-table, alias, formula, formatting, and model-link
  workflows.

### 17. Integrate the provider without noisy context or results

- [x] 17.1 Build provider schemas only from the frozen human-selected surface.
- [x] 17.2 Remove global native tool registration from provider-visible context.
- [x] 17.3 Keep the complete internal capability registry out of prompts.
- [x] 17.4 Inject one compact active-domain state at turn start.
- [x] 17.5 Inject exact selection only when present.
- [x] 17.6 Do not inject command lists, workbench lists, object templates, or
  compatibility instructions.
- [x] 17.7 Replace the current surface-only `state_after` response with the
  concise active-domain state contract.
- [x] 17.8 Remove empty and duplicated fields from normal successes.
- [x] 17.9 Remove full structured diagnostics from normal failures.
- [ ] 17.10 Preserve full diagnostics in opt-in debug capture.
- [x] 17.11 Enforce result byte budgets.
- [x] 17.12 Add explicit bounded reads instead of truncating semantic values.
- [ ] 17.13 Test tool choice with near-neighbor variants on every surface.
- [ ] 17.14 Test that losing earlier receipts does not cause duplicate or stale
  mutations.
- [x] 17.15 Test provider refusal after a human surface switch.

### 18. Replace the old tests with exact new contracts

- [ ] 18.1 Add manifest parity tests for every surface.
- [ ] 18.2 Add provider schema snapshot tests for Model.
- [ ] 18.3 Add provider schema snapshot tests for Sketch.
- [ ] 18.4 Add provider schema snapshot tests for Assemble.
- [ ] 18.5 Add provider schema snapshot tests for Mesh.
- [ ] 18.6 Add provider schema snapshot tests for Analyze.
- [ ] 18.7 Add provider schema snapshot tests for Manufacture.
- [ ] 18.8 Add provider schema snapshot tests for Drawing.
- [ ] 18.9 Add provider schema snapshot tests for Parameters.
- [x] 18.10 Add concise-success contract tests.
- [x] 18.11 Add concise-read contract tests.
- [x] 18.12 Add concise-failure contract tests.
- [x] 18.13 Add debug-diagnostic separation tests.
- [x] 18.14 Add stale-revision tests.
- [x] 18.15 Add duplicate-retry tests.
- [x] 18.16 Add exact transaction-close tests.
- [x] 18.17 Add undo/redo tests.
- [x] 18.18 Add save/reopen state-reconstruction tests.
- [x] 18.19 Add multi-document identity tests.
- [x] 18.20 Add human-selection and ambiguity tests.
- [x] 18.21 Add human ribbon-switch invalidation tests.
- [x] 18.22 Add explicit tests proving no AI workbench-switch capability exists.
- [ ] 18.23 Add UI responsiveness tests for long Mesh operations.
- [ ] 18.24 Add UI responsiveness tests for FEM mesh and solver operations.
- [ ] 18.25 Add UI responsiveness tests for CAM simulation/postprocessing.
- [ ] 18.26 Add UI responsiveness tests for expensive Drawing updates/exports.
- [ ] 18.27 Add clean-profile end-to-end workflow tests for all eight surfaces.
- [ ] 18.28 Run focused native tests after every capability group.
- [ ] 18.29 Run the cross-ribbon GUI regression after every surface is completed.
- [ ] 18.30 Run a clean release build before enabling the selector by default.

### 19. Roll out without presenting a partial system as complete

- [ ] 19.1 Land the manifest and removal inventory first with Native still
  disabled.
- [ ] 19.2 Land the new registry, state, result, transaction, and background
  foundations with Native still disabled.
- [ ] 19.3 Complete and verify Model.
- [ ] 19.4 Complete and verify Sketch.
- [ ] 19.5 Complete and verify Assemble.
- [ ] 19.6 Complete and verify Mesh.
- [ ] 19.7 Complete and verify Analyze.
- [ ] 19.8 Complete and verify Manufacture.
- [ ] 19.9 Complete and verify Drawing.
- [ ] 19.10 Complete and verify Parameters.
- [ ] 19.11 Run the full inventory, provider, lifecycle, and release-build gates.
- [ ] 19.12 Enable Native mode behind a developer preference for live acceptance.
- [ ] 19.13 Exercise human-driven ribbon changes and real assistant use in every
  surface.
- [ ] 19.14 Remove the developer gate only after all blockers are empty.
- [ ] 19.15 Update user documentation with the Native/VibeScript authority
  boundary and human-controlled ribbon behavior.
- [ ] 19.16 Update MCP documentation to state that external AI clients cannot
  switch workbenches through the authoring surface.
- [ ] 19.17 Publish the complete breaking-change list and replacement workflow.

## Objective `DONE` gate

The effort is complete only when:

- every checkbox above is complete or an owner-approved manifest row explicitly
  classifies the capability as human-only;
- the live manifest contains every current ribbon and contextual action exactly
  once per surface;
- no old workbench pack, compatibility alias, direct native provider name, or
  provider-accessible workbench switch remains;
- no Native tool can activate a ribbon/workbench or enter Sketch edit mode; the
  sole Leave Sketch control exact-targets the current task and invalidates the
  turn;
- every surface stays within its tool-count and schema-byte budgets;
- every normal success and failure satisfies the concise result contract;
- a provider can continue from live state without its historical tool-call
  transcript;
- all mutation tools are exact-target, idempotent, atomic, undoable, and
  reconstructable after save/reopen;
- long operations preserve GUI responsiveness and support cancellation;
- all eight clean-profile workflows and the release build pass;
- VibeScript behavior and VibeScript tests remain intact;
- the mode selector is user-visible only after the complete gate passes.

Status must be reported from this ledger and the live manifest, never from an
estimated completion percentage.
