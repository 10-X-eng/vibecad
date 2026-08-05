# VibeCAD Tool Outcome Notes

Live evaluation document: `test9.FCStd`
Evaluation client: Codex through VibeCAD MCP
Started: 2026-08-04

## Purpose

Track whether each tool outcome gives an AI the smallest complete set of facts needed to understand success, repair failure, and choose the next action. Keep response-quality problems separate from CAD/API defects.

## Outcome quality targets

A successful write should normally return:

- `ok`
- readable `document/domain/program` target and current revision
- created, changed, and deleted output identities
- elapsed time and a compact timing breakdown when slow
- warnings only when they affect the requested result

A failed write should normally return:

- one plain-language error
- failure code and exact failing operation/graph node
- the measured condition that violated the contract
- one concrete correction, using the API's actual vocabulary
- working program/revision and whether accepted outputs were preserved
- detailed traceback, raw logs, filesystem paths, and complete graph timing only when explicitly requested

## Findings

### `component_catalog_search`

Useful:

- Exact component reference
- Label, output type, owning program/revision when an edit needs them
- Published interface names and compact connector frames
- Pagination state

Low-signal or unrelated:

- Full assembly contract prose for a simple one-result identity lookup
- Repeated schema names on every nested object
- Full 4x4 matrices when origin/axis/x-direction are already returned
- Saved-document scan counts during an active-document query
- An unrelated corrupt `jet-engine.FCStd` warning on every search
- Storage/provenance fields that do not change the next action

Recommended outcome:

- Default to compact references plus interface names.
- Return full connector metadata only for `detail='full'` or an exact component query.
- Report saved-file indexing problems through one catalog-health warning, not on every unrelated search.

### `core_capture_view_screenshot`

Useful:

- The image
- Document and framed object identities
- Whether capture and framing succeeded
- A compact visual warning when the frame is blank, clipped, or unchanged

Low-signal:

- Artifact directory and internal file path in the ordinary result
- Complete camera state before/requested/resolved/effective
- Every internal capture stage
- Repeated temporary-view restoration details
- Pixel statistics when visual postconditions pass
- Full fingerprint metadata unless duplicate detection matters

Recommended outcome:

- Return image, `ok`, framed objects, elapsed time, and attention flags by default.
- Put stage traces, pixel statistics, camera matrices, paths, and fingerprints behind `diagnostics=true`.

### `vibescript.create_program` failure

Observed failure: a one-point `api.hole(..., through_all=True)` removed zero material even though diagnostics said its cut direction could intersect the body's bounds.

Useful:

- `api.hole did not remove material on its base feature`
- Failing graph node and operation
- Removed material = 0
- Profile origin/normal, cut direction, and base bounds
- Exact readable program target and working revision for `edit_source`
- Confirmation that the document was unchanged

Low-signal:

- Hundreds of repeated `Recompute` and `Processing` progress lines
- Full Python traceback for a handled domain postcondition
- Attempt and staging filesystem paths
- Repeated program/source/revision fields in several nested objects
- Empty candidate lists and normalized objects
- Complete worker phase history for a sub-second deterministic failure

Missing or misleading:

- The recommended direction change was not sufficient: the reported ray already intersected the body. The actual reliable repair was replacing the one-point Hole feature with a circle sketch and subtractive extrusion.

Recommended outcome:

- State that the cutter ray intersected the bounds but produced no topology change.
- Suggest the circle-profile subtractive operation when the one-point native Hole path fails.
- Include raw progress and traceback only on request.

### `vibescript.edit_source` success

The decision-complete response is small:

- `ok`
- readable program target
- accepted revision
- affected output identities

This is the current positive reference for successful write outcomes.

### Stable component-reference authoring

Observed sequence:

1. A normal object schema was rejected with `arbitrary object inputs are forbidden; use a stable reference`.
2. Adding `x-vibecad-reference` without member declarations was rejected because `document_uid` and `object_name` must be declared.
3. The complete reference schema was accepted.

The complete copyable schema does exist in `vibescript.read_api` under `model_operating_contract.input_schema_templates.stable_reference_property`. The initial authoring mistake was therefore avoidable after a complete API-contract read. However, the validation outcomes still reveal one constraint at a time and do not point to or include that existing template.

Recommended outcome:

- The first schema rejection should include the complete `stable_reference_property` template already published by `read_api`.
- `component` and `instances` focused API details should link directly to that template.
- Do not require multiple validation calls to reconstruct one known schema.

### Lightweight component-link publication

Creating three occurrences of one already-open imported motor took approximately 79 seconds. The result objects are lightweight links, so this cost should not scale with the source B-rep size or occurrence count.

Recommended instrumentation and fix:

- Report reference capture, graph evaluation, link creation, candidate finalization, and publication timings separately.
- Capture/fingerprint one referenced definition once per revision and reuse it for every occurrence.
- Do not serialize, validate, or recapture the imported B-rep for each link.

