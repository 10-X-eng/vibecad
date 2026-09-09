# Packaged Windows GUI acceptance

## Windows memory attribution

Run `memory_lifecycle_probe.py` only in a separate packaged GUI with isolated
preferences and a disposable `probe-document.FCStd`. Set
`VIBECAD_ROUNDTRIP_COPY` to that copy and `VIBECAD_TRACE_PROBE_RESULT` to a JSON
path in the same directory. It measures process-private commit and resident
working set before opening and through two open/close cycles; it never saves
the document. It closes only its own diagnostic process when finished.
The sampling, observer logging and Python collection are diagnostic overhead,
not a timing benchmark or a production cache-eviction policy.

Run `blas_memory_probe.py` with the packaged `bin/python.exe` to isolate math
library initialization from the application. It measures memory before and
after loading `openblas.dll`, importing NumPy and multiplying a matrix, verifies
the result, and reports the library configuration and thread count. An explicit
`OPENBLAS_PROBE_DLL` path permits an isolated library-variant comparison; never
replace DLLs in a user's running package. Thread environment experiments must
be process-local, not machine-wide.

Pass `--benchmark` to also measure a 2048-square matrix multiplication, a
1024-square positive-definite solve with eight right-hand sides, and a
512-square singular-value decomposition. Each has one warm-up and three timed
runs with numerical verification outside the timed interval. Set
`OMP_NUM_THREADS` in each fresh process when comparing the packaged OpenMP
OpenBLAS build. The ordinary `OPENBLAS_NUM_THREADS` setting is not effective
for that build. Keep the application worker pool unchanged during comparisons.
These isolated kernels are not an end-to-end CAD benchmark; machine contention
and coarse process CPU-time accounting affect the reported timings/core ratios.
Do not infer a universal thread limit from one size or one run.

Private commit is not resident physical RAM. A high private value alone does
not prove a leak, and a small idle working set does not prove the committed
allocation is free. Repeated-cycle retention and allocation provenance must be
investigated separately. Keep raw reports local, not in git.

## Document round trip

This probe exercises the portable launcher's real environment and the GUI's
open, Save, Close All, and reopen commands. It is not a substitute for solver,
recompute, publication, workbench, or large-document acceptance.

```powershell
./src/Tools/performance/run_portable_roundtrip.ps1 `
  -Bundle <complete-portable-directory> `
  -Document <original.FCStd> -Name roundtrip-001
```

The launcher hashes and copies the input before launch. It only opens the
`probe-document.FCStd` copy, isolates application preferences and agent data,
and refuses to overwrite an existing run. Results default to the ignored
`build/portable-validation-results` directory; `-ResultsRoot` overrides it.
Never commit these outputs or customer documents. The original is never saved.
Do not point the launcher at a development executable or a DLL-overlay package.

Use `-BaselineReport <prior-roundtrip.json>` to compare exact object names,
types, outgoing links, and states with a known-good application. Comparison
normalizes self-document links and uses JSON-compatible lists. Saved invalid
flags must remain unchanged; pre-existing invalid objects are not hidden.

If a previously saved invalid flag clears during restoration, the strict run
stops for inspection. To continue a separate diagnostic run, explicitly name
the inspected objects with `-AllowClearedInvalidObjects <name>`. The report
records these cleared flags at each inventory phase. New invalid objects still
fail, and save/reopen must still preserve the complete opened inventory exactly.
This option does not establish that the previously failed operation now solves
correctly; retain the strict failure and investigate that separately.

The benchmark measures a Qt heartbeat and window-local native input delivery.
A separate bundled `pythonw.exe` posts F23 messages to this diagnostic window
every 250 ms. It does not inject desktop keyboard input or change focus. Queue
timestamps reveal input delayed while the GUI cannot run Python or timers.
The helper stops through a named event, parent-process exit, or window-owner
change. This helper is benchmark instrumentation, not a production worker.

`roundtrip.json` reports integrity and responsiveness separately. `ok` requires
both, including input samples and no measured heartbeat/input delay above
100 ms. Failure is retained as failure; the probe never dismisses an unexpected
save prompt or clears modified state to force a pass. Integrity failures leave
the diagnostic window available for inspection. Successful runs close it.
Screenshots are captured outside the timed interval; inspect them for shaded
geometry, Tree, History, and display correctness.

