# Native SheetMetal regression tests

Use a complete strict Release build with `FREECAD_WARN_ERROR=ON` and all
`FREECAD_USE_SANITIZER_*` options off. Activate the dependency environment used
to build it; NetworkX must be installed there. Run from the repository root:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT"
```

Set `SHEETMETAL_TEST_BUILD` to the build directory and `SHEETMETAL_TEST_OUTPUT`
to a new, nonexistent artifact directory. The Linux runner uses `xvfb-run` with
a private profile, temporary/socket directory, and PID assertions. It does not
connect to an existing GUI and sets no process timeout. Only the private test
instance closes when the suite finishes. Inspect `result.log`, `launch.log`,
`status`, and `exit` in the output directory. An assertion failure during startup
can leave the private GUI alive for diagnosis.

The default suite contains eight SendCutSend regressions, seven document-owner
regressions, seven geometry mapping tests, and five through-cut tests. To run a
subset, append its dotted unittest name, for example `SMTests.testUnfoldMapping`.

`SMTests.testSheetNativeRecovery` exercises the full private assistant panel with
a deterministic provider and real native geometry. A failed cut triggers a
revision conflict, then a separate turn reads its current history and repairs
the same feature. It checks valid folded/flat results, preservation of the parent
BRep, and rejection of a continuation for another document. No live AI provider
or external service is used by this regression.

## Cold asynchronous document restore

Run the fixture producer and restore test in **separate fresh processes**. Do
not combine the restore class with other modules: unittest imports all modules
before running tests, which would hide import-time GUI access on the restore
worker. The test opens through Recent Files, the same asynchronous native path
used by File > Open. It records import thread IDs, checks restored feature
proxies and History, prepares both representations and activates the ribbon.
Only dialogs belonging to its private test process are captured and dismissed;
any restore warning fails the test.

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_FIXTURE_OUTPUT" \
  SMTests.testSheetColdRestore.CreateFixture
VIBECAD_COLD_RESTORE_FIXTURE="$SHEETMETAL_FIXTURE_OUTPUT/cold-sheet.FCStd" \
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_RESTORE_OUTPUT" \
  SMTests.testSheetColdRestore.TestColdRestore
```

Use new artifact directories for both commands. The fixture creates a plate,
shared sheet and editable hole; it does not call an AI provider or open a user file.

For internal folds, run `SMTests.testSheetFoldSource` first, then open its saved
fixture in a separate process:

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_FOLD_OUTPUT" \
  SMTests.testSheetFoldSource
VIBECAD_COLD_RESTORE_FIXTURE="$SHEETMETAL_FOLD_OUTPUT/internal-fold.FCStd" \
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_FOLD_RESTORE_OUTPUT" \
  SMTests.testSheetColdRestore.TestFoldColdRestore
VIBECAD_COLD_RESTORE_FIXTURE="$SHEETMETAL_FOLD_OUTPUT/internal-tab.FCStd" \
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TAB_RESTORE_OUTPUT" \
  SMTests.testSheetColdRestore.TestTabColdRestore
```

The backend suite checks an internal bend and a relief-cut tab, exact input
history, parameter/material edits, Undo and document-owned input hiding. The cold
restore class checks the fold's sketch and allowance expression, History commands
and developed stock dimensions. Run only the selected restore class, since the
base fixture has a different history structure. The fold backend suite also
checks native asynchronous dispatch; `SMTests.testSheetSourceForms` checks the
ribbon action, angle units and frozen sheet/line selection.

The tab restore case prepares the final fold first, while its parent relief-cut
state has no transient mapping. It checks valid folded/flat geometry, preserved
flat volume, and unchanged Undo count, touched state and saved fingerprint.
This caught a gauge lookup that required preparing the parent first; supported
parametric sources now supply their persisted thickness through the retained
history. Unknown source types retain the existing prepared-mapping fallback.

The optional SM-P04 live case requests a fold along an existing bend-line sketch,
preserving the hole, stock dimensions and editable history, and finishing flat.
It uses the production provider without MCP and checks exact retained inputs,
fold parameters, History, validity, developed volume and dimensions. Run it
explicitly; it is not part of the default suite:

```bash
VIBECAD_SHEET_PROMPT_MODEL=gpt-5.6-terra VIBECAD_SHEET_PROMPT_AUTH=chatgpt \
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_FOLD_LIVE_OUTPUT" \
  SMTests.live_sheet_fold_prompt.LiveSheetFoldPrompt
```

SM-P05 starts with a shared flat plate and **no sketches**. It asks for a centered
U-slot and internal folded tab, retaining editable sketches and sheet history.
The oracle checks unchanged parent geometry and stock dimensions, exact removed
volume, valid folded/flat solids, retained profile/fold links and stationary
surrounding stock. It is an explicit live probe, excluded from the default suite.
Use local Qwen to check tool clarity, then Terra for the functional workflow:

```bash
VIBECAD_SHEET_PROMPT_MODEL=qwen3.5:9b VIBECAD_SHEET_PROMPT_AUTH=api_key \
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TAB_QWEN_OUTPUT" \
  SMTests.live_sheet_tab_prompt.LiveSheetTabPrompt

VIBECAD_SHEET_PROMPT_MODEL=gpt-5.6-terra VIBECAD_SHEET_PROMPT_AUTH=chatgpt \
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TAB_LIVE_OUTPUT" \
  SMTests.live_sheet_tab_prompt.LiveSheetTabPrompt
```

For subscription authentication, set `VIBECAD_CODEX_HOME` to an existing signed-in
VibeCAD Codex directory. The runner uses a separate CAD profile, which otherwise
has no subscription sign-in. This probe does not contact RMFG or place orders.

`SMTests.testSheetReferenceFaces` checks a formed bracket's sheet skins against
its known gauge. An extrusion end face previously inferred 70 mm thickness from
1.6 mm stock and published a false flat result. The tests cover rejection,
correct development, explicit reference repair, worker-prepared candidate facts,
and rejection before creating a Tree/History entry. It runs in the default suite.
The focused red/green command is:

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetReferenceFaces SMTests.testSheetSourceFeatures \
  SMTests.testSheetSourceCreation SMTests.testEditableSheet
```

`SMTests.testRMFGExport` exercises detached BREP serialization, Python callbacks
during native serialization, private command-line STEP export and round-trip
geometry checks. It checks that a flat display still exports the folded solid,
without a view change or Undo entry, and that changed/pending/foreign revisions
and cancelled consumers cannot receive a successful stale export. Child tests
reject modified BREP input and multiple solids before STEP writing. These tests
do not contact RMFG or use credentials.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testRMFGExport
```

The standalone `testRMFGJobs.py` checks durable requests without a GUI or network
connection. It covers exact retry inputs after reopening, changed-key rejection,
append-only remote observations, saved document sessions, cross-process writes,
STEP-free metadata listing, private database permissions and damaged-export
detection. Execution checks prove that requests are saved before transport,
remote IDs are read after reopening, ambiguous writes retain their retry key,
malformed responses can be retried without a replacement job, and conflicting
IDs stop network work. Run it with Python, not inside the FreeCAD GUI, because its concurrency
check starts two independent Python processes:

```bash
PYTHONPATH=src/Mod/SheetMetal python -m unittest discover \
  -s src/Mod/SheetMetal/SMTests -p testRMFGJobs.py -v
