# Sheet Metal Windows acceptance audit

This is a coverage ledger, not a claim that a finite test run proves the absence
of bugs. Do not infer whole-workbench readiness from the new ribbon suite alone.

## Baseline

PR #228 at `2aa49687` plus the Windows packaging fix `e581b00b`:
358 native GUI tests, 441 unit tests, and separate cold save/reopen checks passed.
The packaging regression first failed, then passed after preserving the
Spreadsheet test package directory. The complete incremental build passed.

## Reproduced defects and fixes

Each fix below followed a failing regression against the actual implementation.
Native tests use compiled geometry and private documents; task tests click the
real Qt OK/Cancel buttons, including native transaction finalization.

- **Windows packaging:** flattened Spreadsheet test files shadowed the native
  Spreadsheet module. Preserve the test package directory during installation.
  The regression builds and installs a CMake fixture, then checks its layout.
- **Windows test cleanup:** explicitly close the test's SQLite connection before
  its temporary database is deleted. Production already closes its connection.
- **Extend by Sketch:** `execute` accessed `Sketch.ViewObject` on the recompute
  worker. Use the existing document-owned presentation dispatcher instead.
  Geometry remains on the worker; the sketch is hidden on the GUI thread.
- **Selection handling:** guard incomplete sketch attachments, empty flange/hem/
  junction selection, and non-face selection in both Unfold commands. Invalid
  selections disable commands without raising exceptions.
- **Solid corner relief:** a relief larger than sheet thickness used an
  out-of-face point to choose its cutting direction and produced intersecting
  wires. Probe locally to determine direction without changing the requested
  relief size. Native tests verify exact removed volumes for 0.5, 2 and 4 mm
  reliefs in 1.6 mm stock.
- **Extruded Cutout:** swallowed input errors left old geometry marked current.
  Let the existing document recompute handler record the failure and mark the
  feature Invalid. Its editable inputs and previous shape remain available for
  repair; undoing the missing-profile edit restores a valid cut. The owner
  approved this error-semantics correction in the task conversation.
- **Task acceptance:** use the owning document and the existing feature validity
  check before committing. A failed cut remains open for repair; restoring its
  sketch allows normal OK completion. No alternate error framework is added.
- **Legacy base-shape task:** return the success boolean required by the native
  task-dialog contract. Actual OK previously removed the newly created feature;
  OK/Cancel, new Body membership, Undo and Redo now pass.

## Executable coverage

| User operation | Coverage |
| --- | --- |
| Base shapes: Flat, L, U, Tub, Hat, Box | Existing creation/forms, geometry, reopen and cabinet-sized Box; added legacy task OK/Cancel, new Body, Undo/Redo |
| Sheet from sketch / solid | Existing sources, forms and ownership; added legacy task OK/Cancel and Undo/Redo |
| Flange / internal fold | Existing source creation, forms, relief-cut tab and native dispatch; added legacy flange task OK/Cancel and Undo/Redo |
| Shared folded/flat views | Existing presentation, cached meshes, source revisions, selection and no-recompute switching |
| Holes, profile and bend-spanning cuts | Existing geometry, repair, Undo and reopen; additional retained cutout task repair |
| Dimensions, material, allowance | Existing native edit and invalid-input recovery |
| Unfold | Existing document, mapping and presets; added edge/vertex selection guards |
| Hem: Flat, Open, Teardrop, Rolled | Added valid single-solid geometry for every type; legacy task OK/Cancel and Undo/Redo |
| Extend / Extend by Sketch | Added worker recompute, exact extension/reverse-cut volumes, task lifecycle, edit, Undo/Redo, save/reopen |
| Solid bend / junction / relief | Added actual rounded geometry, corner opening, exact relief volumes; relief task lifecycle |
| Bend corner relief | Added Circle, Square, scaled and Weld variants with valid changed geometry |
| Sketch on sheet / extruded cutout / forming | Added exact cut volumes, formed geometry/suppression, task OK/Cancel and Undo/Redo |
| Retained command selection | At least 18 classes with empty, whole-solid, unattached sketch, edge and vertex selections |
| RMFG | Existing native connection, export, manufacturing and saved-job fixtures; no live Windows orders/uploads performed |

These checks do not exhaust every geometric arrangement. In particular, direct
legacy New Sketch and Unfold Update task lifecycles are not fully covered by the
added task tests. No live remote-service claim is made for this Windows audit.

## Final validation

Focused legacy suite: 16 tests passed in 30.278 seconds; subsequently added
base-shape task regression passed in 2.503 seconds after failing before its fix.
The final combined native suite, including all 17 added regressions:
**375 tests passed in 848.637 seconds**, with successful unattended completion.
Final packaging/RMFG/preset unit run: **89 tests and 113 subtests passed in 14.30 s**.
Final changed VibeCAD unit suite: **352 tests passed in 63.84 s**.
Final full incremental build, including the added test resource: **passed (exit 0)**.