`-ProfileCallbacks` also writes `roundtrip.pstats` for Python callback
attribution. Profiling adds overhead: use a separate unprofiled run for timing
acceptance. The input sender, heartbeat, and observer diagnostics also add some
overhead; compare runs with the same instrumentation. The opt-in production
trace switch `VIBECAD_RESTORE_DETAIL_TRACE` is enabled by this launcher.

`-ProfileCommandChecks` measures each instantiated command's `isActive()` check
after the first open and records `command_checks` in the report. It does not
execute commands or create additional actions. Checks are spread across probe
ticks; use a separate run without this option for final timing acceptance.

```powershell
python -c "import pstats; pstats.Stats('<roundtrip.pstats>').strip_dirs().sort_stats('cumulative').print_stats(30)"
```

Keep original logs and failed reports. A successful build, runtime smoke test,
or matching inventory alone does not establish responsiveness.

For original FreeCAD corpus files that predate VibeCAD History, pass
`-AllowTimelineMigration` explicitly. The probe then accepts exactly one new
`App::DocumentTimeline` on first open and records its name as
`timeline_migration`. Every original object must remain; any other addition or
loss still fails. Reopen must preserve the complete migrated inventory. This
option does not relax invalid-state, link round-trip, or responsiveness checks.

`-TraceNativeEvents` adds C++/Qt event attribution to `roundtrip.json`: event
type, receiver class/name, nesting depth, start time, and elapsed time. Native
records are collected only with `VIBECAD_RESTORE_DETAIL_TRACE`; collection
does no file I/O on the GUI thread. A 4,096-record diagnostic buffer reports
overflow explicitly, and the probe drains it each tick. This is a trace-memory
bound, not an operation limit. Use an untraced run for final timing acceptance.
Named native scopes use the same buffer and clock, with `event_type: -1` and
a `phase` label. Close scopes distinguish delete notifications, view-provider
destruction, and viewer/provider-graph release; these scopes can overlap and
their durations must not be added together as independent costs.
`gui_event_trace_probe.py` checks recording and draining with one deliberately
slow diagnostic event; set `VIBECAD_TRACE_PROBE_RESULT` to its ignored JSON path
and run it in a fresh packaged GUI with tracing enabled.

## Generated dependency workload

`generate_boolean_document.py` creates 200 independent Box/Cylinder/Cut
branches: 600 CAD features, plus the native History controller. It checks every
result's solid count and analytic volume before saving, and records recompute
wall time and whole-process CPU time. CPU time divided by wall time estimates
average occupied cores; a configured worker count alone is not parallelism
evidence. Run it through the complete portable **root** command launcher, or a
native build's matching Python/DLL environment:

```powershell
$env:VIBECAD_GENERATED_DOCUMENT = '<ignored-output-directory>/parallel-600.FCStd'
& '<portable-directory>/FreeCADCmd.exe' ./src/Tools/performance/generate_boolean_document.py
```

The generator refuses to overwrite an existing file. Feed the generated FCStd
to the GUI round-trip launcher afterwards; headless geometry checks do not
prove UI responsiveness. Retain the adjacent JSON measurements with the run.
Add `-ExerciseGeneratedRecompute` to touch all 600 generated features and click the real
History recompute button before saving. This records command-return and total
CPU/wall time, waits for native recompute and presentation completion, and
checks types, links, and invalid flags again (Touched may clear). It only accepts the named
generated workload; do not enable it for a customer document.

After the GUI run, check the saved copy's geometry through the same portable
root command launcher without loading geometry synchronously into the GUI:

```powershell
$env:VIBECAD_VALIDATE_DOCUMENT = '<run-directory>/probe-document.FCStd'
$env:VIBECAD_EXPECTED_GEOMETRY = '<generated-document-directory>/parallel-600.json'
& '<portable-directory>/FreeCADCmd.exe' ./src/Tools/performance/validate_boolean_document.py
```

This read-only check compares object count and every result's solid count,
volume, and face count with the generator's original measurements.