### Assembly write in progress

The first seven-component, six-joint, 61-frame simulation candidate remained in the worker for more than two minutes. Some solver and simulation cost is legitimate, but the tool produced no phase update while the caller waited.

Required outcome data:

- Current phase and graph node during long calls
- Reference capture time
- Native solve time
- Per-frame simulation time and pose count
- Candidate finalization and publication time

The call became obsolete after a physical mountability error was identified, but force-cancelling it could leave a retained native transaction. Safe cancellation and supersession need explicit lifecycle semantics.

Observed terminal defect:

- The MCP request eventually ended without any parseable structured outcome.
- No Assembly was published.
- The immediately following `read_source` call blocked for approximately 102 seconds.

This indicates that request completion, worker completion, and document-lock release are not represented by one reliable lifecycle state.

### Part Design publication failure

Editing the accepted base into an open service-frame design built the candidate but failed publication twice with:

`A final resource dependency follows its consumer`

The response does not identify:

- the consumer object,
- the resource/dependency object,
- the dependency edge,
- the final ordering received,
- or the source graph nodes responsible.

`assembly_list_structure` confirmed that no live Assembly existed, so a published Assembly consumer was not the cause. The accepted old base remained live and the revised candidate remained recoverable.

Recommended outcome:

- Name both exact native objects and their source graph IDs.
- Return the offending dependency edge and the expected order.
- Distinguish stale-publication identity conflicts from an invalid candidate graph.

Isolation result:

1. Removed measurement checks: same failure.
2. Removed material and appearance: same failure.
3. Removed published interfaces: same failure.
4. Returned the feature before the final cut: same failure.
5. Returned the feature before the upper plate: same failure.
6. Returned the plate before columns: same failure.
7. Truly truncated the source to only its first sketch and pad: same failure.
8. Created a completely new 10 x 10 x 2 mm sketch-and-pad probe in a new program: same failure.

Conclusion: after the long Assembly request ended without a structured result, the active document's global Part Design history/publication ordering became invalid. The failure is not caused by the robot base geometry, checks, materials, appearance, interfaces, unreachable graph nodes, stable output replacement, or a previously published Assembly (none exists). Any subsequent Part Design publication fails during final block ordering.

Required code investigation:

- Audit Assembly candidate teardown and rollback for retained timeline/history registrations.
- Audit final block dependency sorting across domains after a failed or result-less Assembly publication.
- Verify that failed candidates unregister every provisional consumer/resource node.
- Add an invariant check immediately after teardown and report the exact retained node and owner.
- Add a regression test: fail/cancel an Assembly candidate, then publish a new independent Part Design sketch-and-pad in the same document.

### Missing document lifecycle surface

The MCP surface cannot independently complete a rebuild verification loop because it has no tools to list, open, activate, save, or close documents.

Required universal operations:

- List open and recent documents with exact UID/path and dirty/transaction state.
- Open an exact document path and wait for a terminal opened/failed state without freezing the GUI.
- Activate an exact open document.
- Save or save-as with explicit terminal status.
- Close an exact document, refusing with one actionable transaction/unsaved-state diagnostic.

These operations must remain independent of the active authoring workbench.

Implemented and live-checked:

- `vibecad.manage_document(action='open', path=...)` opened `test9.FCStd` in
  3.9 seconds and returned only the active document name, label, physical path,
  modified state, and object count.
- Opening is now independent of the active workbench and no shell/Python CAD
  operation is required.
- The opened file immediately reported `modified=true`; determine whether this
  is an intentional restore/migration mutation or presentation-only churn before
  changing that result.

### Startup document recovery

The native recovery dialog has two blocking stages: **Start Recovery** performs
the work, then the same button becomes **Finish** and the modal remains open.
Automation that presses only the first stage is still blocked.

Implemented outcome:

- `vibecad.recover_documents` detects the exact native dialog, executes both
  stages, returns every native document/status row, and is an exact no-op when
  recovery is absent.
- Recovery continues to use FreeCAD's native implementation; the MCP tool does
  not parse, copy, or recreate recovery files.

### Readable VibeScript program identity

Internal source GUIDs were not actionable to the model. Source lifecycle tools
now target one readable `document/domain/program name`, while revision hashes
remain where optimistic concurrency requires them. Internal persistence IDs are
removed from provider-visible source inventories and outcomes.

Live result:

- `vibescript.read_source({})` returned nine concise, selectable robot programs
  with copy-ready follow-up actions and no internal source GUIDs.
- One focused source read returned the complete source, current state, expected
  outputs, and exact edit/build arguments without full topology or logs.

### Lightweight imported-component interfaces

Observed robot blocker:

- `api.component` can publish a lightweight imported motor occurrence but cannot
  declare its exact local mounting frame.
- Assembly correctly rejected a fixed motor mount because the authored robot
  structure declared `NEMA23_FLANGE` while the linked motor side had no connector
  contract.
