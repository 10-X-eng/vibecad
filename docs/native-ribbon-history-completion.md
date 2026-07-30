# Native Ribbon and History Completion Ledger

Status: In Progress
Scope owner: VibeCAD native human-tool experience
Last updated: 2026-07-30

This is the completion ledger for the current effort. It is intentionally
short. A ribbon is not complete because code exists or a test happens to pass;
it is complete only when every gate below is satisfied and no listed blocker
remains.

## Fixed scope

VibeCAD must provide:

1. One Fusion-style global History timeline at the bottom of the application.
2. A clear model tree: Bodies show final results, Sketches are independently
   visible, and feature operations are edited or suppressed through History.
3. Predictable native ribbon tools for Model, Assemble, Mesh, Analyze,
   Manufacture, and Drawing.
4. Sketcher is a first-class editing surface with its own command and lifecycle
   acceptance, including entry from Model and return to the prior ribbon.
5. Shared Inspect, Measure, Material, visibility, selection, grid, and tree
   behavior wherever those capabilities apply.
6. Correct native lifecycle behavior: create, edit, accept, cancel, delete,
   suppress, unsuppress, undo, redo, save, reopen, and switch ribbons without
   crashes, abandoned geometry, duplicated rendering, or hidden state changes.
7. Every remaining shipped native workbench has a deliberate VibeCAD ribbon
   home: Surface in Model, Points and Reverse Engineering in Mesh, Spreadsheet
   in a clear Parameters surface, and Robot in Assemble or Manufacture
   according to command purpose. Draft is deliberately excluded: Sketcher owns
   constrained mechanical profiles, while Draft is the retired
   architecture-oriented, unconstrained 2D path.

This ledger does not expand the effort into new VibeScript features, provider
work, release packaging, or unrelated FreeCAD cleanup. Those are separate
unless a native contract change directly breaks them.

## Required behavior

Every shipped top-level command and every leaf inside a drop-down must be
classified and verified as exactly one of:

- **Operation:** creates one user-visible History operation; implementation
  objects are owned resources, never competing tree or viewport results.
- **In-place edit:** changes existing state in one exact undoable transaction
  without inventing a History operation.
- **Read-only:** changes no document state and creates no undo or History entry.

For every applicable command:

- selection and preselection identify the exact intended object or subelement;
- a task owns one exact transaction from launch through Accept or Cancel;
- Accept produces one durable, undoable result;
- Cancel restores the exact pre-task document and never crashes;
- invalid input leaves the document unchanged and gives a useful message;
- edit and suppression operate on the same semantic History operation;
- visibility is not used as a substitute for suppression or model membership;
- undo/redo and save/reopen preserve object identity, order, visibility, and
  History position;
- switching ribbons or opening the Assistant does not move, hide, duplicate, or
  destroy the tree, grid, viewport, or timeline.

## Completion matrix