Commands used from the repository root, with private build locations represented
by variables (the Windows launcher and macro are local audit harnesses):

```powershell
# Full native suite from SMTests/run_native.py's default module list:
& ./build/pr228_after_build.ps1 -BundlePath $Bundle
# Focused red/green native tests:
& ./build/pr228_after_build.ps1 -BundlePath $Bundle -Tests SMTests.testSheetLegacyOperations

# Full incremental build in the activated Rattler build work directory:
cmd.exe /d /c "call build_env.bat >NUL 2>&1 && ninja -C build"

$env:PYTHONPATH = "$PWD/src/Mod/SheetMetal"
$env:CMAKE_GENERATOR = 'Ninja'
python -m pytest -q --import-mode=importlib `
  src/Tools/tests/test_spreadsheet_installation.py `
  src/Tools/tests/test_sheetmetal_installation.py `
  src/Mod/SheetMetal/SMTests/testRMFGClient.py `
  src/Mod/SheetMetal/SMTests/testRMFGAuth.py `
  src/Mod/SheetMetal/SMTests/testRMFGJobs.py `
  src/Mod/SheetMetal/SMTests/testRMFGManufacturing.py `
  src/Mod/SheetMetal/SMTests/testRMFGSnapshot.py `
  src/Mod/SheetMetal/SMTests/testRMFGSavedJobs.py `
  src/Mod/SendCutSendPresets/tests `
  --junitxml=build/pr228-unit-results-final.xml

$env:PYTHONPATH = "$PWD/src/Mod/VibeCAD"
$tests = @(git diff --name-only 01bae317...HEAD -- 'src/Mod/VibeCAD/vibecad_tests/test_*.py')
python -m pytest -q @tests --junitxml=build/pr228-vibecad-unit-results-final.xml
```

The automated test window is labelled as such. Negative tests deliberately
produce Report-view errors; test identities and recovery assertions distinguish
those from unexpected failures. Production errors are not suppressed. Customer
documents and the user's separate portable instance are left untouched.

## Restored-display performance follow-up

The saved Sheet Metal case exposed a distinct presentation bottleneck after the
earlier acceptance run. Hidden history states occupied both mesh workers before
the visible final feature, delaying its start by about 39 seconds. Per-vertex
surface extraction and repeated planar-face normal evaluation then repeated
expensive trimmed-face work. Automatic display requests now defer hidden,
unlinked history, cancel superseded hidden work, and wake on visibility changes.
Linked instances still receive their hidden source's meshes. The same surface
and constant planar normal are reused; curved normals, face orientation, source
geometry, forced requests and the public mesh return format are unchanged.
Pending meshing is shown in a dedicated status-bar label, without overwriting
another operation's message.

Red tests demonstrated 504 surface reads instead of 3, 252 planar normal reads
instead of 2, meshing after a hidden-only update, and a missing pending-status
indicator. A link-source regression also failed against the initial hidden-only
guard before correcting that guard. Green integration: **129 tests passed in
291.813 seconds**, using compiled Part geometry and the real private GUI:

```powershell
& ./build/pr228_after_build.ps1 -BundlePath $Bundle -Tests 'SMTests.testPresentation,SMTests.testSheetSelection,SMTests.testSheetNativeView,SMTests.testSheetNativeInspect,SMTests.testSheetOperations,SMTests.testSheetHistory,SMTests.testSheetCutHistory,SMTests.testSheetProfileHistory,SMTests.testSheetGui,SMTests.testSheetTree'
```

The identically instrumented saved-file diagnostic fell from **88.875 s to
7.157 s** to display-ready (including its startup delay); final mesh preparation
fell to **2.784 s**. Separate unprofiled copied-file opens/reopens took about
5-6 seconds. Mesh publication took about 16 ms in the instrumented final run.
These are measurements on one Windows host/file, not universal latency promises.

The reusable macro opens its own copy three times, including Fit All during
loading, asserts unchanged sheet geometry/state/visibility inventory, records
camera and display-poll timing, and captures images without visibility or
recompute interventions. Run in a fresh GUI with an isolated profile:

```powershell
$env:VIBECAD_SHEET_BENCHMARK_SOURCE = $SavedDocumentCopy
& ./build/pr228_after_build.ps1 -BundlePath $Bundle -Probe 'pr228-windows/src/Tools/performance/sheetmetal_restore_probe.py'
```

The launcher supplies `VIBECAD_TEST_OUTPUT`; other launchers can set it directly
and run `src/Tools/performance/sheetmetal_restore_probe.py` as the macro. The
original file is never saved or changed. This follow-up is Python-only and was
tested in a separate copy of the already fully built portable, leaving the live
user instance untouched. No new native binary or release build is claimed.

## Cross-workspace context and repair follow-up

The resumed Codex thread path previously removed recent conversation replay
unconditionally. Returning from another workspace could therefore omit intervening
user instructions. Tool-only workspace switches also had no durable conversation
entry when the provider returned no prose.