- The available alternatives are both wrong: omit the explicit contract, or copy
  the vendor BREP through `from_object`/`publish` and pay a large rebuild cost.

Implemented correction:

- Add explicit `interfaces=` to Part Design `api.component` and `api.instances`.
- Permit only origin/frame selections because lightweight links deliberately do
  not copy source BREP for topology queries.
- Publish those connector frames in the normal output-local interface table so
  Assembly consumes the same exact contract as native Part Design bodies.
- Keep one local interface definition reusable across repeated imported motors.

Verification:

- A lightweight imported component published a named `Mount` frame with a
  `FIXTURE_MOUNT` connector contract.
- The connector survived native publication, Assembly containment, source
  rebuild, save/reopen, placement updates, and semantic deletion without copying
  the referenced BREP.
- Geometry-dependent selections are rejected for a lightweight link; exact
  origin/frame declarations remain available because they do not pretend the
  source topology was copied.

Live catalog defect and resolution:

- The first accepted motor occurrence stored `MotorMount` on its program root,
  but `component_catalog_search(detail='full')` omitted every interface from the
  occurrence. Assembly therefore still could not select the accepted connector.
- Root cause: interface resolution recognized only an `App::Link` whose linked
  object was a scripted publication. A lightweight imported occurrence links a
  vendor/native definition and is itself the stable managed output carrier.
- Exact managed `component_link` occurrences are now recognized as interface
  carriers; ordinary links are unchanged. Native integration proves direct
  interface resolution and catalog discovery, including the connector contract.

### Failed-program repair identity

Observed during native integration:

- A failed `create_program` retained a working candidate and revision, but the
  normalized failure moved its readable `program` reference into nested
  diagnostics.
- The advertised next step, `read_source` followed by `edit_source`, was
  therefore impossible without rediscovering the candidate through another
  inventory call.

Resolution:

- Failed source writes preserve the exact readable `document/domain/name`
  program reference as actionable top-level continuation data.
- The reference is derived from the submitted program name even when execution
  fails before the domain result contains a program label.
- Native integration now proves one failed create can be read, corrected in
  place, built, and deleted using only that readable reference and its revision.

### Document save state

Observed:

- `vibecad.manage_document(action='save')` completed successfully, but both its
  result and the following `action='list'` still reported `modified=true`.
- The file reopened with all 140 objects, so the save itself completed; the dirty
  flag is not a reliable postcondition for deciding whether the requested file
  write finished.

Root cause and resolution:

- The MCP host called `App::Document.save()`, which writes the file but does not
  clear `Gui::Document.Modified`.
- FreeCAD's native GUI Save command explicitly clears that GUI flag after the
  successful write; MCP now uses the same completion contract.
- The result includes `save_completed=true`, and its post-save document summary
  reports `modified=false`.
- A document may still open modified when native link-stamp restoration detects
  a changed external dependency; that is a separate native state and is not
  silently cleared by saving code before the write completes.

Live MCP verification on the rebuilt host returned
`save_completed=true, modified=false` for `test9.FCStd`.

Lifecycle invariant established during live use:

- Every intentional close or application shutdown must first call
  `vibecad.manage_document(action='save')` for each modified document and verify
  `save_completed=true, modified=false`.
- Recovery is reserved for an actual crash; it is not a substitute for saving.
- A recovered 140-object document and the authoritative 192-object robot
  document were both open with the same physical `test9.FCStd` path. The
  recovery copy was saved safely as `test9-recovery-140.FCStd` before either
  document was allowed to close.
- `action='list'` now reports duplicate physical paths explicitly, and save
  refuses an ambiguous target until one document is saved to a distinct path.
- `action='new'` creates and immediately saves a clean document at a new exact
  `.FCStd` path, so a fresh conversation anchor never begins as an unnamed,
  unsaved native document.

### Result-only deletion performance

Observed before linked-component identity capture was corrected:

- Total: 118.1 seconds
- Worker rebuild: 43.1 seconds
- Candidate finalization: 73.1 seconds
- Actual publication change: 0.8 seconds

Resolution:

- The affected program contained only `api.component`/`api.instances` outputs.
  Those programs now bind exact document/object identity and no longer copy,
  serialize, validate, or classify the imported BREP during a rebuild.
- `vibescript.delete_output` still validates and executes the complete revised
  source. This is deliberate: the call changes both source and output contract,
  so deleting a live object without evaluating the submitted source would make
  accepted CAD disagree with its editable program.
- Pure linked-occurrence rebuilds measured 0.54 seconds for the same imported
  motor case. Geometry-reading programs retain the authenticated BREP path.

## API/runtime defects discovered

Defects found during the run, including their current status:

1. A single-point native `api.hole` could remove no material despite an intersecting through-all cut direction. Fixed: explicit direction, centered through-all, and cuts across a fused material step are covered by native Part Design integration.
2. `vibescript.delete_object` was advertised but failed because `VibeCADObjectDeletion` was absent from the packaged runtime. Fixed: the helper is now in the release CMake package and a packaging regression pins it there.
3. Result-only linked-component deletion rebuilt imported geometry. Fixed by exact metadata-only component capture while retaining source validation.
4. Catalog searches repeatedly surfaced an unrelated corrupt saved document. Fixed: ordinary searches are quiet, exact path searches return their diagnostics, and inventory reports one compact health warning.
5. Publishing three lightweight links to one open imported component took approximately 79 seconds. Fixed; the measured path is now 0.54 seconds.
6. Long Assembly writes provide no compact phase/progress outcome while the client waits.
7. Assembly request completion can occur without a structured terminal result while later read-only calls remain blocked.
8. Part Design publication can report an unnamed resource-order failure that is impossible to repair from the outcome alone.
9. A failed/result-less Assembly attempt can leave global publication ordering invalid so every later independent Part Design program fails.

## Design defect discovered during live verification

The initial J1 motor was placed beneath a base whose ground datum is the same plane as the motor flange. The housing therefore intersects the bench space and is not mountable on a flat surface. This is not a tool implementation defect, but it demonstrates a missing design-verification step. The correction is an open column base with an upper internal motor plate: the motor body hangs inside an accessible frame and its shaft rises into the yaw turntable.

## Build-test status

- Imported NEMA23 motor preserved.
- Obsolete bracket source and its managed outputs removed.
- Robot base created.
- Yaw turret and shoulder yoke created.
- Upper arm created.
- Forearm with integrated gripper created.
- Three lightweight motor occurrences: complete.
- Seven-component Assembly with six exact joints: complete.
- Three-axis motion: generated and visually demonstrated across multiple frames.
- Final exact-object isometric capture: one coherent, mountable mechanism.

## Resolved during this pass

### Cross-program History corruption

Instrumented History diagnostics identified the exact edge:

- consumer: `VibePartdesign_ad59bfb1_Program`, semantic root index 17
- dependency: `VibePartdesign_ad59bfb1_J1Motor`, semantic root index 19

The earlier Part Design program stored its later reusable component occurrences
in a normal `App::PropertyLinkList`. FreeCAD correctly interpreted that registry
as a forward modeling dependency, so every later History operation was invalid.
It was not retained Assembly teardown state.

Resolution:

- Store authoritative occurrence ownership as exact object names.
- Keep the legacy LinkList property present but empty.
- Resolve top-level occurrence ownership through the exact persisted program ID.
- Migrate existing documents on open and before publication.
- Preserve a legacy read path for compatibility.

The original failed `test9` sketch-and-pad probe then built successfully in
0.83 seconds. A focused regression now covers component publication, migration,
an independent later Part Design program, save/reopen, Assembly adoption, and
deletion.

### Lightweight component references

Pure Part Design `api.component` / `api.instances` programs were needlessly
copying, serializing, hashing, and classifying the complete source BREP. A linked
occurrence consumes exact document/object identity, not detached topology.

Resolution:

- Pure linked-occurrence programs use an exact metadata-only reference contract.
- Any geometry-reading API keeps the authenticated detached-BREP path.
- The revision binds document UID, object name, native type, and semantic
  reference-contract digest.

Measured on the real imported `CPM-231x-LS` motor in `test9`:

- Before: approximately 79 seconds for three occurrences.
- After: 0.54 seconds total.
- Reference capture: 0.0007 seconds.
- Candidate finalization: 0.0045 seconds.
- Publication: 0.084 seconds.

The write response for this component-only case was appropriately compact. The
unrelated corrupt `jet-engine.FCStd` diagnostic is now absent from ordinary
searches. An exact search for that document returns its error; initial component
inventory reports one compact catalog-health warning.

### Human-readable source identity

The provider-facing lifecycle previously repeated a 32-character persistence
UUID as `source_id` in context, every read/write result, and every subsequent
tool call. That identifier conveys no CAD meaning.

Resolution:

- Programs are addressed as exact readable `document/domain/name` paths, such
  as `test9/partdesign/Robot Joint Motors`.
- `vibescript.read_source` with no program lists every editable, failed, and
  unbuilt program with its status, outputs, and a copy-ready read action.
- Internal UUIDs remain private persistence keys and are stripped from model
  context, MCP results, provider results, and Anthropic compaction state.
- Successful writes return one compact output summary and exact next actions;
  full face/edge details remain available through `read_geometry` rather than
  being duplicated in every build response.

Live MCP verification after rebuilding returned all nine `test9` sources by
readable path with no GUIDs, including two failed/unbuilt programs and their
empty output state. The result was immediately usable without prior context or
identifier lookup.

### Screenshot result noise and MCP image delivery

