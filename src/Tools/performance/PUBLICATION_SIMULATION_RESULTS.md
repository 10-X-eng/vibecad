# Publication and simulation measurements

Measurements are evidence for the named stages only, not a whole-application
responsiveness guarantee. Customer documents and traces remain outside Git.

## Exact-player reuse and invalidation, September 8

The same task-wrapper identity defect also affected Native launch verification,
failed-launch cleanup and cancellation, plus the service stop postcondition.
`activeTaskDialog()` returns a new Python wrapper per call; ownership now checks
the exact hosted Qt widgets instead. A replacement task is not closed by cleanup
of an older launch. Public signatures and task-conflict behavior are unchanged.

Red: fresh-wrapper launch failed its exact postcondition, and an unclosed task
was incorrectly reported stopped. Green: 39 tests passed in 5.31 s using:

```powershell
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_playback.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py -q --tb=short
```

The compiled-GUI Native lifecycle gate is not yet green: its first run stopped
at a revision conflict following fixture creation/save, before playback launch.
That must be attributed and the gate rerun; unit results do not replace it.

The actual AI service previously rejected an already-open saved simulation,
forcing close/reopen and another roughly 48 s generation. The additive reuse
entry now waits for exact native frame adoption without regenerating. An
isolated GUI replay on the 3,022-object robot completed three AI seeks in
0.216, 0.248 and 0.354 s, then ran Play for three seconds without a frame error.
Initial generation/adoption remained 48.26 s (68.27 process CPU seconds); this
fix does not claim to accelerate the solve. Maximum heartbeat gap including
opening was 0.641 s. Original document and project files were untouched.

The first GUI replay caught a mismatch absent from the initial unit fixture:
`Gui.Control.activeTaskDialog()` constructs a fresh Python wrapper on every
call. Wrapper identity cannot prove task ownership. Lookup and queued player
cancellation now verify the exact hosted Qt form instead. Unrelated tasks,
objects and presentations remain protected. The GUI replay uses
`VIBECAD_SIMULATION_PLAYBACK_ASYNC=1`, `VIBECAD_SIMULATION_AI_PLAYBACK=1`,
`VIBECAD_SIMULATION_AI_RESEEK=1` and an explicit Python candidate source tree;
it is not evidence of a rebuilt native package. The retained probe can run
without candidate injection against the final archive.

Native lifecycle regression: changing `jFramesPerSecond` invalidated solved
channels even though the solver does not read it. The reuse assertion failed
before the fix; all four Assembly native tests now pass (0.758 s). Real input
placement edits still invalidate. The first invalidating object/property is
retained in the Assembly log. This is not proof of the original user's exact
stale-result cause. QMutex teardown warnings remain in the native test runner.

Verification commands:

```powershell
cmd /d /c build\authoritative-runtime-build.cmd Assembly_tests_run
build/authoritative-runtime-native/bin/Assembly_tests_run.exe
$tests=(Get-ChildItem src/Mod/VibeCAD/vibecad_tests -Filter 'test_codex*.py').FullName
.pixi/envs/default/python.exe -m pytest @tests src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_playback.py src/Mod/VibeCAD/vibecad_tests/test_vibescript_publication_progress.py src/Mod/VibeCAD/vibecad_tests/test_assembly_solver_policy.py -q --tb=short
```

Use the configured native dependency and module directories on PATH and its
matching Python home for the native command. Consolidated Python checks:
130 passed in 8.67 s. Added regressions were red before implementation.
The complete official Codex 0.153.4 Windows package installation and actual
stdio app-server smoke passed (0.281 s handshake); final archive verification
remains required. Six platform assets are pinned to official release digests.

## Live publication rollback freeze, 2026-09-08

Three nonblocking Python stack samples taken while the UI reported 894/1159
showed MainThread in `JointObject.solveIfAllowed -> assembly.solve`, called by
`Joint.onChanged` from the publisher's `doc.abortTransaction()`. Observer stacks
also showed solver-induced property notifications. The operation coordinator
and provider progress delivery were waiting for the document dispatcher.

Read-only inspection of the live exception recovered the original failure:
an existing joint at History position 1037 depended on a newly published
component at position 1959. Native chronology validation rejected publication;
rollback then triggered interactive solves while restoring joint offsets.
The retained result confirms its connector changed from the old ground component
to the newly added travel component. Do not
weaken chronological validation or treat a worker-completed result as published.

The joint callback now excludes `Document.Transacting` (native transaction
replay, not merely an open command transaction), as it already excluded loading.
This prevents recompute, presolve and autosolve from overwriting replayed state.
Focused command:

```
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_assembly_solver_policy.py -q --tb=short
```

Red: 5 failed, 9 passed. Green: 14 passed. These execute the production callback
for offset, distance, angle and reference changes, both during replay and normal
editing. The first isolated
GUI attempt encountered a recovery dialog before opening its test copy and was
stopped; it is not passing evidence. No document was saved. The user application
was terminated only after explicit permission; the user reported needing recovery.

The corrected isolated GUI lifecycle probe opened the 3,022-object robot copy,
changed a real joint offset, aborted, then committed/undid/redid another change.
It verified original poses after abort and exact offsets after undo and redo,
waiting for native presentation completion between actions. Result: `ok=true`,
zero interactive solve attempts; abort took 0.093 s. The solve observer records
attempts without running the erroneous solver on a failing baseline. An earlier
attempt called undo before presentation settled and correctly got a busy
rejection; that attempt is not passing evidence. The permanent probe is
`joint_transaction_replay_probe.py`.

The same real-document probe against the unpatched packaged module failed as
expected: four interactive solve attempts, one each during abort, undo, redo
and restoration undo, all with `Document.Transacting=true`. The patched run
recorded none. Both runs used disposable model copies and exited without saving.

Dependency-order repair adds a batched counterpart to the existing native
dependent-closure rebase. Publication plans once per joint/motion/simulation
phase before changing retained references. Complete downstream blocks move
after the latest required input without disabling chronology validation.

```
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_vibescript_publication_progress.py src/Mod/VibeCAD/vibecad_tests/test_assembly_solver_policy.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_playback.py -q --tb=short
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run
App_tests_run.exe --gtest_filter=DocumentTest.dependencyRebase*
```

Planner red: 3 failed, 11 passed (missing helper). Consolidated Python green:
59 passed. Native red: missing batched API compilation failure. Native green:
build succeeded; both shared-closure and existing single-root persistence tests
passed (2/2), using the matched native build environment. Full-package replay
is still required; these tests do not prove speed on the failed robot rebuild.

## Baseline, 2026-09-08

The full portable based on native revision `9e556e0b` with Python through
`87243ed9` was exercised using `simulation_playback_probe.py` in an isolated
GUI profile and a fresh disposable copy of the user's saved robot.

The simulation contains 337 components, 22 frames and 7,414 recorded poses.

| Stage | GUI callback wall time |
| --- | ---: |
| Open player, without generating | 0.273 s |
| Generate simulation and display last frame | 39.487 s |
| Display frame 1 | 0.626 s |
| Display frame 11 | 0.740 s |
| Display frame 21 | 0.605 s |

Frame 11's Python profile attributes 0.476 s to 336 joint redraw callbacks,
including 672 repeated owner resolutions (0.178 s) and 672 marker updates
(0.225 s). Placement notifications invoke the VibeCAD object observer 672 times
(0.219 s), including source-staleness propagation (0.075 s). These nested times
must not be summed as independent costs.

The reported 11m45s publication-heavy build still needs a stage-by-stage
reproduction. It is not a measured publication-only duration.

## Native solver executor repair

`ASMTAssembly::runKINEMATIC` and `runPostDrag` did not copy the host executor
into their newly created solver systems. `runPreDrag` already did. The new
kinematic regression actually solves the four-bar fixture, verifies executor
invocation, and compares every component's position and rotation at every frame
against the serial solver. The existing sparse-solver test independently checks
overlapping executor work and numerical correctness.

The first test run also exposed a release-build parser defect: `assemblyFromFile`
consumed the `Assembly` record inside `assert`, so release builds skipped that
read and corrupted parsing. Consuming and validating the record unconditionally
also repairs the three previously failing Backhoe fixture tests.

Commands (after activating the MSVC x64 environment):

```powershell
.pixi/envs/default/Library/bin/cmake.exe --build build/ondsel-parallel-test -j 16
build/ondsel-parallel-test/tests/test_run.exe --gtest_filter=OndselSolver.KinematicSimulationUsesHostExecutorAndPreservesEveryPose
.pixi/envs/default/Library/bin/ctest.exe --test-dir build/ondsel-parallel-test --output-on-failure
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py -q --tb=short
```

Red: native executor call count was zero. Green: the kinematic test passes;
all 38 Ondsel tests pass in 18.89 s. Joint owner-cache/hidden-marker tests
failed before implementation and pass afterward. The transient-placement scope
test likewise failed before implementation and passes afterward.

These results do not yet validate the updated full application: native packaging,
async generation, parallel frame preparation and the final GUI acceptance gates
remain tracked in `PERFORMANCE.md`.

## Detached generation ownership and cancellation

The new native start method returns a request token; finish/cancel accept it so
an older player cannot consume or cancel a newer job. The player retains its
own token. A callback test failed before this wiring and passes afterward.
`simulation_runtime_probe.py` now also exercises supersession against the real
native API; its updated full-package run is still outstanding.

Ondsel now accepts a host cancellation callback and checks it during setup,
nonlinear iterations and integration steps. The real four-bar regression throws
after the third recorded frame and verifies that no later frame is generated.
The host connects this to its existing cancellation scope, not a timeout.
Red: compilation failed because `setCancellationCheck` did not exist. Green:

```powershell
build/ondsel-parallel-test/tests/test_run.exe --gtest_filter=OndselSolver.KinematicSimulationHonorsCancellationBetweenFrames
.pixi/envs/default/Library/bin/ctest.exe --test-dir build/ondsel-parallel-test --output-on-failure
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_playback.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_simulation.py src/Mod/VibeCAD/vibecad_tests/test_assembly_solver_policy.py -q --tb=short
```

Results: cancellation regression passes, 39/39 Ondsel tests pass in 18.85 s,
25 Python tests pass in 4.74 s. The native `Assembly` target builds successfully
with these changes. These do not establish full-GUI acceptance or bound time
spent inside an individual solver kernel.

Saving during playback now resumes through the same transient-placement scope
as an ordinary frame, once only, rather than applying directly and potentially
again through the slider signal. Its focused regression failed before the fix.

`retained_publication_probe.py` replays copied worker artifacts through the real
validator, cooperative publisher, observer batching and Qt dispatcher, without
rerunning the solver or accepting/saving the program. It records separate
validation/publication profiles, GUI callback durations and heartbeat gaps.

## First retained publication profile (instrumented, not a speed claim)