python -m pytest -q src/Tools/tests/test_sheetmetal_installation.py
```

It also runs 19 shared-document tests: circular-cut creation and reverse edits,
Undo/Redo, save/reopen, material and upstream thickness/radius/flange/angle
changes, exact document ownership, stale inputs, failed-cut repair/removal,
missing references, geometry execution off the GUI thread, persistence precision,
fingerprint changes for source movement, and nested Part/Body placement. Failure-path
tests deliberately produce native recompute errors; their expected outcomes are
recorded in `result.log`.

Fifteen native profile tests extend this with constrained arc/line slots and
periodic B-splines crossing bends; constraint and folded-handle edits; native
sketch attachment after thickness/radius changes; transformed Bodies; Undo/Redo;
save/reopen; recursive copy; missing/open profile repair; and exact document,
dependency-cycle, and custom-property checks. Circle-only features written
before the profile dependency property existed remain editable.

Eleven presentation tests check detached tessellation, invalid mesh tolerances,
the actual native provider type, rendered folded/flat images and triangle counts,
object placement, appearance updates without remeshing, cached switching,
stale and superseded workers, deletion during
preparation, and save/reopen. The image test disables camera animation only in its
private view so it captures the final camera position. Its artifacts include both
PNG images, scene graphs, and `switch-timing.json`. Switch timings measure the
cached API call, not GPU frame time or sustained frame rate.

Fifteen command tests cover shared inspection and edits, one ordinary Undo entry,
current completion revisions, change-and-restore rejection, frozen arguments,
save/reopen epochs, native mutation receipts, legacy metadata, profile ownership,
invalid geometry repair, and superseded asynchronous work. A deliberately paused
worker verifies that GUI callbacks continue and later user edits survive.

Twelve selection tests extend this with exact surface correspondence on both
skins of planar and bent regions, reverse stationary faces, multiple bends,
cut-wall/removed-material rejection, real Coin face picks, viewport clicks,
stale and foreign ownership, and native GUI-thread checks. A viewport click and
a folded bend pick each feed the shared cut definition; both solids update, and
the folded edit is one Undo entry. Moving a parent Part retains the local mapping
and mesh generation. An object in front of the sheet prevents a pick through it.

For Python-only iterations on an already complete strict Release build, update
the built module before running the suite:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --target SheetMetal --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT"
```

The profile increment passes all 61 native tests. Its initial regression failed
because `create_cut_sketch` was absent. Further red tests caught copied sketches
being resolved by old names and unrelated custom links being cleared; native
binding properties and exact property-key matching resolve those failures.

The mapping tests create real solids using upstream `smBase`. They check both
skins and different stationary faces, multiple bends in arbitrary placements,
neutral-axis allowance with material/thickness changes, seam continuity, trimmed
holes, input snapshot ownership, and legacy Unfold output. They establish point
correspondence. The through-cut tests check circular holes crossing both bend
seams, cuts wholly on bends, curved notches, reverse picks, repeated cuts,
material/thickness changes, and explicit rejection of removed selections.

Cut checks distinguish interior material from boundary points. They check the
original skin's hole edge within `1e-6` mm and the curved through-thickness wall
within `1e-4` mm (0.1 micrometer). These are geometric checks; they do not claim
manufacturing precision. OCCT's default `Shape.Volume` integration is less
accurate than the geometry on curved trimmed faces, so its analytical volume
comparison allows `1e-3` relative error and is paired with the tighter boundary
checks. An independent OCCT adaptive-integration probe at `1e-11` agreed with
the three analytical bend-hole volumes within `1.3e-6` mm³. Sampling 96 angles
at four depths measured a maximum wall deviation below `3.9e-5` mm on those
fixtures; this is fixture evidence, not a bound proven for every input shape.

The periodic B-spline fixture also exposes integration error in OCCT's default
volume/area properties. Its mass check allows `1e-3` relative error and checks
128 points on both flat and folded cut boundaries within `1e-6` mm, plus material
classification throughout the mapped regions. An independent native
`BRepGProp::VolumePropertiesGK(shape, properties, 1e-11, true, true)` probe gives
161.595281246 mm³ for both the removed volume and the profile extrusion, differing
by less than `7e-11` mm³. Ordinary adaptive Gauss integration alone did not resolve
this periodic B-spline case; the Gauss–Kronrod probe uses span subdivision.

The document feature persists circular cuts and links to native sketch profiles.
Its mutators join the
caller's transaction; the caller schedules `document.recomputeAsync()` after
immediate edits. `get_prepared()` performs no geometry work and rejects pending
or invalid inputs. A restored file retains both output solids; a command must
schedule preparation before asking for its in-memory mapping. The input hash is
a content fingerprint, not a globally unique document identity or a monotonic
revision counter.

`create_cut_sketch()` attaches a sketch to the stationary upstream face in the
same native container. `add_profile_cut()` binds its closed wires to the shared
history. Constraints remain native Sketcher data. `profile_coordinates()` maps
flat/folded picks into sketch XY coordinates; existing handles can explicitly
map through already-removed material. Profile operations use per-operation native
link properties with stable keys, so recursive copy/import name remaps and target
deletion do not silently redirect them. `ProfileSources` is a derived inspection
list. Removing an operation releases its links and retains the source sketch and
empty binding property. All geometry still uses the shared through-cut path.

The editable feature shares its source's native Part/Body container and uses
that container's local coordinates. Native grouping owns scene transforms and
dependent-object relocation. The placement tests first failed because the new
feature was created at the document root. They now cover nested translations
and rotations, folded-side cuts, save/reopen, edits after reopening, and native
relocation with the source. Moving the whole container preserves the prepared
geometry object and its fingerprint. Body insertion uses the normal native Tip
and initial visibility behavior; view switching remains a separate operation.

Fingerprint regressions cover rebuilding after both text and binary document
save/reopen. `exportBrepToString(True)` opts into native document precision;
the default export is unchanged. The test compares that opt-in output directly
with the document archive's BREP entry and verifies that exporting did not
modify the source. OCCT readback and signed-zero normalization remove transient
serialization differences before hashing. This is a persistence fingerprint
for the current kernel, not a geometric equivalence test across topology
reordering or kernel versions. Revision ownership and exact exported STEP bytes
must still be checked by the later worker/publication and RMFG layers.

The new export test failed with `TypeError` before the optional native argument
was added. The save/reopen test also failed on its unchanged-input hash assertion
before persistence normalization. Run these regressions independently with:

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testEditableSheet
```

The presentation increment adds opt-in `PartGui::ViewProviderCachedPython`, which
retains native placement and selection infrastructure without ordinary Part
meshing. `tessellateDetached()` copies geometry before releasing the Python lock
for native meshing; it leaves the source's binary shape representation unchanged.
Both prepared meshes become Coin nodes on the GUI thread. A warmed toggle changes
only the selected cached child, without recompute, remeshing, visibility changes,
or Undo entries. Geometry and mesh generations reject stale publication. Restored
outputs can display before their mapping is rebuilt, but cannot be used for mapped
edits/export through `current()` until prepared.

Red tests first caught the absent detached API/presentation module, then rejected
fallback to the normal Part provider and found a missing post-restore refresh.
The complete strict build and combined 71-test suite passed. After adding the
appearance update hook, all 11 presentation tests passed (72 distinct native
tests across the combined and focused runs). The installation regression passed
with the presentation module and test included. The 200-toggle combined run
measured 0.066 ms mean and 0.108 ms maximum for the cached switch call; this excludes
GPU frame time. The focused regressions pass after the additive native provider,
restore hook, and appearance update hook.
Run them separately using:

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testPresentation
```

