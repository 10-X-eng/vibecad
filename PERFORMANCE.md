# VibeCAD performance work

Status: active
Last updated: 2026-09-11

## What this work delivers

This work makes VibeCAD stay responsive while it opens, updates, publishes,
renders, and simulates large models. The main change is a shared execution
model: expensive work runs against detached data on application-owned workers,
while the GUI thread performs only the document mutations and presentation that
must happen there.

People using VibeCAD should see practical improvements:

- Large documents open and become usable sooner instead of monopolizing the
  interface until every follow-up task finishes.
- Independent geometry and publication work can use available CPU cores while
  respecting a shared CPU and memory budget.
- Tree and History updates reuse prepared projections and avoid repeated
  whole-document scans when the underlying structure has not changed.
- Saved assembly simulations reopen from authenticated native results instead
  of solving the same unchanged mechanism again.
- Simulation playback moves existing scene objects directly. It no longer
  rewrites hundreds of document placements and refreshes the Tree every frame.
- Cancellation, close, rollback, and stale-result rejection follow the same
  operation state instead of leaving detached work to race a changed document.
- Linux AppImages use the host's matching DRM stack, avoiding startup failures
  caused by an older bundled `libdrm` shadowing the libraries used by host Mesa.
- Large valid requests are governed by real resource admission and explicit
  caller limits instead of arbitrary model, output, source-event, or JSON-size
  ceilings.

This is shared infrastructure rather than a collection of workbench-specific
fast paths. Assembly, VibeScript, Tree, History, rendering, document lifecycle,
and import/export code use the same runtime and ownership rules.

## What changed

### Shared runtime and resource admission

VibeCAD now has an application-owned runtime for CPU work, I/O work, isolated
processes, and GUI adoption. Nested work shares one CPU budget, so parallel
algorithms cannot silently create competing pools and oversubscribe the host.
Cancellation and shutdown propagate through nested submissions.

Numerical libraries are initialized with bounded thread teams and admitted
according to available CPU and memory. This prevents large per-thread OpenBLAS
scratch allocations from scaling with every logical processor before useful
work begins. The native pool still retains roughly 75% of logical CPU capacity
for independent CAD work.

### Document lifecycle and interface responsiveness

Open, restore, recompute, save, close, and presentation work now expose
authoritative operation state and queue eligible follow-up work. Expensive
preparation can run away from the GUI thread; live document adoption remains on
the document owner. Results carry document and revision identity so obsolete
work is rejected rather than applied to a replacement or edited document.

Tree and History maintain reusable projections of document structure. Ordinary
placement, visibility, and value changes can update presentation without
rebuilding the hierarchy, while structural, schema, extension, and linked
document changes still invalidate the projection.

Rendering preparation can fan out detached mesh and buffer work. Final Coin and
view-provider installation remains coordinated with the GUI, preserving
selection, appearance, transformed normals, edge indices, and document
ownership.

### Publication

VibeScript publication now uses indexed live-object membership, call-local
History ownership, batched dependency rebasing, persistent isolated validation,
and narrower observer work during adoption and rollback. Independent candidate
preparation overlaps where dependencies permit. Final document changes remain
serial and transactional so identities, links, revision checks, rollback, and
cancellation keep their existing meaning.

Automatic source-operation and definition-size ceilings were removed from the
default path. Explicit caller-requested limits, cancellation, overflow checks,
resource admission, candidate isolation, and geometry validation remain.

### Assembly solving and simulation playback

OndselSolver kinematic work now preserves the host executor and receives host
cancellation checks. VibeCAD prepares and authenticates native playback tracks
that can be reused after reopening an unchanged document. An edit that changes
the simulation input invalidates the cached result and schedules a fresh solve.

Interactive playback reads placements from those cached tracks and applies
them directly to existing ViewProvider scene-graph transforms. The document is
not changed for transient frames, so playback avoids recompute, property
notifications, undo traffic, and Tree refreshes. Frames advance sequentially;
a delayed render does not cause wall-clock catch-up jumps. Closing the player,
changing workflows, or starting export restores the graphics from the current
document placements. The existing document-backed presentation path remains
available where durable state is required.

This distinction is what makes smooth playback possible: the renderer moves
already-created geometry, while the CAD document remains authoritative before
and after the animation.

### Linux AppImage graphics compatibility

The AppImage build removes every `libdrm*.so*` file from its staged runtime.
Mesa loads hardware drivers from the host, so their DRM dependencies must come
from the same host graphics stack. A wildcard handles present and future ABI
filenames for core DRM and vendor libraries. The existing AMD host-library
launcher fallback remains compatible.