The real saved candidate validated and published successfully: 3 public outputs
plus 925 members, 3,022 document objects before and afterward. Copied document,
manifest, request and result hashes were unchanged. No program acceptance or
document save was performed.

Validation took 9.53 s under profiling. Publication and presentation took 89.44 s,
but the first probe spent 35.70 s inside per-object progress serialization;
that total must not be presented as uninstrumented application performance.
The probe now retains events but rate-limits compact snapshot writes. A second
run is required for comparison; GUI profiling itself still adds overhead.

Within the 62.43 s of profiled GUI callbacks, the leading production costs were:

| Production path | Cumulative time | Calls |
| --- | ---: | ---: |
| Native provisional timeline resource reconciliation | 14.82 s | 338 |
| Publication target-identity verification | 3.81 s | 935 |
| Output configuration | 2.60 s | 928 |
| Native staging of resource reconciliation | 1.78 s | 338 |
| Output metadata | 1.49 s | 930 |

These are nested measurements and must not be summed indiscriminately. Native
reconciliation currently rebuilds and validates whole-document timeline indices,
semantic blocks, topological order and dependency reachability for each staged
resource owner. This is the next native optimization target; removing required
integrity checks without equivalent transaction-scoped evidence is not a fix.

## Low-overhead replay and native graph ordering

The compact-progress replay completed successfully with the same 928 outputs
and members and unchanged input hashes. Validation took 8.48 s; publication
including presentation took 39.34 s. Of 24.25 s of profiled GUI callbacks, native
resource reconciliation accounted for 14.03 s. These remain instrumented
baseline measurements, not timings of the new native implementation.

The native ordering implementation now captures reachable dependencies once
into a value-only graph. It stops duplicate traversal at enrolled semantic
blocks, uses a minimum-priority ready queue to preserve the prior stable order,
and retains malformed/retired dependency and cycle rejection. The subsequent
full reachability walk was redundant with the unchanged graph just validated;
its checks now reside in the capture/order pass. Resource identities, metadata,
state mapping, hidden consumers, canonical ownership and atomic application
checks remain in place.

Red: the new native test failed to compile before the graph kernel existed.
Green commands, with the configured native Python/dependency environment:

```powershell
cmake --build build/authoritative-runtime-native --target App_tests_run --config Release -j 21
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=DocumentTest.*:SemanticDependencyOrder.*
```

All 74 tests passed (1.018 s), including reconciliation rollback/persistence,
deleted-resource identity checks, 100 deterministic random-DAG comparisons
against full transitive reachability, same-block cycles, cross-block cycle
rejection, and untracked resource dependencies. The 10,000-block chain test
visits exactly 9,999 edges. Updated-package GUI timing remains outstanding.

## Detached native frames and in-memory result reuse

The native simulation job now copies six solved channels per component into
immutable frame tracks before owner adoption. Independent frame transforms run
on HostRuntime; workers do not touch document, Qt or Coin objects. One current
request token selects the frame eligible for owner adoption. New seeks cancel
older requests; edits invalidate the result; document teardown does not join a
worker. Only one requested frame is expanded into placements, rather than
precomputing a matrix table for every component and frame.

The fixed Rx * Ry * Rz playback rotation is composed directly as quaternions.
Tests compare noncommuting rotations and static offsets against Ondsel's matrix
calculation to 1e-12, and confirm the copied samples survive source mutation.
Ondsel's general EulerAngles implementation remains unchanged.

The 50-component native document test exercises actual generation, unchanged
input reuse without another solve, generation/frame token supersession,
cancellation, nested transient-presentation scopes, changed-input rejection,
and closing an owner with work pending. The reuse assertion failed before its
implementation. All four Assembly tests pass (0.772 s in the recorded run).
The test executable prints QMutex teardown warnings after completion; normal
packaged GUI shutdown remains an explicit verification requirement.

```powershell
cmake --build build/authoritative-runtime-native --target Assembly_tests_run --config Release -j 21
build/authoritative-runtime-native/bin/Assembly_tests_run.exe
```

Run the executable with the configured host Python and native dependency/module
directories on PATH. No portable binaries were overlaid or launched for these
native tests. Source-level player tests cover nonblocking submission, readiness,
transient scope restoration and save-resume behavior. Full-package GUI frame
timings, retained-artifact reuse, asynchronous AI launch and export are still
outstanding; these tests do not establish whole-player responsiveness.

## Asynchronous provider integration (source verification)

Both AI playback entry points now launch background generation and complete
only after the requested native frame has applied. Native seek, step and pause
use the same frame-completion contract. The provider runner waits outside the
document dispatcher, without a solver deadline or model-visible polling loop.
Public synchronous entry points remain intact for existing external callers;
the application's asynchronous paths select their own handlers explicitly.

The native dispatcher retains argument authorization, postcondition checks and
cached call results across deferred completion. A duplicate pending call cannot
replace its eventual result. Player futures terminate on generation/frame
failure, cancellation or task closure. Cancellation is sent to the Qt owner and
can reject only the exact launched dialog; task teardown is queued to avoid
destroying the player inside a frame notification.

Focused tests were red before the asynchronous opener, frame request and service
entry existed. The final consolidated command below passed 143 tests (16.50 s),
including a real Qt cross-thread cancellation check. The player suite alone
passed 18 tests (1.39 s). The Qt check verifies queued owner delivery and signal
cleanup; it is not a full FreeCAD GUI or performance test.

```powershell
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py src/Mod/VibeCAD/vibecad_tests/test_native_dispatch.py src/Mod/VibeCAD/vibecad_tests/test_native_session.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_playback.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_simulation.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_view.py src/Mod/VibeCAD/vibecad_tests/test_tool_surface_guardrails.py -q --tb=short
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py -q --tb=short
```

Packaged GUI launch/postconditions, real robot timings, animation export and
remaining publication work are still outstanding. No new user instance has
been launched from this intermediate source boundary.

The retained `simulation_playback_probe.py` supports the optional environment
setting `VIBECAD_SIMULATION_PLAYBACK_ASYNC=1`. It records submission duration,
completion duration/CPU time, and GUI heartbeat gaps while checking exact
adopted frame selection. Without the option it retains the legacy baseline
path. The updated probe is syntax-checked but awaits the complete package.

## Detached animation export (source and native checks)

Save Animation now selects an asynchronous controller. It requests one exact
native pose, captures the current viewport on its Qt owner, and submits detached
pixels to HostRuntime for orientation, scaling, PNG compression and disk writes.
Only one PNG is in flight per viewer. Frame preparation cannot advance until
the preceding pose and PNG have completed. The input snapshot (frame zero) is
still exported; the paused frame is restored asynchronously after capture.

The existing background manager supervises final encoding on the existing
persistent isolation pool. Staging, encoding, final atomic file replacement and
cleanup all stay outside Qt. Cancellation waits outside Qt for acknowledgement
that native PNG writing released staging before deleting it. Failure or accepted
pre-commit cancellation does not overwrite an existing export. Public synchronous
export methods remain available to external callers; no fallback selects them.

GIF encoding streams one decoded frame at a time with per-frame palettes using
Pillow's documented [GIF plugin APIs](https://pillow.readthedocs.io/en/stable/reference/plugins.html#module-PIL.GifImagePlugin),
instead of retaining every decoded image. Roundtrip tests check frame count,
colours and timing. Video checks exercise the installed MP4 encoder/decoder.
The native framebuffer capture itself still renders on the owner: this change
does not establish a bound on its render/readback latency.

Red: the encoder/controller tests failed because their modules did not exist;
frame-zero selection failed on the missing optional argument. The native image
tests initially failed to compile before the helper existed. Green native build:

```powershell
cmd /d /c build\authoritative-runtime-build.cmd Gui_tests_run
build/authoritative-runtime-native/bin/Gui_tests_run.exe --gtest_filter=AsyncImageExport.*
```

The ignored build wrapper configures VS x64 and the native dependency/Python
environment, then invokes CMake's Gui_tests_run target. Three native image tests
pass (29 ms): detached pixels/scaling, cancellation preserving existing output,
and repeatable destination failure. The focused Python command passed 30 tests
(2.65 s), including the actual Qt controller's single-frame sequencing and
closing while a PNG is still in flight:

```powershell
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_animation_encoder.py src/Mod/VibeCAD/vibecad_tests/test_animation_export.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py -q --tb=short
```

The packaged-GUI probe now accepts `VIBECAD_SIMULATION_EXPORT=1` alongside the
async-player option. It calls the real Save Animation method with only its file
chooser redirected to a disposable GIF, watches completion and validates frame
count. It is syntax-checked, not yet executed on the updated package. Image
orientation, real render latency, UI responsiveness and whole-player shutdown
remain to be measured there; source/native checks are not substitutes.

## Packaged GUI measurements (79b04224, September 8)

The complete `pixi reinstall -e default vibecad`, `pixi install -e package`,
and `pixi run -e package create_bundle` workflow completed successfully in
`package/rattler-build`, including the bundled runtime smoke checks. The 7z
was extracted and the following actual GUI probes used that exact package:

- `simulation_runtime_probe.py`: pass; 50 grounded components, four generated
  frames, result reuse, stale-input rejection, supersession, cancellation and
  close with work pending; 2.59 s total.
- `simulation_playback_probe.py` with `VIBECAD_SIMULATION_PLAYBACK_ASYNC=1` and
  `VIBECAD_SIMULATION_EXPORT=1`: pass on the disposable robot, 3,022 objects.
  Generation plus first adoption took 47.72 s (64.77 process CPU seconds).
  Frame submissions took 3.1-3.4 ms; completions took 250-373 ms. Export took
  10.70 s and decoded as 22 frames, 1516 by 536 pixels. The maximum heartbeat
  gap including opening was 656 ms. The selected assembly was hidden in both
  saved App and ViewProvider visibility; the identical frames therefore do
  not prove visible moving-pose accuracy. No original document was changed.
- `retained_publication_probe.py`: pass, 3 outputs plus 925 members. Validation
  took 9.70 s and publication including presentation 40.17 s. The maximum
  heartbeat gap was 6.16 s; the largest individually profiled adoption callback
  was 234 ms. Reconciliation used 8.10 s, versus 14.03 s in the earlier callback
  profile. Repeated target-identity checks used 4.37 s. Final presentation
  spanned roughly 15.8 s, not necessarily one blocking callback. A concurrent
  user-instance launch makes this run unsuitable for a clean total-speed claim.

The retained publication probe now drains the existing native GUI event tracer
to attribute callbacks outside Python-dispatched adoption. Its first attribution
run passed: validation 9.67 s, publication 33.78 s, 928 outputs/members. The
6.03 s maximum heartbeat gap preceded adoption, during validation, and includes
a 3.31 s QTimer callback followed by a 2.53 s queued main-thread callback.
Receiver identities do not yet identify their owning functions. Final
presentation spanned 13.4 s with short recorded callbacks; the maximum gap must
not be attributed to that finalization phase. No native trace events were lost.