These tests do **not** yet prove arbitrary Link instances/reference lifecycle,
complete ribbon/native AI integration, large-model frame rates, or
manufacturing export/publication.
Those remain part of the agreed workflow.

`map_surface_point()` resolves a zero-based face index in the current result to
the base sheet region and its exact folded/flat coordinates. Opposite-skin picks
project through the known sheet thickness; thickness/cut walls are rejected.
At a tangent seam either adjacent region must give the same correspondence;
conflicting mappings fail explicitly. Rendered triangle hits first project onto
their analytic result face, so a cylindrical mesh chord is not mistaken for the
actual bend surface.

`SheetViewProvider.pick()` copies a live Coin pick into a `SheetPick` record;
`pick_screen()` uses the supplied viewport's nearest visible primitive.
`validate_pick()` rejects records from another feature or prepared generation.
View switching and camera movement do not invalidate them. These APIs choose
neither a document nor a face through global selection or active-document guesses.
`PartGui.getPickedFaceIndex()` performs a native detail-type check and reads its
index while the owning pick action remains alive; it retains no Coin data and
rejects worker-thread access.

The initial selection tests failed on the missing geometry and view APIs. The
next native run caught Pivy exposing only the generic `SoDetail` interface;
the checked native index reader resolves that failure. Run selection separately:

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetSelection
```

The complete strict Release build passed after the native reader addition. All
84 combined native tests passed in 151.097 seconds; installation passed with the
new selection test included. The final 200-toggle run measured 0.068 ms mean
and 0.220 ms maximum for the cached switch call, excluding GPU frame time.

`SheetMetalOperations.prepare()` freezes validated arguments against the exact
open-document session and monotonic native revision. `start()` commits a short
transaction and schedules native asynchronous recompute. Retain its `EditRun`
until completion; its future/status reports ready, failed, superseded, or closed.
Failed geometry remains repairable through parameter edits or operation removal.
The ribbon uses these operations directly; the built-in AI adapter remains to be
connected. No MCP server is required. Ordinary document Undo is
tested, but assistant receipt/Undo finalization after asynchronous recompute is
still required. The module omits the immediate runner's stale Undo-availability
flag rather than weakening the native ledger's revision guard.

The initial command tests failed because the module was absent. Subsequent red
tests caught completion before the native revision batch closed, missing-profile
handling, unrelated text metadata, missing changed-object receipt identities, and
repair being incorrectly blocked by invalid geometry. All 15 focused tests pass
in 24.831 seconds after those fixes. Run the focused tests with:

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetOperations
```

The complete strict Release build passed with `--parallel 12`; installation
passed in 1.16 seconds. The commands above apply to this increment as well.
The final combined suite passed all 99 native tests in 173.920 seconds against
that strict build with the updated module.

Thirteen GUI tests exercise the registered commands and the existing native
ribbon's Sheet Metal surface. They cover exact-face asynchronous creation,
material and dimension edits, a real Coin mouse event placing a bend-crossing
hole, cut removal, stale panels/source revisions, document isolation, and cached
view switching. A real task panel stays open through an edit and can switch
representations without another Undo entry. Closing it during preparation retains
the committed edit and releases its callbacks.

Red tests first found the missing GUI module and unbuilt ribbon surface. Reviewing
the native log found errors that the initial functional tests did not catch:
Qt 6's standard-button enum needs explicit value conversion, and `closed` is a
native callback name, not an available boolean field name. Separate failing tests
now cover both contracts. All 13 focused tests pass in 16.751 seconds with those
errors absent from the log. The full strict Release build passed with 12 jobs;
all 112 combined native tests passed in 177.929 seconds. The final render check
uses `Gui.updateGui()` and an explicit redraw before capture, and asserts visible
model pixels with the task panel open. Its artifacts include
`sheet-panel-model.png` and `sheet-cuts-panel.png`.
The final capture waits for the native ribbon surface and synchronizes the
window system before grabbing the screen, so both OpenGL content and the current
ribbon are included. That focused check passed in 2.020 seconds; the separate
render/close-during-preparation checks passed in 3.086 seconds.

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetGui
python -m pytest -q src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/VibeCAD/vibecad_tests/test_ribbon_surface.py
```

The Python ribbon/install command passed all 14 tests in 1.13 seconds. These
GUI checks do not establish tree/History integration or AI tool usability. The
latter requires the ordinary-prompt sharpening runs described in the workflow
plan, not payload-coached calls to the domain API.

Seven tree tests exercise real model indexes and double-click events. New shared
sheets opt into an additive cached detail provider; existing provider types and
the original read-only detail interface remain unchanged. Folded/flat rows switch
without document changes, geometry execution, or detail reconstruction. Cut rows
resolve stable operation IDs and the exact owning document before opening their
editor. Definition/material edits refresh row text, removed cuts reject stale
actions, and save/reopen restores the rows without extra document objects.

The first red run failed because the tree-enabled creation option was absent.
A later run caught missing row invalidation after cuts were added or removed.
The final seven focused tests passed in 13.824 seconds. The full strict Release
build passed with 12 jobs; all 119 combined native tests passed in 191.017 seconds.
The installation and existing model-tree schema checks passed four tests in
1.02 seconds. The combined run also captured `sheet-tree-editor.png`, visually
checked for the tree, exact cut editor, and rendered bend-crossing hole. Its
Report View retains expected diagnostics from earlier negative geometry tests;
the focused tree run has no such errors. Persisted per-cut History and its
rollback behavior still require their own implementation and tests.

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetTree
python -m pytest -q src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/VibeCAD/vibecad_tests/test_model_tree_role_clarity_schema.py
```

Seven History tests now exercise shared-sheet creation through its native
operation/replacement contract. They use actual timeline buttons and editor
double-clicks, reject future sources/sheets, and verify Undo/Redo and save/reopen.
Container coverage checks native Part membership and Body Tip/result geometry.
Repeated Undo/Redo retains one native representation switch, renders its result,
and rejects old picks. This covers sheet creation; individual cuts still reside
in the shared JSON definition and are not separate native History operations yet.

The initial red run found missing metadata/editor registration, failure to restore
the source at an earlier marker, and a closed provider after Redo. Native Body
insertion requires finalizing its current-transaction enrollment after native
grouping/visibility changes, while root creation publishes exact replacement
metadata before hiding the source. Existing deletion tests also caught retained
scene references. Reacquiring a borrowed Coin child without its owning switch
caused a private-test crash during close/reopen; the final lifecycle retains the
native owner while attached and releases all scene references on deletion.
All eight focused deletion/History checks passed in 14.796 seconds with a clean
native lifecycle log. The full strict Release build passed with 12 jobs.

Run the startup test **alone**, in its own fresh GUI. It deliberately verifies
that no SheetMetal editor command was registered before reopening the file.
The red run found no editor after reopen; the green run passes in 2.076 seconds,
including actual editor invocation without changing the current workbench.
This additional startup test is installed but is not part of the combined suite.

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testPresentation.TestPresentation.test_deleting_sheet_cancels_its_inflight_mesh \
  SMTests.testSheetHistory
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_STARTUP_TEST_OUTPUT" \
  SMTests.testSheetHistoryStartup