A live isometric `frame='all'` capture produced a correct 1280x679 image, but
its text result repeated camera state, every framing object, every excluded
origin plane, every internal stage, hashes, temporary restoration state, and
pixel-analysis details. The actual useful facts were only: capture succeeded,
what was framed, the image, whether it was blank/duplicate, and any visual
attention flags. MCP also returned no image content because the result did not
publish the existing image-attachment marker.

Resolution:

- Keep the complete stage record privately for duplicate detection and host
  state.
- Return one concise target/camera/image/observation envelope.
- Attach the PNG directly to both MCP and internal provider tool results.
- Collapse failed captures to their exact failure stage instead of returning
  all successful setup stages.

That first capture exposed the base-motor mounting defect. The final accepted
revision replaces it with an open service-frame base and internal motor plate;
the exact-object Assembly capture shows one coherent robot with the three
motors and all moving links in their intended mountable arrangement.

### Long source writes and MCP timeout

The accepted seven-component Assembly took longer than the MCP client's
300-second request window. The host kept computing, accepted the source, and
published the Assembly after the caller had already received a timeout. A
follow-up source read then queued behind the same document work. The CAD result
was correct, but the request lifecycle falsely looked failed and encouraged a
duplicate write.

Resolution:

- Every VibeScript source mutation starts one shared background operation and
  immediately returns a readable `operation-N` handle.
- `vibescript.read_operation` returns phase/progress while running and the exact
  original success or failure when terminal.
- MCP and the internal agent use this same provider runner; it is not an
  MCP-only fork.
- A second mutation receives one explicit active-operation result and the exact
  status call to make next.
- Document open/close/recovery remain MCP host controls because the internal
  assistant's conversation is bound to its active document.

Future performance note: the long Assembly worker saturated one CPU core.
Profile deterministic solve/simulation/reference stages for safe parallel CPU
work first; evaluate GPU acceleration only for numerically equivalent kernels
whose exact CAD/solver contracts can be preserved.

### Assembly structure classification

`assembly.list_structure` reported 12 components for the verified seven-part
robot because it counted linked definitions and motion/simulation resources as
placed mechanism components.

Resolution:

- Count independently placed occurrences only.
- Exclude linked definitions when their occurrence is present.
- Exclude explicit joint, motion, simulation, diagnostics, BOM, exploded-view,
  measurement, check, and dependency-anchor resource types.
- Use native types and output contracts only; no label heuristics.

### Simulation observation and exit

Playback originally had two automation defects: the agent could start it but
could not safely close it, and screenshots could only sample whatever frame the
wall-clock timer happened to reach.

Resolution:

- `assembly.stop_simulation` closes only an explicitly marked saved read-only
  Assembly player. It restores placements, visibility, and camera and is an
  idempotent no-op when no player is active.
- `assembly.play_simulation` now supports `autoplay=false` plus an exact
  `time_seconds` so the agent can inspect stable, reproducible frames.
- Both are on the shared Model/Assembly surface for MCP and the internal agent.
- Native GUI tests cover play, exact-frame inspection, stop, repeated stop,
  state restoration, save/close/reopen, and resumed playback.

### Screenshot hidden-object contamination

`core.capture_view_screenshot(frame='all')` temporarily exposed hidden source
bodies, producing a duplicate static robot beside the animated occurrences.
Framing the seven exact Assembly components produced one correct mechanism.

Resolution:

- `frame='all'` derives its fit from currently visible top-level model targets.
- Its temporary fit isolation may hide unrelated objects, but cannot reveal a
  hidden Body Tip, feature, linked definition, or container ancestor.
- Exact-object framing retains its existing explicit reveal behavior. No tool
  argument or schema was added.
- A regression records visibility during `fitAll()` and proves every object is
  restored to its original state afterward.

### Native integration output noise

The complete Part Design integration passed, but its terminal stream contained
thousands of raw `Recompute`, `Processing`, import-percentage, and repeated BREP
version lines. These are useful for a local developer console, not for an AI
tool outcome. Provider/MCP results must continue to expose only the compact
operation progress envelope and terminal structured result; raw native console
streams belong behind explicit diagnostics.

Verification after the shared lifecycle/playback changes:

- Complete local build: passed.
- VibeCAD Python suite: 624 passed, 4 skipped.
- Native Part Design API integration: passed, including failed-source repair.
- Native Assembly playback GUI gate: 4 passed.
- Native model browser/body/sketch/occurrence GUI gate: 23 passed.
- Diff whitespace check: passed.

### False Assembly staleness after reopen

Live MCP validation reopened `test9.FCStd` with the accepted seven-component
robot intact, but `read_source` marked all Assembly outputs stale because
`VibePartdesign_ad59bfb1_J1Motor._LinkTouched` changed. `_LinkTouched` is a
hidden, non-persistent Link notification emitted during execute/restore so view
providers can refresh. It is not component geometry, placement, interface, or
contract state.

Resolution:

- Treat `_LinkTouched` like `_GroupTouched`: it never invalidates a source
  snapshot and never marks dependent programs stale.