The session now reuses its canonical conversation records for a bounded handoff:
original/latest requirement anchors, unseen cross-thread events and recorded tool
outcomes. A per-provider-thread cursor advances only after successful delivery;
failure, interruption, compaction and thread replacement invalidate it. The current
user message appears once. Small original/latest anchors intentionally remain;
already-delivered event history is not replayed. The new read-only
`conversation.read` tool pages the same records (including full recorded content
and bounded tool-activity metadata); it does not create a second history store or
claim historical geometry is current. Excerpts and omitted events are explicit.
Large raw CAD outputs are still not persisted/replayed as conversation history.
The session tool is declared beside, not inside, frozen CAD authority, including
the other provider adapters. Tool-only and failed-turn outcomes are retained so
continuations do not infer success from missing prose. Steering remains in the
recorded tool outcomes.

Every workspace's navigation context now explains the existing-source repair
route. Sheet inspection links the real upstream source and the existing paginated
history/profile references. Manufacturing responses point to that inspection and
require fresh analysis/quote after repairs. `sketch.open` correctly names the
provider's `sketch.finish` tool; the internal `sketch.control` API remains intact.
No DFM-to-face mapping, design dimensions or automatic repair choices are invented.

Red regressions covered missing handoff state/cursor, a resumed thread losing a
new instruction, missing workspace/manufacturing repair guidance and missing
native source pointers. Broader verification caught duplicated current-user text
and a description exceeding the existing Modeling schema budget; both were fixed
without increasing that budget. The stale schema-description assertion was updated
to require the actual provider-facing finish tool.

The real-GUI workflow follows a profile cut's stored sketch reference through
Sheet Metal -> Parameters -> Modeling -> Sketching, uses native sketch-open and
sketch-finish runtimes, changes its existing constraint, returns to Sheet Metal,
and verifies changed, valid geometry with unchanged object identities. It requests
fresh analysis and a quote, then repeats for a bend-only parameter edit. Only the
RMFG controller responses are mocked; no live order or model-generated repair is
claimed. The complete affected native suite and provider tests below are the
acceptance boundary, not a promise of every possible geometric repair succeeding.

Commands from the worktree (the private Python environment contains the same
MCP 2.0.0 package as the portable; the initial base environment lacked it):

```powershell
$env:PYTHONPATH = "$PWD/src/Mod/VibeCAD"
& ../pr228-context-test-env/Scripts/python.exe -m pytest -q `
  src/Mod/VibeCAD/vibecad_tests/test_conversation_handoff.py `
  src/Mod/VibeCAD/vibecad_tests/test_codex_subscription.py `
  src/Mod/VibeCAD/vibecad_tests/test_model_context_contract.py `
  src/Mod/VibeCAD/vibecad_tests/test_native_surface_continuation.py `
  src/Mod/VibeCAD/vibecad_tests/test_native_session.py `
  src/Mod/VibeCAD/vibecad_tests/test_mcp_tool_servers.py `
  src/Mod/VibeCAD/vibecad_tests/test_gemini_provider.py `
  src/Mod/VibeCAD/vibecad_tests/test_provider_history_budget.py `
  src/Mod/VibeCAD/vibecad_tests/test_native_workspace_schema.py `
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_manufacturing.py `
  src/Mod/VibeCAD/vibecad_tests/test_native_model_structure_schema.py `
  src/Mod/VibeCAD/vibecad_tests/test_native_sheetmetal_provider.py `
  --tb=short --junitxml=../pr228-context-final.xml
```

Native suite command is the same ten-module command in the performance section,
now including the source-pointer and full repair-workflow regressions. The full
incremental build uses the above `build_env.bat`/Ninja command after copying the
changed sources into the Rattler source sandbox. CMake regenerates and includes
`VibeCADConversationContext.py` in its installation manifest. No running user
portable is patched or restarted for these checks.

Final combined provider/session/tool suite: **291 passed, 1 skipped in 47.50 s**.
The skip is the optional installed `cua-driver` integration, not a CAD or provider
failure. Final combined real-GUI suite: **131 passed in 278.513 s**, successful
unattended completion. Full incremental native build: **passed, exit 0**.
After the final compact sketch-open wording was copied into the review portable,
the exact surface-discovery, all-ribbon navigation and DFM repair workflow checks
also passed: **3 tests in 20.378 s**. Command:

```powershell
& ./build/pr228_after_build.ps1 -BundlePath $Bundle -Tests 'SMTests.testSheetNativeInspect.TestSheetNativeInspect.test_model_surface_discovers_the_native_tool,SMTests.testSheetNativeView.TestSheetNativeView.test_each_ribbon_workspace_retains_an_agent_route_to_other_tools,SMTests.testSheetNativeView.TestSheetNativeView.test_dfm_repair_follows_existing_sketch_across_workspaces_and_reanalyzes'
```