python -m pytest -q src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/VibeCAD/vibecad_tests/test_model_tree_role_clarity_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_ribbon_surface.py
```

The Python installation/tree-schema/ribbon command passed all 17 tests in
0.98 seconds. Use separate nonexistent output directories for the two native
commands above; the same strict-build/environment requirements apply to both.
The final combined suite passed all 126 native tests in 198.444 seconds, with
a successful process exit. Together with the standalone startup test, this
covers 127 distinct native tests. Its 200 warmed view switches averaged
0.050 ms per call with a 0.201 ms maximum, excluding GPU frame time.

Native circular-cut states now have their own History tests in
`SMTests.testSheetCutHistory`. Each feature owns one hole and links its exact
predecessor. Tests check actual History navigation and editor mouse events,
suppression, downstream recompute, folded-side edits, stale revision rejection,
save/reopen, Undo/Redo, and cached view commands. Geometry-call assertions verify
that new cuts reuse the prepared mapping and that radius changes run off the GUI
thread without repeating Unfold. The suite also checks native Tree rows and
exact-owner editing; representation rows do not create document objects or Undo
entries.

The initial red tests found the missing cut-state module and editor support.
Further checks caught unsupported existing view commands and duplicate re-import
of a shared cut state. A worker-mutation test stalled in its private GUI before
an early GUI-thread gate was added; debugger attachment was denied, so no native
stack was captured. The isolated test was stopped manually, without a process
timer. The corrected path rejects worker mutation before reading the document.
Deletion-before-completion also exposed a detached wrapper with `Name=None`;
completion now reports that exact cut as closed.
The same detached-wrapper check was added to the cut editor after a separate
red test reproduced the failure when reloading an already deleted cut.

The full strict Release build with 12 jobs passed. The installation/tree-schema/
ribbon checks passed all 17 tests in 1.01 seconds using system Python, which has
pytest installed; the build's dependency Python does not have pytest. Native
tests must still use the build's dependency environment.

The combined suite passed all 141 tests in 237.184 seconds with a successful
process exit. Its captured `sheet-cut-history-editor.png` was visually checked
for the native History selection, exact hole panel, and rendered hole crossing
the bend. Report View contains the expected oversized-hole diagnostic from a
preceding negative test. The standalone startup test passed in 2.174 seconds.
After adding the Tree-click and deleted-panel checks and fixing the latter,
the final full strict build passed again and all 17 focused cut-state tests
passed in 40.227 seconds. Across the combined and standalone runs, 144 distinct
native tests passed. The final focused log contains only the expected invalid
oversized-hole diagnostic, with no detached-object or lifecycle exception.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetCutHistory
```

This increment adds the circular-cut operation foundation. The existing ribbon
cut-creation panel still uses legacy JSON operations; connecting it to native
cut states remains pending. The subsequent sketch-cut increment is described below.

Eleven `SMTests.testSheetProfileHistory` tests add native sketch-cut operations to
mixed hole/profile chains. A constrained slot crosses a bend; constraint edits
update both solids and downstream holes off the GUI thread without repeating
Unfold. Tests cover folded-side handle coordinates, upstream thickness changes,
suppression of an open contour, exact native Tree/History sketch editing,
rollback, missing-sketch repair, and save/reopen with a stable prepared hash.

The initial six tests failed on the missing factory. A separate cleanup error
left a private test document unclosable; that completed red-test GUI was stopped
manually and the fixture now waits for its own document before closing it.
After implementation those six tests passed. Further red tests found the absent
replacement API, a replacement sketch placed after its dependent cut in History,
and a constraint change incorrectly reported as the original pending hole edit.
Repair now uses the native dependent-closure reorder API. Edit completion compares
serialized sketch inputs at edit boundaries; cached view reads never serialize
those inputs. A control case also verifies that an unchanged sketch allows the
pending edit to complete normally.
That control initially caught false superseding from native ZIP timestamps.
The snapshot now compares all saved archive entries, including binary payloads,
without archive timestamps; it does not omit or round geometry/constraint data.
The final replacement/downstream-order and changed/unchanged completion controls
passed in 7.613 seconds.
The full strict Release build passed with 12 jobs. The installation, Tree-schema,
and ribbon Python checks passed all 17 tests in 1.28 seconds; the independent
fresh-start editor test passed in 2.172 seconds.
The final combined suite passed all 154 tests in 283.927 seconds with a successful
process exit. Including the standalone startup case, 155 distinct native tests
passed. This includes the original hole, profile, presentation, Tree, ribbon,
document-isolation, and SendCutSend checks on the same final runtime.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetProfileHistory SMTests.testSheetCutHistory
```

The native log deliberately includes missing-sketch and open-contour errors from
repair tests; these are expected geometry failures. Existing JSON profile APIs
and saved documents keep their previous path. Ribbon creation, broader sketch
authoring, and native assistant tools remain separate pending integration work.

Fifteen `SMTests.testSheetRibbonHistory` tests exercise the ribbon's new native
operation path. Actual viewport picks create holes and retain flat view. The
panel follows new results, edits earlier holes, creates cuts from constrained
sketches, suppresses/restores cuts, and opens the exact native sketch editor.
Dimension/material edits from the selected tip prepare the shared chain. Legacy
JSON cuts and direct legacy panels remain supported. Failed holes stay available
for repair; stale panels and future suppressed cuts cannot mutate the model.

The first red run found the missing panel option and that the ribbon still
modified the root JSON rather than creating a native operation. Six initial
tests passed after implementation, followed by all 38 combined ribbon/panel/
command checks. Further red tests caught a full History walk during view toggles
and a detached-object exception in a deleted panel. Both are fixed. Three final
view/deletion/rollback-completion checks passed in 5.216 seconds. Rolling History
back supersedes pending creation, while a current suppressed operation retains
a revision-bound editor for restoration. The shared async runner's default
completion behavior remains available to existing callers.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetRibbonHistory SMTests.testSheetGui SMTests.testSheetOperations
```

The full strict Release build passed with 12 jobs. All 17 Python installation,
Tree-schema, and ribbon checks passed in 1.21 seconds. The ribbon uses the new
typed `SheetMetalHistoryOperations` service; native AI registration and final
assistant receipt/Undo handling after async recompute still require their own
implementation and ordinary-prompt sharpening runs.
The combined suite passed all 167 tests in 313.921 seconds, and the standalone
startup check passed in 2.392 seconds. Its `sheet-native-cuts-panel.png` shows the
native Sheet Metal ribbon, expanded representation rows, History entry, and flat
hole result. The expected oversized-hole diagnostic from a preceding repair test
remains in Report View.

Two additional red checks found that an altered prepared wrapper could redirect
its legacy target to another document, and that editing a missing sketch closed
the panel before reporting the error. Execution now resolves the frozen request
again and compares exact target identities before opening a transaction. Sketch
links are validated before closing their repair panel. A follow-up check caught
parameter-order differences across the request's JSON round trip; preparation
now derives its targets from the canonical argument order. Add Hole is placed
beside its radius input in the native panel, while the legacy panel keeps its
existing layout.
The final strict build passed again, and all 43 ribbon/panel/command checks
passed in 71.506 seconds after those fixes. The updated panel capture was
visually checked with Add Hole directly below the radius field. Across the
combined suite, final focused checks, and standalone startup case, 170 distinct
native tests passed. Native AI adapters and their sharpening are not covered
by these direct GUI/domain tests.