- Continue invalidating on the real native property notification that caused a
  meaningful model or contract change.
- Apply the same filter before reference-cache eviction so a reopen cannot
  discard authenticated detached geometry for a presentation pulse.
- Native GUI save/reopen coverage now asserts that no accepted VibeScript
  output becomes stale when linked occurrences restore.

### Live post-restart robot validation

- `test9.FCStd` reopened through MCP with 192 objects.
- `assembly.list_structure` returned exactly 7 components, 6 joints, and one
  grounded base; linked definitions and simulation resources were no longer
  counted as mechanism components.
- Exact-object isometric capture returned one connected foreground component
  and clearly showed the open service-frame base, yaw turret, dual-plate arm,
  forearm/gripper, and three motor occurrences.
- Stable playback at exactly 2.5 s returned frame 25 of 62 in 0.23 seconds.
- The exact-component capture at that held frame visibly differed from the
  solved baseline, proving saved motion survives reopen without wall-clock
  screenshot timing.

### Live asynchronous lifecycle validation

- `vibescript.create_program` returned `operation-1` in about 0.7 seconds,
  proving source mutation no longer holds the MCP request open for the worker.
- The current Codex MCP client still had its pre-rebuild tool-name cache, so
  `vibescript.read_operation` and `assembly.stop_simulation` were not callable
  even though the rebuilt server advertises them to a fresh client. A client
  reconnect is required before their terminal happy and unhappy paths can be
  validated through MCP.
- Obsolete `Base Publication Probe`, `Open Frame Robot Base`, and `History
  Ordering Probe` programs were submitted as `operation-2` through
  `operation-4` and removed. The final source inventory contains exactly six
  accepted robot programs: five Part Design modules and one Assembly program.
- MCP save reported `save_completed=true` and `modified=false` for
  `/home/robit/Documents/test9.FCStd`; the document contains 192 objects.

Document lifecycle remains intentionally MCP-only because the opened document
anchors the external conversation. Authoring, inspection, Assembly, simulation,
and operation-status tools remain one shared surface for MCP and the internal
agent.

### Multiple-instance MCP endpoint collision

A second VibeCAD process inherited the persisted MCP-enabled preference while
the primary instance owned `127.0.0.1:8765`. Uvicorn attempted the bind inside
an asynchronous task and printed a full `SystemExit` traceback plus "Task
exception was never retrieved" even though the CAD process remained usable.

Resolution:

- Reserve the TCP listener synchronously before starting uvicorn and pass that
  exact socket into the server.
- A collision now produces one actionable controller error stating that
  another VibeCAD instance may own the endpoint, with no asynchronous traceback.
- A focused test binds a real ephemeral listener and verifies the duplicate-bind
  failure. The native Assembly playback gate was rerun while the primary MCP
  server was active; all four tests passed and the traceback was absent.

### Automatic simulation collision evidence

Simulation previously proved that placements changed but gave neither a human
nor an agent evidence that the moving mechanism remained physically usable.
Requiring a separate verification call would be easy to omit and would leave
the saved simulation without a durable safety result.

Implemented behavior:

- Every VibeScript Assembly simulation evaluates collision automatically; the
  source API and tool arguments are unchanged.
- Solver-output poses use authenticated BREP-derived OCCT collision meshes with
  an AABB broad phase, surface proximity, and exact solid-containment witnesses.
  This detects interpenetration without rebuilding expensive common solids at
  every frame. The result explicitly says when interference volume was not
  measured; it never presents a mesh hit as an exact volume.
- A detected collision is diagnostic, never a publication failure. If any
  geometry preparation or collision evaluation fails, the simulation still
  publishes with `status='incomplete'` and an authenticated warning instead of
  pretending the trace is clear.
- The host authenticates the retained trace, BREP identities, warning list, and
  derived summary before publication; it does not repeat the worker's OCCT
  collision pass on the GUI thread. Imported solids, native Part Design outputs,
  and modeled-thread fasteners use the same geometry path.
- Saved simulation objects retain collision-free state, affected frame and pair
  counts, analysis completeness, warning count, and volume-completeness state.
  The existing player shows red on colliding frames, amber when collision occurs
  elsewhere or analysis is incomplete, and green for a fully clear trace.
- `assembly.play_simulation` returns a compact trace summary plus the component
  pairs colliding at the displayed frame; internal broad-phase counters and
  repeated event records are not duplicated in the ordinary tool outcome.

Verification:

- Two separated 10 mm native boxes remain collision-free.
- Moving one box into a 5 mm overlap reports one deterministic two-frame
  collision interval and explicitly marks interference volume as unavailable.
- A hollow cylindrical solid and a smaller solid inside its open void have
  overlapping AABBs but remain collision-free, proving the broad phase does not
  create a false positive.
