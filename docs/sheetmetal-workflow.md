# SheetMetal workflow

The agreed six-step SheetMetal workflow is implemented and locally validated.
This document records its contracts, scope and verification; it does not describe
a published release.

## Scope

1. Reuse upstream SheetMetal geometry and retain existing commands, documents,
   SendCutSend presets, and VibeCAD ownership/threading/performance fixes.
2. Maintain one editable sheet definition with folded and flat representations.
   Edits in either mode update the same feature history, including cuts crossing
   bends, holes, flanges, bend parameters, material, and thickness.
3. Cache both shapes and render meshes by revision. Switching prepared views
   changes presentation only: no recompute, mesh rebuild, persistent visibility
   changes, or Undo entry. Geometry work for edits runs asynchronously.
4. Prove geometry, revision ownership, Undo/Redo, save/reopen, and presentation
   behavior with red/green tests and isolated native GUI tests. Use strict Release
   builds with 12 jobs; measure actual presentation latency.
5. Add Create, Bend/Form, Cut/Relief, Materials, and Folded/Flat controls to the
   existing ribbon. Provide native AI tools using the same model operations.
   Represent the shared sheet and its folded/flat views clearly in the tree.
   Actual cuts, bends, and material/dimension edits must have understandable
   history and remain editable. Use the existing tree/timeline conventions;
   presentation-only view switches must not add Undo or recompute work.
   Retain exact source sketch/solid links. Tree and History editors must target
   the same feature after Undo/Redo, rollback, and save/reopen.
6. Integrate RMFG sign-in, validated folded STEP export, manufacturing findings,
   current materials/quantities, quotes, and website checkout. Quotes belong to
   an exact model revision and become stale after edits.

Unrelated PRs, refactors, dependency upgrades, releases, replacement ribbon
frameworks, and autonomous payments are outside this goal. No interaction with
the user's running GUI, builds, or tails. No process kill timers.

## Current completion audit

The six-step implementation is complete on the local development branch. The
progress notes below are chronological; earlier pending-work statements do not
replace this audit.

| Goal | Implementation and verification |
| --- | --- |
| 1. Upstream and compatibility | Upstream builders/unfolders and existing commands remain. The 346-test native suite covers original Unfold ownership, SendCutSend preset application and transactions, and source persistence. |
| 2. Shared folded/flat editing | One editable definition retains source sketches and cut/bend history. Tests cover cross-bend cuts, reverse edits, flanges, stock/material/thickness changes, suppression, Undo/Redo and reopen. All six base shapes and a cabinet-sized Box retain saved inputs through preparation and editing. |
| 3. Presentation and concurrency | Prepared switches reuse cached nodes and meshes with no recompute, persistent visibility change or Undo entry. Worker and stale-generation tests cover geometry, presentation and export ownership. The 200-toggle sample averaged 0.057 ms per cached call, maximum 0.274 ms; GPU frame time is excluded. |
| 4. Validation | The ownership-audited suite passed 346 tests in 959.433 s with normal unattended exit. The C++ gate passed 43 tests. Subsequent return-only changes passed 63 related unit tests; the exact-field increment also passed 17 isolated native tests. The final normal Release build completed with warnings as errors and 12 jobs; installed application Python modules match source. |
| 5. Ribbon, Tree/History and native tools | The existing ribbon has Create, Bend/Form, Cut/Relief, Materials and Folded/Flat groups. Native tools use the same model operations. Tree/History tests retain exact editors and source links across rollback and reopen. Terra completed the internal-tab geometry/History oracle and the cabinet Assembly → Sheet Metal → Modeling handoff. Qwen exposed dispatcher and repair-message defects that received regression fixes; its imperfect geometry strategy is not a completion gate. |
| 6. Direct RMFG | Ribbon and native tools share browser sign-in, catalogs, revision-bound configuration/findings/quotes, validated folded STEP export and website checkout. The authenticated live native run reached a ready quote and opened checkout while retaining the model revision, Undo state and flat view. Stale-revision checks block outdated export and checkout. No order or payment was performed. |

Qwen is a diagnostic gauge, not an expectation of perfect modeling. The two
last private probes were deliberately stopped with traces preserved and marked
incomplete after the owner clarified that distinction. Their geometry outcomes
are not claimed as passes. Fixes improve shared schemas, dispatch and truthful
returns; no Qwen-specific geometry, retries or prescribed modeling strategy were
introduced.

This audit does not authorize publication, merging, a release, payment or an
order. Existing two-action source/shared creation and first-save behavior remain
available. Historical documents already mutated by older Boolean cuts are not
automatically rewritten. Full cabinet assembly, drawer motion and load capacity
are not claimed by the SheetMetal tests.

## Model and performance contracts

The feature definition is authoritative. Folded and flat solids are derived from
it; they are not independently edited solids reconciled after the fact. Retain
region and bend identities, thickness, allowance policy, stationary face, seam
choice, and the mapping between representations.

Upstream V2 already computes a tangent-face graph and per-bend transforms. An
additive API can retain that correspondence without changing the legacy Unfold
return contract. Planar regions use rigid maps. Bend regions need cylindrical
maps and allowance-aware geometry. Cuts spanning bends must be split across
regions and reconstructed correctly, rather than moved with one rigid transform.

Snapshot geometry inputs before asynchronous work. Publish folded/flat results
for one revision together, discard superseded results, and verify exact document
and object identity before publication. Camera changes and view switches do not
invalidate geometry. Feature/material changes do. Keep the last valid display
while preparing changes, but do not export or quote it as the new revision.

Existing SheetMetal documents retain their existing path. Recognizing an imported
solid as an editable sheet must be explicit. Missing or ambiguous topology
references must not silently attach to another face.

## Native AI tools

Native tools must inspect sheet regions, bends, material, revision, and available
operations; create/edit the shared features in either representation; validate
both results; and export/quote the intended revision. They must not implement a
second geometry path or rely on generated Python to approximate a ribbon action.

Integrate with VibeCAD's native capability discovery, typed schemas, selection
resolution, transaction/Undo handling, and asynchronous completion reporting.
Keep ordinary public tools and existing ribbon surfaces compatible. Use expected
revision and exact owner checks to reject stale edits. Switching presentation
through a human or native tool must obey the same no-recompute contract.

Native tools and the ribbon share the same direct RMFG REST client and
export/configuration/revision handling. No MCP connection is required for any
of the agreed CAD or manufacturing operations.