Four additional ribbon-service inspection tests cover exact native History
identities with duplicate labels, suppressed display predecessors, missing sketch
repair information, and save/reopen. Inspection retains existing state and does
not recompute, remesh, change selection/visibility, or add Undo entries. The first
two tests failed with the absent `history` result before implementation, then
passed in 4.503 seconds. The final full strict Release build passed with 12 jobs;
all 47 ribbon/panel/command tests passed in 84.772 seconds with process exit 0.
All 17 installation, Tree-schema, and ribbon Python checks passed in 0.95 seconds.
This is shared inspection plumbing; native assistant bindings and ordinary-prompt
tool-sharpening evidence remain pending.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetRibbonHistory SMTests.testSheetGui SMTests.testSheetOperations
python3 -m pytest -q src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/VibeCAD/vibecad_tests/test_model_tree_role_clarity_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_ribbon_surface.py
```

`SMTests.testSheetNativeInspect` exercises the registered `sheet_metal.inspect`
capability against real native documents. The tool is discovered on the live
Modeling surface, and production runtime bindings resolve its implementation.
Calls read the shared sheet overview and bounded History/cut/region pages.
Checks cover exact targets, current visibility, no document changes, stale-page
rejection, malformed arguments, inactive documents, suppression, and invalid
geometry with repair information. Reads use the existing shared inspection;
no MCP server or generated modeling script is involved.

The first native red run had one discovery failure and four missing-runtime
errors. The schema red run failed on the missing contract. An implementation
check caught the unsupported nullable-type schema form; it was corrected using
the existing schema composition rules. Six focused native tests then passed in
17.228 seconds. The combined native suite passed 53 tests in 100.466 seconds.
A final red check caught missing visibility in the state list, needed to
distinguish displayed results from hidden predecessors.
After adding visibility, the full strict Release build passed again with 12 jobs
and all six focused native tests passed in 19.186 seconds, with process exit 0.
The final schema/registry subset also passed all four checks in 3.41 seconds.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeInspect SMTests.testSheetRibbonHistory \
  SMTests.testSheetGui SMTests.testSheetOperations
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_inspect_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_capability_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_schema_rules.py \
  src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/VibeCAD/vibecad_tests/test_model_tree_role_clarity_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_ribbon_surface.py
python3 -m pytest -q src/Mod/VibeCAD/vibecad_tests/test_vibecad_package_manifest.py
```

The Python suite passed all 51 checks in 3.80 seconds; the separate full VibeCAD
module packaging check passed in 0.03 seconds. Native assistant mutations,
Sheet Metal surface action mapping, and ordinary-prompt
Qwen/Terra tool-sharpening runs remain pending. These native runtime tests do not
establish model-driven tool usability.

The build/install regression can run without loading FreeCAD:

```bash
python -m pytest -q src/Tools/tests/test_sheetmetal_installation.py
```

The native `sheet_metal.view` increment has source-level red/green coverage for
exact sheet targeting, presentation-only registration, unavailable states,
suppressed predecessors, and preserved visibility. The initial run failed on
the missing capability/runtime (one failure, ten errors). Eleven checks passed
after implementation. Additional checks then caught omitted visibility in the
result. The first combined Python suite below passed 47 checks in 9.61 seconds.
The registry's 240-character description limit also caught an overlong tool
description; the description was shortened without changing that limit.

```bash
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_view.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_inspect_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_capability_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_schema_rules.py \
  src/Mod/VibeCAD/vibecad_tests/test_vibecad_package_manifest.py
```

`SMTests.testSheetNativeView` adds real-GUI checks for cached node reuse,
round-trip representation switching, preserved document/History state,
suppression, invalid geometry, and live native discovery. The combined native
run passed 69 of 71 checks in 130.067 seconds. Both errors came from exceeding
the 64-KiB Modeling schema limit by 243 bytes. The new view contract now takes
`object_name` in its locked document; returned identities still include the
document UID. Concise inspection descriptions preserve the existing inspection
arguments. The limit is unchanged. The final focused view/inspection run passed
all ten checks in 33.244 seconds, including both discovery cases. The full strict
Release build and the subsequent incremental builds passed with 12 jobs.
The final Python registry/schema/runtime/package suite passed all 48 checks in
3.49 seconds after the compact contract change.
Ordinary-prompt sharpening remains separate from these deterministic checks.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeView SMTests.testSheetNativeInspect \
  SMTests.testSheetRecomputeOrigin SMTests.testPresentation \
  SMTests.testSheetRibbonHistory SMTests.testSheetGui SMTests.testSheetOperations
# Use a new output directory for the focused rerun.
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeView SMTests.testSheetNativeInspect
```

`SMTests.testRMFGClient` runs without FreeCAD or an RMFG account. It tests direct
REST encoding, stable upload retries, catalog cursors, durable status IDs,
identical quote/cart configuration, non-finite JSON rejection, and controlled
HTTP/transport errors. A private loopback HTTP server verifies that the default
transport refuses redirects. It does not contact RMFG or test authentication.
The initial run produced seven missing-module errors; seven checks passed after
implementation. A further red check caught JSON exponent overflow to infinity.
After correcting parsing and adding the loopback check, all eight tests passed
in 0.596 seconds. Installation/preset checks passed all four tests in 2.60 seconds.
The initial combined pytest command hit an existing `tests` package-name
collision; importlib collection resolved that harness issue.

```bash
PYTHONPATH=src/Mod/SheetMetal python3 -m unittest -v SMTests.testRMFGClient
python3 -m pytest -q --import-mode=importlib \
  src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/SendCutSendPresets/tests/test_sheetmetal_integration.py
```

The strict production build/copy passed. Browser OAuth, refresh-token
storage/serialization, persisted jobs, revision-bound native export/publication,
ribbon/native controls and authenticated live testing are not covered by this
transport increment.

`SMTests.testRMFGSnapshot` covers immutable revision/configuration capture,
exact STEP digests, retry keys, completed-design quantities, and stale quote
inputs after model edits, reopen, or Undo ABA. Six tests initially failed with
the absent module. After implementation, the combined snapshot/client suite
passed all 14 tests in 0.581 seconds. The four installation/preset checks passed
again in 2.09 seconds. These are detached data tests, not native geometry export
or authenticated RMFG tests.

The built modules also passed all 14 snapshot/client tests under the build's
Python environment in 0.524 seconds:

```bash
PYTHONPATH="$SHEETMETAL_TEST_BUILD/Mod/SheetMetal" python -m unittest -v \
  SMTests.testRMFGSnapshot SMTests.testRMFGClient
```

```bash
PYTHONPATH=src/Mod/SheetMetal python3 -m unittest -v \
  SMTests.testRMFGSnapshot SMTests.testRMFGClient
python3 -m pytest -q --import-mode=importlib \
  src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/SendCutSendPresets/tests/test_sheetmetal_integration.py