## Publication identity-cache boundary (September 8)

An additive native removal-generation token now lets the publisher retain exact
target checks across slices. New targets are checked once; removal or clear
invalidates the operation-local cache. Property edits and object additions do
not invalidate the identity of existing targets. Cached wrappers remain strongly
referenced to prevent Python ID reuse, and document identity is checked on every
call. Callers without a cache retain the existing validation behavior.

Red: the Python test failed on the missing `cache` argument, and the native
test failed compilation on the missing `getObjectRemovalGeneration` API.
Green commands:

```powershell
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=DocumentTest.*Generation*
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_vibescript_publication_progress.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_playback.py -q --tb=short
```

The native test command used the build's matching host Python and Qt/DLL paths.
Three native tests passed (1.51 s including application startup); 42 Python tests
passed (5.70 s). Fifty incrementally added targets needed fifty lookups rather
than 1,275, and replacing a target was still rejected. The full package replay
has not yet measured the production speedup; this is not an end-to-end completion
claim.

## AI service playback reproduction (September 8)

With `VIBECAD_SIMULATION_PLAYBACK_ASYNC=1` and
`VIBECAD_SIMULATION_AI_PLAYBACK=1`, the packaged probe called the real
`assembly.play_simulation` service entry on the 3,022-object robot. The assembly
was made visible in the disposable document. Generation plus initial display
took 47.90 s (66.17 process CPU seconds); three exact seeks completed in
237-346 ms. Play ran for three seconds without a frame error and the owned
diagnostic process closed. Maximum heartbeat gap including opening was 547 ms.
This did not reproduce the user's subsequent stale-result error. A second
run adds the intervening native geometry-capture calls. The original document
and the user process were not modified.

## Python context and Codex boundary (September 8)

The model-facing source index now uses searchable pages through the existing
`vibescript.read_source` tool (`query`, `offset`, `limit`); exact source-line reads
remain available. Full internal source authority is unchanged. The latest saved
successful robot request's active-state payload fell from 182,911 to 15,780 bytes,
with all 26 programs discoverable. The failed 304,315-byte request was rejected
before provider capture, so that exact failing packet was not measured.

Codex packaging now pins 0.153.4 for all six targets. The staged Windows runtime
passed handshake and model-list checks; the deployed application's signed-in
account returned `gpt-6-astra`. The actual GUI startup probe also verified that
the new tool parameters were loaded and a synthetic 10,000-output program
produced an 805-byte initial prompt. This is a Python/runtime overlay on the
verified package, not a newly generated 7z archive.

Red/green: the four initial paging tests failed on the prior implementation;
the runtime release test failed on 0.144.5. Consolidated green: 250 tests in 5.77 s:

```powershell
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_source_context_paging.py src/Mod/VibeCAD/vibecad_tests/test_model_context_contract.py src/Mod/VibeCAD/vibecad_tests/test_modeling_surface_architecture.py src/Mod/VibeCAD/vibecad_tests/test_codex_subscription.py src/Mod/VibeCAD/vibecad_tests/test_codex_runtime_package.py -q
```

## Synchronous recompute owner/worker boundary (September 8)

The actual GUI lifecycle fixture exposed a wait cycle in synchronous
`Document::recompute`: its GUI caller waited for feature workers while a worker
waited for an owner-thread notification. The compatibility entry now sends its
coordinator through the existing HostRuntime document lane and services Qt
events until completion. The document mutation lease remains active throughout;
the public synchronous result and exception contract are retained.

Red: `AsyncRecomputeTest.SynchronousGuiCallerKeepsEventsAndMutationLeaseLive`
failed both event-liveness and mutation-lease assertions on the old code. Its
watchdog releases the fixture so the regression fails instead of hanging.
Green: all eight selected native tests passed in 798 ms:

```powershell
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=AsyncRecomputeTest.*:HostWorkflowTest.*
```

The executable used the matching build-prefix Python, Qt and module paths.
After rebuilding every native target together, the compiled GUI lifecycle gate
passed generation, moving seek, step, bidirectional playback, pause, mutation
rejection, save/dirty-save baselines, manual close, idempotency, pose restoration,
selection preservation and stable Native revisions. The exact source gate was
`src/Mod/VibeCAD/vibecad_tests/native_assembly_playback_gui_integration.py`.
The earlier revision conflict did not reproduce in this completed run.

A diagnostic crash before the full native rebuild was traced to a stale
AssemblyGui module: debugger type layouts differed from AssemblyApp by 48 bytes.
It was not a solver-vector defect. The consistent native tree passed the GUI
gate; the final release-style portable must still be rebuilt and checked.

## Independent Windows input measurement (2026-09-08)

`window_input_probe.py` shares the existing `native_input_sender.py` with the
retained-publication and simulation-playback probes. The separate sender posts
window-local F23 messages; the native event filter measures queue age and the
Qt event filter consumes the keys. It does not alter desktop focus or physical
keyboard state. Explicit close signals the sender without waiting on the GUI;
parent exit also terminates it.

A disposable real Qt window check, run with the portable Python/Qt runtime via
`<bundle>/bin/python.exe build/window_input_probe_check.py`, collected 12 samples
and detected 719 ms queue age during a deliberate 750 ms owner-thread stall.
Sender shutdown returned exit code 0, including an idempotent second close.
This validates the measurement mechanism, not CAD performance. The diagnostic
script is local; the reusable sampler and CAD probes remain in this directory.

Syntax/diff checks passed:

```text
<bundle>/bin/python.exe -m py_compile src/Tools/performance/window_input_probe.py src/Tools/performance/retained_publication_probe.py src/Tools/performance/simulation_playback_probe.py
git diff --check -- src/Tools/performance
```

The publication probe now distinguishes successful worker return from settled
document presentation. It keeps profiling and collecting input through the
existing document update/recompute/presentation flags and the quiet-period
check, and reports success only afterward. Failed final checks reset `ok` to
false. The full packaged robot runs with this instrumentation remain pending.

## Simulation dependency-scoped invalidation (2026-09-09)

The native lifecycle regression reproduced the stale-generated-results error
after creating and moving an unrelated object in the assembly's document.
The prior observers invalidated on every new/deleted object and almost every
property change, but missed linked inputs in other documents.

Generation now captures the input dependency/placement-ancestor identity set
once on the document owner thread. Application-level change/deletion observers
check that set, preserving solved frames for unrelated edits while rejecting
changed linked inputs. Presentation and FPS exceptions use exact object identity
rather than document-local numeric IDs. No public API or stored format changes.

Red: `AssemblyObjectTest.DetachedSimulationAndFrameLifecycle` failed in 219 ms
with `Simulation frames require current asynchronously generated results`.
Green: all four Assembly native tests passed in 944 ms, including unrelated
creation/change/deletion, cross-document source placement invalidation, reuse,
supersession, cancellation and frame transform/parallel executor checks.
The first cross-document fixture needed `setAllowExternal(true)` on its link;
without it the fixture itself correctly threw before generation.

Exact build and test commands (matching native module and dependency paths):

```powershell
cmd /d /c build\authoritative-runtime-build.cmd Assembly_tests_run
$native = (Resolve-Path build/authoritative-runtime-native).Path
$prefix = (Resolve-Path build/performance-system/package/rattler-build/.pixi/bld/vibecad/FIH7JWrkDlw).Path
$mods = (Get-ChildItem "$native/Mod" -Directory).FullName -join ';'
$env:PATH = "$native/bin;$mods;$prefix/host;$prefix/host/Library/bin;$prefix/bld/Library/bin;$env:PATH"
$env:PYTHONHOME = "$prefix/host"
$env:PYTHONPATH = "$native/Ext;$native/Mod;$mods"
& "$native/bin/Assembly_tests_run.exe"
```

Both commands exited 0. The executable printed two `QMutex: destroying locked
mutex` shutdown warnings, also observed before this fix; their cause is not
established. Full packaged GUI acceptance for this change is still pending.

## Release-style portable lifecycle check (2026-09-09)

The complete rebuild and portable packaging finished successfully. The archive
contains 47,666 files and is 582,587,725 bytes. It includes the synchronous
recompute correction and complete Codex runtime, but predates the dependency-
scoped simulation invalidation commit. A separate full rebuild includes that
later correction; do not label the earlier archive the final handoff.

The actual moving-mechanism GUI gate passed against the extracted archive with
no production module overlays. It verified generation, seek, step, bidirectional
playback, pause, mutation rejection, save baselines, manual close, idempotency,
restored poses, selection and stable Native revisions. It exited its event loop
normally. A concurrent rebuild means this run establishes correctness, not an
uncontended performance baseline.

The first attempt exposed fixed event-pass counts in the test setup: it tried
playback during a pending recompute, then tried closing the busy document during
failure cleanup. The corrected fixture waits on native pending-work flags in a
Qt event loop, with a test-only watchdog that fails without cancelling work.
The green run reported no recompute-busy warnings. No production guard was
removed or bypassed.

Commands:

```powershell
# In package/rattler-build, with the pixi executable directory on PATH:
pixi reinstall -e default vibecad
$env:BUILD_TAG='v26.3.1-RC6-build1'
$env:MAKE_PORTABLE_ARCHIVE='true'
$env:MAKE_INSTALLER='false'
pixi run -e package create_bundle
# Extract the archive to a new directory, then use the isolated local runner:
build/run-packaged-edit-check.ps1 -Name packaged-lifecycle-0d17f735-ready-20260909 -Bundle <extracted-bundle> -Script build/packaged_playback_lifecycle_probe.py
```

The local launch script schedules the retained
`native_assembly_playback_gui_integration.py` gate after GUI bootstrap. The
archive's own Python/Qt/native modules execute the test; there are no source
overlays or mocked production commands. The final archive still requires this
same check plus the full robot publication/playback checks.

## Complete retained-candidate rollback/retry (2026-09-09)

The extracted portable at native revision `25b67eee` opened the fresh saved
3,022-object robot copy, validated the retained 1,159-item candidate, cancelled
after 900 published items, verified rollback, then retried successfully through
settled presentation. The output document contained 3,253 objects. Original
document, request, result and manifest hashes were unchanged. The portable
application closed normally afterward.

Rollback comparison checked every object's name/type, outgoing links, placement,
label, visibility and program revision. It does not claim byte-for-byte equality
of every arbitrary property. The optional probe mode is
`VIBECAD_PUBLICATION_CANCEL_AFTER=900`, alongside the existing retained working-
candidate settings. It exercises the real cooperative cancellation exception,
native abort, then re-captures the native document revision before retrying.

Correctness passed; responsiveness did not. This was a profiled run concurrent
with compilation and is not an uncontended benchmark:

- Initial ready-to-replay span: 224.34 s including document opening.
- Detached candidate validation: 15.28 s.
- Complete successful retry including its presentation waiter: 116.80 s.
- Final presentation/check completion: 485.44 s from probe startup.
- Maximum independent input queue delay: 15,188 ms; maximum heartbeat gap: 15.39 s.
- The largest Qt timer callback was 15,144.55 ms, coincident with validation.
- The real cancellation dispatch took 9,729.87 ms; native abort accounted for
  9.67 s cumulatively. This is not the earlier lightweight abort fixture.

Across cancellation and retry, the owner-thread profile recorded 84.45 s in
457 native History block publications, 21.21 s in 896 native resource
reconciliation finalizations, and 17.99 s in 457 object creations. About 10.95 s
was diagnostic progress serialization; do not attribute that to production.
The retained probe now includes opt-in rollback/retry and independent input;
native events and cProfile files remain local. A process snapshot was captured
during the run, but mismatched/partial native symbol resolution must not be
treated as an authoritative function-level stack diagnosis.

## Native History dependency traversal (2026-09-09)

`DocumentTimeline::publishProvisionalOperationBlock` validated the complete
transitive ancestry again for every tracked operation. Since the outer pass
already validates each tracked node, after checking an edge's History order it
can stop traversal at another tracked node. That node's outgoing edges are
checked independently. Untracked intermediate objects still require traversal;
structural-edge exclusions and malformed/forward-dependency rejection remain.
No cache, extra worker pool, public API or persisted format was added.

Red: the native 50-operation chain fixture observed 1,274 structural-edge checks
for publication of one additional dependent operation, failing its linear
300-check bound. The invalid untracked-bridge fixture already passed. Green:
all 75 `DocumentTest` cases passed in 1,070 ms, including the bounded traversal
and untracked bridge cases, History ownership, rollback, persistence and
dependency rebasing. This is algorithmic/native correctness evidence, not yet
the large packaged publication benchmark.

```powershell
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run
# With the matching native/Python/module paths documented above:
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=DocumentTest.semanticPublication*
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=DocumentTest.*
```

The focused pre-fix run exited 1; the rebuilt full Document group exited 0.
The full release-style rebuild of the earlier dependency-invalidation commit
also completed and reinstalled successfully, but predates this History change.
Do not package that intermediate environment as the final combined handoff.

## Empty occurrence graphs and validation isolation (2026-09-09)

Ordinary Assembly component occurrences with no old or final owned resources
were still staging and finalizing a complete History resource reconciliation.
The caller already captures the exact resource graph and validates the native
operation role. An empty, unstaged graph now does not enter reconciliation.
Nonempty graphs and already-staged replacements still execute the existing
native finalizer, including a staged replacement whose final graph is empty.
No public method, stored contract, or native validation was changed.

Red: after extracting the existing call sequence unchanged, the empty graph
case observed `stage, finalize` instead of no work; the other three cases passed.
Green: all 39 tests in these two suites pass (1.43 s, exit 0):

```powershell
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_vibescript_timeline_publication.py src/Mod/VibeCAD/vibecad_tests/test_vibescript_publication_progress.py -q
```

The complete native `all` build also finished successfully for the preceding
History traversal correction. The combined portable replay remains required.

An isolated actual portable GUI ran the retained Assembly result through the
production detached validator without opening a document: 3.47 s unprofiled,
maximum independent input delay 140 ms; 12.64 s with cProfile, maximum 203 ms.
A synthetic JSON workload in the same GUI observed at most 16 ms. Neither
reproduced the full replay's 15.15 s Qt callback. These checks narrow the
investigation; they do not disprove the full-document responsiveness failure
or establish its cause. Both isolated processes exited normally.

## Reproduced ribbon/worker interpreter contention (2026-09-09)

The disposable full-robot replay with off-owner stack sampling again completed
cancel/rollback/retry. During its initial publication validation, a main-thread
sample caught `RobotWorkbench.Initialize -> import RobotGui`; subsequent input
latency grew throughout the worker validation. The Assembly ribbon's
`pageGroups` initializes the composed Robot workbench from its refresh timer.

A separate, empty-document portable reproduction starts the same validator
and activates Assembly concurrently. Input delay reaches 4,407 ms for a 4.20 s
unprofiled validation, then 14,094 ms for a 14.13 s profiled validation. Replacing
the validator with a synthetic JSON loop also reproduces approximately 5.2 s
input delays. Without workbench activation, both workloads remain responsive
as recorded above. These are diagnostic runs, not acceptance measurements.

A Process Snapshot dump resolved with the **exact bundled python311.pdb**
places the GUI in `take_gil -> PyEval_RestoreThread -> posix_do_stat` during
Python import. The captured GIL interval is 5,000 microseconds, not a modified
long timeslice. This confirms the owner-thread interpreter wait; it does not
yet explain why handoff starves for the whole worker duration. FreeCAD symbol
names from this older archive remain mismatched and are not used as evidence.
Do not substitute worker-count or timer adjustments for this remaining fix.

The retained playback/export probe now shows its target assembly for export,
allows a combined AI playback/export run, and rejects an animation whose
frames all have identical pixels. The previous hidden-assembly export cannot
serve as visible-motion evidence. A passing combined run is still required.

The cold-ribbon synthetic-workload reproduction measured 787 owner-thread
import-path stat calls totaling 3.873 s; no individual measured call exceeded
16 ms. This supports repeated interpreter handoff during imports, rather than
one multi-second filesystem call. With Assembly selected before starting the
worker, the first phase still overlapped deferred composed-workbench setup
(4,000 ms maximum input delay), but the subsequent 26.48 s profiled validation
observed only 62 ms maximum delay. Do not label this a fixed deadlock or change
the interpreter timeslice on this evidence.

The publication probe accepts `VIBECAD_PUBLICATION_PROFILE=0` for end-to-end
timing without cProfile overhead. It keeps the original profiled default and
all independent input, native callback, rollback and correctness checks.
The report records which mode ran; Python profiles and wall-clock acceptance
measurements must not be conflated.

The stack-sampling replay also exposed a **probe-induced** stall: its profile
attributes 131.02 s to 201 `Path.write_text` calls, and its longest GUI callback
was 102.42 s. Do not attribute that callback to application publication. The
publication and simulation probes now share a coalescing diagnostic writer;
serialization and disk writes run outside the measured GUI, with at most one
in-flight and one pending snapshot. Final completion is polled before exit.
Blocked writes, coalescing, write-error propagation and closed-writer rejection
are exercised by the focused helper tests (initial red: missing implementation;
green: two tests, 0.11 s):

```powershell
.pixi/envs/default/python.exe -m pytest src/Tools/performance/test_async_report_writer.py -q
.pixi/envs/default/python.exe -m py_compile src/Tools/performance/retained_publication_probe.py src/Tools/performance/simulation_playback_probe.py
```

This only corrects instrumentation. It does not fix or dismiss the separately
reproduced cold-workbench/import wait or the earlier native rollback duration.

## Combined portable lifecycle gate (2026-09-09)

The complete release-style build installed successfully and the packaging
command exited 0. The portable archive contains 47,666 files read by 7z and is
582,564,783 bytes. Extraction exited 0; the launched application reports native
`BuildRevisionHash=c15f99d3db3caeb2869242cd08e4beda54dc3f03`. Subsequent source
commits only change the diagnostic probes and documentation.

```powershell
# From package/rattler-build, with the normal pixi binary directory on PATH:
pixi reinstall -e default vibecad
pixi install -e package
$env:BUILD_TAG='v26.3.1-RC6-build1'
$env:MAKE_PORTABLE_ARCHIVE='true'
$env:MAKE_INSTALLER='false'
pixi run -e package create_bundle
```

The Actions-pattern smoke checks passed for dependencies, provider subprocesses,
Codex app-server 0.153.4, geometry worker and windowless provider execution.
The exact extracted package then passed the compiled-GUI lifecycle gate and
exited normally: generated, seek, step, bidirectional playback, pause, mutation
blocking, baseline save, dirty-save clean state, manual close, idempotency,
restored pose, selection preservation and stable revision. Recompute remains
exercised; no guard was bypassed. The full robot replay and visible export are
still separate acceptance requirements.

## Combined portable robot replay and visible export (2026-09-09)

The same extracted `c15f99d3` package completed the unprofiled 1,159-item
publication with cancellation after item 900, exact pre/post rollback inventory
equality (3,022 objects), and a successful retry through settled presentation.
Detached validation took 3.578 s, retry publication 88.391 s, and the final
presentation settled about 3.2 s after publication returned. The final document
contained 3,253 objects; the copied source and input artifacts remained unchanged.
The process exited normally with empty stderr.

This is **not responsiveness acceptance**: cancellation occupied one owner
callback for 8.078 s (8,016 ms independent input delay). A separate 3.529 s Qt
callback overlapped cold workbench initialization and worker validation; one
adoption callback lasted 1.493 s. These remain actionable failures. The earlier
116.8 s replay ran with profiling and concurrent compilation, so these two
durations are not a controlled speedup comparison.

The visible 337-component robot simulation then passed the actual AI service
entry points and GUI Play control. All 22 frames generated asynchronously;
player submission took 0.421 s and generation/display completion took 47.950 s.
Three exact-frame AI seeks completed in 0.221, 0.354 and 0.262 s. The probe made
any second generation call fail, and the seeks and Play still succeeded.

The asynchronous GIF export submitted in 0.074 s and completed in 12.736 s.
The actual visible artifact contains 22 frames at 1516 x 536 pixels, with 20
frames differing from frame zero. Visual inspection confirmed rendered robot
geometry, not a blank export; numerous visible joint markers remain visible in
this fixture. The process exited normally with empty stderr.

Independent Windows input queued **after generation submission returned** and
delivered before generation completed had a maximum delay of 78 ms across 188
samples. A 2,110 ms sample labelled `async_generate` was queued earlier, during
the probe's synchronous assembly visibility/camera/fit setup and player launch;
the label records delivery-time phase, not enqueue-time phase. Do not attribute
that delay to the generation worker. Maxima during Play and export were 203 ms
and 312 ms respectively. Cancellation/edit/close of the large visible fixture
and authenticated persisted-result reuse remain separate requirements.

Reproduction uses `run-packaged-edit-check.ps1` with the exact extracted bundle,
a disposable copy, and the retained production probes:

```powershell
$env:VIBECAD_PUBLICATION_WORKING_CANDIDATE='1'
$env:VIBECAD_PUBLICATION_CANCEL_AFTER='900'
$env:VIBECAD_PUBLICATION_PROFILE='0'
# VIBECAD_PUBLICATION_ATTEMPT and VIBECAD_PUBLICATION_MANIFEST identify the
# ignored copies; run src/Tools/performance/retained_publication_probe.py.

$env:VIBECAD_SIMULATION_PLAYBACK_ASYNC='1'
$env:VIBECAD_SIMULATION_AI_PLAYBACK='1'
$env:VIBECAD_SIMULATION_AI_RESEEK='1'
$env:VIBECAD_SIMULATION_EXPORT='1'
# Run src/Tools/performance/simulation_playback_probe.py in a fresh process.
```