Sharpen these AI tools using the established
[tool-sharpening method](cam-tool-sharpening.md#tool-sharpening-method) and
[native benchmark rules](native-tool-sharpening-benchmark.md#fixed-run-rules).
Use ordinary requests on fresh fixtures, without tool names, payload coaching,
or prescribed call sequences. Local Qwen probes discovery, argument clarity,
inspection, execution, truthful results, and repair. Classify failures before
changing production code; distinguish tool defects from model taste/ability.
Run matching independent Terra cases, retain complete tool traces and resulting
documents, and verify geometry with deterministic checks. Do not add hidden
retries, forced calls, benchmark detectors, or case-specific prompt steering.
Passing direct API tests alone does not establish AI tool usability.

## RMFG integration contract

Official references checked on 2026-09-15 (API version `2026-09-01`):

- [API workflow](https://www.rmfg.com/docs/api)
- [Hosted MCP](https://www.rmfg.com/docs/api/mcp)
- [Agent guide](https://www.rmfg.com/docs/api/agent-guide.txt)
- [Live API schema](https://api.rmfg.com/v1/openapi.json)

Use the REST API at `https://api.rmfg.com` with browser OAuth device authorization
or PKCE, credential storage, and serialized refresh-token rotation. All agreed
operations must work with no MCP servers configured. The existence of RMFG's
hosted MCP does not make it a dependency of this integration. Credentials must
stay out of documents, prompts, logs, and shared preferences.

Submit the folded STEP with bends modeled even when the user is viewing the flat
pattern. Preserve flat DXF/SVG export for cutting and inspection. Bind remote
design/quote IDs to the exported bytes, model revision, and configuration. Resume
existing remote jobs and reuse operation keys only for identical requests.

Read current catalog IDs and manufacturing findings. RMFG's general material
schema has thickness and bendability; prepared DFM tooling can separately provide
K-factor and bend radius. Do not assume these are all catalog fields or silently
overwrite the user's geometry with manufacturing corrections. Present decisions
and substitutions for review. Checkout goes to RMFG's website; no purchase tool
is part of this goal.

`SheetMetalRMFGClient` now provides the shared direct REST transport for material
pages, folded STEP upload, analysis/DFM/quote status, manufacturing evaluation,
quotes, and website carts. Identical retries retain their operation key and
request bytes. Pending IDs and rate-limit metadata remain available to the
workflow; transport errors never trigger an automatic second write. The HTTP
transport refuses redirects and reports errors without echoing response bodies
or credentials. Eight isolated client tests and four installation/preset checks
pass. This is transport plumbing: browser sign-in, credential rotation, durable
job storage, revision binding, ribbon/native bindings, and authenticated live
validation remain to be implemented. No remote uploads or carts were created
during these tests.

`SheetMetalRMFGSnapshot` binds detached export bytes to the exact document,
document session, sheet object, structural revision, and prepared input hash.
Quote requests also retain an immutable manufacturing configuration, design ID,
design-unit quantity, and retry key. Their fingerprint includes the STEP digest;
changing any bound input produces a different request. Revision checks reject
edits and reopen/Undo ABA cases even when the geometry hash matches. Fourteen
combined snapshot/client tests pass. These data records do not themselves prove
that native folded geometry was validated or exported; that GUI/worker bridge
and quote-result publication checks remain pending.

## Validation sequence

Start with a bracket and a cut spanning its bend. Test cuts authored in flat
coordinates and resulting folded geometry, reverse edits, allowance changes,
multiple bends, and repeated round trips. Then verify Undo/Redo and save/reopen.
Test view switching with recompute/geometry/mesh execution counters as well as
document state and rendered latency. Test stale workers, document switches,
deletion and closure in isolated processes.

Native tools must produce the same model result and Undo boundaries as the GUI.
Test stale-revision rejection and asynchronous completion. RMFG contract tests
must cover revision changes, pending jobs, retries, and review configuration.
Exercise the complete native AI and ribbon flow with no MCP server configured.
An authenticated live test requires browser login; mocked results do not count
as proof of the live service.

## Progress

- Step 1: imported SheetMetal 0.8.23 with attribution and existing ownership fixes;
  added module installation and its NetworkX requirement. Standalone installation
  regression failed before integration and passes after it. The complete strict
  Release build passed with 12 jobs, warnings treated as errors, and sanitizers
  disabled. All 15 isolated native GUI tests passed against the built module:
  eight SendCutSend tests and seven Unfold ownership tests, including both
  unfolders, background presentation, Undo, and save/reopen. Python checks passed:
  143 tools tests (five skipped, 46 subtests) and 38 preset tests.
- Step 2 foundation: added bidirectional planar/cylindrical point maps retaining
  the upstream tangent graph's transforms and material allowance. Seven new
  native geometry tests pass, including multiple bends, both skins, arbitrary
  placement, seam continuity, thickness/K-factor changes, trimmed boundaries,
  and legacy output compatibility. The complete strict build and combined
  native suite pass. Added paired folded/flat through-cut geometry, including
  circles crossing both bend seams, curved notches, reverse picks, repeated
  cuts, and removal-aware selection checks. All 27 native tests pass against
  the strict build; the installation test passes with 15 subtests. Cut geometry
  is implemented independently of documents and presentation.
- Step 2 document layer: `SheetMetalEditable` now links the upstream source
  history and persists shared circular-cut operations with stable operation IDs.
  Both solids update from that definition, including reverse edits and upstream
  thickness, radius, flange length, bend angle, and allowance/material changes.
  Undo/Redo, save/reopen, stale-input rejection, cross-document isolation, and
  failed-cut repair/removal pass native tests. Geometry preparation is verified
  to execute off the GUI thread through native asynchronous recompute. The full
  strict build passes; installation passes with 17 subtests. Input fingerprints
  now use native document precision and normalize transient OCCT serialization
  differences. Rebuilding after text and binary save/reopen preserves the hash;
  moving the source changes it even with unchanged volume. All 43 combined
  native tests pass after fixing cleanup to wait for each private document.
  The placement increment adds the feature to its source's native Part/Body
  container. Nested rotations/translations, folded-side edits, save/reopen, and
  native relocation of source plus dependent sheet are covered. Whole-container
  movement retains the prepared geometry and its fingerprint. The expanded
  19-test document suite passes.
- Step 2 profile history: native Sketcher profiles now feed the same folded/flat
  definition, including constrained arc/line slots and periodic B-spline cuts
  across bends. Native attachment follows the source, and folded/flat picks map
  into the same sketch coordinates. Stable native links retain the association
  across save/reopen and recursive copy; deleted profiles fail explicitly and
  their operations remain removable. Native tests cover constraint edits, reverse
  handle edits, Undo/Redo, transformed Bodies, failed-profile repair, and existing
  circle-only schemas. All 61 combined native tests pass on the strict Release
  build; installation passes with 18 subtests. Arbitrary Link instances and broader reference lifecycle
  still need coverage as the command/selection layer is connected.
- Step 3 presentation foundation: both render meshes are prepared off the GUI
  thread and published together into GUI-owned scene nodes. The opt-in native
  provider avoids falling back to ordinary Part meshing. Warmed toggles retain
  the same nodes and leave geometry, visibility, and Undo untouched. Tests check
  actual rendered images/triangle counts, object placement, stale/superseded
  results, deletion during preparation, and save/reopen. Saved outputs display
  before mapping preparation; mapped edits still require current geometry.
  The complete strict Release build passes with 12 jobs. The command/export
  revision coordinator remains to be connected.
  The combined 71-test suite passed; the final appearance update also passed all
  11 presentation tests, covering 72 distinct native tests across both runs.
  Installation passed. Measured cached switch calls averaged 0.066 ms over 200
  toggles, with a 0.108 ms maximum; GPU frame time is not included.
- Step 3 mapped selection: clicks on both folded and flat sheet skins now resolve
  to the same definition, including both sides of bends. Rendered hits project
  onto exact analytic faces before mapping. A pick retains the exact feature and
  prepared generation; stale, foreign, occluded, and cut-wall picks are rejected.
  Twelve focused native tests pass, including real viewport-to-cut execution,
  folded-side cut/Undo, and parent Part movement without mesh regeneration.
  The complete strict Release build and all 84 combined native tests pass;
  installation also passes. Shared command revision handling and the ribbon/native
  AI bindings remain to be connected.
- Steps 3–5 command foundation: `SheetMetalOperations` provides shared inspection,
  circle/profile edits, material/K-factor changes, upstream parameter edits, and
  repair/removal. Commands validate the exact open document and its monotonic
  native revision before a short transaction; geometry completes asynchronously.
  Completion waits for the native change batch to settle and reports failure or
  superseding edits explicitly. View toggles leave the revision and Undo unchanged.
  Fifteen focused native tests pass, including ordinary Undo, save/reopen epochs,
  change-and-restore rejection, GUI responsiveness, and legacy circle-only files.
  The full strict Release build passes with 12 jobs; installation and all 99
  combined native tests pass.
  The native assistant adapter still needs receipt/Undo finalization after async
  recompute: the earlier transaction's Undo-availability flag is deliberately
  not reported as current. Existing native Undo guards remain intact.
- Step 5 ribbon foundation: the existing native ribbon now has a Sheet Metal
  page with Create, Bend/Form, Cut/Relief, Materials, and Folded/Flat groups.
  New shared-sheet creation uses the exact selected source face and asynchronous
  geometry. Panels edit supported upstream dimensions, material/allowance, and
  circular or sketch-profile cuts through the shared operations. Hole placement
  uses actual viewport picks in either representation. Panels reject stale edits
  and document switches; closing one retains its committed asynchronous edit.
  Folded/flat controls also work while a panel stays open. The full strict Release
  build and all 112 combined native tests pass; 14 Python ribbon/install checks
  pass. Native panel tests cover actual task creation, mouse callbacks, and Close.
  Initial red runs caught the absent GUI, missing ribbon surface, Qt button-flag
  conversion, and a collision with the native `closed()` callback.
  Existing upstream source-creation commands retain their original behavior.
  Additional bend/form authoring and sketch-authoring integration, operation
  History, and native AI bindings remain pending.
- Step 5 tree presentation: new shared sheets expose Folded, Flat, Dimensions,
  Material, and individual cut rows beneath their single document object.
  Double-clicking a view switches its cached presentation; double-clicking a cut
  opens its exact stable operation in the shared editor. The rows are not copied
  solids or additional document objects. Only relevant definition fields refresh
  them; view switching does not rebuild the tree or add Undo entries. Existing
  cached providers and read-only detail providers retain their contracts.
  Seven focused tests pass, including actual tree mouse events, stale operation
  rejection, unrelated selection, transaction/document isolation, row refresh,
  and save/reopen. The full strict Release build passes with 12 jobs, as do all
  119 combined native tests and four installation/tree-schema checks. These
  rows do not yet make the shared JSON cuts separate persisted History steps.
- Step 5 History foundation: shared-sheet creation now publishes its exact
  native operation and source replacement. Its timeline entry opens the sheet
  dimensions editor, including after a file is opened in another workbench.
  Moving before creation restores the standalone source or the Body's previous
  Tip; inactive sources/sheets are rejected by the shared command coordinator.
  Root publication and Body enrollment use their respective native contracts.
  Undo/Redo reconnects the same native display mode without duplicating scene
  nodes. Deletion cancels mesh work and releases Python-held model, scene, and
  cache references; restored borrowed nodes retain their owning native switch
  until GUI-thread cleanup. This required correcting a lifecycle crash found
  in the isolated tests. Eight focused deletion/History tests pass, as does a
  separate fresh-start editor-restoration test. All 126 combined native tests
  pass. The full strict build passes with 12 jobs and all 17
  installation/tree-schema/ribbon checks pass.
  Per-cut persisted operations, their suppression/rollback geometry, and
  additional bend/form authoring remain pending. The existing shared JSON
  definition and low-level creation defaults remain compatible.
- Step 5 circular-cut History: an additive native cut feature now owns each
  hole's radius, developed center, and exact predecessor link. Its folded and
  flat results share the predecessor's prepared mapping; adding or resizing a
  hole does not repeat Unfold. Editing an earlier hole updates its descendants
  asynchronously. Native History rollback restores the preceding sheet, and
  suppression bypasses the selected hole while retaining subsequent cuts.
  Tree rows expose both cached representations and the exact hole editor;
  the native History entry opens that same editor. Revision checks reject stale
  edits, and invalid cuts remain repairable. Legacy JSON operations and their
  mutators retain their existing behavior. This is the operation foundation:
  the ribbon's cut-creation panel still uses its existing JSON path, and sketch
  cuts still need separate native operation entries.
  The full strict Release build passes with 12 jobs. All 141 combined native
  tests pass; after two additional Tree/deletion checks and the final panel fix,
  all 17 focused cut-state tests pass. Together with the standalone startup
  check, these runs cover 144 distinct native tests. All 17 Python installation,
  Tree-schema, and ribbon checks pass.
- Step 6 service integration: not implemented yet. Native AI tools must share the
  same operations as the ribbon. Neither local editing nor direct RMFG integration
  requires MCP. AI usability still requires the ordinary-prompt sharpening runs.

Native sketch cuts now extend the same operation chain as holes. Each cut links
its original constrained sketch, derives both shapes from the preceding state,
and retains the prepared bend mapping. Tree and History open that exact sketch
in the native editor. Suppression bypasses a cut, and replacing a missing sketch
keeps the cut identity while rebasing its full downstream History block after the
new dependency. Edit completion checks native sketch inputs as well as links, so
a later constraint change cannot masquerade as the original pending hole edit.
These input snapshots are taken at edit boundaries, never on cached view reads.
The operation foundation is now connected to the ribbon as described below.
The strict Release build passes with 12 jobs. All 154 combined native tests and
the standalone startup case pass, covering 155 distinct native tests; all 17
Python installation, Tree-schema, and ribbon checks pass.

The ribbon now creates holes and sketch cuts as native History states through
`SheetMetalHistoryOperations`. Its panel follows the resulting sheet and preserves
the current folded/flat mode. Users can resize an earlier hole, suppress/restore
cuts, edit the linked native sketch, or replace its link. Material and dimension
controls work from the selected chain tip and prepare the whole result chain.
Existing JSON cuts remain editable alongside native cuts; direct legacy panel
construction and low-level APIs keep their prior defaults. A current suppressed
cut can stay in its panel for restoration, while future History states remain
blocked. Cached view switches do not inspect the complete chain or serialize
sketch inputs. Native assistant receipt finalization and bindings remain pending;
the shared typed command service is not itself proof of usable AI tools.
The strict build passes with 12 jobs. All 167 combined native tests passed;
after additional exact-target and missing-sketch recovery fixes, all 43 focused
ribbon/panel/command checks passed. Together with the standalone startup check,
these runs cover 170 distinct native tests. All 17 Python integration checks pass.

Shared inspection now describes each native sheet state by document UID and
internal object name, with its label, predecessor, History index, suppression,
editor command, and original sketch link. The currently displayed predecessor
is identified separately when the selected cut is suppressed. Missing sketch
links remain visible with repair information. These are read-only descriptions
of the existing Tree/History model; they do not create another model or enroll
new timeline entries. Native inspection registration is described below.
Red/green inspection checks and the final 47 native ribbon/panel/command tests
pass, including duplicate labels, suppression, missing links, and save/reopen.
The full strict Release build passes with 12 jobs, as do all 17 Python integration
checks. Existing tests continue to cover actual Tree/History interactions.

The native `sheet_metal.inspect` tool is registered on the existing Modeling
surface. It lists shared sheet states with their visibility and reads material, dimensions, revision,
readiness and representation through the ribbon's shared inspection service.
History, cuts and mapping regions have bounded pages; subsequent pages require
the first page's structural revision, and document changes reject stale pages.
Targets use exact document UIDs/internal names. Suppressed states identify their
display predecessors, while invalid geometry remains inspectable for repair and
cannot publish current mapping regions. Imports are deferred so native registry
construction does not load SheetMetal geometry or a workbench.

Six focused native tests pass, including live Modeling-surface discovery, actual
runtime binding construction, no-change reads, stale pages, document isolation,
suppression and failed geometry. The combined native suite passed 53 tests; the
six inspection checks passed again after the final visibility addition. The full
strict Release build passed with 12 jobs, along with 51 Python registry/schema/
integration checks and a separate VibeCAD packaging check.
Native assistant edits,
full Sheet Metal surface action mapping, and ordinary-prompt sharpening remain
pending. This read capability is an implementation increment, not completion of
the native editing workflow or evidence from a model-driven usability run.

The next increment connects the native AI edit controls and
assistant receipt finalization after async geometry. Test representation selection
without document mutation, meaningful operation editing, History position changes,
Undo/Redo, and save/reopen. Keep edits
and exports tied to the exact owning document and current prepared definition;
do not substitute presentation readiness for geometry/revision validation.

Async ownership work adds an opt-in native `recomputeAsyncTracked()` request.
It returns an opaque origin ID and request count; it is not a completion or Undo
receipt. The ID follows the request through the document worker and its nested
geometry workers. A temporary observer can subscribe to
`slotChangedObjectWithOrigin(object, property_name, origin)` to receive explicit
property-event provenance. The worker scope is never installed on the GUI
thread, so later GUI edits, including edits made inside an observer callback,
remain untagged. Ordinary recompute APIs and observers keep their existing
contracts. This infrastructure still needs to be connected to assistant receipt
finalization; it does not itself authorize Undo or prove successful geometry.
Task submission also captures the origin, including an empty origin for
untracked work. Each task restores its caller's scope after execution or failure;
cancelled work runs without an origin. This matters when a waiting compute
worker helps another queued task. A focused C++ probe reproduced an independent
task inheriting the waiting task's origin, then passed after origin handling was
moved to the task boundary. The full strict Release build passes with 12 jobs.
All 43 C++ origin, async recompute, cancellation, cleanup and executor checks pass,
as do all three native GUI provenance checks. The old headless visual-lifetime
test was corrected to assert headless recompute behavior; production lease
behavior was preserved. Assistant receipt finalization remains pending.

Reproducible build/test commands and isolation requirements are in
[the native test instructions](../src/Mod/SheetMetal/SMTests/README.md).

The native `sheet_metal.view` tool provides one explicit `set_representation`
operation (`folded` or `flat`). It takes `object_name` in the locked native
document and resolves the exact sheet,
checks its current prepared state, and calls the same cached switch used by the
ribbon. The result identifies both the requested History state and its actual
display predecessor, including visibility. Switching preserves visibility,
geometry, structural revisions, and Undo history. Registry and isolated runtime
checks cover targeting, unavailable states, and presentation semantics. The
combined native run passed 69 of 71 tests; both failures were the same schema
budget error in discovery. The compact view contract and concise inspection
descriptions fit the existing 64-KiB Modeling limit without changing that limit
or existing accepted inspection arguments. All ten focused native view/inspection
tests then passed, including both discovery checks. The strict build passes.
Ordinary-prompt tool sharpening and native assistant edits remain pending.

The shared History edit service now accepts an optional native transaction
runner and tracked recompute queue. Ribbon callers retain their existing path.
`NativeMutationRunner.start_deferred` commits verified parameters as one short
transaction, closes mutation observation, and returns immutable prepared evidence
without issuing a completion receipt. It refuses synchronous recompute and
after-recompute work inside that parameter transaction. Existing immediate
mutation signatures, results, and behavior remain compatible.

This lets an AI edit create the same native sheet feature, editable History
entry, and folded/flat Tree controls as a ribbon edit. A queue failure retains
the feature for repair and normal document Undo. The adapter must still verify
current geometry, document/turn ownership, and the absence of unrelated edits
before completing the evidence and recording assistant Undo. These hooks alone
do not provide that proof or publish an AI editing capability. All 21 native
ribbon/History regressions passed, including the two new integration checks;
the strict Release build used 12 jobs. Mutation/state/Undo regression commands
and red/green evidence are recorded in the native test instructions.

`SheetMetalNativeEdit.start` now connects those hooks to native completion.
It queues one tracked recompute after committing parameters, observes explicit
worker-origin property events, and waits for the shared geometry service to
settle. A receipt is issued only when current inputs/geometry, document/turn
ownership, the coalesced revision, and the exact Undo stack agree. Independent
edits (including change-and-change-back), untracked changes, and moved History
cannot be absorbed into the receipt. Presentation-only changes and another
document's edits do not count as changes to this sheet. The temporary observer
is detached before publishing the Future result.

Successful edits record assistant Undo at the final geometry revision. Failures
retain the committed parameters and identify the editable feature for repair;
they do not issue a successful completion receipt. Client cancellation leaves
native geometry work alone and suppresses its receipt while pending. Receipt
publication takes atomic ownership of the Future, preventing cancellation from
racing a published result. Completed-ticket replay does not recompute or create
another Undo entry.

The strict Release build and install checks pass. The combined native edit,
origin and ribbon suite passed all 36 tests. A further red/green test reproduced
and fixed the publication cancellation race; all 13 focused native edit tests
then passed. This is the native domain adapter. Provider edit schemas, runtime
bindings, full Sheet Metal surface mapping, and ordinary-prompt sharpening are
still required before declaring the AI editing workflow complete.

The native `sheet_metal.edit` contract now binds eight explicit operations to
that adapter: add/update circle, add/replace sketch profile, suppress/restore,
remove a legacy definition cut, set material/K factor, and set upstream
dimensions. It uses exact internal object names in the locked document and
requires the edit capability's own call ticket. Invalid preflight requests
report that parameters were not changed; failed asynchronous completion reports
the retained feature and committed parameters. The dispatcher preserves those
optional repair details without changing existing failure fields.

The tool uses the production native provider's asynchronous dispatch path.
Its synchronous entry point rejects before mutation instead of waiting on the
GUI. Schemas and registry construction do not import SheetMetal geometry. The
edit tool is scoped to Sheet Metal; existing Modeling inspection/view discovery
and its schema budget remain intact.

The native inventory now includes the actual Sheet Metal ribbon, including its
shared View/Inspect actions. Folded/flat actions stay presentation-only. Create
actions are classified as asynchronous mutations and retain an explicit
incomplete `sheet_metal.create` capability until all implementations are ready;
they are not hidden as human-only actions to make discovery appear complete.
Native tests verify sequential tool edits and final receipts within one turn,
safe replay, external-edit rejection, argument/preflight repair, and the live
surface's exact remaining gap.

`sheet_metal.create` now implements `from_source`: an exact source object and
planar reference face produce one shared editable sheet with both prepared
representations. It uses the same creation/History service as the ribbon and
the same deferred native receipt proof as sheet edits. The source and its
editable parameters remain linked; one Undo entry removes the new sheet and
restores source visibility. Stale references and concurrent source edits cannot
produce a successful owned receipt. The old human creation signature/defaults
remain compatible, with an optional tracked queue added for native callers.

The initial four native creation tests and existing History/edit regression
suite passed all 24 checks. After binding the tool, all 23 native
creation/dispatch/edit checks passed, as did 37 source contract/runtime checks,
the strict Release build, and five package/install checks. The three upstream
source builders (`base_shape`, `base_from_sketch`, `from_solid`) and
ordinary-prompt sharpening remain pending before complete Sheet Metal AI
surface availability. Upstream GUI helpers open their own transactions and
recompute synchronously; the native implementations must reuse their feature
builders through owned asynchronous transactions, not invoke those GUI helpers.

The opt-in `SheetMetalSourceFeatures` proxies now reuse those three upstream
builders and validate their resulting solid on the recompute worker. They retain
the original editable parameters and exact sketch/solid links. Validity evidence
is transient; failed recomputes and missing inputs cannot reuse an earlier
success. Reopening preserves parameters and requires fresh geometry validation.
These proxies are preparation for the remaining native creation bindings, not
completed tool integration or proof that every valid source can unfold.

The new tests first failed with five missing-module errors. The strict Release
build with 12 jobs then passed, followed by 30 isolated native source, Tree,
History, and profile-History tests and four installation/preset checks. This
includes real Tree/History editor activation, rollback, Undo/Redo, save/reopen,
and folded/flat switching without model or History changes.

`SheetMetalSourceOperations` now creates base shapes, sketch-based sheets, and
solid conversions through short owned transactions and native asynchronous
recompute. Parameters are explicit; constructors cannot substitute saved GUI
preferences. The result retains its exact source and Part/Body container, with
one native History operation and an asynchronous parameter editor shared by Tree
and History. Native completion uses the existing origin/revision/Undo proof.
Failed geometry keeps editable parameters without issuing a success receipt.
Parameter edits also check active dependent folded/flat states; superseded
editors cannot overwrite newer dimensions.

Eleven source-creation/editor tests, five worker-source tests, and eighteen
existing native creation/edit tests pass against the strict Release build
(34 total). They include real History double-click and rollback, save/reopen,
Body editing, exact replacement links, failed geometry, and concurrent edits.
Four installation/preset checks pass. Red tests exposed missing entry points,
incorrect source publication ordering, a Body insertion rule applied to edits,
and stale-editor/downstream-result handling. The three new creation paths still
need native schema bindings and ribbon creation forms; existing upstream
commands and defaults remain unchanged.

All four `sheet_metal.create` variants are now registered: `base_shape`,
`base_from_sketch`, `from_solid`, and the compatible `from_source` path. The
three source variants use the owned asynchronous creation service with bounded
typed arguments, explicit source/container identity, and no MCP dependency.
Descriptions distinguish valid upstream sources from prepared folded/flat
state. The live Sheet Metal capability inventory resolves completely without
increasing schema budgets. All 100 contract/registry checks and 22 native
creation/dispatch tests pass against the strict Release build. Ordinary-prompt
sharpening is still required; direct calls do not establish tool usability.
Ribbon creation forms must still be connected to this same shared service.

The ribbon's Base Shape, From Sketch, and From Solid actions now open additive
creation forms backed by that shared asynchronous service. Original upstream
commands remain registered and available through their existing paths. The
forms show explicit defaults, retain the selected source/subelements, allow an
explicit Part/Body container for new bases, and reject stale or foreign-document
requests. Opening a form creates no feature or transaction; submitting creates
one owned History operation. Closing the form leaves committed geometry work
running. After creation, the user selects a stationary planar face and chooses
Editable Sheet to prepare its shared folded/flat state.

The strict Release build passed with 12 jobs, along with 74 contract checks,
24 native form/source/dispatch checks, and five package checks. A final focused
run passed 20 form/source checks after matching upstream option visibility for
Flat, L-Shape, Hat and other base types. The creation form was visually inspected.
Ordinary-prompt sharpening and the remaining RMFG integration are still pending.

The current Tree/History regression run passes all 54 checks for representation
rows, exact-feature editors, source links, circular/profile cut steps, rollback,
Undo and save/reopen. Folded/flat rows reuse the prepared view and do not create
model objects or History operations.

The first ordinary-prompt probe (supplemental case `SM-P01`) exposed a missing
SheetMetal active-context builder before the provider could run. The new bounded
context references existing source, shared-sheet, sketch and container objects,
prioritizes the current selection, and reports exact editors, predecessors and
History position. It does not prepare meshes or perform full geometry inspection.
Red tests reproduce the missing builder and distinguish Body containers from
shape inputs. The strict Release build passes with 12 jobs; seven native
inspection tests verify the real document context and unchanged model state.
The explicit `SMTests.live_sheet_prompt` probe uses an ordinary bracket request,
the production native provider path with MCP disabled, a private profile, and no
process deadline. It retains traces, documents, STEP files and a geometry oracle;
passing direct inspection tests is not a claim that this prompt succeeds.

Direct RMFG authorization now has a device-approval protocol and an OS credential
store, requesting only `designs dfm quotes carts`. The caller performs network
work off the GUI thread and schedules polling at RMFG's interval. Pending,
slow-down, denial, expiry, cancellation and replaced login attempts are explicit.
Refresh consumes the saved token before sending a request; an ambiguous response
or failed replacement save requires browser reconnection. A Qt file lock spans
processes, while credential records live in Secret Service, macOS Keychain or
Windows Credential Manager without a plaintext fallback.

Nine authorization tests and five credential-store tests pass, including a real
second-process lock probe with a fake keyring. Together with the existing client
and export/quote snapshot tests, 28 tests pass; four installation/preset checks
pass. No test contacts RMFG or reads the user's credentials. The browser panel
and native connection access below build on this protocol; authenticated service
verification remains pending. The complete
strict Release build passed with 12 jobs after the private prompt run exited;
all 28 RMFG tests also passed against the installed build modules.

The `SM-P01` run produced a valid 70 x 50 x 25 mm bracket source, but no shared
folded/flat state, and failed its geometry oracle. Its trace exposed a provider
projection mismatch: sheet inspection requires exact document/object targets,
while provider-visible responses discarded the document ID. Sheet tool results
and SheetMetal active context now preserve those required IDs; other domains
retain their existing compact output. Public tool contracts and document-owner
checks remain unchanged. Source creation also returns up to 32 exact planar-face
names and areas from worker-prepared evidence, so attaching the shared state can
use observed faces. Reading this metadata performs no GUI geometry work.

Five new provider checks, 93 broader provider regressions, 30 SheetMetal contract
and package checks, and 26 native source/creation/inspection checks pass. Red
tests reproduced missing target identity and missing face metadata. This fixes
the observed data gaps; a fresh unchanged ordinary-prompt run is still required
to establish whether the assistant completes the shared folded/flat workflow.

The SheetMetal ribbon now includes RMFG connection access. The same modeless
panel is available through `sheet_metal.connection`; its status operation reads
credentials on a worker and returns only public connection state. Sign-in shows
the browser approval code and opens the approval page after the user chooses
Connect. Closing or pressing Escape cancels pending approval without closing the
application or changing the document. Network calls and credential-store access
run off the GUI thread. Approval codes/links stay in the panel, outside AI context.

The strict Release build passed with 12 jobs, 65 registry/contract/package checks
passed, and 35 RMFG protocol, cross-process locking and Qt interaction tests passed
against the built modules. Native tests exercise the actual ribbon, registered
tool binding, document guards and cancellation. They caught a Qt `disconnect`
name collision and synchronous polling that never returned its completed state;
both were fixed and all three focused native connection tests pass. The other
13 native dispatch/source-form regressions also pass. The panel was visually
inspected. Export, durable manufacturing jobs, catalog/configuration/quote UI and
authenticated service verification remain pending.

The next unchanged `SM-P01` run reached shared-sheet creation and view switching,
but failed its final geometry check: it left three sheets instead of one. All
three used `Face1`; their saved flat bounds match their folded bounds. The final
sheet's reported flat normal is along X, while its flat X extent is 50 mm and
the source thickness is 1.6 mm. Reference-face eligibility and inferred thickness
must be investigated before this can count as a usable folded/flat result. The
failed documents, STEP files, traces and images are retained; no prompt rewrite
or forced tool calls were used.

A small native bracket regression confirmed the cause: selecting its extrusion
end face inferred 70 mm stock thickness from a 1.6 mm source. The shared feature
now checks the inferred thickness against the supported source builder's nominal
gauge before publishing prepared geometry. A wrong reference stays explicit and
can be repaired by selecting a sheet skin; it is never silently retargeted.
The document-independent geometry API accepts an optional expected thickness,
while existing callers and the upstream unfolder retain their original behavior.

Validated sources also prepare a bounded list of reference-face candidates on the
geometry worker. Native creation returns these names alongside the existing
planar-face list. Cached evidence rejects known thickness walls before adding
Tree or History entries. A face outside the scan, or a restored source without
that cache, still receives full worker validation. This adds no geometry work to
folded/flat view switches or source-inspection calls.

The initial four reference-face checks failed, as did a separate pre-transaction
Tree/History check. After the fix, all five checks and the existing source,
creation and shared-document suites pass: 42 native tests. The complete strict
Release build passed with 12 jobs, and 25 native contract/provider/installation
checks pass. Ordinary-prompt usability is still unproven; the earlier failed
AI run remains a failure.

Another 62 native regressions pass for mapping, through-cuts, cached presentation,
operations, Tree, History, native creation and dispatch. The small presentation
fixture records 200 cached switch calls averaging 0.045 ms (maximum 0.199 ms),
excluding GPU frame time. These checks preserve the fast view-switch path; they
do not establish large-model frame rates.

The subsequent unchanged Qwen `SM-P01` run also failed: it left a valid source
but no shared folded/flat state. Its trace includes repeated invalid calls and
a request targeting a source that had already been undone. Suitable reference
faces were present in the returned metadata. This does not establish usability;
the source-only result and the model's unsupported claims about both views are
retained as failure evidence. The first independent Terra attempt stopped before
CAD work because its isolated profile lacked subscription authentication; it
does not count as a model or geometry result.

The internal RMFG export path captures a prepared folded solid and its exact
document revision, regardless of the displayed representation. Its worker writes
a detached BREP and starts a private command-line STEP writer, isolating STEP's
process-wide settings from the interactive application. The child checks solid
validity and round-trip volume/bounds before returning STEP bytes. Completion
rejects a changed sheet revision or a cancelled consumer. No upload or quote is
started by this export API; panel/native manufacturing bindings remain pending.
The additive `Shape.exportBrepDetached()` method copies topology and geometry
under Python's GIL, then releases it during serialization. It preserves existing
export methods. Snapshot copying still has a cost; this is not a claim that every
part of capturing an arbitrarily large model is instantaneous.

Six initial export tests failed before implementation. The first installed run
passed 30 of 31 checks: the STEP itself passed validation, but `SystemExit` in
the new child bypassed FreeCADCmd's native teardown and a background material
loader crashed during shutdown. Returning normally through console EOF fixes
that worker lifecycle; neither failed process exits nor failed geometry checks
are accepted. The complete strict Release build passes with 12 jobs, and all
31 export, immutable snapshot, presentation and native-creation checks pass.
This includes a real STEP round trip from a sheet displayed flat, unchanged
Undo/revision/view state, and Python callbacks during native serialization.
The test retains the child log, BREP and STEP for diagnosis. No live RMFG service
or authenticated manufacturing request has been verified yet.

The independent Terra `SM-P01` run on that build passed its geometry oracle:
one shared sheet, folded bounds 70 x 50 x 25 mm, and a valid flat solid measuring
70 x 71.9971677852 x 1.6 mm. Both view switches succeeded and the saved folded
image was inspected. It took 14 calls, including five rejected calls that it
recovered from. The failures identify remaining source/shared-state guidance
and argument-discovery friction; this is a correct result, not clean first-call
usability. The corresponding Qwen run failed: it left a valid bracket source and
a separate flat plate, with no shared editable sheet. That separate flat object
does not satisfy the shared-definition requirement. Both traces are retained;
neither the fixed prompt nor the geometry oracle was changed.

RMFG request persistence now has a private SQLite store for the exact export,
manufacturing payload and operation key. A retry key cannot be repurposed for a
different model, quantity, configuration or operation. Response observations are
append-only, preserving remote IDs and ready responses across restarts even when
an older pending response arrives later. Sheet listings read metadata without
loading STEP blobs; loading a saved export verifies its byte digest. The panel
and native tool bindings still need to interpret remote states, validate current
document authority, and present the manufacturing workflow.

The worker runner now submits only previously saved inputs. After a remote ID
has been recorded, it reads that design, DFM report, quote or cart instead of
creating it again. Connection failures and replies without usable IDs leave the
original key and payload available for an explicit retry; there is no automatic
retry loop. Conflicting remote IDs stop the operation. The runner saves the
response before returning it, and makes no payment or order request.

The initial six storage tests and four execution tests failed before their
implementations. A further failing test caught ID-less responses preventing a
same-key retry; that case is fixed. The full strict Release build passes with
12 jobs, and all 27 storage, execution, snapshot and REST transport tests pass
against the installed modules. These checks use local fakes and private SQLite
files. Manufacturing state interpretation, catalog/configuration and quote UI,
native tool bindings, and authenticated RMFG verification remain pending.


The RMFG manufacturing controller and panel now use the validated folded export
and durable request runner for material selection, design analysis, quotes and
website checkout. Export, database and HTTP work execute on workers. Every
analyzed part needs an explicit catalog material; quantity means completed
design units. Visible manufacturing findings are shown without internal reviewer
notes. A ready quote must match the exported revision and effective materials
before checkout; a fresh remote read checks its status and expiry. The panel
opens the website for final review and does not submit payment or an order.

Document edits and changed settings invalidate the current quote. Late replies
remain in the durable job record without replacing current settings. Closing
the panel discards its pending completion and does not open a browser later.
Uncertain replacement-quote requests retain their saved retry key; an older
quote cannot become current simply because the material catalog was refreshed.
This increment supplies the shared controller and panel. Ribbon registration,
native manufacturing bindings, restored-job presentation and authenticated live
RMFG validation still need to be completed.

Tree and History regression verification passes all 55 checks on the strict
Release build. This includes actual native rows and double-click editors,
source and operation identity, rollback, Undo/Redo, save/reopen, native
inspection, and cached view switches without recompute or extra Undo entries.


The Sheet Metal ribbon now has a **Manufacture with RMFG** command beside its
connection control. It targets the selected shared sheet at the current History
position. The native `sheet_metal.manufacturing` capability uses the same
controller and settings: status, cached/catalog material pages, folded analysis,
per-part material selection, quantity, quote, refresh and website checkout.
Opening the panel does not upload a model. Native results contain exact sheet
identity, revision and manufacturing state, with paged material rows and no
private checkout link. The native checkout action opens the website only after
the request completes and document/turn authority still holds.

The initial native contract tests failed for missing registration/runtime and
ribbon inventory; six private GUI tests failed before the shared controller
factory existed. A further failing test exposed an unbounded catalog response;
material inspection now provides explicit offsets and continuation metadata.
All 120 source contract, dispatch, registry and packaging checks pass. The full
strict Release build passes with 12 jobs. All 19 combined manufacturing,
connection and panel GUI tests pass; after pagination, all eight focused native
GUI tests pass, including the production dispatcher, live ribbon discovery,
call replay without another upload, and a changed document requiring a new turn.
These use a fake RMFG service with real CAD export. Restored-job UI and live
browser sign-in/remote manufacturing verification remain pending, as do the
previously identified CAD tool-sharpening and full-goal coverage checks.


Saved RMFG jobs can now be listed and inspected from a separate panel tab and
through the native tools. Inspection reads local metadata and the last recorded
response without loading STEP blobs or contacting RMFG. Historical jobs stay
visible with their revision marked; they do not replace the current quote.
Resuming an analysis or quote requires the exact current document session and
sheet revision. It restores the saved configuration and request key, reads known
remote resources, and retains that key for an uncertain submission. Reopening
the panel therefore no longer requires a duplicate upload. Reopening the CAD
document changes its session, so an earlier job remains historical rather than
silently becoming a current quote.

Document closure now cancels that controller's pending publication, removes it
from the controller/panel caches, and closes its own manufacturing panel. Tests
also reject document or quantity changes while restoring a job. Initial backend,
GUI and native tests failed before implementation. The strict Release build
passes with 12 jobs; 39 backend checks and 70 native contract/dispatch/package
checks pass. All 24 combined GUI checks pass, and another 15 checks pass in a
separate relocated Release runtime, including native restoration and live ribbon
discovery. A user preview has been launched from that isolated runtime with a
fresh profile and no automatic document opening or process timeout. Authenticated
RMFG service verification and the remaining full-goal audit are still pending.

The ordinary creation trial exposed a recovery defect: the assistant twice tried
to switch an upstream source rather than its shared sheet. Native view target
errors now explain how to find an existing shared sheet, or attach one using the
source and an observed reference face. The error retains its original target,
type and error code. It does not create geometry or change History. The failing
provider-result and private GUI tests now pass; 58 source regressions and 13
native view/inspection tests pass on the strict Release build. This verifies the
repair contract, not a new ordinary-prompt usability result.

The remaining dimension audit identified a specific concern: the shared
parameter panel calls an upstream `length` property “Flange length”, while a
formed base shape has a distinct `height` property for its wall. Its exact source
editor already exposes these separate dimensions. The shared panel/native edit
path still needs a geometry regression and an additive way to expose those
dimensions without changing existing parameter meanings.


Shared base-shape editing now exposes base width, formed wall height, and
Hat/Box return-flange width to both the parameter panel and native tools. The
existing `flange_length` field still edits the same upstream property; for a
base shape its panel label now correctly reads “Base length”. Flat sources do
not offer ineffective wall/return-flange controls. New edits use the existing
asynchronous shared-model path and revision checks.

Three schema cases and three private GUI cases failed before this increment.
The resulting L bracket changes wall height while preserving base length even
when edited in flat view; both solids update, Undo/Redo restores dimensions,
and a reopened document retains dimensions and accepts another asynchronous
edit. Hat return-flange edits change developed geometry. All 18 source-creation
GUI tests and 72 source contract/scope tests pass; existing shared operation,
panel, native edit, and cached view regressions also passed. Strict Release
builds used 12 jobs. The separate workspace-switch regression remains under
active repair following user cabinet feedback.


Following the cabinet trial, the owner explicitly lifted the temporary restriction
on agent ribbon changes. Native agents now retain `workspace.switch`, including
inside Analysis and Manufacture's scoped tool lists. SheetMetal and 3D Print are
included alongside every other ribbon destination. The tool explains that the
destination tools become available after switching, on the automatically continued
next turn. Existing document/edit guards and stale-turn rejection remain intact.
MCP instructions also describe refreshing tools after a switch.

All seven isolated native view/workspace tests pass, including actual activation
of every ribbon destination plus Sketch setup, production tool authorization,
SheetMetal-to-Assembly-and-back discovery, unchanged sheet geometry/History, and
stale-turn rejection. The full strict Release build passes with 12 jobs. There
are 188 passing source scope/registry/dispatch/continuation checks, followed by
84 passing manifest/workspace/MCP checks after the final Print inventory update.
Print's existing slicer/setup dialogs remain interactive commands; registering
the ribbon for navigation does not add a native slicing or printer-control API.
No user instance or model was manipulated.

Cabinet feedback still to address: simplify source-to-shared creation, remove
irrelevant mandatory creation inputs, and clarify or improve folded-coordinate
region selection. First-save support needs a proper output-authorized path.
Workspace discovery is verified; assembling and checking the cabinet with sheet
component links and joints still needs an independent CAD integration test.


The native Assembly handoff now has an isolated CAD integration regression.
It creates a shared L bracket with an editable circular-cut History state,
creates an assembly through the production dispatcher, and inserts the sheet
at an explicit translation and rotation. Editing the original wall height
updates the linked occurrence while retaining placement, source links, and
cut History. This passes with existing Assembly implementations; no new
placement API was necessary. Slider joints, motion, and the complete cabinet
remain separate verification work.


Base creation no longer requires an unused return-flange width. Omitting
`flange_width` uses the ribbon's existing 8 mm default; supplied values retain
their behavior. The native schema and shared source-preparation path agree.
Seven source tests and three GUI subcases failed before implementation; all
22 source tests and 27 source-creation/form GUI tests now pass, including
geometry equivalence between omitted and explicitly supplied defaults for
L, Tub and Hat shapes. The strict Release build passed with 12 jobs.


The native hole-edit contract now states when a mapping region is required:
folded centers need one; flat centers and radius-only edits do not. A missing
folded region is rejected after exact target/authority checks, before preparing
or committing an edit, with a repair pointing to `sheet_metal.inspect list_regions`.
The original edit-error family and error code remain compatible. The native
GUI test rejects the incomplete request without changing geometry or Undo,
uses the inspected region to create a valid bend-crossing hole, and resizes it
without a region. Two source tests and one GUI test failed before implementation;
94 source regressions and 19 native edit/dispatch checks now pass. Strict Release
builds used 12 jobs. No automatic region guessing was introduced.

The unchanged SM-P01 ordinary bracket request was rerun against revision
`7a713665` using both Terra and Qwen, with no tool hints or process timeout.
Both produced exactly one editable shared sheet, with valid folded and flat
solids, the requested 70 × 50 × 25 mm folded bounds, 1.6 mm gauge and 2 mm
inside radius. Both successfully switched the shared representation. Terra
completed in 53.772 seconds (12 tool calls, four rejected calls); Qwen completed
in 183.575 seconds (11 calls, six rejected calls). These single trials establish
successful recovery, not reliable first-attempt tool use or a latency benchmark.
Remaining errors include invented arguments, required creation settings, and
trying to switch a source before creating its shared sheet. Neither trial
exercised ribbon navigation; that is covered by the separate native tests above.

Native folded/flat switching now accepts the current saved display pair after
reopening a document. Previously it incorrectly required the in-memory edit
mapping, even though both saved meshes were already available. The additive
`current_display()` check verifies the mesh pair against the current feature
and dependencies; the existing `current()` check still requires a prepared
mapping for picks and mapped edits. Switching does not prepare geometry.

One source test and two isolated save/reopen tests failed before this fix.
The strict Release build passes with 12 jobs, along with 52 source tests and
32 native presentation, view, and selection tests. Both a reopened base sheet
and a reopened cut state switch without remeshing, recomputing, serialization,
visibility changes, or Undo/History changes. Source edits invalidate the saved
pair. Preparing restored mappings for subsequent cut creation and manufacturing
still needs a usable workflow; this fix covers presentation only.

An additive `SheetMetalPreparation.start_preparation` API now rebuilds missing
edit mappings from detached saved inputs, without recomputing the document or
writing feature properties. It uses the same root and cut geometry routines as
normal native recompute. The worker checks every saved fingerprint; the GUI
checks the exact document revision, inputs and cache identities before publishing
the complete chain. Current ancestors are reused, so preparing a descendant
retains the mapping used by other branches. Cancellation, document closure and
source changes prevent publication.

The missing-module regression failed before implementation. All eight new native
preparation tests pass, including legacy cuts, mixed sketch/hole History,
suppression, a real subsequent hole edit and preservation of an existing ancestor.
The combined geometry/history suite passes all 70 tests, and 51 source checks
pass. Strict Release builds pass with 12 jobs. This is the shared preparation
API; panel and native-tool integration are still pending, as is authenticated
RMFG verification. Existing feature classes, mutators and persisted data remain
compatible.

The editing panel now offers **Prepare for editing** when its sheet lacks a
current edit mapping, including after reopening a saved document. It uses the
detached preparation API and adds no document or History changes. Edit controls
are disabled while preparation runs; closing the panel cancels its uncommitted
cache publication. Source changes produce an error and restore the controls.
The action is hidden when a mapping is already prepared.

Three new panel tests failed before implementation. All 45 preparation, panel
and ribbon/History tests now pass on the strict Release build, including an
actual new hole after reopening and preparation. Preparation preserves Undo;
the subsequent hole adds one entry. Native-agent preparation and automatic
manufacturing preparation still need integration. Existing user preview
instances were not updated or manipulated.

Native agents can now call `sheet_metal.inspect` with `operation="prepare"`
and the exact sheet target to prepare a reopened sheet for cuts and export.
This is an asynchronous read: it changes no document properties, structural
revision or Undo history. Existing inspection operations keep their synchronous
behavior. Completion checks the original document/turn, exact sheet revision,
fingerprint and prepared geometry; cancellation propagates to cache publication.
Inspection errors describe this recovery path for an unprepared reopened sheet.

Twelve source checks and the native preparation request failed before this
increment. The resulting live dispatcher prepares a saved sheet, reads its
regions and creates a valid hole in the same turn, with exactly one cut Undo
entry. Another native case rejects a source edit during preparation. All 68
source checks pass. The broader native run passed 34 of 35 cases; its only
failure was a new fixture omitting assistant Undo ownership. After initializing
the fixture like the existing edit tests, both focused native cases pass.
All ribbon navigation and view regressions passed without increasing schema
limits. Strict Release builds used 12 jobs. RMFG's automatic preparation path
and authenticated service verification remain pending.

RMFG analysis now prepares a reopened sheet's missing mapping before exporting
its folded STEP. The additive `start_prepared_export` wrapper keeps preparation
and export bound to the original revision; already-prepared sheets use the
existing export path directly. The original `start_export` entry point and
behavior remain available. Cancelling the manufacturing request propagates to
the active preparation/export stage and prevents a subsequent upload.

The new export and panel regressions failed before implementation. All 39
export/manufacturing/native/saved-job tests now pass, with 30 passing source
checks and a strict Release build using 12 jobs. A real STEP round trip from a
reopened sheet matches its folded solid while the display remains flat, with
unchanged document revision, shapes and Undo. Source changes and cancellation
during preparation prevent STEP export; closing the manufacturing controller
also prevents upload. RMFG responses in these tests are mocked; authenticated
API, quote and browser checkout verification remains outstanding.

## Active Assembly and agent ribbon changes

An active Assembly uses FreeCAD's GUI edit mode during ordinary assembly work.
Previously, an agent could change ribbons but its automatic continuation then
stopped with “an edit session is active,” leaving the next tools unavailable.
At the owner's request, `workspace.switch` now deactivates the exact active
Assembly before changing workbench. Deactivation uses the native GUI operation;
it does not close sketches or other editors, modify geometry, or add Undo.
An open assembly task or pending transaction must finish before this action.
The existing continuation check remains intact. The tool description explains
both deactivation and availability of destination tools on the next turn.

Red evidence: the private GUI reproduced the original continuation failure in
both directions, then the deactivation regression failed in both directions
before implementation. The new source test also failed on the missing helper.
Green evidence: 19 source checks passed, and 11 private GUI cases passed in
36.375 seconds, including active Assembly → SheetMetal, a manually retained
Assembly while switching back, automatic continuation, all ribbon tool surfaces,
and placed sheet occurrences retaining their editable history. Exact checks:

```sh
python3 -m pytest -q src/Mod/VibeCAD/vibecad_tests/test_native_surface_authority.py src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py src/Mod/VibeCAD/vibecad_tests/test_edit_state.py
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" SMTests.testSheetNativeView SMTests.testSheetSourceCreation.TestSheetSourceCreation.test_native_assembly_places_a_linked_sheet_and_keeps_its_editable_history
```

The build and native runner used the dependency environment and strict Release
settings described above; both exited successfully. These checks verify the GUI
handoff to the next agent turn; an ordinary model-driven request is a separate
usability check. Existing public methods, arguments and tool result shapes are
retained. Automatic Assembly deactivation is the owner-approved behavior change.

## RMFG browser verification

The owner confirmed that RMFG browser sign-in works in the local preview on
2026-09-15. This is live user verification of authentication, beyond the mocked
OAuth tests. Live folded STEP upload, manufacturing findings, quotes and website
checkout still require verification; successful login alone does not prove them.

## Reopened primitives and saved coordinate precision

The ordinary SM-P02 request exposed a preparation failure that the earlier
sketch-based fixture did not: copying an unchanged reopened Base Shape can
compose saved location matrices with last-bit coordinate roundoff. The source's
persisted BRep remains identical, but hashing the newly copied coordinates can
reject the saved sheet fingerprint.

Preparation now also considers the persisted folded root as a source snapshot
when that root has no embedded cuts. It accepts that candidate only when the
serialized topology matches the current source copy, every differing real
number differs only within a double-precision bound at the sheet coordinate
scale (32 machine epsilons), and preparing the candidate
reproduces the **exact existing fingerprint**. It never rewrites the fingerprint
or BRep text. Legacy embedded-cut roots keep their existing path. Worker-side
checks, exact document revision checks, and all-or-nothing publication remain.
This handles ordinary shared History chains without adding document changes or
Undo entries during preparation.

The new L-shaped source/cut-chain regression failed before implementation.
With the candidate implementation, all 26 related preparation and STEP tests
passed in 56.339 seconds. U-shaped and tray source/cut-chain checks also passed
(2 tests, 14.253 seconds). These cases preserve the saved source, both fingerprints,
revision and Undo; then they resize the existing hole through one actual Undoable
edit. A displaced input snapshot with unchanged topology and volume is rejected
without publishing either mapping. The candidate Python module was loaded into
private processes using the strict Release binary while an older live probe
retained its unchanged build runtime. No user instance was modified.

### SM-P02 — Reopened sheet editing from an active Assembly

Fixed ordinary request:

> On this reopened sheet-metal bracket, cut a 10 mm diameter through-hole centered halfway along the bend and midway across its curved portion. The hole should continue into the panels on both sides of the bend. Keep the existing dimensions and editable history, and finish in folded view.

The fixture contains a saved/reopened 70 × 50 × 25 mm L-shaped shared sheet,
1.6 mm thick with a 2 mm radius, and an active Assembly. It starts without a
transient edit mapping. No tool names, call sequence, payload hints, hidden
retries or process deadline are supplied. The runner retains the input fixture,
events, responses, final document, STEP shapes, geometry report and viewport.
The oracle checks the same source parameters and fingerprint, one new circular
History step, its mapped center, diameter, flat removed volume, valid folded/flat
solids, sampled folded material removal and the final folded presentation.

Run explicitly with the chosen provider environment:

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" SMTests.live_reopened_sheet_prompt
```

The initial Terra high-reasoning run switched successfully but failed the geometry
oracle because preparation rejected the unchanged sheet fingerprint. With the
candidate fix and unchanged prompt, Terra passed in 175.206 seconds: 19 tool calls,
including two rejected off-surface picks that it corrected through inspection.
It switched out of the active Assembly, prepared the sheet, authored the real
bend-crossing hole and finished folded. This is live provider and native geometry
evidence; RMFG responses are not involved. The original Qwen run encountered the
same fingerprint failure and continued trying sketch-based repairs. That obsolete
private run was manually stopped after the failure was confirmed; it did not
complete the final geometry oracle. A fresh run on the corrected build is pending.
The stopped run is not counted as successful provider validation.

### Preparation diagnostics for agents

An unchanged reopened sheet can be valid while its transient edit mapping is
absent. Both inspection APIs now retain the actionable preparation error in
that case; the document object's generic `Valid` status no longer masks it.
Actual invalid-feature status remains available. A native regression reads the
sheet, rejects region inspection until preparation, prepares it, and verifies
that inspection succeeds without changing revision, History or Undo.

Red: the new regression failed with three `prepare`-versus-`Valid` assertions.
Green: 37 native preparation, inspection and operation tests passed in 87.544 s.
The candidate interpreted modules were loaded from a frozen private overlay
with strict Release binaries; the running user and baseline runtimes were not
modified. The installed-module command for repeating the checks is:

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" SMTests.testSheetPreparation SMTests.testSheetNativeInspect SMTests.testSheetOperations
```


### Formed sheets: detached cuts and rigid unfolding

The expanded save/reopen checks cover Flat, L, U, Tub, Hat and Box sources,
including a 400 × 500 × 600 mm box with 1.2 mm stock and a 1.5 mm bend radius.
They exercise a real native circle cut, save/reopen, detached preparation and a
radius edit, and reject a displaced source snapshot before publishing caches.
The cut must preserve both persisted representations of its parent.

These checks exposed three additional precision boundaries:

- A Boolean cut on a Hat shape modified its parent's vertex tolerances. Cutting
  a detached solid protects the cached parent, persisted History and other
  workers that share the parent geometry.
- Composed unbend matrices acquired tiny scale/shear terms. Keeping their known
  rotation/translation as a native Placement prevents OCCT's scaling-location
  error and uses the same rigid transformation for faces and point mapping.
- ZipWriter stores native floats with 16 decimal places in fixed format. A
  near-zero circle center changed on reopen, while the saved Operation JSON
  retained the original double. Preparation recovers those original values only
  if their native serialization exactly matches the current fields and the
  operation identity matches. The complete chain must still reproduce every
  saved fingerprint. Invalid JSON and genuinely different parameters retain the
  current-input path.

These are preparation/edit changes. Cached view switching, tool names, native
property formats and saved fingerprint formats remain unchanged. They prevent
new cuts from modifying a parent; they do not rewrite damaged older snapshots.

Red evidence: the initial Hat and Box reopen checks rejected fingerprints; the
cabinet-sized Box initially failed with `Location with scaling transformation
is forbidden`. A separate native before/after check reproduced the Hat parent
BRep mutation. After isolating the Boolean operand, the Hat chain passed in
19.925 s. After the rigid transform and exact saved-float recovery, the cabinet
chain passed in 17.959 s. The installed mapping, across-bend cut, preparation and
ribbon suite passed all 36 checks in 121.042 s on strict Release binaries:

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" SMTests.testUnfoldMapping SMTests.testEditGeometry SMTests.testSheetPreparation SMTests.testSheetNativeView
```

Final installed source/preparation/export validation:

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" SMTests.testSheetSourceCreation SMTests.testSheetPreparation SMTests.testRMFGExport
```


The source/preparation/export run passed 47 of 53 checks; six new assertions
caught parent **flat** BRep mutation. Detaching the developed face and region
Boolean operands fixed that remaining mutation. The final focused installed
suite passed all 15 checks in 93.664 s, including all seven size/shape cases,
across-bend cuts, mixed History/suppression, prepared-ancestor reuse and a real
reopened STEP export. Exact selection:

```sh
source_test=SMTests.testSheetSourceCreation.TestSheetSourceCreation
prepare_test=SMTests.testSheetPreparation.TestSheetPreparation
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testEditGeometry \
  "$source_test.test_reopened_base_shape_prepares_its_cut_chain_without_changing_saved_inputs" \
  "$source_test.test_reopened_u_shape_prepares_its_cut_chain_without_changing_saved_inputs" \
  "$source_test.test_reopened_tub_prepares_its_cut_chain_without_changing_saved_inputs" \
  "$source_test.test_reopened_flat_prepares_its_cut_chain_without_changing_saved_inputs" \
  "$source_test.test_reopened_hat_prepares_its_cut_chain_without_changing_saved_inputs" \
  "$source_test.test_reopened_box_prepares_its_cut_chain_without_changing_saved_inputs" \
  "$source_test.test_reopened_cabinet_sized_box_prepares_its_cut_chain" \
  "$prepare_test.test_reopened_mixed_chain_and_suppression_share_one_mapping" \
  "$prepare_test.test_prepared_ancestor_is_reused_for_a_restored_descendant" \
  SMTests.testRMFGExport.TestRMFGExport.test_reopened_sheet_prepares_and_exports_real_folded_step_without_an_edit
cmake --build "$BUILD" --parallel 12
```

The strict Release build returned zero after updating the interpreted modules;
no native source changed in this increment. All nine updated Python modules and
tests byte-match between author source, strict source/build and the independently
verified runtime. The user's already running preview was kept unchanged.


### Editing discovery from the Modeling ribbon

Sheet inspection and presentation are available from Modeling, while sheet
mutations belong to the SheetMetal ribbon. The corrected Qwen run prepared the
sheet but repeatedly attempted ordinary modeling operations instead of switching
to the sheet editing tools. Inspection/preparation results outside SheetMetal now
include `edit_guidance` naming `workspace.switch`, the `sheet_metal` destination
and next-turn tool availability. Once SheetMetal is active, that extra guidance
is absent. Existing result fields and inspection behavior remain intact.

Successful workspace switches also return a `message` explaining that the
current turn should end and VibeCAD will continue automatically with the destination
tools. The existing `workspace` and `next_turn_required` fields remain unchanged.
No provider-specific prompt, forced call, hidden retry or new argument was added.

The guidance is in results because the compact provider schema strips operation
descriptions. Enlarging the family description instead exceeded the native Model
schema limit (65,602 versus 65,536 bytes). The final tool definitions retain their
previous content and size; the native all-ribbon check passes.

Red: a real native Model inspection lacked `edit_guidance`; source tests also
failed for prepared-result guidance and the switch completion message. Green:
51 source tests passed in 5.46 s, and all 19 native inspection/view/ribbon tests
passed in 70.140 s, including both switch directions and unchanged document/Undo
state. The source test checks that provider projection retains the guidance.

```sh
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_inspect_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_preparation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_capability_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetNativeInspect SMTests.testSheetNativeView
```

The new interpreted modules were copied to a separate strict Release runtime
after its previous GUI tests terminated. No native source changed. The existing
Qwen run and the user's preview retain their original modules, so they do not
prove a live-model improvement from this new guidance.

### Complete native regression audit

After the formed-sheet fixes, the default integrated native suite passed all
**322 tests in 808.233 seconds**, with process exit zero, successful status and
the private GUI process gone. This includes the existing SendCutSend preset and
Unfold ownership checks and the shared geometry, profiles, presentation, picks,
operations, panels, Tree/History, native discovery/dispatch, source creation,
RMFG connection/export/configuration/saved jobs and recompute-origin modules:

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS"
```

These tests used an independent copy of the strict Release runtime containing
the formed-sheet fixes. They prove the integrated local workflow against its
fixtures, not live manufacturing-service behavior or universal model support.
The later result-guidance change has its own 51-test source and 19-test native
proof; the integrated run retained its original module files throughout execution.

A subsequent isolated native RMFG test reused the already authorized connection
from a closed preview through the production secure store and refresh lock.
It passed in 23.099 s (exit 0): folded STEP upload, current material selection,
quantity one, ready quote and website checkout opening. The document revision,
Undo and flat presentation stayed unchanged. No payment or order was made.
Artifacts include the uploaded STEP, observed catalog/design/quote responses,
native call results and screenshot. The first isolated attempt failed because
the GUI harness omitted desktop keyring access; the corrected harness used the
normal secure store, without copying tokens or changing product authentication.
The owner separately reported no-finding quotes for the existing cabinet parts;
those quotes do not verify assembly completeness or load capacity.

### Cabinet feedback: discover tools before declaring the work blocked

The owner reported that the agent stopped with assembly-only tools, then later
repaired the four existing sheet parts, changed their stock and bend radius,
and obtained individual RMFG quotes. Internal supports/stops and top/back
closures remained unfinished. Native assembly insertion already accepts an
initial placement. At that point, native sheet creation supported base shapes,
sketch-derived bases and solid conversion; the internal-fold addition is
documented below. Existing bend parameter edits are a different operation.

The detailed workspace-switch instructions were on the optional operation
field, which provider projection removes. Successful-switch result guidance
cannot help an agent that never calls switch. Provider-visible live state now
adds `workspace_navigation` before each eligible turn, identifying the existing
switch tool, automatic Assembly deactivation, and next-turn tool refresh.
The original state is not mutated; sketch edit gets no switching invitation.
The tool description stays compact and all arguments, destinations and schema
limits remain unchanged. No provider-specific instruction or forced call was
added. This change does not implement the missing internal-bend operation.

Red/green: the source regression first failed because provider-visible switch
instructions were absent, then because the navigation context was absent.
The final source group passed 100 tests in 5.25 s. Installed native validation
checks every ribbon's schema plus actual native state in Modeling, Assembly
and SheetMetal, and both switch directions with unchanged geometry/Undo.
All 10 tests passed in 42.631 s, process exit zero. The final compact-description
source subset also passed all 6 tests in 2.62 s.
A broader snapshot experiment also found the existing Printing state-builder
gap; no Printing implementation change is included here.

```sh
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_capability_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_preparation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_drawing_provider_state.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_manufacture_provider_scope.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_turn.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_common_runtime.py
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetNativeView
```

Only interpreted Python changed. Modules were installed into the idle independent
strict Release runtime, then kept immutable during each private GUI test. The
original build remains in use by a prior live Qwen probe and was not modified.
The owner's running preview was not modified or closed.

The unchanged SM-P02 live assistant case passed from its own saved bracket and
active Assembly: 1 test in 170.094 s, exit zero. Its first call switched workspaces
and the final oracle verified the cross-bend cut, retained dimensions/history
and folded presentation. The older Qwen run on the pre-guidance runtime finished
without the required cut (1 failure in 3464.721 s); it was not interrupted.

### New edge-flange source operation

The additive `add_flange` source operation reuses upstream `SMBendWall` with
worker-side validity evidence. It accepts an exact parent and face/edge list,
length, bend radius/angle, direction and length/bend conventions. Original
upstream proxies, commands, defaults and existing source operations remain.
The wrapper inherits stock gauge from its parent, including a shared sheet cut;
it retains the exact source link and native Tree/History editor. The source
editor displays bend angles in degrees. New feature creation uses the existing
short transaction and asynchronous recompute/receipt path.

Red: creating the flange was rejected as an unavailable operation. Green:
creation and invalid-input tests passed (2 tests, 3.609 s). A separate cut →
flange → shared-sheet test passed save/reopen and an upstream thickness change
(1 test, 3.678 s). It preserves the circle feature and parent folded/flat BReps.
The fixture retains each asynchronous run until completion; an initial fixture
version dropped its run handle and did not finish.

The complete source features/creation/forms/reference-face regression group
passed **48 tests in 148.763 s**, exit zero. Existing native provider/preparation
source tests passed **21 tests in 2.75 s**. These used the independent strict
Release runtime; only interpreted modules changed in this increment.

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetSourceFeatures SMTests.testSheetSourceCreation \
  SMTests.testSheetSourceForms SMTests.testSheetReferenceFaces
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_preparation.py
```

Ribbon and provider exposure are the next increment. This source operation adds
an edge flange; bending along an internal sketch line remains separate work.
Future owner previews must reuse the owner's saved profile and conversations;
private automated tests continue to use isolated profiles.

### Add Flange in the ribbon and native tool registry

Bend/Form now includes **Add Flange**. Its form captures the exact selected
boundary, uses degree units for the bend angle, and calls the same asynchronous
source operation as `sheet_metal.create` / `add_flange`. The native schema names
folded source topology explicitly: thickness-side faces or straight boundary
edges. Existing creation variants and the upstream Add Wall command remain.
The new source's returned reference faces support the existing `from_source`
step for shared folded/flat presentation; this increment retains that two-step
workflow. A flange can use an existing shared cut as its parent.

Red: the provider surface rejected the unclassified new action, the GUI lacked
the button, and native dispatch rejected the operation. Green: **52 source
tests passed in 3.89 s**. The strict Release build completed with warnings as
errors and **12 jobs**. Three focused installed tests passed in 11.935 s;
the ribbon/forms/view and existing source-variant regression group then passed
**20 tests in 52.547 s**, process exit zero. It covers all-ribbon schema limits,
both workspace-switch directions, frozen form selection and cached views.

```sh
cmake --build "$BUILD" --parallel 12
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_preparation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_capability_registry.py
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetSourceForms SMTests.testSheetNativeView \
  SMTests.testSheetSourceCreation.TestSheetSourceCreation.test_registered_source_variants_create_valid_sources_without_mcp
```

The explicit SM-P03 probe starts with a plate containing an editable hole in flat
view. Its ordinary prompt requests a 20 mm edge flange, 90-degree bend and 2 mm
radius, preserving the hole and dimensions and finishing flat. The external
geometry oracle checks exact parent/history links, parameters, boundary extent,
solid validity and hole samples. This is
an edge-flange test, not proof of internal sketch-line folds or a complete cabinet.

SM-P03 subsequently passed with the production native provider using
`gpt-5.6-terra` and ChatGPT authentication: **1 test in 144.842 s**, process exit
zero. The retained parent BRep, hole, dimensions, flange parameters, exact history
links and final flat view passed the independent oracle. No MCP was enabled.
An earlier run failed because artifact STEP export changed OCCT Checked flags on
the shape being checked. Saved input/result parent BReps were byte-identical;
a separate diagnostic established the export side effect. The artifact writer
now exports a copy. Its regression failed before that fix and passed afterward,
including a valid STEP round trip with matching solid count and volume. The
geometry oracle itself remains unchanged; old failed artifacts are retained.

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testLiveSheetArtifacts
VIBECAD_SHEET_PROMPT_MODEL=gpt-5.6-terra VIBECAD_SHEET_PROMPT_AUTH=chatgpt \
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$LIVE_ARTIFACTS" \
  SMTests.live_sheet_flange_prompt
```

### Restore sheet history before the ribbon has been activated

Opening a saved sheet through File > Open or Recent Files restores Python
features on a document worker. The upstream base-shape module obtained the main
window during import, which failed on that worker and left source, sheet and cut
proxies missing. Earlier reopen tests had imported these modules on the GUI
thread first and missed this path.

The window lookup now uses the existing main-thread dispatcher, retaining the
public window reference, command registration and GUI classes. Background
document loading and geometry preparation remain intact. Presentation callbacks
also decline incomplete sheet proxies instead of repeatedly dereferencing them.
This prevents the secondary error flood; it does not treat failed restore as
valid geometry or make stale geometry editable.

The new standalone regression produces a saved plate with shared sheet and hole
history in one process and opens it in a second process through Recent Files.
It records that source modules import on the worker, verifies restored proxies
and History commands, prepares valid folded/flat geometry, activates SheetMetal
and saves the restored document. Run it alone, without other test modules that
could preload SheetMetal. See the native test README for the two-process command.

Red: the asynchronous cold-open test reproduced the reported window-thread
failure and missing proxies; the separate incomplete-proxy presentation test
reproduced the AttributeError. Green: the cold-open test passed in **2.207 s**,
process exit zero. The strict Release build with warnings as errors and
**12 jobs** also passed. The broader presentation, preparation, source and cut
history regressions passed **59 tests in 111.940 s**, process exit zero.

```sh
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testPresentation SMTests.testSheetPreparation \
  SMTests.testSheetSourceFeatures SMTests.testSheetSourceForms SMTests.testSheetCutHistory
```

### Internal folds and relief-cut tabs: geometry backend

The new `fold_from_sketch` source operation reuses upstream `SMFoldWall`. It
retains the parent sheet and editable straight-line sketch as History inputs.
A U-shaped relief cut can free an internal tab so only its bridge bends, while
the surrounding plate stays in place. The geometry regression checks points
inside both regions, retained cut history, one valid solid and developed volume.
The native AI creation action and ribbon form are documented below.

Fold angle, radius, side, position and K-factor remain editable. New shared
states link their K-factor to the fold's allowance so development recovers the
original stock dimensions; material edits also update that linked source. The
new path supports middle, backward and forward positions. The upstream
intersection-of-planes command remains available separately. Input hiding now
uses each object's owning document through the existing GUI dispatcher, avoiding
changes to a same-named object in another active document.

Red tests exposed the missing operation, inconsistent fold/development factors,
material edits superseding themselves and wrong-document input hiding. Green:
**64 backend, source, form and operation tests passed in 185.881 s**, and the
strict Release build completed with warnings as errors using **12 jobs**. A
fresh-process asynchronous reopen then passed in **4.276 s**, retaining the
sketch, History commands and K-factor expression and recovering the flat stock
dimensions. No user instance is needed for these tests.

```sh
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetFoldSource SMTests.testSheetSourceCreation \
  SMTests.testSheetSourceFeatures SMTests.testSheetSourceForms SMTests.testSheetOperations
VIBECAD_COLD_RESTORE_FIXTURE="$ARTIFACTS/internal-fold.FCStd" \
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$RESTORE_ARTIFACTS" \
  SMTests.testSheetColdRestore.TestFoldColdRestore
```

### Internal-fold ribbon and native action

The Bend/Form group now includes **Internal Fold** (`SheetMetal_CreateFold`).
Select a sheet skin and an editable straight-line sketch; the form captures both
inputs before creation, so later selection changes cannot redirect the operation.
It uses the same asynchronous source service as `sheet_metal.create` with
`operation="fold_from_sketch"`. Both retain source/cut and sketch history. Use
Editable Sheet / `from_source` on a returned reference face to attach linked
folded/flat editing. Existing upstream commands remain available.

The native schema requires one skin face and the exact sketch, distinguishes
bend direction from which side moves, and describes relief cuts for internal
tabs. Red tests reproduced the absent ribbon action and rejected native
operation. Green: **53 source tests in 4.20 s**, the strict Release build with
warnings as errors and **12 jobs**, **2 focused installed tests in 3.741 s**, and
**26 installed fold/form/view/workspace regressions in 65.663 s** all passed.

```sh
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_preparation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_capability_registry.py
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetFoldSource SMTests.testSheetSourceForms SMTests.testSheetNativeView \
  SMTests.testSheetSourceCreation.TestSheetSourceCreation.test_registered_source_variants_create_valid_sources_without_mcp
```

The reported automatic Modeling-to-SheetMetal switch-back remains unresolved.
Private assistant-panel tests received Modeling tools after switching, including
real Codex turns on a small fixture (26.534 s), a cold-opened copy of the cabinet
(36.692 s), and preparing all four cabinet sheets before switching (65.103 s).
The cabinet probes used `gpt-6-astra` and the latter verified the preparation
sequence in its tool trace. These passes do not reproduce or explain the owner's
failure. They do not establish that it is fixed.

The SM-P04 ordinary-request test subsequently completed a 90-degree fold along
an existing bend-line sketch, with a 2 mm radius, preserved hole and stock, and
final flat presentation: **1 test passed in 176.354 s**, using the production
native provider (`gpt-5.6-terra`, ChatGPT authentication, no MCP). Its independent
oracle checks retained source and sketch BReps, fold parameters, History links,
valid solids and original developed volume/dimensions.

An earlier run selected a face off the sketch plane and failed inside the
upstream builder. New preflight rejects that selection before adding an object
or Undo entry and lists matching face candidates. It checks the selected face
directly and bounds extra repair suggestions to 32 faces; no Boolean operation
or unfolded-geometry rebuild runs on the GUI thread. Red reproduced the missing
rejection; the final focused geometry/form group passed **8 tests in 14.890 s**,
including translated and rotated source/sketch placements.

The successful live run still needed **59 calls with 13 rejected calls**. Exact
input names, required fold fields and the source-to-shared-state step remain
sharpening work. That run also exposed a revision conflict blocking later agent
reads after a committed geometry failure; the fresh-state recovery below
addresses that path. No owner preview is ready yet.

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$GEOMETRY_ARTIFACTS" \
  SMTests.testSheetFoldSource \
  SMTests.testSheetSourceForms.TestSheetSourceForms.test_internal_fold_ribbon_freezes_sheet_skin_and_sketch_selection
VIBECAD_SHEET_PROMPT_MODEL=gpt-5.6-terra VIBECAD_SHEET_PROMPT_AUTH=chatgpt \
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$LIVE_ARTIFACTS" \
  SMTests.live_sheet_fold_prompt.LiveSheetFoldPrompt
```

### Source creation supplies the folded/flat follow-up

Source-creation results now identify that shared state has not yet been created
and include a `next_step` call using the new source's exact name and a face from
its worker-prepared reference candidates. That next call still validates the
complete unfold. Existing parameter edits do not receive creation instructions.
The tool's input-name descriptions also identify the existing source, sketch or
solid being used. Existing operations, required fields and return fields remain
available; the result guidance is additive.

Red: the installed regression could not find the follow-up in the provider-visible
result. Green: it executed the returned call through native dispatch and obtained
valid shared geometry (**1 test, 1.903 s**); **44 source tests passed in 3.18 s**.
The strict Release build completed with warnings as errors and 12 jobs.

The unchanged SM-P04 prompt and independent geometry oracle passed with
`gpt-5.6-terra` in **77.338 s**, using **14 calls and 4 rejected calls**. The
preceding run used 59 calls and 13 rejections in 176.354 s. This is one controlled
case comparison, not a general timing guarantee. It measures fewer agent round
trips; the geometry algorithms and view-switching path are unchanged.

```sh
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_preparation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_capability_registry.py
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetFoldSource.TestSheetFoldSource.test_native_fold_result_supplies_valid_shared_state_followup
VIBECAD_SHEET_PROMPT_MODEL=gpt-5.6-terra VIBECAD_SHEET_PROMPT_AUTH=chatgpt \
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$LIVE_ARTIFACTS" \
  SMTests.live_sheet_fold_prompt.LiveSheetFoldPrompt
```

### Resume from fresh document state after a revision conflict

A failed asynchronous geometry edit retains its editable parameters and reports
failure without a successful geometry receipt. Previously, the next agent call
hit the unchanged turn revision guard and requested a new turn, but that request
was not carried through the assistant panel. The agent could no longer inspect
the failed feature in that turn.

A revision conflict that explicitly requests a new turn now carries a separate
`cad_document_state_changed` continuation. It retains the error, captures the
original document UID and observed workspace, and asks the next turn to inspect
current state before continuing. The GUI rejects a different document and uses
its existing workspace/edit-session checks. No workbench is activated by this
recovery path. Existing revision guards, receipts and failed-geometry semantics
remain intact; the new event type and result metadata are additive.

Red: source tests rejected the missing transition and event type, and the full
private GUI test stopped after one turn. Green: **92 source tests in 10.40 s**,
**43 installed mutation/creation/view/fold tests in 92.208 s**, and strict Release
builds with warnings as errors and **12 jobs** passed. The strengthened full GUI
test passed in **9.377 s**: a native cut failed, the next read reported a revision
conflict, and a fresh turn inspected and corrected that same cut to valid folded
and flat geometry. The failed call still had no success receipt; the repaired
edit had one. The original parent BRep remained unchanged. The test uses a
deterministic provider with real session, dispatcher, GUI and geometry code.

```sh
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_session.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_mutation.py
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetNativeRecovery SMTests.testSheetNativeEdit \
  SMTests.testSheetNativeDispatch SMTests.testSheetNativeCreation \
  SMTests.testSheetNativeView SMTests.testSheetFoldSource
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$REPAIR_ARTIFACTS" \
  SMTests.testSheetNativeRecovery
```

The automatic ribbon reversal remains a separate unresolved report. A further
private test cold-opened the cabinet, activated its Assembly, manually selected
SheetMetal while retaining that active Assembly, prepared all four sheets, and
requested Modeling through the actual `gpt-6-astra` provider. It passed in
**67.191 s** with Modeling tools on the next turn and the Assembly deactivated.
That test still did not reproduce the owner's reversal.


### Prepare a reopened internal tab directly

A relief-cut tab retains its parent cut state and bend-line sketch. After a cold
open, those parents have saved results but no transient mapping cache. Reading
the fold's material gauge previously demanded that parent cache, so preparing the
visible final tab failed with “Prepare the current sheet geometry before editing.”

Supported parametric sources now provide the persisted gauge through their
retained cut history. Geometry preparation remains detached and asynchronous;
unknown source types keep the existing prepared-mapping fallback. No document
properties, public APIs or geometry algorithms changed.

The new cold asynchronous restore regression failed before the fix (one error
in 2.769 s) and passed afterward (one test in 3.395 s). It opens through Recent
Files in a fresh private process, prepares the final tab first, checks valid
folded/flat solids and developed volume, and verifies unchanged Undo count,
touched state and saved fingerprint. Strict Release with warnings as errors
and 12 jobs built successfully. The fold, flange/source and preparation regression
suite passed **59 tests in 169.540 s**, process exit 0. Separate original-sheet
and plain-fold cold restore cases also passed (one each, 2.332 s and 2.594 s);
their commands are recorded in `src/Mod/SheetMetal/SMTests/README.md`.

```sh
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$FIXTURE_ARTIFACTS" \
  SMTests.testSheetFoldSource.TestSheetFoldSource.test_internal_tab_uses_relief_history_without_bending_the_surrounding_plate
VIBECAD_COLD_RESTORE_FIXTURE="$FIXTURE_ARTIFACTS/internal-tab.FCStd" \
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$RESTORE_ARTIFACTS" \
  SMTests.testSheetColdRestore.TestTabColdRestore
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$REGRESSION_ARTIFACTS" \
  SMTests.testSheetFoldSource SMTests.testSheetSourceCreation \
  SMTests.testSheetSourceFeatures SMTests.testSheetPreparation
```

### Preserve ordinary solid sources during shared-sheet creation

The integrated suite caught an internal-fold regression: the new K-factor
expression path read `source.Proxy` even for ordinary `Part::Feature` solids.
Container-owned shared-sheet creation then failed before commit. The check now
uses an optional proxy; only actual fold sources receive the expression. Plain
solid sources retain their existing creation behavior and allowance settings.

The 340-test integrated run had two errors in the same container-creation test
(the second followed from the first failed subtest). A strengthened focused test
also failed before the fix, then verified both Part and Body containers, retained
source BRep, native History/frame ownership and no fold-specific expression.
A fold regression fixture now waits for presentation readiness after Undo before
requesting flat view; its geometry future alone does not prove meshes are ready.
Production view switching and asynchronous mesh preparation are unchanged.

Strict Release with warnings as errors and 12 jobs built successfully. The final
History, fold and native inspection subset passed **25 tests in 50.431 s**, exit
0. The full integrated suite still needs a final run after the remaining native
discovery work settles.

```sh
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$RED_ARTIFACTS" \
  SMTests.testSheetHistory.TestSheetHistory.test_container_owned_creation_keeps_native_history_and_frame
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$GREEN_ARTIFACTS" \
  SMTests.testSheetHistory SMTests.testSheetFoldSource SMTests.testSheetNativeInspect
```

### Discover sketch tools and repair construction-only bend lines

`sheet_metal.inspect` now includes a `sketch_creation` route in its `read_sheet`
result. It points to Sketching, explains that creation tools arrive on the next
turn, and tells the agent to return to Sheet Metal for cuts and folds. Existing
result fields, schemas and size budgets are unchanged. The new result survives
provider projection without altering the document revision or Undo.

The SM-P05 ordinary request starts from a flat sheet without sketches. Initially,
the agent stopped without geometry because it could not find sketch creation.
With result guidance it created the relief profile and bend-line sketches and
applied the relief cut. It then marked the bend line as construction, saw an
empty sketch shape and failed to repair it. The complete live oracle remained
red: 61 calls, 11 rejections, 397.655 seconds. That artifact is retained; sketch
creation alone does not count as completing the tab.

The fold diagnostic now names a sketch whose only line is construction geometry
and explains how to turn construction off before retrying. It still rejects the
input before mutation. An isolated repair of the saved live result verified that
changing only that flag allows a valid folded/flat tab, the exact expected cut
volume and unchanged parent BReps (one test, 3.390 seconds). The upstream builder
warned that the line could overhang farther; its resulting solid and development
passed these checks. This direct repair is not a live-agent success claim.

Red/green evidence: the projected-result test failed for missing
`sketch_creation`, and the construction-line test failed because the error omitted
the exact sketch. Both pass; the combined fold and inspection suite passed
**19 tests in 39.593 seconds**, process exit 0. Strict Release with warnings as
errors and 12 jobs built successfully. The full integrated regression run has
reported failures under investigation; no feature-complete claim is made here.

```sh
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetFoldSource SMTests.testSheetNativeInspect
VIBECAD_SHEET_PROMPT_MODEL=gpt-5.6-terra VIBECAD_SHEET_PROMPT_AUTH=chatgpt \
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$LIVE_ARTIFACTS" \
  SMTests.live_sheet_tab_prompt.LiveSheetTabPrompt
```


The fresh Terra SM-P05 run with the construction diagnostic still did not
complete the tab: **47 calls, 5 rejections, 436.854 seconds**. It correctly reported
the construction-line problem but stopped without opening the sketch to repair
it. The earlier 61-call result and this run are individual observations, not a
timing guarantee. A single Astra comparison also failed (136.476 seconds): it
issued workspace switches but created no sketches or fold. Its final claim that
the application returned to Sheet Metal is not proof of an automatic reversal;
the trace includes explicit agent requests to switch back. The owner's separate
report of automatic switching remains unresolved.

Use local Qwen for subsequent tool-sharpening probes and Terra for functional
workflows. Improve discoverable capabilities, concise input contracts and truthful
results; do not encode one model's preferred strategy in tools or context. The
Astra comparison is diagnostic evidence, not the default validation model.

The subsequent strict Release validation passed **60 tests in 190.671 seconds**
across source creation, internal folds, ribbon forms and native inspection.
This includes `10e97f41`, which preserves ordinary imported-solid parents when
preparing flanges: the new regression failed with a missing Python `Proxy`
before the fix, then verified unchanged source geometry, retained links and one
valid solid in each shared representation.

The complete 342-test run had 53 failures in 1293.202 seconds. Every failure
started while fixture document recomputation failed to settle, before the
affected GUI test body. The first affected test and a three-test sequence around
it pass independently. Native thread stacks were captured from that private
test process; the common cause is still under investigation. A new full run
captures document state and Python stacks at its first failure. These focused
passes do not supersede the failed integrated run.

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetSourceCreation SMTests.testSheetFoldSource \
  SMTests.testSheetSourceForms SMTests.testSheetNativeInspect
```


The subsequent document-ownership audit isolated a Tree refresh lease left on a
surviving sheet when another document closed. Closing reset the shared sliced
refresh and stopped its timer; the old reschedule condition only checked object
queues, which could already be empty. The fix resumes the existing bounded
refresh when a surviving document still owns projection work. It preserves the
asynchronous rendering and document-lifetime guards.

The added `test_closing_other_document_finishes_sheet_projection` failed in its
body on the third repetition before the fix (3 tests, 28.791 seconds). With the
fix it passed **20 repetitions in 54.609 seconds**, and the private GUI exited
normally. The affected C++ library compiled and linked using the strict Release
CMake-generated commands, including warnings as errors, in an isolated runtime.
The original runtime remained untouched while its local Qwen probe was running.
The full integrated suite is running with an additional per-test document
ownership check; its result is still required before declaring final validation.


The local Qwen tab probe exposed repeated malformed sketch-batch constraints:
missing point selectors, a geometry field from a different constraint kind, and
unsupported kinds. Returns now identify the missing/extra fields, explain point
references using the current batch's geometry, and state that the rejected batch
created nothing. Existing accepted inputs, schemas, error code and exception type
remain compatible. This is tool-use guidance, not a prescribed design strategy.

The four new diagnostic regressions failed before the change; the complete batch
unit suite then passed **12 tests in 2.72 seconds**. These checks verify the repair
messages and unchanged input payloads. They do not establish that a live model
will complete the tab. The ongoing Qwen run uses its original immutable runtime.

```sh
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sketch_batch.py \
  -k 'explains_how_to_repair or explains_required_point_selector'
# Before implementation: 4 failed, 8 deselected (2.90 s).
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sketch_batch.py
# After implementation: 12 passed (2.72 s).
```


The first full run of the copied runtime stopped in Assembly activation. A
focused reproduction and loaded-library audit identified mixed original/copied
module libraries, including two `_PartDesign.so` copies. The native test launcher
now resolves module dependencies from the requested build before its retained
CMake RUNPATH entries. The same Assembly activation test passes with consistent
libraries (**1 test, 5.450 seconds**, normal exit); no Assembly source change was
needed. A fresh full ownership-audited run is pending with this corrected setup.

```sh
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetNativeView.TestSheetNativeView.test_workspace_switch_deactivates_assembly_and_continues_in_both_directions
```

A real Terra assistant-panel probe also exercised chained workspace continuations
(Model → Sheet Metal → Model, repeated by the model). It passed in 78.530 seconds:
each continuation received the requested ribbon's context and tools, and the run
ended in Modeling. This covers a gap in the previous single-handoff live tests;
it does not reproduce or resolve the owner's separate automatic switch-back.

The cabinet reproduction subsequently failed in 103.985 seconds: the agent
repeated Assembly → Sheet Metal → Modeling → Sheet Metal transitions and ended
in Sheet Metal. Auditing **both** owner session files corrected the earlier
single-file conclusion: the Modeling thread explicitly requested the switches
back. Each ribbon resumes a separate managed provider thread, and the previous
continuation supplied fresh tools but omitted the preceding thread's tool results.
That allowed older task history to replay already completed steps.

Native continuation events now optionally carry the preceding turn's tool trace.
The next session receives bounded recent outcomes, including failures and pending
receipts, with explicit truncation/omission information. Current document state
remains authoritative. Legacy events retain their existing prompt and signature;
the handoff adds no user conversation entry and does not restrict ribbon choice
or prescribe a modeling strategy.

Red/green evidence:

- Continuation unit tests: **2 failed, 4 passed** before implementation; **6 passed**
  afterward. They cover actual outcomes, pending/rejected calls, bounded output,
  preservation of the latest switch, and legacy continuation behavior.
- Native GUI continuation regression: failed in both directions with missing
  handoff data before implementation (**1 test, 10.680 seconds**). The complete
  native view suite then passed **10 tests in 53.222 seconds**.
- Related session, workspace-schema and SheetMetal provider tests passed
  **51 tests in 17.14 seconds**.
- The same private cabinet/Terra assistant-panel reproduction passed **1 test in
  69.851 seconds**, with exactly Assembly → Sheet Metal → Modeling. It inspected
  all four sheet parts, reported positioning operations available and ended in
  Modeling without changing geometry. This live pass verifies that reproduction;
  the full integrated suite and ordinary Qwen tab probe remain separate gates.

```sh
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_session.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetNativeView
# Live cabinet replay uses the preserved private reproduction and a copied file:
python "$HANDOFF_RUNNER" --build "$BUILD" --output "$ARTIFACTS" \
  workbench_handoff_cabinet_chained.WorkbenchHandoffCabinetChained
```

These Python changes were tested in a separate strict Release runtime; native
libraries were unchanged by this increment. No owner instance was launched or
modified, and no process kill timer was used.


The corrected runtime's complete ownership-audited suite finished successfully:
**344 tests in 968.446 seconds**, normal exit code 0, no per-test leftover
documents, and no manual save-prompt dismissal. This supersedes the earlier
343-test result that required manual cleanup. It validates the Tree projection
fix across the integrated SheetMetal/preset/RMFG suite. The handoff increment
was validated separately by the 51 unit tests, 10 native view tests and live
cabinet reproduction above; the ordinary internal-tab provider probes remain
open.

```sh
python "$OWNERSHIP_AUDIT_RUNNER" --build "$BUILD" --output "$ARTIFACTS"
# Same default run_native.py suite, with a per-test document ownership assertion.
# 344 passed (968.446 s); status True; exit 0.
```


The next ordinary SM-P05 Terra run finished in 302.157 seconds with valid folded
and flat replacement geometry, but **failed** its unchanged completion oracle:
a failed first fold was still present alongside the successful replacement. This
is not a successful tab run. It also exposed four serial schema rejections for
four missing fold fields; the previous diagnostic named only the first missing
field at each retry.

Required-field diagnostics now retain the original error code, primary message,
`required_field`, path and example, and add a bounded `required_fields` list
(with an omitted count when needed). The message lists the missing fields
together. A regression using the real fold schema failed before implementation
(**1 failed, 32 deselected, 0.37 s**); dispatcher, SheetMetal provider and sketch
batch tests then passed **50 tests in 2.25 seconds**. Seven native dispatch,
recovery and retained-source tests passed in **25.468 seconds**.

The retained-feature failure return now explains that creating another feature
does not replace the failed one. It points to the existing Modeling History
operation for explicitly discarding an exact feature after inspecting its
dependencies. The private native proof passed (**1 test, 1.734 seconds**): it
removed the selected failed source while retaining every other object. The
regression for this return failed before implementation (**1 failed, 1 passed,
3.412 seconds**). Failed parameters are still retained; no automatic deletion,
rollback, success receipt or alternative geometry implementation was added.
The complete source/dispatch/recovery check passed **38 tests in 163.716 seconds**
with normal exit. The fresh unchanged SM-P05 Terra run subsequently passed **1 test in 283.784 seconds** with normal exit. Its 42 calls included three corrected pre-mutation rejections; it left no failed fold feature. The oracle verified relief volume, both valid solids, stock bounds, stationary surrounding material, exact editable source/sketch history and final flat view.

```sh
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py -k missing_fold_fields
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sketch_batch.py
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetSourceCreation SMTests.testSheetNativeDispatch SMTests.testSheetNativeRecovery
VIBECAD_SHEET_PROMPT_MODEL=gpt-5.6-terra VIBECAD_SHEET_PROMPT_AUTH=chatgpt \
  python "$HANDOFF_RUNNER" --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.live_sheet_tab_prompt.LiveSheetTabPrompt
```


The fold repair example itself was then checked against the exact schema. This
red test failed because the generic example used `value` for a `FaceN` selector
(**1 failed, 32 deselected, 0.29 s**). The fold schema now supplies a syntactically
valid face example; actual face selection still requires inspecting the model.
All dispatcher, SheetMetal provider/surface and workspace-schema tests passed
**51 tests in 4.97 seconds**. Accepted inputs and required fields are unchanged.
A fresh local Qwen SM-P05 run now exercises the updated handoff and return text;
the earlier Qwen baseline remains untouched in its separate runtime.


The completion audit rechecked the actual assertions behind the integrated pass:
`testEditGeometry` covers cuts spanning both bend seams and reverse picks;
`testEditableSheet` covers shared cut identity, stock/bend/material changes,
Undo/Redo, reopen and document isolation; `testPresentation` checks cached node
identity, unchanged persistent state and rejection of late worker generations.
The latest integrated `switch-timing.json` records 200 cached calls averaging
0.070 ms (maximum 0.226 ms), excluding GPU frame time.

`testRMFGExport` roundtrips an actual folded STEP while flat view stays active
and rejects stale/foreign exports. `testRMFGNativeManufacturing` verifies shared
ribbon/agent configuration and blocks checkout after revision changes. The
preserved authenticated live run also reports `live_service`, `native_dispatch`,
`ready_quote`, `browser_opened` and `document_unchanged` all true, with flat view
retained (1 test, 23.099 s; exit 0). These checks establish the recorded workflow;
no order or payment was performed. The current-return Qwen probe remains open,
so the full goal has not been marked complete.


The current-return Qwen probe exposed a dispatcher defect rather than an
unsupported model request: the published line/polyline schema compacted its
operation choices into `anyOf`, but dispatch only read direct `const`/`enum`
values. Both advertised operations were rejected with an empty allowed-operation
list. The dispatcher now reads explicit choices through compact unions, checks
those choices against the operation schema, and still applies the selected
variant's exact field validation. Unfrozen operations remain rejected.

Red/green evidence for this correction:

- Real compact sketch schemas: **2 failed, 1 passed, 33 deselected (0.61 s)**
  before the fix; the related dispatcher, sketch provider, SheetMetal surface and
  continuation suites passed **56 tests in 4.92 seconds** afterward.
- The real GUI session factory reproduced the empty-operation-list failure
  (**1 failed, 5.064 s**, normal test exit). With the correction, the GUI created
  the expected line and polyline geometry. The complete native recovery,
  dispatch and view check passed **17 tests in 61.523 seconds**, normal exit.
- Both obsolete private Qwen probes were deliberately cancelled after retaining
  their traces because they used the confirmed broken dispatcher. They are
  recorded as incomplete, not passed or completed model failures. This was a
  targeted cancellation after a reproduced defect, not a process kill timer.
  A single fresh local Qwen SM-P05 run exercises the correction with the same
  ordinary prompt and unchanged geometry oracle.

```sh
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py -k compact_sketch
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sketch_provider_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetNativeRecovery SMTests.testSheetNativeDispatch SMTests.testSheetNativeView
python src/Mod/SheetMetal/SMTests/run_native.py --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.live_sheet_tab_prompt.LiveSheetTabPrompt
```


After the obsolete private probes stopped, the original strict runtime was
verified unused, its source mirror was refreshed, and the normal build completed
successfully with `--parallel 12` (exit 0). Its installed SheetMetal/VibeCAD Python
modules byte-match the current source. A new full per-test document-ownership
audit is running on that rebuilt runtime, while the current Qwen case uses a
separate immutable runtime.

```sh
cmake --build "$BUILD" --parallel 12
python "$CPP_TEST_RUNNER" --build "$BUILD" --output "$ARTIFACTS" \
  --filter 'RecomputeOriginScopeTest.*:AsyncRecomputeTest.*:MainThreadCleanupTest.*:HostWorkflowTest.*:ApplicationTest.*'
python "$OWNERSHIP_AUDIT_RUNNER" --build "$BUILD" --output "$ARTIFACTS"
```

The rebuilt C++ gate passed **43 tests**, with no failures or errors.

Qwen's next run reached sketch editing, but repeatedly mixed line endpoint fields
with the polyline operation. Rejected calls now add the exact selected operation's
allowed fields and any missing required fields to the existing error details and
message. Lists remain bounded, with explicit omitted counts; schemas accepting
pattern-based names do not claim that the fixed property list is exhaustive.
Existing error codes, paths, expectations, examples and accepted inputs remain.

The regression first failed on the missing guidance: **2 failed, 1 passed,
36 deselected (0.65 s)**. After implementation, all dispatcher tests passed
**39 tests in 0.77 s**; the related provider/surface/continuation check passed
**59 tests in 6.54 s**. The running Qwen probe uses its unchanged earlier runtime,
so its outcome does not establish the effectiveness of this new guidance.
The focused native recovery/dispatch/view check also passed **17 tests in
73.989 seconds**, with normal exit, in an isolated strict Release runtime whose
top-level SheetMetal and VibeCAD Python modules byte-match the current source.

```sh
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  -k 'mixed_line_fields or pattern_properties or explicit_omission_counts'
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sketch_provider_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py
```


The rebuilt full ownership-audited integration suite passed **346 tests in
959.433 seconds**, with normal unattended exit. It includes the real compact
sketch dispatch regression and failed-source History recovery. The integrated
200-toggle sample averaged **0.057 ms**, maximum **0.274 ms**, for cached switch
calls only; GPU frame time is excluded.

After that suite released the runtime, the final argument-guidance source and
tests were copied into the build mirror and the normal strict Release build
completed with `--parallel 12` (exit 0). Installed top-level SheetMetal and VibeCAD
Python modules byte-match current source. This last change has the separate
59-unit/17-native evidence above; the 346-test run preceded it. The unchanged
Qwen probe is still running in its separate runtime.

The final-field-guidance Qwen case then exposed an invalid repair example for
`inspect.query`: its element selector requires `FaceN`, `EdgeN` or `VertexN`,
but the generic string example was `value`. Example generation now supplies a
canonical selector only when it validates against that field's exact schema.
This demonstrates syntax; it does not claim that the example exists on the model.
Four regressions failed before the fix (**4 failed, 39 deselected, 0.41 s**).
The dispatcher and related provider/surface/continuation suites passed
**63 tests in 5.95 seconds** afterward, using the same combined unit command
above. Accepted schemas and target-resolution behavior are unchanged.


Final completion evidence: the private Qwen probes were stopped deliberately,
with their traces preserved as incomplete, after the owner clarified that Qwen
is a usability gauge rather than a perfection gate. The final selector-example
change was then incorporated through a normal strict Release build with
`--parallel 12` (exit 0). Runtime/source Python parity, the 346-test integrated
pass, focused native pass, Terra tab and cabinet handoff, and authenticated live
RMFG results were rechecked. No owner instance, build, tail or document was
modified by these private probes.


## Folded-wall sketch cuts and input attachments

Native profile cuts now accept a closed sketch authored on either planar skin
of a folded sheet, as well as the existing developed-plane sketches. Geometry
preparation maps a detached profile through the existing unfold correspondence;
it does not move or detach the authored sketch. The linked sketch remains
editable from folded and flat views. Unsupported or ambiguous correspondence
is rejected rather than projected onto an arbitrary face.

A sketch supported by the input sheet is a valid dependency of a new downstream
cut. Creation and ribbon selection now distinguish that from a sketch depending
on the resulting cut, which remains rejected as a real cycle. The older API
that edits a sheet in place retains its dependency checks.

Red/green evidence: the folded-wall cut failed as Invalid and input-sheet
attachment was incorrectly rejected before the fix (two regressions). A second
regression reproduced failed coordinate editing on both skins. The resulting
27-test native run passed in 72.108 seconds, covering both skins, editable sketch
coordinates, relief-cut internal bending, upstream edits, Undo/Redo, save/reopen,
real-cycle rejection, stale work and the pre-existing flat-sketch paths. The
rebase also exposed a save/reopen fixture closing before document ownership was
released; the fixture now waits for native `isClosable()` before closing.

```sh
cmake --build "$BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetProfileHistory SMTests.testSheetFoldSource \
  PartDesignTests.TestModelTreeBrowser.TestModelTreeBrowser.test_hidden_source_and_mixed_assembly_occurrences_survive_save_reopen
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_document_restore_rendering.py \
  src/Mod/VibeCAD/vibecad_tests/test_partdesign_history_presentation.py \
  src/Mod/VibeCAD/vibecad_tests/test_set_view_visibility.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sketch_provider_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py
```

The strict Release build uses warnings as errors and completed with exit 0.
The unit command passed **107 tests in 5.60 seconds** after its registry check
caught and prompted shortening an overlong tool description. No tool fields,
variants or defaults changed. The rebased baseline is main `01bae317`.

A private copy of the reported cabinet already contained the agent's workaround.
Restoring its three relief regions to the folded wall produced valid folded and
flat solids and the same flat volume (968705.564596044 mm³) as that workaround.
The owner's saved document was not modified. This verifies the cut workflow;
it is not verification of a finished cabinet or load capacity.


The final native receipt/ribbon check plus the two private cabinet checks passed
**20 tests in 43.062 seconds**. The attached-profile case exercises the same
asynchronous prepare/start path as Native tools. The tracked portion is:

```sh
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$BUILD" --output "$ARTIFACTS" \
  SMTests.testSheetProfileHistory.TestSheetProfileHistory.test_profile_attached_to_input_sheet_is_not_a_result_dependency_cycle \
  SMTests.testSheetNativeEdit \
  SMTests.testSheetRibbonHistory.TestSheetRibbonHistory.test_sketch_creation_and_suppression_keep_the_original_profile \
  SMTests.testSheetRibbonHistory.TestSheetRibbonHistory.test_missing_sketch_keeps_its_panel_open_for_replacement
```

The private runner additionally selected
`inspect_cabinet_folded_profile.InspectCabinet` (saved-workaround and folded-wall
three-relief checks). An earlier invocation also named a nonexistent test module;
that invocation was not a pass. The corrected selection above, with the final
shortened description, completed normally. The final strict rebuild passed and
all changed installed Python modules were byte-checked against source.


## Parameters handoff with Assembly BOMs

A saved cabinet contained an `Assembly::BomObject`, a subclass of
`Spreadsheet::Sheet`. Parameters snapshot discovery correctly included it, but
passed it to the exact base-sheet identity reader. That mismatch raised a
`TypeError` while building the next agent turn after a Parameters workspace
switch. It also prevented a new user request while Parameters remained active.

The snapshot now reads generated spreadsheet subclasses with their actual type
and bounded content metadata, and directs editing back to the owning workbench.
Normal spreadsheet summaries, identity hashes, mutation target checks, formulas,
formatting and CSV paths remain unchanged. Assembly BOM creation, recomputation,
columns, quantity aggregation, ownership and persistence remain unchanged.
Generic Parameters tools do not gain write authority over generated BOMs.

Two new regressions reproduced the TypeError before implementation. The final
unit selection passed **53 tests in 0.51 seconds**:

```sh
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_parameters_snapshot.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_snapshot.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_assembly_bom.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_assembly_provider_state.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py
cmake --build "$BUILD" --parallel 12
python "$GUI_GATE_RUNNER" \
  src/Mod/VibeCAD/vibecad_tests/native_assembly_bom_gui_integration.py \
  VIBECAD_NATIVE_ASSEMBLY_BOM_GUI_OK --build "$BUILD" --output "$BOM_ARTIFACTS"
python "$GUI_GATE_RUNNER" \
  src/Mod/VibeCAD/vibecad_tests/native_parameters_gui_integration.py \
  VIBECAD_NATIVE_PARAMETERS_GUI_OK --build "$BUILD" --output "$PARAMETERS_ARTIFACTS"
```

Both compiled-GUI gates passed with normal exit. The private gate launcher uses
the profile/environment isolation of `SMTests/run_native.py`, executes the named
integration script, and requires both exit 0 and its success marker. There is no
process timeout. The BOM gate covers two BOMs, quantity aggregation, custom
properties/columns, parts filtering, stale calls, idempotency, Undo/Redo,
save/reopen, ownership and unchanged component placements. It additionally
checks the Parameters snapshot and verifies that reading it leaves the BOM
table unchanged. The Parameters gate covers native/human creation, values,
formulas and dependent dimensions, aliases, merging/splitting, formatting,
stale ranges, selection, CSV import/export, History, Undo/Redo and save/reopen.

The existing GUI fixtures needed ownership-aware close waits. The Parameters
fixture also performed a direct document edit and then reused a stale Native
turn; it now constructs a fresh turn at that explicit external-edit boundary.
The initial gates failed at those fixture boundaries; production ownership and
revision guards were not relaxed. The final strict Release build with warnings
as errors and 12 jobs passed.

On a private copy of the reported cabinet, the Parameters snapshot now reads
its generated parts list (21 nonempty cells, A1:E5) without throwing. The saved
owner document remains unchanged. This fix does not retarget its assembly
links, reposition its drawers, or establish clearance or manufacturing readiness.
A separate two-cut visibility probe retained hidden predecessors across four
workspace switches and save/reopen; the reported saved visibility state has not
yet been reproduced as a code defect.

## Saved sheet recompute and Assembly edit restoration

Leaving or editing a restored cut sketch could recompute its downstream cut
while a clean upstream sheet still lacked its transient bend mapping. Native
dependency ordering does not execute a clean ancestor merely because its Python
cache was cleared on restore. Cut recompute now rebuilds that missing mapping
under native recompute ownership, using the existing detached preparation path.
It reuses warm ancestors, verifies the entire saved fingerprint chain and current
inputs, and publishes only transient caches. Persisted upstream shapes and
History are unchanged. Interactive inspection and view switching remain free
of geometry work; stale inputs and invalid geometry are still rejected.

Workspace switching also used a generic edit reset to deactivate an Assembly.
That retained Assembly's request to restore itself after a later sketch edit.
A live Assembly unexpectedly reactivated; a deleted one produced `cannot edit
detached object`. Switching now uses Assembly's explicit deactivation action,
which clears that restore request as the normal UI action does.

Red tests reproduced both cold profile/hole recompute failures, unexpected
Assembly reactivation, and the exact detached-object Report view message.
Green validation used private GUIs and profiles, with no process timeout:

```sh
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$BUILD" --output "$RECOMPUTE_ARTIFACTS" \
  SMTests.testSheetPreparation \
  SMTests.testSheetProfileHistory \
  SMTests.testSheetCutHistory
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$BUILD" --output "$WORKSPACE_ARTIFACTS" \
  SMTests.testSheetNativeView
PYTHONPATH=src/Mod/VibeCAD python -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_authority.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py
cmake --build "$BUILD" --parallel 12
```

The recompute selection passed **53 tests in 160.533 seconds** and the workspace
selection passed **12 tests in 43.965 seconds**. Unit checks passed **18 tests in
22.25 seconds**. Coverage includes restored sketch edits, mixed predecessor
chains, warm-cache reuse, worker-thread geometry, unchanged upstream results,
Undo/Redo, cancellation, stale publication rejection, saved-hash mismatch,
suppression, repair, and repeated sketch open/close after Assembly deactivation
and deletion. Invalid saved fingerprints still fail without replacing outputs.

A private copy of the reported cabinet passed four alternating open/close
cycles on its two relief sketches, with no invalid features and unchanged folded
and flat volumes (**1 test in 168.482 seconds**). This exercises the reported
sequence without changing the owner's document; it does not establish assembly
clearance or load capacity. The strict Release build uses warnings as errors
and 12 jobs; installed changed modules are checked byte-for-byte against source.

The final installed-build selection passed **7 tests in 30.516 seconds**. It also
checks compatibility for existing direct callers consuming a warm mapping; only
missing-cache rebuilding requires recompute ownership. An intermediate guard
incorrectly rejected that warm read; its added assertion failed before the guard
was narrowed. The final command was:

```sh
PREPARATION=SMTests.testSheetPreparation.TestSheetPreparation
VIEW=SMTests.testSheetNativeView.TestSheetNativeView
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$BUILD" --output "$INSTALLED_ARTIFACTS" \
  "$PREPARATION.test_ready_mapping_does_not_run_geometry_again" \
  "$PREPARATION.test_reopened_profile_edit_rebuilds_cold_ancestors_in_native_recompute" \
  "$PREPARATION.test_reopened_circle_edit_rebuilds_cold_mixed_predecessors" \
  "$PREPARATION.test_reopened_profile_can_enter_and_leave_sketch_without_preparing_sheet" \
  "$PREPARATION.test_recompute_preparation_does_not_bypass_saved_hash_or_dirty_inputs" \
  "$VIEW.test_deactivated_assembly_is_not_restored_after_a_later_sketch_edit" \
  "$VIEW.test_deleted_deactivated_assembly_is_not_an_edit_restore_target"
```