```

`SMTests.testSheetRecomputeOrigin` initially failed with three missing-method
errors. Its final native run passed all three tests in 3.810 seconds. It checks
worker provenance, GUI callback payloads, independent nested GUI edits, ordinary
untracked recompute, rejected requests, and document isolation. A focused C++
red/green probe also reproduced a helping worker inheriting another task's
origin before the fix. A source-compatibility compile probe caught the new
`RecomputeRequest` field displacing legacy aggregate callbacks; appending it
preserves those callers.

The first C++ run used a stale object linked to the updated executor layout and
crashed during cancellation. Fresh timestamps and a full strict rebuild removed
that mismatch; the executable lists all three new origin tests. The expanded
run then passed 42 of 43 tests. The remaining headless visual-lifetime test
expected GUI-observer presentation signals. Its corrected checks verify visual
lifetime during recompute and explicit blocking-presentation transitions,
without changing production behavior. All 43 C++ checks then passed in 0.665
seconds. The C++ process used a private working directory and private
`FREECAD_USER_HOME`, `FREECAD_USER_DATA`, `FREECAD_USER_TEMP`, `XDG_CONFIG_HOME`,
`XDG_DATA_HOME`, `XDG_RUNTIME_DIR`, and `TMPDIR`, like the GUI runner. Inside that
environment the test command was:

```bash
"$SHEETMETAL_TEST_BUILD/tests/App_tests_run" \
  --gtest_filter='RecomputeOriginScopeTest.*:AsyncRecomputeTest.*:MainThreadCleanupTest.*:HostWorkflowTest.*:ApplicationTest.*' \
  --gtest_output="json:$SHEETMETAL_TEST_OUTPUT/results.json"
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetRecomputeOrigin
```

Origins identify worker property events; they are not completion or Undo
receipts. Async assistant receipt finalization still needs its own integration
and validation.

Deferred parameter transactions were introduced with six failing tests for the
missing `NativeMutationRunner.start_deferred` method. The implementation closes
the transaction and mutation observer before returning prepared evidence, does
not issue an early receipt, preserves completed-call idempotency, and aborts
failed parameter edits. Additional coverage rejects both synchronous recompute
targets and after-recompute callbacks. Existing immediate mutation, revision,
state persistence, and Undo tests remain part of the regression command:

```bash
python3 -m pytest -q src/Mod/VibeCAD/vibecad_tests/test_native_mutation.py -k deferred
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_mutation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_state.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_state_persistence.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_undo.py
```

The final combined Python run passed all 115 tests in 0.43 seconds.

Two native History-service tests failed first because `start` had no optional
transaction/queue hooks. After adding the hooks, all 21 tests in
`SMTests.testSheetRibbonHistory` passed in 56.701 seconds, using the strict
Release build with sanitizers disabled. The new tests exercise a real deferred
Native transaction with tracked asynchronous geometry, the exact native Tree
and History entries, and an unavailable queue leaving an editable cut that a
later recompute repairs. The existing cases cover ordinary ribbon callers,
legacy definitions, save/reopen, suppression, repair, and stale requests.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetRibbonHistory.TestSheetRibbonHistory.test_deferred_native_runner_creates_the_same_tree_and_history_state \
  SMTests.testSheetRibbonHistory.TestSheetRibbonHistory.test_failed_custom_queue_leaves_an_editable_history_entry
# Use a new output directory for the regression suite.
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetRibbonHistory
```

The build/copy completed successfully. These tests establish parameter
transaction and shared-service behavior. Domain ownership checks before final
assistant receipt/Undo publication remain a separate integration requirement;
the fake later revision in the unit test is not native provenance evidence.

`SMTests.testSheetNativeEdit` exercises the real native completion adapter.
The initial red run had eight missing-module failures and one cleanup error:
an extra private document needed its pending GUI work drained before close.
The identified private test process was closed after its completed failure
report; no user instance was involved. Cleanup now waits for that document to
be closable. The first implementation passed all eight tests in 11.907 seconds.

Expanded coverage includes thickness/flange/material changes through a cut
chain, circle edits, suppression/restoration, exact assistant Undo after async
geometry, unrelated label ABA, nested GUI edits inside geometry notifications,
other-document isolation, tab changes, failed geometry/queue repairability,
stale preflight, completed-ticket replay, client cancellation, and an unexplained
structural revision. The combined run passed all 36 tests in 72.491 seconds:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeEdit SMTests.testSheetRecomputeOrigin SMTests.testSheetRibbonHistory
```

A separate red test cancelled the client Future during receipt publication,
reproducing `CancelledError` despite a stored receipt. Completion now uses the
Future's atomic running/cancelled transition before verifying and publishing.
All 13 final native edit tests passed in 23.400 seconds; the strict build/copy
passed. The final installation/preset run passed all four tests in 1.10 seconds
and checks byte-for-byte installation of the new adapter and test module.

```bash
# Use a new private output directory for each native invocation.
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeEdit.TestSheetNativeEdit.test_cancellation_cannot_race_receipt_publication_once_completion_starts
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeEdit
python3 -m pytest -q --import-mode=importlib \
  src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/SendCutSendPresets/tests/test_sheetmetal_integration.py
```

The default private native runner now includes these edit tests. Provider
dispatch/schema integration and ordinary-prompt sharpening remain pending;
these are domain/geometry tests, not a model-driven usability run.

The native edit contract/binding source tests first failed with nine absent
definition failures and 14 missing-runtime errors. Registration/schema/runtime
tests then passed 25 checks. Two source surface tests reproduced the absent
Sheet Metal inventory. Explicit action classification passed the expanded 83
checks. Further red tests caught a wrong-capability ticket being accepted and a
domain preflight error lacking repair details. The final source run passed all
117 checks in 9.22 seconds, including the native dispatcher regression for
preserving retained parameter/feature details while excluding unknown fields:

```bash
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_edit.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_action_manifest.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py
```

`SMTests.testSheetNativeDispatch` runs real geometry through the production
registry, runtime bindings, and `NativeTurnDispatcher`. The tool-execution tests
use a controlled provider snapshot containing the edit contract; a separate
test activates the real Sheet Metal workbench and resolves its live ribbon.
The initial built-runtime red run had three absent-definition errors. After
integration, 25 of 27 combined native tests passed in 57.298 seconds. The two
failures exposed shared View/Inspect actions missing from the inventory and
repair details filtered out by the dispatcher. After fixing both and adding a
preflight repair check, all five focused tests passed in 11.463 seconds. The
strict Release build passed with 12 jobs. Existing Model discovery passed in
the combined run; no schema limit was raised.
The final package manifest and installation/preset checks passed all five tests
in 1.17 seconds.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeDispatch SMTests.testSheetNativeEdit \
  SMTests.testSheetNativeView SMTests.testSheetNativeInspect
# Use a new private output directory for the focused rerun.
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeDispatch
python3 -m pytest -q --import-mode=importlib \
  src/Mod/VibeCAD/vibecad_tests/test_vibecad_package_manifest.py \
  src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/SendCutSendPresets/tests/test_sheetmetal_integration.py
```

Live discovery remains unavailable while `sheet_metal.create` is incomplete.
Its `from_source` operation is now implemented; finish the other three Create
actions and update the live availability expectation before ordinary-prompt
tool sharpening. The dispatcher tests are not proof of a complete
provider-selected Sheet Metal workflow.

`SMTests.testSheetNativeCreation` began with four missing-method errors. The
native creation entry point and optional tracked queue then passed all 24
creation/History/edit tests in 41.922 seconds. They verify shared folded/flat
geometry, one owned Undo entry, native History editor metadata, presentation-only
switching, source visibility restored by assistant Undo, concurrent-edit
rejection, stale preflight, and invalid-face rollback.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeCreation SMTests.testSheetHistory SMTests.testSheetNativeEdit
```

The `from_source` provider contract first produced one absent-definition
failure and seven missing-runtime errors. After registration, 37 source tests
passed in 10.23 seconds. The full strict Release build passed with 12 jobs.
The final native creation/dispatch/edit run passed all 23 tests in 40.366
seconds, including the registered creation runtime and the live surface's
updated incomplete-Create status. Package manifest and installation/preset
checks passed all five tests in 0.99 seconds.

```bash
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_create.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_edit.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_registry.py
# Use a new private output directory.
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeCreation SMTests.testSheetNativeDispatch SMTests.testSheetNativeEdit
python3 -m pytest -q --import-mode=importlib \
  src/Mod/VibeCAD/vibecad_tests/test_vibecad_package_manifest.py \
  src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/SendCutSendPresets/tests/test_sheetmetal_integration.py