| Surface | Current state | Must be true before `DONE` |
|---|---|---|
| Shared application shell | **Open regression:** full release and no-op rebuild; 22 source-contract, 21 core transaction/timeline, 19 cross-ribbon/Inspect GUI, 4 grid, 1 startup across 17 workbenches, 47 timeline, and 21 tree tests pass, but exiting Sketcher can still hide the tree panel until it is toggled off and on | Global timeline, tree, grid, document tabs, ribbon switching, selection, visibility, save/reopen, and undo pass together |
| Model: Part Design + retained Part + Fasteners | **Verified:** clean build; 13 ownership/inventory, 64 retained-Part lifecycle, 26 retained-dialog, 34 native-ribbon, 52 exact-input, 27 native-task, 47 timeline, 21 tree, and 20 fastener tests pass | Every shipped command is classified and exercised; native solid, surface, datum, dress-up, pattern, transform, and fastener tools use correct selection, task, Body-result, and History semantics |
| Sketcher | **Verified:** clean build; 35 complete-ribbon GUI, 45 internal-profile, 9 exact-factory, 6 source transaction, and 32 surrounding native regression tests pass | Every shipped command and composite leaf is classified; create/edit/close/cancel, geometry, constraints, attachment, visibility, exact-document selection, undo/redo, save/reopen, and Model-ribbon return preserve one coherent sketch and transaction |
| Assemble | **Verified:** clean build; 48 GUI lifecycle and 16 core tests pass, including rendered-handle transform commit, no-op cancel, undo/redo, and edit re-entry | Transform dragger passes clean-profile start/move/finish/cancel acceptance |
| Mesh + MeshPart | **Verified:** clean build; 69 GUI lifecycle and 3 source-contract tests pass with no skips | All shipped convert, modify, boolean, cut, segment, and analysis commands satisfy their declared operation/read-only contract; command icons, exact transactions, suppression, undo/redo, and save/reopen are covered |
| Analyze: FEM | **Verified:** clean build; 74 GUI lifecycle tests and the six-ribbon live gate pass | Analysis, material, constraint, mesh, solver, result, and post-processing commands satisfy their declared lifecycle; absent external solvers produce one actionable error without document mutation |
| Manufacture: CAM | **Verified:** clean build; 121 GUI/source-contract tests pass and 2 dependency-gated tests skip intentionally | Job, tool, operation, dress-up, simulation, inspection, and post-processing commands preserve exact inputs, task ownership, History, undo, and reopen behavior |
| Drawing: TechDraw | **Verified:** clean build; 38 GUI and 22 application lifecycle tests pass in clean profiles | Page, view, projection, section, dimension, annotation, decoration, and file commands preserve source links, embedded-template rendering, and task/History behavior |
| Inspect + Measure | **Verified:** clean build; 6 GUI lifecycle tests pass for exact linked Part/Mesh/Points occurrences, and saved mass properties are verified against live nested and repeated-link occurrences | Linked occurrence placement, rendering, dependency tracking, stale-target clearing, macro replay, edit/suppress, undo/redo, and save/reopen satisfy the declared contracts |
| Materials + Appearance | **Verified:** clean build; 17 GUI and 5 core exact-transaction tests pass in clean profiles | App::Link material edits retain definition ownership while enlisting occurrence and owner documents in one target-stable transaction; Cancel, grouped undo/redo, close, replacement refusal, legacy behavior, and save/reopen are covered |
| Model: Surface | **Verified:** clean build; 10 GUI lifecycle and 1 application test pass in a clean profile | Surface creation, filling, sectioning, projection, curve, and modification commands are composed into Model with correct selection, task, History, undo/redo, and reopen behavior |
| Mesh: Points + Reverse Engineering | **Verified:** clean build; 9 Points and 9 Reverse Engineering GUI lifecycle tests pass in clean profiles | All 18 commands are composed into Mesh with valid icons; point import/export/edit/convert and reconstruction tasks preserve exact source/result ownership, transaction boundaries, History, undo/redo, close, and reopen behavior |
| Parameters: Spreadsheet | **Verified:** clean build; 8 Parameters lifecycle tests plus the native copy regression pass in one clean profile | All 16 commands have valid icons and one clear Parameters surface; sheet creation, cell/range edits, aliases, formatting, import/export, model dependencies, undo/redo, cancel, and reopen preserve exact state |
| Assemble + Manufacture: Robot | **Verified:** clean build; 13 GUI lifecycle tests pass in one clean profile | All 15 commands are placed by purpose with valid icons; placement, trajectory creation/editing, replacement History, simulation preview, KRL export, grouped link undo/redo, task cancellation, and save/reopen preserve exact native state |

## Known blocking defects

Only concrete product defects belong here. Remove an item only after its full
lifecycle is verified.

- Exiting Sketcher can hide the tree panel. Toggling the panel off and on
  restores it, but the edit-teardown visibility transition must be found and
  fixed rather than masked by reload logic.

## Objective `DONE` gate

The effort is complete only when all of the following are true:

- every matrix row says **Verified**;
- every shipped command and composite leaf appears once in the command
  classification inventory and has applicable lifecycle coverage;
- the blocking-defect list is empty;
- touched native targets and one full release configuration build successfully;
- focused suites and the cross-ribbon regression pass with no crash, unexpected
  skip, leaked transaction, abandoned object, or stale saved state;
- repeatable clean-profile on-screen acceptance drives tree, grid, timeline,
  viewport, Assistant, ribbon switching, native selection, Accept/Cancel, and
  reopen;
- the final diff has no unrelated rewrite, accidental API removal, local path,
  generated noise, or compatibility break lacking owner approval.

Status is reported from this matrix and blocker list, not from an estimated
percentage.