- The native Assembly lifecycle intentionally overlaps two components and
  reports the collision through worker execution, authenticated validation,
  publication, edit/rebuild, failed-candidate preservation, save/reopen, and
  deletion.
- Two exact modeled-thread ISO 4762 fastener BREPs are detected when placed in
  the same pose.
- A STEP round-trip solid follows the same collision path and is detected when
  moved into overlap.
- The six-test GUI playback gate passes with complete and incomplete collision
  indicators, exact-time service results, stop/restoration, and save/reopen.

Performance and native root cause:

- OCCT 7.6.3 defines `BRepExtrema_ShapeProximity::IsDone()` as "at least one
  triangle pair overlaps." FreeCAD's wrapper incorrectly threw when a valid
  collision-free comparison returned false. The wrapper now treats successful
  `LoadShape1`/`LoadShape2` calls as the initialization contract and returns
  empty overlap maps for a valid clear pair.
- The wrapper releases Python's GIL around the read-only OCCT proximity pass.
  Independent candidate pairs run in a bounded four-thread pool with stable
  ordered results. On the seven-component robot, the worker phase fell from
  roughly 167 seconds to 137 seconds, and observed per-frame surface work fell
  from about 1.3 seconds to 0.67 seconds.
- Timestamp analysis located the remaining approximately 53 seconds before the
  worker starts, in imported-reference capture and host-side shape facts—not in
  publication. Assembly now records only exact topology counts and bounds for
  references, omitting unused mass properties and face/edge descriptions, and
  leaves the single exact BREP validity pass to the isolated worker.
- On the saved robot document, compact facts for a 22,068-face / 53,415-edge
  shape completed in 1.19 seconds. The native Assembly lifecycle still
  authenticated, solved, published, saved, reopened, and deleted successfully.

### Simulation time and framebuffer correctness

Live use found two defects in existing tools:

- Native frame 0 is the unsolved input snapshot. Solver frame 1 corresponds to
  `start_time_s`, but the player previously labeled it as one time step later,
  rejected the exact start time, and exposed a range ending one step too late.
  The native player and `assembly.play_simulation` now use one shared mapping:
  input frame 0 has no simulation time, and frame N has
  `start_time_s + (N - 1) * time_step_s`.
- Immediately after activating the 53 MB robot document, the first
  `core.capture_view_screenshot(frame='all')` could grab a partially repainted
  framebuffer even though framing found all 14 visible targets. The existing
  capture now flushes the scheduled Coin/Qt render before reading pixels. A
  clean restart followed by activation, exact 0.0-second playback, and immediate
  capture produced the complete robot image without a preparatory view call.

No AI/MCP/VibeScript tool or parameter was added for either correction.

### Background operation result duplication

The completed robot build returned the same full collision interval table in
candidate diagnostics, publication metadata, and live-output metadata. The
terminal `vibescript.read_operation` response grew to roughly 90 KiB even
though the agent needed only the accepted revision, output identities, phase
timings, collision verdict, first collision, and warnings.

Resolution:

- The process-local operation manager still retains the exact raw result.
- Provider/MCP terminal reads now apply the existing source-lifecycle projection
  to the nested result, just as synchronous source writes already did.
- A simulation output keeps one compact collision signal: completeness,
  collision-free verdict, evaluated/colliding counts, first collision, and
  warnings. Per-pair intervals remain retained in the saved simulation and are
  available through the existing playback path instead of being repeated three
  times in the write result.
- The callable and parameter schema are unchanged; this fixes an existing
  projection boundary that background operation wrapping had bypassed.

Zero-wait status resolution:

- The MCP child retains the exact most recent host status for each operation.
- `vibescript.read_operation(wait_seconds=0)` returns that status immediately
  and launches at most one background refresh while the operation is running.
- Nonzero waits and every mutation still use the authoritative host result.
- A concurrency regression blocks the host status read and proves the zero-wait
  response remains immediate, then observes the cached terminal success after
  the host becomes available.

### Toggle-clamp authoring noise observed on 2026-08-05

The Assembly vocabulary itself remained small and understandable: component,
instances, connector, joint, assembly, solve, motion, simulation, fastener, and
BOM. The following lifecycle and diagnostic output made that API materially
harder to use than necessary.

1. `vibescript.read_source(include_logs=false)` returned every stale live output,
   including internal fastener-source, dependency-anchor, restore-guard, motion,
   and prior joint objects. A focused source/failure read exceeded 16,000 tokens
   even though the actionable information was one error, one working revision,
   and the source text.
2. A terminal `vibescript.read_operation` result exceeded the provider byte
   limit and was replaced wholesale by `_vibecad_value_omitted`. The caller then
   had to issue `read_source` to discover whether the operation failed and why.
   Terminal projection must happen before byte-limit enforcement and must never
   omit `ok`, error, failure code/stage, revision, accepted-state preservation,
   and the exact next action.