```


### RMFG manufacturing panel and worker state

`testRMFGManufacturing` began with six missing-module errors, and the first five
private GUI tests failed before the panel existed. Additional failing GUI tests
cover clearing a selected material, rejecting a second request without changing
the running job identity, retrying an uncertain replacement quote with the saved
key, and keeping an older quote superseded after a catalog refresh. These tests
use fake RMFG responses, private job storage, and real folded STEP exports; they
do not authenticate, upload a model, or create a remote cart.

Run against the completed strict Release build with its dependency environment
active. Each GUI invocation needs a new output directory; do not rebuild its
runtime while a test process is running.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
PYTHONPATH=src/Mod/SheetMetal python3 -m unittest \
  SMTests.testRMFGManufacturing SMTests.testRMFGJobs \
  SMTests.testRMFGClient SMTests.testRMFGSnapshot -v
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testRMFGManufacturingGui SMTests.testRMFGManufacturing SMTests.testRMFGExport
python3 -m pytest -q src/Tools/tests/test_sheetmetal_installation.py
# Use a separate new output directory for Tree and History regressions.
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_HISTORY_OUTPUT" \
  SMTests.testSheetTree SMTests.testSheetHistory SMTests.testSheetCutHistory \
  SMTests.testSheetSourceCreation SMTests.testSheetNativeInspect SMTests.testSheetNativeView
```

The completed strict Release build passes with 12 jobs. Against its installed
modules, all 33 manufacturing/storage/client/snapshot checks pass, as do all
24 private GUI manufacturing/export checks and all 55 Tree/History regressions.
The installation test passes and compares the new runtime modules and tests
byte-for-byte. A subsequent layout regression exposed a clipped thickness
heading; its focused red result is retained with the other GUI evidence.
All 10 panel checks pass after sizing that column to its contents; the resulting
panel screenshot has been inspected. Authenticated RMFG verification remains pending.


### Direct RMFG native/ribbon access

`SMTests.testRMFGNativeManufacturing` exercises the real command registration,
shared panel/controller, live provider-surface discovery, asynchronous native
runtime and production dispatcher. It uses a private document and fake remote
responses, without MCP or live RMFG uploads. Tests reject foreign documents and
stale completions, preserve current request identity on call replay, and prevent
cancelled checkout work from opening a browser. An external document edit must
also reject the next call until the provider starts a fresh turn.

The initial source run had five failures and sixteen missing-runtime errors;
the initial GUI run had six missing-controller-factory errors. After integration,
all 19 combined native/connection/panel checks pass. The final focused eight
native tests pass after bounded material pagination and dispatcher coverage.
All 120 source and package checks pass against a completed strict Release build.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testRMFGNativeManufacturing SMTests.testRMFGNativeConnection SMTests.testRMFGManufacturingGui
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_manufacturing.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_action_manifest.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_vibecad_package_manifest.py \
  src/Tools/tests/test_sheetmetal_installation.py
```


### Saved RMFG jobs and document closure

`testRMFGSavedJobs` covers local metadata-only history, hidden service fields,
original-key retries, known-resource reads, and stale/foreign revision rejection
before STEP loading or network access. `testRMFGSavedJobsGui` verifies settings
restoration, read-only historical results, late-result rejection after document
or quantity edits, and controller/panel cleanup when the document closes.
`testRMFGNativeManufacturing` additionally restores a saved quote through the
registered native tools after closing the original panel.

The initial six backend tests had nine absent-method errors, six GUI tests had
five absent-control/method errors and one document-close failure, and the native
contract run had four failures. After implementation, 39 backend tests and 70
native contract/dispatch/package checks pass. All 24 combined GUI tests pass.
The final 15 saved-job/native tests also pass in a separately copied Release
runtime whose ELF library paths were relocated; this checks that the preview
does not depend on modules left in the original build directory.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
PYTHONPATH=src/Mod/SheetMetal python3 -m unittest \
  SMTests.testRMFGSavedJobs SMTests.testRMFGManufacturing SMTests.testRMFGJobs \
  SMTests.testRMFGSnapshot SMTests.testRMFGClient -v
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testRMFGSavedJobsGui SMTests.testRMFGManufacturingGui SMTests.testRMFGNativeManufacturing
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_manufacturing.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_vibecad_package_manifest.py \
  src/Tools/tests/test_sheetmetal_installation.py
```


### Source versus shared-sheet view repair

The ordinary SM-P01 trial rejected two view requests against an upstream source.
A provider-result regression first failed with a missing `repair` field (one
failure, 13 passes); the isolated view suite reproduced that missing field (one
error, four passes). Both pass after adding recovery guidance while retaining
`NativeTargetError`, its error code and exact target/type metadata. No view
request creates a shared state automatically. The GUI regression follows the
inspection advice to find the existing sheet and checks unchanged geometry,
visibility, document revision, Undo and native History.

With the build dependency environment active, run:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_view.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_targets.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeView SMTests.testSheetNativeInspect
```

Results: strict Release build exit 0; 58 source tests passed in 3.13 seconds;
13 native GUI tests passed in 41.165 seconds, process exit 0. Output directories
must be fresh. These deterministic checks do not replace the next matching
ordinary-prompt Qwen/Terra trials.


### Shared base dimensions

Initial red checks: three native schema failures (25 existing passes) and three
GUI failures for missing shared height/width support. Validation commands:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_edit.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_inspect_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetSourceCreation SMTests.testSheetNativeView
```

Strict Release build exit 0; 72 source tests passed in 12.66 seconds. All 18
source-creation GUI tests passed in the combined 23-test run. Its remaining
error was a separate workspace test calling the runtime without its operation
field, now being corrected with production workspace authorization coverage.
Earlier runs also passed all existing SheetOperations, SheetGui, and native
edit tests. Two new test-fixture mistakes were corrected: retaining the async
job handle and rebuilding transient mappings through an actual edit after
reopen instead of assuming a no-op recompute prepares unchanged saved shapes.


### Agent ribbon navigation

Owner authorization lifts the former human-only ribbon restriction. Red tests
reproduced missing SheetMetal navigation, stripped production workspace access,
and missing Analysis/Manufacture navigation. The live sweep also caught missing
Print classification and the Model schema byte limit. The switch description
was shortened without raising that limit. All seven native view/workspace tests
now pass in 28.365 seconds, process exit 0, including every ribbon workspace.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeView
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_analyze_provider_scope.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_manufacture_provider_scope.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_action_manifest.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_ribbon_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_variants.py
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_action_manifest.py \
  src/Mod/VibeCAD/vibecad_tests/test_mcp_control_mode.py
```

Build exit 0; source suites: 188 passes in 4.54 seconds and 84 passes in 2.94
seconds. Model/Assembly and other destination tool families are discovered from
the actual ribbon. Authoring mode, active-edit guards, and document ownership
are unchanged. The user's separate preview runtime was not modified.


### Native Assembly handoff

The real dispatcher can create an assembly and insert a placed sheet with native
cut History. The integration test verifies translation, rotation, linked-source
identity, and propagation of a later wall-height edit. No production change
was needed. An initial fixture failure captured the old ribbon before queued
activation completed; waiting for the actual Assembly surface fixed the test.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetSourceCreation.TestSheetSourceCreation.test_native_assembly_places_a_linked_sheet_and_keeps_its_editable_history
```

Strict Release build exit 0; one native integration test passed in 5.997 seconds,
process exit 0. This verifies component placement and editing, not cabinet
clearances, joints, or motion.


### Optional return-flange creation input