## Native Assembly status storm during cancellation (2026-09-09)

Five process snapshots from a disposable repeat of the 900-item cancellation
locate the owner inside `Transaction::apply -> TransactionDocumentObject::applyDel
-> Document::_removeObject -> PropertyLinkBase::breakLinks`. Two samples enter
`AssemblyObject::onChanged -> updateSolveStatus -> numberOfComponents`, including
repeated History activity checks. Another is in `PropertyXLinkSubList::breakLink`.
These are stack samples, not a statistical division of the total cancellation
time. The repeat still completes exact rollback and successful publication.
Windows CPU recording could not enable the profiling policy; no recorder was
left running. Private snapshots remain in ignored local storage.

Group-derived status now defers while transaction replay, cooperative mutation,
or restore is active and refreshes once at the document's existing stable
boundary. Closing a transaction inside an active lease does not flush early.
Ordinary edits and the public `updateSolveStatus()` entry retain their behavior.
The deferred refresh reads existing diagnostics without implicitly starting a
new solve; it therefore cannot auto-ground newly published components during
presentation completion. Connections disconnect on unsetup and destruction.

Red: the real 50-component native rollback emitted 51 solver-status updates,
including intermediate replay states, and publication emitted 50 updates inside
the lease. Green: one final update for each batch, exact restored Group/DoF and
immediate notification for a subsequent ordinary edit. A second red regression
showed that merely delaying the old method still launched a solve at lease end
(DoF became zero); the corrected projection leaves the component ungrounded and
reports its unsolved DoF without starting computation.

```powershell
cmd /d /c build\authoritative-runtime-build.cmd Assembly_tests_run all
# With the matching native/dependency environment documented above:
& "$native/bin/Assembly_tests_run.exe"
```

The full native build exits 0. All six Assembly tests pass in 1.346 s, retaining
the existing two test-process QMutex shutdown warnings. Full portable timing of
this change is still required; coalesced notifications do not prove that the
remaining link cleanup is responsive.

## Complete portable verification of cd7581be (2026-09-09)

The Actions-pattern `pixi reinstall -e default vibecad` exits 0. The complete
`pixi run -e package create_bundle` exits 0 with portable archives enabled and
installer creation disabled. Provider, geometry-worker, windowless-provider and
Codex 0.153.4 smoke checks pass. Extraction exits 0. The probe records the actual
application revision `cd7581bed8c57df15456e701270dfa822df73ef6`; production modules
are not overlaid. Under Windows PowerShell, use `ErrorActionPreference=Continue`
around native Pixi commands and check `LASTEXITCODE`: its success status is written
to stderr, which `Stop` otherwise converts into a terminating wrapper error.

The unprofiled 1,159-item replay passes cancellation after item 900, exact baseline
inventory equality for 3,022 objects, and retry through settled presentation with
3,253 objects. Validation takes 3.625 s; retry publication takes 87.406 s. This is
essentially unchanged from the prior 88.391 s run, not an overall publication
speedup claim. The process exits normally with empty stderr.

The longest measured owner callback falls from 8.078 s to 2.234 s and coincides
with rollback; independent Windows input there is delayed by 2,110 ms. A separate
adoption callback still takes 1.484 s. The largest overall input delay is 3,641 ms
at the cold validator/Workbench overlap, before publication starts. These pauses
remain open; correct rollback and a smaller pause are not full responsiveness
acceptance. Sampled peak working set during the run is about 1.52 GB decimal.

## Joint visibility invalidated solved simulation frames (2026-09-09)

The extended real-model probe uses `VIBECAD_SIMULATION_LIFECYCLE=1` with the
asynchronous AI player, seek and export flags. In the cd7581be package, generation
succeeded, 336 joints were hidden, then the first AI seek returned
`SIMULATION_PLAYBACK_FAILED`: "Simulation frames require current asynchronously
generated results". The observer recorded the joint group's `_GroupTouched`
notifications. This is a real failed lifecycle gate, not a successful playback
claim. A preceding probe attempt stopped on a test-harness getter typo before
visibility testing; the retained probe now uses the exported `Joints` property.

GroupExtension emits `_GroupTouched` on child visibility changes and execution.
The solver does not read this derived notification. Native input observers now
exempt that exact extension property; similarly named properties on unrelated
objects are not exempted. Group membership, actual component/joint properties
and linked-source edits remain observed. No GUI work moves to a worker.

The 50-component native regression initially threw the exact stale-results
exception on replay of the group notification. After the fix, all six Assembly
tests pass in 1.342 s, including the existing real-placement and external-source
invalidation checks. The two existing test-process QMutex shutdown warnings remain.

```powershell
cmd /d /c build\authoritative-runtime-build.cmd Assembly_tests_run
# With the matching native/dependency environment documented above:
& "$native/bin/Assembly_tests_run.exe"
```

Both the targeted build and final test command exit 0. Python compilation of the
publication and simulation probes also passes. The GUI failure was preserved in
ignored diagnostic storage and its disposable process closed. The fix still needs
the next complete packaged robot replay; do not treat the cd7581be archive as fixed.

## Native deletion link-query cache (2026-09-09)

Cancellation snapshots also identify `PropertyLinkBase::breakLinks`. Previously
every deletion enumerated every owner's properties and invoked each link
property's cleanup, even if that owner had no reference to the deleted target.
The native 50-target regression records 5,000 cleanup calls against a bound of
100 (two properties on each of the 50 affected owners). This is an operation
count, not a host-dependent elapsed-time assertion.

The private owner-confined query now caches all property-link targets, including
hidden links that are deliberately absent from the dependency DAG. Existing
link mutation and dynamic-property removal invalidation clears it. Cleanup of
the removed owner's outgoing links and the external-document link registry still
runs; null/detached-target queries preserve the existing general contract.
Coverage includes hidden link edits, transaction abort, dynamic links added or
removed after a cached query, and outgoing-reference cleanup without a self-link.

```powershell
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run
# With the matching native/dependency environment documented above:
& "$native/bin/App_tests_run.exe" '--gtest_filter=DocumentObjectTest.linkCleanup*'
# Rebuild every native module after the private DocumentObject layout changes:
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run all
```

The initial test build exits 0; the red test command exits 1 with the 5,000 versus
100 count failure, while the original compatibility test passes. The full native
rebuild exits 0; both cleanup tests pass in 443 ms including application startup.
All six Assembly tests pass. Packaged rollback timing remains an acceptance
requirement; fewer property callbacks alone do not establish UI responsiveness.

## Recompute completed but its dispatcher still waited (2026-09-09)

The consolidated App suite stopped in
`AsyncRecomputeTest.SynchronousGuiCallerKeepsEventsAndMutationLeaseLive`.
A Windows process snapshot, decoded with the matching native symbols, shows
`Document::recompute` in the Qt dispatcher wait with `count=1` and `complete=true`;
the runtime workers are idle. The queued completion had run, but
`processEvents(AllEvents | WaitForMoreEvents)` did not return to check its flag.
The owned test process was stopped after preserving this evidence, not counted
as a successful run.