## VibeScript edit and cancellation lifecycle

Run `vibescript_edit_probe.py` through the complete portable GUI launcher with
an isolated user profile. Set `VIBECAD_TRACE_PROBE_RESULT` to a new, ignored JSON
report path. The probe creates its own 50-output document and project, exercises
the real create, source-patch, and input-edit adapters, then cancels a patch
during publication. It checks geometry, retained object identities, and rollback.
It records phase events and GUI heartbeat gaps, then closes its own document
and diagnostic instance. Do not inject it into a user's working session.

The probe reuses the repository's native integration service fixture; production
modules and the worker runtime come from the running build. A successful model
assertion alone is not a responsiveness pass: inspect the recorded gaps and
native traces as well. Initial object adoption and final projection costs are
reported separately from the background worker duration.

## Model-capacity and retained-result checks

These Python-only checks use the packaged native kernel, without opening or
modifying a user's GUI document. On Windows, use the portable's matching Python,
DLLs and modules (not the system Python):

```powershell
$bundle = '<extracted-portable-directory>'
$env:PYTHONHOME = "$bundle/bin"
$env:PYTHONPATH = "$bundle/bin;$bundle/Mod/VibeCAD"
$env:PATH = "$bundle/bin;$env:PATH"
& "$bundle/bin/python.exe" src/Tools/performance/model_capacity_probe.py --module-dir src/Mod/VibeCAD
& "$bundle/bin/python.exe" src/Tools/performance/large_definition_probe.py --module-dir src/Mod/VibeCAD
& "$bundle/bin/python.exe" src/Tools/performance/collision_scale_probe.py --module-dir src/Mod/VibeCAD
& "$bundle/bin/python.exe" src/Tools/performance/assembly_retained_validation_probe.py --module-dir src/Mod/VibeCAD --attempt '<retained-attempt-directory>'
```

The capacity probe captures and reloads 337 native leaf shapes and 2,437
occurrences, then executes a small joint simulation through the real worker,
host postconditions, publication and acceptance. `--components 3000` requests
an expensive native simulation stress run; it is not the default smoke test.
The large-definition probe validates a >1 MB mesh through worker and host.
The collision probe measures sparse box fixtures, including 10,000 components;
its timings are not representative of arbitrary aircraft or robot geometry.
The retained-result probe reads an existing successful Assembly worker attempt
and re-runs host validation, checking that request/result files and the native
document list remain unchanged. It does **not** publish into a user's document.

The capacity-fix batch passed 290 focused Python tests. Packaged checks passed:
1,301,778-byte mesh worker/host validation; native hierarchy and small simulation
publication; 10,000-component sparse collision analysis; and a retained simulation
with 22 frames, 7,414 poses and a 1,259,836-byte largest definition. A separate
3,000-component native stress run was stopped during recompute and is **not**
passing evidence. These checks establish capacity/validation behavior, not GUI
responsiveness for every model.

Run the focused regression batch from the repository environment:

```powershell
.pixi/envs/default/python.exe -m pytest src/Mod/VibeCAD/vibecad_tests/test_model_size_limits.py src/Mod/VibeCAD/vibecad_tests/test_mechanism_engine.py src/Mod/VibeCAD/vibecad_tests/test_native_assembly_bom.py src/Mod/VibeCAD/vibecad_tests/test_vibescript_definition_size.py src/Mod/VibeCAD/vibecad_tests/test_mechanism_geometry_scale.py src/Mod/VibeCAD/vibecad_tests/test_modeling_surface_architecture.py src/Mod/VibeCAD/vibecad_tests/test_domain_artifact_batch.py src/Mod/VibeCAD/vibecad_tests/test_native_model_fastener.py -q --tb=short
```

Removing fixed model-count and definition-size ceilings does not remove finite
number, identity, connectivity, ownership, cycle, or artifact-integrity checks.
AI previews remain bounded with omission counts. Existing memory-related guards
(including the 64 MiB simulation trace and 256 MiB native artifact guards),
recursive depth guards and explicitly requested read budgets remain. This is
not a claim that every limit elsewhere in the platform has been removed.