This fixes the failure tracked in
[issue #191](https://github.com/10-X-eng/vibecad/issues/191), where the AppImage
could fail graphics initialization even though a local build launched correctly.
Qt itself was not the incompatibility: the AppImage's older DRM library was
being loaded alongside the host's newer Mesa driver.

### Diagnostics and regression tooling

Opt-in phase tracing records elapsed time, execution lane, operation, document,
and presentation phases without synchronous file I/O on the GUI thread.
Reusable probes cover publication, simulation, native input delivery, document
lifecycle, memory, numerical-runtime behavior, rendering, and portable-package
round trips. Detailed measurements and red/green history are retained in
[the performance results ledger](src/Tools/performance/PUBLICATION_SIMULATION_RESULTS.md).

## Measured results

These are concrete checkpoints from the tested documents and machines, not
universal performance guarantees:

- The PR #205 Windows package passes clean startup and real GUI acceptance for
  Part Design task accept/cancel, every additive and subtractive primitive,
  Assembly simulation playback, Drawing redraw and section views, Analyze/FEM,
  and Manufacturing/CAM. A disposable 2,685-object robot document opened,
  saved, reopened, and compared equal by exact object type, link, and state
  inventory. Its first display settled in 176.4 seconds and save completed in
  9.4 seconds, with no access violation, Qt thread-affinity, invalid-extension,
  lost-link, or nested-recompute diagnostic.
- A saved robot simulation prepared and displayed in 47.648 seconds from a
  fresh solve and 0.571 seconds after reopen from matching authenticated native
  results. All 337 component poses were checked at three frames, along with 672
  joint-marker poses.
- A packaged 60-frame jet-engine playback probe produced consecutive frames
  with zero document-property changes, Tree updates continuously enabled, and
  zero active Tree refresh timers. Owner testing found the resulting simulation
  substantially smoother.
- The real 1,159-item publication replay completed in 42.750 seconds. Cancelling
  after 900 objects restored the exact 3,022-object inventory; retry completed
  with 3,253 objects. Profiled cancellation callback time fell from 1.187 to
  0.453 seconds.
- A concurrent linked-source regression produced 12,663 wrong results in
  60,000 lookups before the fix. The corrected path completed 1.5 million
  concurrent lookups without an error.
- Avoiding the blocking native camera animation reduced measured simulation
  setup from 1,554 to 33 milliseconds. Retained Coin marker fields reduced the
  measured display cost for 336 joints from 662 to 504 milliseconds.
- The complete Linux AppImage built successfully, contained no bundled DRM
  libraries, passed its packaged command-line and simulation probes, and
  launched successfully on the owner's real display.

## Verification

The final Linux playback and AppImage changes were developed with focused
red/green tests. Before implementation, the playback regression observed
document-placement use, the Tree regression observed an active refresh timer,
and the packaging regression had no wildcard DRM exclusion. After the fixes:

```text
cmake --build build/release --target Assembly_tests_run -j4
cmake --build build/release --target FreeCADGui DocumentBulkMutation_Tests_run -j4
  PASS: touched native targets rebuilt.

ctest --test-dir build/release \
  -R '^AssemblyObjectTest\.SimulationFramePlacementsSuppressTransientTouchState$' \
  --output-on-failure
  PASS: 1/1.

xvfb-run -a build/release/tests/DocumentBulkMutation_Tests_run \
  transientLinkPlacementsDoNotScheduleTreeRefresh
  PASS: 3 passed, 0 failed with Qt 6.11.1.

python3 -m pytest -q \
  src/Mod/VibeCAD/vibecad_tests/test_simulation_playback_cache.py
  PASS: 54 passed.

python3 -m unittest src.Tools.tests.test_linux_appimage_launcher
  PASS: 3 passed.

cd package/rattler-build/linux
CREATE_BUNDLE_PHASE=appdir ./create_bundle.sh
CREATE_BUNDLE_PHASE=appimage ./create_bundle.sh
  PASS: VibeCAD-26.3.1-RC6-build1-Linux-x86_64.AppImage built; SHA-256 verified.

APPIMAGE_EXTRACT_AND_RUN=1 \
  ./VibeCAD-26.3.1-RC6-build1-Linux-x86_64.AppImage \
  freecadcmd --safe-mode --version
  PASS: VibeCAD 26.3.1-RC6 (Build 1).
```

The performance results ledger records the broader Windows native/Python test
runs, real-GUI probes, failure history, and exact fixture measurements.

## Compatibility and remaining work

Existing public entry points, preference keys, schemas, and default workflows
remain available. New simulation frame APIs are additive. Durable presentation
and export behavior retain the document-backed path, while ordinary interactive
playback uses transient graphics transforms. The OndselSolver dependency points
to revision `f6498019`, merged into the `10-X-eng/OndselSolver` fork's `main`.

The current Linux AppImage has direct build and owner acceptance evidence.
Remaining work is focused on final acceptance rather than another architecture
rewrite:

- Repeat packaged publication cancel/rollback/retry, independent multi-output
  create/patch/input edits, playback invalidation, and export measurements on
  future publication changes. The current ABI-consistent Windows archive and
  document lifecycle acceptance are complete.
- Reduce the remaining large-document first-load and teardown event spans. The
  final PR #205 package preserves exact state and stays operational, but the
  independent input probe still records isolated 0.12-0.38 second open spans
  and a 0.75 second document-close span against the research-grade 100 ms gate.
- Complete the remaining application-wide acceptance inventory for Mesh,
  import/export, multi-document, memory-pressure, and shutdown scenarios.
- Run equivalent packaging and lifecycle acceptance on macOS.
- Continue measuring direct GUI callbacks against the 8-millisecond p95 target
  and investigate any repeatable heartbeat or native-input gap over 100
  milliseconds.

Future fixes should identify a measured failing path, add a focused regression
first, use the shared runtime and ownership model, and preserve public behavior.
Passing an isolated unit test is evidence for that path; final acceptance still
requires the complete packaged application and real documents.