The completion callback now calls `events.quit()` after storing the result.
[Qt's implementation](https://github.com/qt/qtbase/blob/6.8/src/corelib/kernel/qeventloop.cpp)
routes this through the dispatcher's interrupt operation. The owner continues
serving events until completion; computation remains on HostRuntime. There is
no production polling timer or execution deadline. A test-only watchdog wakes
the old implementation so the regression fails normally instead of hanging.

```powershell
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run
& "$native/bin/App_tests_run.exe" '--gtest_filter=AsyncRecomputeTest.SynchronousGuiCallerKeepsEventsAndMutationLeaseLive'
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run all
& "$native/bin/App_tests_run.exe" '--gtest_filter=AsyncRecomputeTest.SynchronousGuiCallerKeepsEventsAndMutationLeaseLive:DocumentObjectTest.linkCleanup*'
& "$native/bin/App_tests_run.exe"
& "$native/bin/Assembly_tests_run.exe"
```

Red exits 1 because the watchdog had to interrupt the dispatcher after two
seconds. The green full native build exits 0. All three focused tests pass;
the recompute case takes 121 ms without the watchdog firing. The full App suite
now terminates in 3.522 s: 589 pass, two skip, four fail. Three failures throw
Windows' missing symlink-creation privilege error. The fourth,
`DocumentObjectTest.getSubObjectList`, calls `BOPFeatures.finalize_result`, which
imports `PartGui` in a console process. A separate native console reproduction
confirms `ImportError: Cannot load Gui module in console application`. These
failures are not reported as passes or silently excluded. All six Assembly
tests pass in 1.447 s. The real-model packaged acceptance remains outstanding.

## Exact document membership index (2026-09-09)

Tracing the remaining publication work found that `Document::containsObject`
used a linear search through `objectArray`. Semantic History validation calls
this repeatedly while examining operations, owners and dependencies. The
creation-order array is still retained; a private address set now answers
membership without dereferencing the candidate pointer. Registration, removal,
clear and restore keep the index consistent. Undo/redo use the same registration
and removal paths. No public signature or document format changes.

The native regression creates 5,000 objects and measures the median of three
20,000-query runs for the first and last objects. Its relative-scaling check
fails on the old implementation: first 94,100 ns, last 16,872,900 ns (about
179x). With the index, first 111,600 ns and last 111,700 ns. This measures the
membership primitive, not overall publication. The same test checks null and
invalid pointers without dereferencing them, removal, undo/redo and clearing.

```powershell
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run
& "$native/bin/App_tests_run.exe" '--gtest_filter=DocumentObjectTest.membershipLookupDoesNotScanCreationOrder' '--gtest_output=xml:build/membership-index-red.xml'
cmd /d /c build\authoritative-runtime-build.cmd App_tests_run all
& "$native/bin/App_tests_run.exe" '--gtest_filter=DocumentObjectTest.membershipLookupDoesNotScanCreationOrder:DocumentObjectTest.linkCleanup*:AsyncRecomputeTest.SynchronousGuiCallerKeepsEventsAndMutationLeaseLive' '--gtest_output=xml:build/membership-index-green.xml'
& "$native/bin/App_tests_run.exe"
& "$native/bin/Assembly_tests_run.exe"
```

Both builds exit 0. Red exits 1 on the scaling assertion; green passes all four
focused cases in 699 ms. The full App suite finishes in 3.653 s: 590 pass, two
skip and the same four failures documented above. All six Assembly tests pass
in 1.366 s. The intermediate d7c92dac package build was intentionally cancelled
to include this core fix; it is not a failed compilation or a verified archive.
The saved unprofiled trace places its remaining 1.484 s adoption callback at
item 490 (`wing_deploy`); that identifies the step, not the cause of its cost.
Repeat packaged timing before claiming this index removes that pause.

## Combined 1bbfa624 portable publication (2026-09-09)

The full `pixi reinstall -e default vibecad`, package-environment install,
`create_bundle` with portable archive enabled, and archive extraction all exit 0.
The 582,573,173-byte archive contains 48,260 files. Dependency, provider,
windowless provider, geometry-worker and Codex 0.153.4 execution smokes pass.
The actual GUI reports native revision
`1bbfa6248ac5c0e6939755d9b2646e252fb6d5e0`; no source overlays are used.

The retained publication probe runs with profiling disabled, working-candidate
validation enabled and cancellation after item 900. The fresh robot opens with
3,022 objects. Validation takes 3.562 s. Cancellation is exercised and rollback
matches the complete original inventory before retrying all 1,159 outputs and
members. Retry through publication/presentation takes **66.281 s**, versus
87.406 s in cd7581be (24.2% less elapsed time). The final settled document has
3,253 objects, the probe passes, stderr is empty, and its GUI exits normally.

The largest measured publication callback is 0.750 s (native event 742.806 ms)
at cancellation, previously 2.234 s. The next callback is 0.344 s; the previous
1.484 s adoption pause is absent in this run. Input after validation peaks at
640 ms during cancellation; the largest remaining non-cancellation sample is
282 ms. These are improvements, not proof of a never-blocking interface.
Cold Workbench/validation overlap still delays input by 3,594 ms (native
dispatcher event 3,648 ms), and remains an acceptance issue.

External five-second process samples peak at 1,560,088,576 bytes working set.
One preparation interval consumes 97.9 CPU-seconds in 5.0 wall-seconds, showing
actual concurrent native work rather than merely a configured thread count.
This does not make live owner-side document mutation parallel. The packaged
simulation lifecycle acceptance is run separately to avoid competing GUI tests.

## Combined 1bbfa624 simulation acceptance (2026-09-09)

The same extracted archive passes `simulation_playback_probe.py` on a separate
fresh robot copy with `VIBECAD_SIMULATION_PLAYBACK_ASYNC`,
`VIBECAD_SIMULATION_AI_PLAYBACK`, `VIBECAD_SIMULATION_AI_RESEEK`,
`VIBECAD_SIMULATION_EXPORT`, and `VIBECAD_SIMULATION_LIFECYCLE` set to `1`.
The 3,022-object document opens in 159.047 s. The active simulation has 337
components and 22 frames (7,414 poses); generation/display takes 47.865 s and
65.188 process CPU-seconds. This modest generation overlap is not a claim
that the sequential kinematic frame dependency has been removed.

All 336 joints are hidden without invalidating generated results. Three actual
AI seeks complete in 47, 76 and 72 ms without calling generation again, and
hidden marker callbacks remain absent. Showing the joints produces 672 exact
marker-pose comparisons, all passing. Play advances to frame 15. The asynchronous
GIF export completes in 12.428 s, contains 22 frames at 1516 by 536 pixels, and
20 frames differ from frame zero. The exported image was visually inspected.
Closing with a pending frame resolves that request without accepting it and
restores all 337 original component poses exactly. The probe passes and exits.

Input samples queued after generation submission stay within 63 ms; the
2,047 ms sample labelled `async_generate` was queued before submission during
the probe's camera/visibility setup. Similarly, the 719 ms sample labelled
`play` completes during the preceding bulk marker-show/verification callback,
before Play starts. Playback reaches 250 ms, export 281 ms, and close 235 ms.
These setup/bulk-show costs remain distinct outstanding responsiveness work.
Observed peak working set is 1,451,761,664 bytes. A bad-Reference2 warning names
a joint in a different, older assembly; the active simulation completes all
checks, but the warning is retained for attribution rather than suppressed.

The independent `native_assembly_playback_gui_integration.py` gate also passes
against this archive, without source overlays: generation, seek, step,
bidirectional playback, pause, mutation blocking, baseline save, clean state
after save, manual close, idempotency, exact restoration, selection preservation
and stable revision. Its stderr is empty and the GUI exits normally.

## Remove discarded validation work (2026-09-09)

The retained validator profile attributes 5.99 profiled seconds to formatting
JSON whose chunks are immediately discarded, and 1.51 seconds to constructing
95,082 Path objects, mostly for ordinary identifiers. Validation now inspects
JSON-supported values directly while preserving finite floats, allowed/sortable
keys, shared values versus cycles, and the interpreter's integer-conversion
policy. It does not allocate an encoded copy of the result. Definition strings
only undergo path-component parsing when they contain a possible parent segment;
absolute-path and traversal checks remain intact.

```powershell
& .pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_model_size_limits.py -k definition_paths -q
& .pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_domain_json_validation.py -q
& .pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_domain_json_validation.py src/Mod/VibeCAD/vibecad_tests/test_model_size_limits.py src/Mod/VibeCAD/vibecad_tests/test_domain_artifact_batch.py src/Mod/VibeCAD/vibecad_tests/test_domain_timeline_runtime.py -q
```

The path regression fails red with 5,004 parses instead of one; all nine
path-rejection cases already pass. The JSON regression fails red because
validation invokes the forbidden encoder; 26 compatibility cases pass. Green
passes all 62 combined tests in 7.47 s. A pytest collection issue with displaying
a 5,001-digit parameter was corrected by assigning type-based test IDs before
the meaningful red run. The packaging environment does not contain pytest;
these commands use the repository's development environment.

The permanent retained-validation probe runs six separate processes using the
same extracted 1bbfa624 Python/native runtime: three with its packaged module,
then three with the source candidate. Packaged elapsed times are 3.334, 3.319,
3.602 s; candidate times are 2.364, 2.364, 2.365 s (29.1% median reduction).
All runs accept the same three public outputs, 42 frames and 18,774 poses,
preserve the request/result bytes, and create no documents.

The isolated cold-Workbench GUI probe explicitly loads the candidate Python
module without changing the archive. Validation takes 2.593 s unprofiled, but
input still waits up to 2,813 ms during concurrent imports. The profiled overlap
reaches 7,969 ms. This source change reduces CPU work, but does not resolve
the GUI interpreter contention. It is not yet included in a new full archive.

## Isolate Assembly result validation (2026-09-09)

Assembly's production adapter now runs the same result validator in the
existing persistent isolation runtime. It does not start another pool or skip
reauthorization. Only detached JSON crosses the boundary; live publication
checks are unchanged. A request nonce prevents accepting a previous result,
worker errors preserve validation exception details, and AI/editor entry points
forward cancellation through an additive adapter capability. Legacy adapter
signatures remain callable. Publication probes now exercise this adapter rather
than bypassing it with direct validation.

The original focused regression failed because validation executed on the host
interpreter. The cancellation/legacy-adapter tests then failed red because the
session bridge was absent. A test assertion initially used the wrong failure
field; it was corrected to the existing `failure_code` contract.

```powershell
& .pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_assembly_validation_isolation.py src/Mod/VibeCAD/vibecad_tests/test_domain_json_validation.py src/Mod/VibeCAD/vibecad_tests/test_model_size_limits.py src/Mod/VibeCAD/vibecad_tests/test_domain_artifact_batch.py src/Mod/VibeCAD/vibecad_tests/test_domain_timeline_runtime.py src/Mod/VibeCAD/vibecad_tests/test_native_session.py src/Mod/VibeCAD/vibecad_tests/test_scripted_editor_architecture.py -q
```

Green: 109 passed in 19.33 s. Using the extracted 1bbfa624 runtime with the source
validator explicitly loaded, the actual cold-Workbench GUI test validates three
outputs in 4.453 s with 719 ms maximum independent input latency. Its profiled
repeat takes 4.422 s with 297 ms maximum input latency. The prior direct-host
candidate measured 2.593 s / 2,813 ms, and 8.047 s / 7,969 ms when profiled.
This trades some transfer/start overhead for eliminating the measured long GIL
contention. It is not a claim that cold Workbench initialization costs nothing.

The permanent retained-result probe also exercises real native cancellation:

```powershell
$bundle = (Resolve-Path 'build/portable-publication-simulation-1bbfa624/VibeCAD-26.3.1-RC6-build1-Windows-x86_64').Path
$env:PATH = "$bundle/bin;$env:PATH"
$env:PYTHONNOUSERSITE = '1'
& "$bundle/bin/python.exe" src/Tools/performance/assembly_retained_validation_probe.py --module-dir src/Mod/VibeCAD --attempt build/portable-edit-checks/publication-rollback-fixture-20260908/attempt --adapter --cancel-after 2.5
```

Cancellation returns at 2.932 s; the same-process retry succeeds in 4.400 s,
validating 42 frames, 18,774 poses and three public outputs (largest definition
1,543,146 bytes). It creates no documents and preserves retained request/result
bytes. An earlier 0.25 s cancellation also passes. These are explicit source
candidate checks against the prior archive, not a newly built final package.

## Simulation setup attribution and local render coordinates (2026-09-09)

The full robot GUI attribution run separates visibility, camera fitting and
marker verification. Assembly visibility takes 6.8 ms, camera orientation
2.5 ms, and native `fitAll` 1,554 ms. The native viewer's `animatedViewAll`
uses a nested event loop excluding user input. Simulation setup now temporarily
disables the existing per-view animation control around both orientation and
fitting, restoring it in `finally`. No new viewer API or preference is needed.
An experimental extra `fitAll` argument was removed after reading that existing
control; its in-progress native build was deliberately stopped and superseded.

Showing 336 joints takes 662 ms separately from the 225 ms verification pass.
Profiling attributes 208 ms to repeated Coin field lookup. Retaining translation
and rotation field wrappers with their owning transform reduces the candidate
show pass to 504 ms. All 672 actual marker poses still match their independent
expected placements. Hidden markers remain uncomputed. The actual AI service
seek, playback and pending-frame close checks pass, restoring 337 exact baseline
poses. Candidate camera setup takes 33 ms; this is a source-overlay measurement
against archive `1bbfa624`, not final package acceptance.

The permanent 50-object camera GUI regression fails on the old production path
because setup enters a nested event loop (416 ms). The candidate eliminates that
loop (1.4 ms), but its independent framing assertion exposed a second real bug:
the rendered center is (113, 53) instead of the model's (59, 29). Native render
preparation had omitted the old renderer's top-level location reset, so the
scene graph applied root placement a second time. Both translated vertices and
rotated normals are affected. The fix clears only the private mesh copy's root
location, preserving nested compound placements and the original shape.

Red/green commands:

```powershell
& .pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py -k camera_fit -q
& .pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py src/Mod/VibeCAD/vibecad_tests/test_animation_export.py src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py -q
& build/authoritative-runtime-build.cmd Part_tests_run
& build/authoritative-runtime-native/bin/Part_tests_run.exe --gtest_filter=BRepMeshTest.renderCoordinatesExcludeOnlyTheRootPlacement
& build/authoritative-runtime-build.cmd all
& build/authoritative-runtime-native/bin/Part_tests_run.exe '--gtest_filter=BRepMeshTest.*'
```

Python camera cases fail red before the scoped toggle; the consolidated suite
passes 36 tests in 2.63 s. The marker regression fails on repeated field lookup
before caching. Native root-placement regression fails red with transformed
vertices/normals; all nine native mesh tests pass after the correction (1.142 s,
including startup). One initial test compared freshly constructed OCCT location
identities instead of retaining the originals; that assertion was corrected.
An initial build command used the nonexistent `Part_tests` target and was
corrected to `Part_tests_run`. Native tests use the matching native-module and
host-runtime environment documented in the earlier build evidence.

The full native `all` build completes successfully. The rebuilt actual GUI
passes the 50-object framing regression: center (59, 29), camera setup 1.1 ms,
no nested event loop, animation setting restored, and clean automatic close.
No errors are written to its stderr. The native Qt render-controller suite also
passes all nine cases in 1.687 s:

```powershell
& build/authoritative-runtime-native/bin/RenderMeshController_Tests_run.exe
& build/run-native-gui-diagnostic.ps1 -Name camera-fit-root-local-7fb65966-20260909 -Script src/Tools/performance/simulation_camera_fit_probe.py
```

The controller suite uses the matching host/native environment and Qt's
offscreen platform; its expected missing-font-directory and unsupported
QOpenGLWidget warnings are retained. The separate actual GUI check uses the
Windows platform. Final full-archive acceptance is still pending.
The export cancellation probe now covers frame preparation, PNG capture and
encoding, followed by a full export retry. Its first run was deliberately
stopped after finding a probe-local Python import shadowing the module binding;
that instrumentation bug was corrected before rerunning. No user process or
original document was touched.

The subsequent native run passes cancellation at all three requested phases,
but the full export retry fails with missing `psutil`: the local diagnostic
launcher still selected an older portable Python runtime. The packaging recipe
already declares this dependency and the verified `1bbfa624` archive contains
it. The diagnostic launcher now accepts an explicit bundle argument, and the
export probe checks this dependency before opening a large model. The rerun
uses the rebuilt native modules with that complete matching Python/Qt bundle;
this does not substitute for final newly packaged archive acceptance.

The complete-runtime rerun passes (`ok: true`) and closes automatically:

```powershell
$env:VIBECAD_SIMULATION_PLAYBACK_ASYNC = '1'
$env:VIBECAD_SIMULATION_AI_PLAYBACK = '1'
$env:VIBECAD_SIMULATION_AI_RESEEK = '1'
$env:VIBECAD_SIMULATION_LIFECYCLE = '1'
$env:VIBECAD_SIMULATION_EXPORT = '1'
$env:VIBECAD_SIMULATION_EXPORT_CANCEL = '1'
& build/run-native-gui-diagnostic.ps1 -Name simulation-export-cancel-complete-runtime-20260909 -Script src/Tools/performance/simulation_playback_probe.py -Document build/simulation-publication-20260908-150322/Johhny5.FCStd -Bundle build/portable-publication-simulation-1bbfa624/VibeCAD-26.3.1-RC6-build1-Windows-x86_64
```

Generation/display takes 47.585 s (65.203 s process CPU); camera setup 32.9 ms;
showing 336 joints 502 ms plus 226 ms independent verification of all 672 poses.
Frame, PNG and encoding-phase cancellation each preserve the previous output,
remove staging after worker completion and restore the selected frame 9. The
complete retry exports 22 frames in 10.786 s, with 20 visibly changed frames.
Closing with a pending frame restores all 337 baseline poses. Independent input
delay is at most 344 ms during generation, 47 ms during seeks, 219 ms during
playback and 250 ms during export. A 547 ms sample spans the bulk-marker check;
the largest heartbeat gap is 765 ms including probe comparisons. Do not label
that entire interval as generation or claim the remaining bulk work is free.

The older assembly still emits a bad-Reference2 warning (this run at `j_pin_10`;
the previous native run reported Reference1 at `j_pin_13`). It remains an open
attribution item. This successful selected-simulation test does not establish
that all older assemblies are valid. No provider/encoder error remains in the
complete-runtime retry.

## Remaining publication cost and export teardown (2026-09-09)

The rebuilt-native reference-only audit completed in 164.078 s. All 30 older
track-pin joints and both targets per joint were active and resolved after
opening; stderr was empty. This rules out permanently absent saved references
in that run, not the intermittent warning observed previously. Opt-in native
reference diagnostics remain available for the next reproduction.

The current native publication replay used the same protected 3,022-object
document and retained 1,159-item candidate, cancelling after item 900 before
retrying. It passed exact rollback and completed with 3,253 objects; source,
request, result and manifest bytes remained unchanged. Isolated validation
took 4.687 s and the profiled retry took 71.969 s through presentation.
The profile attributes 49.300 s across 457 calls to
`publishProvisionalTimelineOperationBlock`, across cancellation and retry.
Prior-publication pruning rescans all History ownership for every publication
record. The candidate shares one call-local membership census while retaining
exact per-record identity, role, ownership, order and property-status checks.
This is removal of repeated native work, not parallel live-document mutation.

The cancellation callback was 1.172 s with cProfile enabled (previous
unprofiled measurement: 0.750 s). The 1.813 s maximum input sample at replay
startup overlaps probe fixture decoding/contract checks and cold Workbench
activation; do not attribute that entire sample to the isolated validator.
Evidence: ignored `publication-callback-profile-dea5265d-20260909` diagnostic.

Document-close export coverage also passes against the complete rebuilt-native
runtime. The actual AI player generates and seeks the robot, then its real
export entry starts native PNG capture. Closing the disposable document with a
pending image token cancels the export. The previous output is unchanged,
staging is removed only after worker completion, the controller stops polling,
and no restore frame is requested for the deleted player. The probe completed
in 224.266 s and exited normally, with empty stderr. Player-close coverage and
final archive repetition remain pending.

```powershell
$env:VIBECAD_SIMULATION_PLAYBACK_ASYNC = '1'
$env:VIBECAD_SIMULATION_AI_PLAYBACK = '1'
$env:VIBECAD_SIMULATION_AI_RESEEK = '1'
$env:VIBECAD_SIMULATION_EXPORT = '1'
$env:VIBECAD_SIMULATION_EXPORT_CLOSE = 'document'
& build/run-native-gui-diagnostic.ps1 -Name simulation-export-document-close-dea5265d-20260909 -Script src/Tools/performance/simulation_playback_probe.py -Document build/simulation-publication-20260908-150322/Johhny5.FCStd -Bundle build/portable-publication-simulation-1bbfa624/VibeCAD-26.3.1-RC6-build1-Windows-x86_64
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_animation_export.py src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py -q
```

The `publication-joint-notification-green-20260909` replay passes exact
900-item rollback and retry, preserving all fixture bytes and finishing with
3,253 objects. Profiled retry time falls from 48.344 to 43.110 s. Across the
same 29,264 calls, joint-change callback time falls from 6.433 to 0.948 s;
owning-assembly lookups fall from 11,300 to 4,912. Marker property callback
time falls from 1.232 to 0.439 s across 23,936 calls. Cancellation is still an
oversized 1.187 s owner callback; this change does not claim to fix that pause.

## Concurrent linked-source resolution (2026-09-09)

The activity diagnostic catches `link_target_unresolved` for a published source
link during recompute. Its saved `LinkedObject` points at a valid named source
inside its program container. The native resolver calls `getSubName()`, which
clears and rewrites shared strings/vectors on every read. It then passes the
returned string pointer into subobject traversal. Simultaneous display reads
can clear that storage between those steps, returning the link itself or a
different object instead of the linked source. This can silently exclude a
constraint during validity checks as well as produce the reported warning.

The native regression warms ordinary dependency caches, leaves the document
unchanged, and concurrently resolves one subobject link while reading its
public sub-element cache. Before the fix it records 4,204, 4,527 and 3,932 wrong
targets across three readers (12,663 failures in 60,000 target resolutions).
The resolver now derives its traversal path into local storage directly from
the unchanged link property. It no longer mutates or borrows display-cache
storage. Existing APIs, sub-element parsing rules and property-change handling
are retained; no retry, warning suppression or GUI-thread solve is introduced.

```powershell
build/authoritative-runtime-build.cmd App_tests_run
# Red: exit 1; 12,663 target mismatches, regression takes 36 ms.
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=DocumentTest.concurrentLinkedSubobjectResolutionPreservesTarget
build/authoritative-runtime-build.cmd FreeCADApp App_tests_run
# Green: all 25 repetitions pass, 1.5 million target resolutions.
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=DocumentTest.concurrentLinkedSubobjectResolutionPreservesTarget --gtest_repeat=25
# Green: 81 native document/dependency tests pass.
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=DocumentTest.*:SemanticDependencyOrder.*
# Final consolidation: 115 document, dependency and Link tests pass.
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=DocumentTest.*:SemanticDependencyOrder.*:LinkTest.*
```

Commands use the matching native dependency/Python environment documented
above. The actual robot lifecycle then passes in
`simulation-link-resolution-green-20260909`, completing in 246.688 s and exiting
automatically. Stderr is empty, with neither the bad-reference warning nor the
new inactive-source diagnostic. The actual AI service generates and displays
the simulation, performs exact seeks and playback, verifies marker poses,
cancels export during frame preparation, PNG capture and encoding, then retries
the complete animation. Full export takes 11.431 s. Closing with a pending frame
restores all 337 baseline poses. Maximum independent input delay is 578 ms
across the entire instrumented lifecycle (including bulk marker verification);
the largest heartbeat gap is 750 ms. Final full-archive acceptance remains
outstanding; this does not claim every owner callback is short.

```powershell
$env:VIBECAD_SIMULATION_PLAYBACK_ASYNC = '1'
$env:VIBECAD_SIMULATION_AI_PLAYBACK = '1'
$env:VIBECAD_SIMULATION_AI_RESEEK = '1'
$env:VIBECAD_SIMULATION_LIFECYCLE = '1'
$env:VIBECAD_SIMULATION_EXPORT = '1'
$env:VIBECAD_SIMULATION_EXPORT_CANCEL = '1'
$env:VIBECAD_SIMULATION_EXPORT_CLOSE = ''
& build/run-native-gui-diagnostic.ps1 -Name simulation-link-resolution-green-20260909 -Script src/Tools/performance/simulation_playback_probe.py -Document build/simulation-publication-20260908-150322/Johhny5.FCStd -Bundle build/portable-publication-simulation-1bbfa624/VibeCAD-26.3.1-RC6-build1-Windows-x86_64
```

The consolidated Python command passes: 36 tests in 3.63 s. The new native
membership-count regression was added first; the red build fails because
`countSemanticMembers` is not yet present. The full native build then passes,
and all 80 Document and semantic-dependency tests pass in 1.917 s, including the
live-document ownership-change/provenance regression. No newly packaged
archive is claimed here.

```powershell
build/authoritative-runtime-build.cmd App_tests_run # red: missing census helper
build/authoritative-runtime-build.cmd all           # green: exit 0
# Native test dependency/Python environment configured as above:
build/authoritative-runtime-native/bin/App_tests_run.exe --gtest_filter=SemanticDependencyOrder.*:DocumentTest.*
```

The same profiled robot replay now passes in
`publication-membership-green-20260909`: exact 900-item rollback, full retry,
3,253 final objects and unchanged fixture bytes. The native History publication
calls fall from **49.300 to 6.447 s** across the same 457 calls (86.9% less).
The full retry through presentation falls from **71.969 to 48.344 s** (32.8%
less). The overall owner-thread profile drops from 91.252 to 48.872 s.
Stderr is empty and the diagnostic exits normally.

Cancellation remains a separate oversized callback: 1.282 s profiled, with
1,078 ms independent input delay. The next-largest owner callback is 219 ms.
Do not claim that the ownership census fixes rollback latency or every UI
pause; it removes the measured repeated History work without weakening its
live-state checks.

## Player teardown and joint notification filtering (2026-09-09)

Player-close export coverage passes in
`simulation-export-task-close-d12fbc13-20260909` (222.125 s, automatic exit).
Rejecting the actual task while native PNG capture is pending cancels the
export, restores all 337 baseline poses, preserves the previous animation,
and removes staging after the worker finishes. The controller stops polling
and does not attempt to restore a frame into the closed player.

```powershell
$env:VIBECAD_SIMULATION_PLAYBACK_ASYNC = '1'
$env:VIBECAD_SIMULATION_AI_PLAYBACK = '1'
$env:VIBECAD_SIMULATION_AI_RESEEK = '1'
$env:VIBECAD_SIMULATION_LIFECYCLE = '0'
$env:VIBECAD_SIMULATION_EXPORT = '1'
$env:VIBECAD_SIMULATION_EXPORT_CANCEL = '0'
$env:VIBECAD_SIMULATION_EXPORT_CLOSE = 'task'
& build/run-native-gui-diagnostic.ps1 -Name simulation-export-task-close-d12fbc13-20260909 -Script src/Tools/performance/simulation_playback_probe.py -Document build/simulation-publication-20260908-150322/Johhny5.FCStd -Bundle build/portable-publication-simulation-1bbfa624/VibeCAD-26.3.1-RC6-build1-Windows-x86_64
```

This run reproduces the older assembly's Reference1 warning on two joints.
The native diagnostic reports an unresolved moving part despite an existing
target and two saved subreferences. Immediately repeated joint/target History
checks pass. This establishes an intermittent runtime failure, not its cause;
the target is not permanently absent from the saved document. Additional
opt-in diagnostics record the failing activity-check stage during recompute.

The publication profile attributes 6.433 s to 29,264 `Joint.onChanged` calls,
including 11,300 owning-assembly lookups. Most notifications are metadata or
playback placements for which that handler has no action. The handler now
filters those properties before resolving History or ownership. Reference and
joint-type handlers return after their existing work. The view-provider
handler likewise ignores properties other than its two marker placements
before resolving `ViewObject`. Existing editing behavior remains unchanged.

Red: 14 focused unhandled-property cases failed before implementation. Green:
45 playback-cache tests pass, including seven handled-property edit cases.
The consolidated command passes 57 tests in 3.51 s; the native touched-area
build exits 0. Actual publication timing is recorded separately after replay.

```powershell
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py -q
build/authoritative-runtime-build.cmd Assembly src/Mod/Assembly/AssemblyScripts
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_animation_export.py src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py -q
```

## Persisted native playback (2026-09-09)

Previously, asynchronous playback reused solved channels only while the native
Assembly object remained alive. Closing/reopening a document required another
kinematic solve. The new additive `startSimulationPlayback` entry reuses private
persisted channels for matching detached native inputs. Existing
`startSimulation` and `generateSimulation` retain full-solver semantics. The
human/AI read-only player selects the playback entry; authoring generation
continues selecting the full solver.

Input keys use the existing Ondsel serializer at round-trip double precision,
canonical container ordering, component names/offsets, and producer revision.
Hashing streams through the serializer without an input temporary file or a
whole-text allocation. Cached channels are checked against their input key,
dimensions, actual file length, finite samples, byte order and payload digest.
Truncated/corrupt/missing data is regenerated asynchronously on the existing
HostRuntime, with no synchronous GUI recovery path. File-size arithmetic, not
an arbitrary component/frame ceiling, limits decoding. Atomic cache writes
cannot replace a good entry with a partial one. An optional cache write failure
is reported without throwing away the successful simulation; cancellation and
solver failures still propagate.

Cached poses do not masquerade as a complete ASMT engine: full solver data and
diagnostics remain separate. The same observed input graph, object identity
checks, request tokens, cancellation and pose application protect both sources.
The legacy frame method remains available; the interactive player continues
using parallel detached frame preparation and nonwaiting owner adoption.

Red: the native cache test initially fails to compile because the cache API is
absent; the input-key test then fails for the missing key API. The Python
entry-selection test fails for read-only playback (one failure, one pass).
Green: nine native cache/Assembly/frame tests and 59 Python playback tests pass.
Native coverage checks unchanged-input reuse, retaining full solver identity,
full-state requests after cached playback, corruption, truncation, invalid
keys, producer/input changes, invalidating an in-flight cached frame and
regenerating after a time-range edit, and independent frame preparation.
The final native run passes all nine tests in 1.853 s; the consolidated Python
export/service/playback/report-writer run passes 73 tests in 6.78 s. The complete
native `all` build exits 0, not just the Assembly target.

```powershell
build/authoritative-runtime-build.cmd Assembly_tests_run
build/authoritative-runtime-native/bin/Assembly_tests_run.exe
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_playback.py -q
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_animation_export.py src/Mod/VibeCAD/vibecad_tests/test_async_simulation_service.py src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_playback.py src/Tools/performance/test_async_report_writer.py -q
build/authoritative-runtime-build.cmd all
$env:VIBECAD_SIMULATION_PLAYBACK_ASYNC = '1'
$env:VIBECAD_SIMULATION_AI_PLAYBACK = '1'
$env:VIBECAD_SIMULATION_AI_RESEEK = '1'
$env:VIBECAD_SIMULATION_LIFECYCLE = '1'
$env:VIBECAD_SIMULATION_EXPORT = '1'
$env:VIBECAD_SIMULATION_EXPORT_CANCEL = '1'
$env:VIBECAD_SIMULATION_PERSISTED_REOPEN = '1'
& build/run-native-gui-diagnostic.ps1 -Name simulation-persisted-reopen-20260909 -Script src/Tools/performance/simulation_playback_probe.py -Document build/simulation-publication-20260908-150322/Johhny5.FCStd -Bundle build/portable-publication-simulation-1bbfa624/VibeCAD-26.3.1-RC6-build1-Windows-x86_64
```

The rebuilt native GUI passes the real 3,022-object robot lifecycle in
408.719 s with empty stderr and normal exit. Initial generation/display takes
47.648 s (64.500 CPU s); after closing/reopening the same disposable document,
playback preparation/display takes 0.571 s (0.797 CPU s), excluding document
opening and the 0.442 s player submission. All 337 component poses match the
generated baseline at frames 1, 11 and 21. All 672 joint-marker poses match;
showing 336 joints takes 0.482 s. Frame/PNG/encoding export cancellation restores
the selected frame. A successful 22-frame export follows in 11.340 s, then
closing with a pending frame restores all 337 original poses.

The maximum input sample is 1,219 ms at the probe's combined visibility
restoration, player close and document close/reopen callback. The next-largest
sample is 500 ms during initial player submission. This is not evidence of
uniform sub-100-ms interaction. The probe now profiles these cleanup actions
separately and records process CPU/RSS at event boundaries for final-archive
acceptance. This native diagnostic is not the final packaged-build acceptance.

## Discarded rollback notifications (2026-09-09)

The remaining profiled cancellation callback took 1.187 s. Earlier attribution
showed 10,808 VibeCAD GUI observer notifications consuming 0.889 s during native
abort; their aggregate revision/cache/dependency work is subsequently discarded
when the publication batch ends with `commit=False`.

An explicit thread-local rollback scope now lets only VibeCAD's advisory
object-created/deleted/changed observer skip this already-discarded work.
The production cooperative publisher enters that scope around its existing
native `abortTransaction` call. Native transaction replay and all other native
or third-party observers still receive their ordinary notifications. The scope
does not apply to normal undo/redo, other documents or other threads, and does
not activate when no atomic batch exists. A failed abort cannot leak the scope.
The existing outer batch still determines the commit/rollback outcome and
performs its normal final refresh. Public entry points and defaults are retained.

Seven new regression cases fail before implementation. Afterwards, the
consolidated state, publication and progress suites pass 108 tests in 1.87 s,
including the added unbatched compatibility case. The script build exits 0.

```powershell
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_native_state.py -q -k 'publication_abort_skips or rollback_notification_scope'
# Red: seven failures before the rollback scope and integration exist.
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_native_state.py src/Mod/VibeCAD/vibecad_tests/test_vibescript_timeline_publication.py src/Mod/VibeCAD/vibecad_tests/test_vibescript_publication_progress.py -q
build/authoritative-runtime-build.cmd src/Mod/VibeCAD/VibeCADScripts
$env:VIBECAD_PUBLICATION_ATTEMPT = (Resolve-Path build/native-diagnostics/publication-profile-fixture-dea5265d/attempt).Path
$env:VIBECAD_PUBLICATION_MANIFEST = (Resolve-Path build/native-diagnostics/publication-profile-fixture-dea5265d/program.json).Path
$env:VIBECAD_PUBLICATION_PROFILE = '1'
$env:VIBECAD_PUBLICATION_CANCEL_AFTER = '900'
$env:VIBECAD_PUBLICATION_WORKING_CANDIDATE = '1'
& build/run-native-gui-diagnostic.ps1 -Name publication-rollback-observers-green-20260909 -Script src/Tools/performance/retained_publication_probe.py -Document build/simulation-publication-20260908-150322/Johhny5.FCStd -Bundle build/portable-publication-simulation-1bbfa624/VibeCAD-26.3.1-RC6-build1-Windows-x86_64
```

The actual run passes exact 900-item rollback, returns to the original 3,022
objects, then retries all 1,159 publication items and finishes with 3,253
objects. All source/request/result/manifest bytes remain unchanged. Stderr is
empty and the process exits normally. The profiled cancellation callback falls
from 1.187 to 0.453 s (61.8% less); native `abortTransaction` totals 0.438 s,
including 0.257 s of native work. The next owner callback is 0.343 s. Full retry
takes 42.750 s. The 1,562 ms maximum input sample occurs during replay startup,
which also includes probe fixture checks and cold Workbench setup; it is not
the rollback callback. Final unprofiled archive acceptance remains required.