Red: seven source failures (15 existing passes), three GUI subcase errors when
`flange_width` was omitted. Green commands:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python3 -m pytest -q src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_create.py
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetSourceCreation SMTests.testSheetSourceForms
```

Build exit 0; 22 source passes in 7.59 seconds; 27 GUI passes in 71.488 seconds,
process exit 0. Omitted width matches the human ribbon's 8 mm default, with
matching solid volume and bounds for L, Tub and Hat sources. Existing explicit
values and source parameter properties remain unchanged.


### Folded-center region repair

Red: two source failures (28 passes) and one native GUI failure for missing
conditional documentation/actionable repair. Green validation:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_create.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_edit.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeEdit SMTests.testSheetNativeDispatch
```

Build exit 0; 94 source passes in 18.55 seconds; 19 native GUI passes in
38.068 seconds, process exit 0. The GUI case follows the inspection repair,
creates a folded bend-crossing hole, and performs a radius-only edit without
requiring a region. Missing-region rejection preserves geometry and Undo.


### Ordinary bracket trials after cabinet feedback

At production revision `7a713665`, reran the unchanged SM-P01 request through
the real provider in isolated Release GUIs. Use a distinct output directory for
each invocation and the configured provider credentials/local model endpoint:

```bash
VIBECAD_SHEET_PROMPT_MODEL=gpt-5.6-terra \
VIBECAD_SHEET_PROMPT_AUTH=chatgpt \
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TERRA_OUTPUT" \
  SMTests.live_sheet_prompt
VIBECAD_SHEET_PROMPT_MODEL=qwen3.5:9b \
VIBECAD_SHEET_PROMPT_AUTH=api_key \
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_QWEN_OUTPUT" \
  SMTests.live_sheet_prompt
```

Both processes exited 0. Terra: one pass in 53.772 seconds, 12 tool calls with
four rejected calls. Qwen: one pass in 183.575 seconds, 11 calls with six rejected
calls. Each created exactly one shared sheet with valid folded/flat geometry,
requested dimensions, gauge and bend radius, and successful representation
switches. Full events, responses, saved documents, geometry reports, STEP files
and images were retained locally. Neither prompt included tool instructions;
neither process had a timeout or was restarted.

These trials still expose invented arguments, missing required settings, and
source/shared confusion before recovery. They do not test Assembly navigation,
cabinet joints, first-save support, or live RMFG. This entry records existing
test results only; no implementation changed, so red/green TDD does not apply
to the documentation update.


### Native switching after save/reopen

Red: one source failure (14 passes) and two native GUI errors because the view
tool required a restored edit mapping despite current saved meshes. Green:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_view.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetNativeView SMTests.testPresentation SMTests.testSheetSelection
```

Strict Release build exit 0; 52 source passes in 2.50 seconds; 32 native passes
in 61.764 seconds, process exit 0. Saved base-sheet and cut-state switches retain
cached nodes, geometry fingerprints, document state, visibility, Undo and History.
The tests prohibit meshing, unfolding and BREP serialization during switching,
retain the mapped-edit guard, and reject the saved display after a source edit.
Existing rendering, surface picking and stale-worker regressions also pass.
The 200-call cached-switch sample averaged 0.070 ms, with a 0.232 ms maximum;
this measures the switch call only, excluding GPU frame time.


### Restored edit mapping preparation

Red: the new native regression module failed to import the missing preparation
API. It now covers eight cases: restored legacy cuts and an actual subsequent
hole edit; mixed profile/circle History with suppression; existing-ancestor
reuse; already-ready cache reuse; cancellation; document closure; source edits
during preparation; and saved-fingerprint mismatch.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetPreparation SMTests.testEditableSheet SMTests.testProfileCuts \
  SMTests.testSheetCutHistory SMTests.testSheetProfileHistory
python3 -m pytest -q \
  src/Tools/tests/test_sheetmetal_installation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_edit.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_view.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py
```

Strict Release build exit 0. All 70 native tests passed in 183.068 seconds,
process exit 0; all 51 source checks passed in 13.20 seconds. Preparation runs
off the GUI thread, retains persisted shapes, document state, Undo and History,
and publishes no mapping after cancellation or stale-input rejection. Normal
feature recompute, geometry edits, save/reopen and Undo/Redo regressions remain
green after sharing the detached geometry routines. Panel/tool wiring and live
manufacturing are not covered by this API test suite.


### Prepare reopened sheets from the editing panel

Red: three native panel errors for the missing preparation control. Green:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetPreparation SMTests.testSheetGui SMTests.testSheetRibbonHistory
python3 -m pytest -q src/Tools/tests/test_sheetmetal_installation.py
```

Strict Release build exit 0; 45 native tests passed in 86.416 seconds, process
exit 0; one installation check passed in 1.08 seconds. A restored sheet can be
prepared and receive a native hole through the same panel, with no preparation
Undo entry and exactly one subsequent cut entry. Preparation hides its control
when ready, disables editing while pending, restores controls on failure, and
discards cache publication if the panel closes. Existing committed-edit closure,
material/dimension edits, selection isolation and History panels remain covered.


### Native agent preparation of restored sheets

Red: 12 source failures for the missing variant/async runtime; one native
dispatcher failure because `prepare` was not an available operation.

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_inspect_schema.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_preparation.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_surface.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_registry.py \
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_variants.py
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetPreparation SMTests.testSheetNativeInspect \
  SMTests.testSheetNativeDispatch SMTests.testSheetNativeView
```

Build exit 0; 68 source checks pass in 4.55 seconds. The combined native suite
passed 34 of 35 cases in 83.122 seconds; the remaining new test had not initialized
assistant Undo ownership before trying a cut. Production correctly rejected it.
The fixture now initializes the same native authority and Undo run as existing
edit tests. A focused rerun with a fresh output directory passed both cases in
6.715 seconds, process exit 0:

```bash
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testSheetPreparation.TestSheetPreparation.test_native_preparation_is_a_read_and_next_cut_has_one_undo \
  SMTests.testSheetPreparation.TestSheetPreparation.test_native_preparation_rejects_a_source_edit_while_pending
```

The real dispatcher verifies no document change during preparation, successful
region inspection and a subsequent valid cut with one Undo entry in the same
turn. Source checks cover cancellation, stale authority/revision, worker failure,
fingerprint mismatch and exact-target validation. Ribbon/schema/view regressions
passed in the combined run; no schema size limits were raised.


### Manufacturing export after reopening

Red: one missing-wrapper error and one manufacturing-panel failure requiring
manual geometry preparation. Green:

```bash
cmake --build "$SHEETMETAL_TEST_BUILD" --parallel 12
python src/Mod/SheetMetal/SMTests/run_native.py \
  --build "$SHEETMETAL_TEST_BUILD" --output "$SHEETMETAL_TEST_OUTPUT" \
  SMTests.testRMFGExport SMTests.testRMFGManufacturingGui \
  SMTests.testRMFGNativeManufacturing SMTests.testRMFGSavedJobsGui
python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_manufacturing.py \
  src/Tools/tests/test_sheetmetal_installation.py
```

Strict Release build exit 0; 39 native tests pass in 60.103 seconds, process exit
0; 30 source checks pass in 3.36 seconds. The new real STEP round trip verifies
folded bounds, volume and a single valid solid from a reopened sheet displayed
flat. Preparation/export preserve the document revision, shapes and Undo.
Cancellation or source edits during preparation prevent the STEP writer from
starting, and controller closure prevents upload. Existing prepared exports,
quote staleness, native tools and saved-job tests remain green. RMFG service
responses are mocked; this is not authenticated remote verification.