3. Handled native solver failures included hundreds of raw `Recompute`,
   `Processing`, redundancy, and convergence lines. Ordinary outcomes need the
   final solver status, conflicting joint output names, maximum residuals, and
   correction. Raw iterations belong behind `include_logs=true` only.
4. Background progress repeatedly reported only `phase=worker`. During a
   four-minute run it did not distinguish reference capture, native graph
   construction, static solve, per-frame motion solve, collision analysis, or
   result serialization. Long work needs the current subphase, frame/pair
   counters where applicable, and elapsed time in that subphase.
5. `edit_source` immediately performs the full build. Connector-contract edits
   that can be checked without geometry still paid the complete worker cost.
   Add an internal cheap preflight before launching native build; keep the
   public edit tool cohesive rather than adding a separate validation tool.
6. Failed writes advanced the working revision even when a follow-up
   `build_program` used unchanged source. This forced an additional source read
   merely to obtain a new concurrency token. Return the next expected revision
   in every terminal result and keep it in the compact projection.
7. `read_api(connector)` says connector local +Z is the joint axis, but does not
   explicitly state that offset vectors are expressed in the complete local
   connector frame. The first rigid link-pair offset therefore moved along the
   wrong global direction. Show one copyable offset example and the resulting
   world-frame interpretation.
8. The native requirement that a screw relation needs a collinear slider was
   reported only after an expensive Assembly build. This dependency belongs in
   focused `read_api(joint)` details and in source preflight.
9. Two simulation attempts with 41 and 11 requested frames both consumed about
   four CPU minutes and exited by signal 24 without a structured domain result.
   The nearly identical time rules out trace density as the dominant cost. The
   worker progress remained at `native_build`, and only a manually requested raw
   log showed a native solver convergence loop. CPU-limit termination must
   return the last native subphase, joint/output being solved, iteration count,
   latest residual, and whether collision analysis ever began.
10. Source results expose implementation labels such as candidate object names,
    internal output suffixes, staging paths, and restore objects. Default output
    should use authored output names and human labels; exact native identities
    remain available only when needed for a repair or explicit diagnostic read.

Target default response: one readable program path, accepted/working revision,
current state, compact output identities, phase timings, one actionable failure,
and one exact next call. Everything else should be opt-in diagnostics.

Implemented cleanup:

- Concise source reads now default to `include_logs=false`, return only declared
  authored outputs, and omit native object identifiers. Explicit diagnostic
  reads retain exact native identities and logs.
- Terminal background results are compacted before byte enforcement. Even a
  pathological native diagnostic preserves the operation verdict, failure
  code/stage, revision, and legal recovery call; stdout, stderr, tracebacks,
  repeated graph records, and internal outputs do not compete with them.
- Long workers report changed native subphases with phase elapsed time and
  component, joint, or frame progress. POSIX `SIGXCPU` is classified as an
  exact CPU-time-limit failure and carries the last crash-safe worker phase.
- Assembly performs structural coupled-joint preflight before constructing
  native objects when a solved graph is required. Screw and rack/pinion
  relations identify their required collinear slider in both API guidance and
  preflight failures.
- Connector documentation now states that offsets use the complete local
  connector frame and includes a local +Z example.
- Every accepted or failed source write projects the current working revision
  and exact next call without requiring a redundant source read.
- Native solver success now carries an authenticated `validation_scope`:
  `joint_constraint_consistency`. It explicitly sets
  `mechanical_operation_verified=false` and requires separate collision,
  operating-range motion, retention/access, and manufacturability evidence.
  The same contract is returned by VibeScript and direct Assembly tools.

Verification completed with focused provider/process/engine tests and the full
native Assembly VibeScript lifecycle, including create, solve, simulation,
collision, save/reopen, rebuild, failed-candidate preservation, and deletion.

Live MCP verification on the rebuilt application:

- A successful Assembly build returns the authenticated solver scope both at
  the top level and on its Diagnostics output. It explicitly reports
  `mechanical_operation_verified=false` and names the required collision,
  motion-range, retention/access, and manufacturability evidence.
- A later `read_source(include_logs=false)` retains that same scope after native
  publication instead of reducing the result to a generic accepted state.
- A deliberately invalid connector edit returned one plain error, the readable
  program path, the new working revision, confirmation that accepted outputs
  were preserved, and copy-ready read/build recovery calls. No GUID,
  filesystem path, raw stdout/stderr, or empty tool arguments were exposed.
- Restoring the prior source rebuilt successfully and returned its original
  deterministic revision.
- Part Design publication and catalog inspection use the authored native Body
  label rather than the hidden stable-link `001` label. Internal BodyState
  resources no longer appear as catalog components.
- Focused `api.component` guidance no longer tells authors to return every
  member separately when a mapped `api.assembly` already owns it.
- When focused API names belong to another workbench, the existing unknown-name
  response now identifies the workbench that covers the complete request and
  states the required switch instead of leaving the caller to infer it from a
  flat name inventory.
