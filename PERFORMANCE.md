# VibeCAD Performance Program

Status: ACTIVE  
Owner: VibeCAD engineering  
Last updated: 2026-09-04  
Tracking issue: [#167](https://github.com/10-X-eng/vibecad/issues/167)  
Foundation PR: [#168](https://github.com/10-X-eng/vibecad/pull/168)

## Mission

VibeCAD must remain responsive while opening, inspecting, modifying, analyzing,
solving, publishing, rendering, importing, exporting, or saving complicated CAD
documents. Upstream synchronous behavior is not an acceptable product constraint.
If a FreeCAD or third-party operation cannot safely run concurrently in the UI
process, VibeCAD must isolate it in a worker process, stream its progress, and
apply its result without starving the interface.

This document is the authoritative plan and completion ledger for that work. A
performance item is not complete because the code looks asynchronous or because
one manual test felt faster. It is complete only when the required measurements,
correctness tests, stress tests, and packaged-build acceptance evidence are
recorded here.

## Non-negotiable outcomes

1. The GUI event loop never performs unbounded computation, filesystem I/O,
   network I/O, process waits, or repeated whole-document traversal.
2. Every operation that can take noticeable time returns control to the GUI,
   displays a truthful phase and progress state, and supports cancellation at a
   safe checkpoint.
3. Expensive results are cached by the exact inputs and document revision that
   produced them. Expensive work is not repeated without a relevant change.
4. Independent, thread-safe computation uses up to approximately 75% of the
   machine's logical CPUs by default, leaving capacity for the UI and operating
   system.
5. Live FreeCAD and Qt objects are never accessed from an unsafe thread. Work that
   cannot satisfy this rule runs in an isolated process and returns immutable
   data.
6. Performance improvements preserve document correctness, undo/redo behavior,
   saved-file compatibility, public APIs, tool schemas, and existing defaults.
7. No optimization is accepted without a failing regression test or benchmark
   first and a measured before/after result afterward.

## Responsiveness contract

These are engineering gates, not aspirational observations.

| Signal | Target | Failure threshold |
|---|---:|---:|
| Direct UI event handler, p95 | <= 8 ms | > 16 ms |
| One GUI-thread apply unit, target | <= 1 ms | > 16 ms without an explicit measured exception |
| GUI heartbeat gap during background work, p99 | <= 50 ms | > 100 ms |
| Visible acknowledgement of a command | <= 50 ms | > 100 ms |
| First truthful progress state | <= 100 ms | > 250 ms |
| Progress heartbeat while active | <= 1 s | > 2 s without a declared uninterruptible phase |
| Cancellation acknowledgement | <= 100 ms | > 250 ms |
| Cancellation checkpoint | <= 1 s | > 2 s except a documented external atomic call |
| Synchronous disk/network/process wait on GUI thread | 0 | Any occurrence |
| Redundant full-document scans per logical operation | 0 | Any occurrence |

An unavoidable third-party atomic call longer than 16 ms must be named in the
trace, have progress painted before entry, and have a plan for isolation or
replacement. Merely recording that a slice exceeded its budget does not satisfy
this contract.

## Correct execution architecture

### UI process

The UI process owns Qt widgets, the Coin scene graph, live `App::Document`
presentation, selection, and lightweight command orchestration. It may apply
small, measured document changes, but it must not construct or validate complex
geometry, wait on workers, tessellate hundreds of solids serially, or rebuild the
same projection once per object.

### Persistent runtime

VibeCAD will initialize a host-owned runtime during application startup:

- an I/O pool for filesystem, network, subprocess communication, and downloads;
- a native CPU pool for explicitly thread-safe C++ and OpenCASCADE algorithms;
- persistent isolated processes for Python CPU work and unsafe FreeCAD work;
- a single-writer document actor/queue for each open document;
- a bounded GUI apply queue for thread-affine document and presentation changes;
- one progress, cancellation, diagnostics, and back-pressure contract shared by
  human commands and AI tools.

The default CPU concurrency is `max(1, floor(logical_cpu_count * 0.75))` for
parallelizable CPU work. The runtime must cap queued work, avoid oversubscription
with libraries that already parallelize internally, and lower worker priority
when necessary to preserve input and rendering latency.

### Data boundaries

- Workers receive immutable snapshots, authenticated artifact paths, revisions,
  and explicit settings.
- Workers return immutable geometry, render meshes, metadata, diagnostics, and a
  deterministic publication plan.
- No `DocumentObject`, `ViewObject`, Qt object, Coin node, or borrowed OpenCASCADE
  pointer crosses a worker boundary.
- Each result carries its source document UID, revision, input fingerprints, and
  settings fingerprint. Stale results are rejected before apply.
- Modeling BREP remains authoritative. A worker-generated display mesh is a
  cacheable presentation artifact and never replaces modeling geometry.

### GUI apply

- A document-scoped mutation lease prevents conflicting undo, redo, close,
  recompute, refresh, or mutation while an apply operation is active.
- Apply work is divided into independently measured units.
- Cheap units may be adaptively batched until the current frame-time budget is
  reached. The scheduler returns to the event loop before the next batch.
- A single slow unit cannot be repaired by batching. Its expensive preparation
  must move to a worker or be subdivided below the thread-affine boundary.
- Observers, Tree updates, Timeline updates, cache invalidations, and visibility
  changes are coalesced once per logical operation.
- Terminal success or failure is emitted only after commit or rollback and all
  required stabilization work has completed.

## Measurement and proof infrastructure

### Cross-language trace

Add one low-overhead trace format shared by C++ and Python. Each span records:

- operation and phase name;
- document UID and revision, without document contents;
- thread and process identity;
- start time, duration, item count, and byte count where relevant;
- GUI-thread classification;
- cache hit/miss;
- cancellation and outcome;
- parent operation ID.

Development builds write bounded Chrome Trace Event JSON so Windows Performance
Analyzer, Perfetto, or Chromium tracing can show C++, Python, process, and GUI
events on one timeline. Release builds keep only aggregate counters and slow-span
diagnostics unless detailed tracing is explicitly enabled.

### GUI watchdog

Add a Qt event-loop watchdog that records heartbeat gaps without calling
`processEvents()` re-entrantly. Test mode fails the benchmark when the applicable
threshold is crossed. Diagnostics name the active operation and most recent GUI
span so a freeze is never reported only as "not responding."

### Required native spans

- `Document::recompute` and async recompute handoff
- document transaction open, commit, abort, undo, and redo
- document object creation/removal and property application
- `ViewProviderPartExt::updateVisual`
- BREP tessellation and Coin geometry construction
- Tree visibility mutation and folder-status traversal
- Tree stable refresh and model rebuild
- Feature Timeline scheduling and rebuild
- selection updates and 3D-view fit/redraw
- file open, restore, save, and autosave

### Required VibeCAD spans

- provider request/stream/tool dispatch
- context capture and cache lookup
- Analyze and Drawing source discovery
- VibeScript worker startup, import, execution, validation, and artifact I/O
- VibeScript publication per output/member and final stabilization
- Assembly joint construction, solve, collision sweep, and publication
- FEM/CFD mesh generation, deck preparation, solver, parsing, and presentation
- Drawing generation, redraw, screenshot, and publication
- manufacturing, slicer discovery, slicing, post-processing, and preview
- import/export and McMaster catalog/browser operations

### Benchmark evidence

Each benchmark produces a small summary containing:

- commit and build identity;
- hardware and operating-system summary;
- fixture identity and size;
- cold and warm results;
- wall time, CPU time, peak memory, GUI heartbeat percentiles, longest GUI span,
  cache behavior, and cancellation latency;
- correctness result and output fingerprint.

Do not commit customer documents or machine-specific paths. Add deterministic
synthetic fixtures for CI and use a separately configured local private benchmark
set for representative large documents.

## Benchmark matrix

| ID | Scenario | Required scale | Primary proof |
|---|---|---:|---|
| B01 | Cold application launch | packaged build | first usable input, heartbeat, memory |
| B02 | Open and restore document | 2,000+ objects | GUI gaps, restore spans, visual readiness |
| B03 | Save and autosave | 2,000+ objects | GUI gaps, bytes, atomic-save behavior |
| B04 | Workbench/ribbon switch | every VibeCAD surface | activation p95 and maximum |
| B05 | Tree expand/filter/selection | 2,000+ objects | handler time and traversal count |
| B06 | Bulk visibility | 150+ joints and 2,000+ mixed objects | one refresh/invalidation cycle |
| B07 | VibeScript publication | 300+ solids/members | per-item cost, tessellation, final tail |
| B08 | Assembly construction | 150+ joints | CPU utilization, progress, cancellation |
| B09 | Assembly drag/edit | 150+ joints | no visibility storm, interactive latency |
| B10 | Motion/collision sweep | 150+ joints, 100+ frames | parallel efficiency and progress ETA |
| B11 | Analyze context capture | 2,000+ objects, mostly hidden | hidden-object pruning and cache reuse |
| B12 | FEM mesh/deck/solve | 100k+ nodes | background execution and stage progress |
| B13 | CFD preparation/solve | representative multi-body model | background execution and cancellation |
| B14 | Drawing source discovery | 2,000+ objects, mostly hidden | pruning, cache reuse, heartbeat |
| B15 | Drawing generation/redraw | multi-page drawing | background preparation and UI apply |
| B16 | Screenshot/render export | multiple pages/views | no GUI wait and exact requested output |
| B17 | Sketch edit/recompute | constrained production sketch | interaction and dependent update latency |
| B18 | Part/PartDesign operations | complex bodies/history | worker/recompute use and GUI gaps |
| B19 | Mesh operations | large STL/mesh | CPU scaling, memory, cancellation |
| B20 | Import | STEP, STL, and Fusion archives | deduplication, progress, visual readiness |
| B21 | Export | STEP, STL, drawing, and manufacturing outputs | no GUI I/O wait |
| B22 | Manufacturing/3D printing | production mesh and slicer | no console, background work, progress |
| B23 | McMaster catalog/browser | cold and authenticated warm session | launch/search/import latency |
| B24 | Assistant conversation | long conversation plus CAD tools | stream latency and responsive controls |
| B25 | Preferences/start/update UI | cold and warm | interaction latency and no blocking I/O |
| B26 | Cancel/close/undo stress | every long-running class | integrity, no deadlock, bounded response |
| B27 | Multi-document load | three active documents/jobs | fairness, memory, document isolation |

## Work plan and ledger

Status values are `DONE`, `IN PROGRESS`, `NOT STARTED`, `BLOCKED`, and
`REVALIDATE`. `DONE` requires linked evidence in the final column.

| ID | Work item | Status | Completion evidence |
|---|---|---|---|
| P00 | Establish cooperative mutation lease and block conflicting document mutations | DONE | PR #168; native GUI tests 138/138 |
| P01 | Yield VibeScript publication between output/member apply steps | DONE | PR #168; Python suite 4,380 passed, 9 skipped; large Assembly became interactively usable |
| P02 | Add publication phase/item progress | DONE | PR #168; direct VibeScript progress callback tests |
| P03 | Instrument cross-language spans and GUI watchdog | NOT STARTED | Required before optimization claims |
| P04 | Capture cold/warm baseline for B02, B06, B07, B09, and B24 | NOT STARTED | Trace and benchmark summaries |
| P05 | Remove Assembly drag/bulk-visibility refresh storm | NOT STARTED | Red test, traversal count of one, B06/B09 pass |
| P06 | Measure BREP assignment, tessellation, and Coin construction independently | NOT STARTED | Native trace identifies per-solid costs |
| P07 | Pre-tessellate eligible solids in isolated workers and cache render meshes | NOT STARTED | Same visual/correct BREP output; B07 comparison |
| P08 | Drive ordinary per-object GUI apply cost toward <= 1 ms | NOT STARTED | B07 per-item distribution and longest span |
| P09 | Add adaptive batching for proven-cheap apply units | NOT STARTED | Higher throughput without heartbeat regression |
| P10 | Bound publication commit/rollback/final-stabilization phases | NOT STARTED | No unnamed GUI span > 16 ms in B07/B26 |
| P11 | Coalesce Tree, Timeline, cache, and observer notifications | NOT STARTED | One logical refresh per operation |
| P12 | Eliminate full-object stable refresh when exact changed identities exist | NOT STARTED | B05/B07 traversal and allocation evidence |
| P13 | Measure and optimize Tree projection and Feature Timeline rebuild | NOT STARTED | B05 and B07 final-tail evidence |
| P14 | Implement persistent startup worker runtime and bounded queues | NOT STARTED | Startup, reuse, fairness, shutdown, and crash tests |
| P15 | Implement per-document actor ownership and revision-safe result application | NOT STARTED | B26/B27 stress evidence |
| P16 | Standardize human/AI progress, cancellation, and diagnostics contract | REVALIDATE | VibeScript direct progress exists; audit every other long operation |
| P17 | Audit and eliminate synchronous filesystem/network/process waits in UI callbacks | NOT STARTED | Static audit plus runtime trace shows zero occurrences |
| P18 | Validate and optimize application startup and document restore | NOT STARTED | B01/B02 cold/warm evidence |
| P19 | Validate and optimize Analyze/FEM/CFD workflows | NOT STARTED | B11-B13 evidence |
| P20 | Validate and optimize Drawing/TechDraw workflows | NOT STARTED | B14-B16 evidence |
| P21 | Validate and optimize Assembly edit, solve, motion, and collision workflows | NOT STARTED | B08-B10 evidence |
| P22 | Validate and optimize Sketcher, Part, PartDesign, Mesh, and import/export | NOT STARTED | B17-B21 evidence |
| P23 | Validate and optimize manufacturing, 3D printing, and external tools | NOT STARTED | B22 evidence |
| P24 | Validate and optimize McMaster and embedded browser behavior | NOT STARTED | B23 evidence |
| P25 | Validate and optimize assistant, conversation, context, and persistence UI | NOT STARTED | B24 evidence |
| P26 | Validate preferences, start page, updates, ribbon, and workbench switching | NOT STARTED | B04/B25 evidence |
| P27 | Run multi-document, cancellation, rollback, autosave, undo, close, and shutdown stress | NOT STARTED | B26/B27 evidence with no corruption/deadlock |
| P28 | Run full unit/integration/native suites and actual Windows portable packaging | NOT STARTED | Exact commands and results recorded below |
| P29 | Run packaged interactive acceptance across the entire benchmark matrix | NOT STARTED | Signed-off result for B01-B27 |

## Immediate implementation sequence

### Phase 1: measurement foundation

1. Add failing tests for the GUI watchdog and nested performance spans.
2. Add C++ RAII and Python context-manager spans with the same trace schema.
3. Instrument the confirmed publication, tessellation, Tree, Timeline, and bulk
   visibility paths.
4. Capture cold and warm baselines before changing those paths.
5. Record the results in this document.

### Phase 2: confirmed event storms

1. Add a failing test proving bulk visibility currently triggers repeated folder
   refreshes.
2. Add an additive batch/coalescing scope used by Assembly drag and general bulk
   visibility commands.
3. Emit one browser refresh, one cache invalidation, and one selection update at
   the end of the logical command.
4. Verify rollback and single-object behavior remain compatible.
5. Re-run B05, B06, and B09 and record the before/after result.

### Phase 3: rendering and tessellation

1. Separate timings for BREP load, `Shape` assignment, view-provider attachment,
   OpenCASCADE tessellation, and Coin node construction.
2. If tessellation dominates, prototype worker-side meshing with the exact active
   deviation/angular-deflection settings.
3. Determine the safest transport after measurement: BREP with retained
   triangulation or a companion indexed render mesh keyed by shape/settings
   fingerprints.
4. Preserve authoritative BREP, face/edge selection mapping, colors, normals,
   transparency, and saved-document behavior.
5. Prove that cached worker output prevents host re-tessellation and produces the
   same visible and selectable model.
6. Re-run B02 and B07 cold and warm.

### Phase 4: publication and projections

1. Reduce each GUI apply unit below the responsiveness contract.
2. Add adaptive batching only after individual unit cost is bounded.
3. Carry exact changed-object identities through commit and rollback.
4. Remove whole-document Tree invalidation when exact identities are available.
5. Coalesce Feature Timeline and other projection refreshes.
6. Move only pointer-free computation off-thread; final Qt model application
   remains on the GUI thread.
7. Ensure progress is painted before any measured atomic phase and remains
   truthful through stabilization.

### Phase 5: persistent runtime

1. Measure current per-operation process startup/import overhead.
2. Add the startup-owned runtime without removing existing entry points.
3. Route new work through persistent pools while preserving a compatible legacy
   adapter during rollout.
4. Add per-document serialization, global back-pressure, CPU budgeting, crash
   replacement, clean shutdown, and stale-result rejection.
5. Stress multiple documents and simultaneous human/AI requests.

### Phase 6: interface-wide audit

For each P18-P26 area:

1. Capture an interaction trace.
2. Add a regression benchmark for every slow or blocking path.
3. Move computation/I/O to the correct executor.
4. remove redundant work and add revision-aware caching;
5. coalesce resulting UI changes;
6. verify progress and cancellation;
7. record cold/warm before-and-after evidence.

The audit is complete only after every action reachable from VibeCAD's ribbons,
menus, assistant tools, document lifecycle, and primary workbenches has either a
passing trace or an explicit benchmark entry.

### Phase 7: release proof

1. Run all Python, C++, GUI, provider, and packaging tests applicable to the
   touched code.
2. Produce the actual Windows portable package through the repository's Rattler
   workflow; do not substitute an installed development tree.
3. Run B01-B27 against that package.
4. Verify document fingerprints and expected outputs against the baseline.
5. Record exact commands, durations, test counts, artifact identity, and known
   platform limitations below.
6. Do not mark the performance program complete while any row remains
   `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, or `REVALIDATE`.

## Test and build record

Append evidence; do not overwrite prior measurements. Each record must include
the commit under test.

### 2026-09-04 — PR #168 foundation

- Commit: `fcf47b46cd7c6dd5cdf80094196923ec0a30f8bc`
- Merge commit: `7289751459ceace471ac771f3a0ec39b972d1949`
- Python tests: 4,380 passed, 9 skipped.
- Focused cooperative publication tests: 13 passed.
- Native GUI tests: 138 passed out of 138.
- Windows Rattler build: completed successfully; package
  `vibecad-26.3.1RC6-h3c70cbc_1.conda`.
- Interactive result: large Assembly publication became substantially more
  usable, but measurable freezes remained during final publication and bulk
  visibility. This is foundation evidence, not completion evidence.

## PR discipline

This program may require multiple small PRs, but they all track this one plan.
Every code PR must:

1. solve one measured bottleneck or add one coherent piece of proof
   infrastructure;
2. include the failing test/benchmark observed before implementation;
3. preserve public APIs and existing behavior unless the owner explicitly
   approves a break;
4. report exact commands and before/after results;
5. update the applicable ledger rows and test record in this document;
6. avoid unrelated cleanup, generated noise, local paths, credentials, and test
   documents.

## Completion definition

The performance program is complete only when all of the following are true:

- Every P00-P29 ledger row is `DONE`.
- Every B01-B27 scenario has cold and warm evidence from a packaged build.
- No benchmark contains an unexplained GUI heartbeat failure.
- No expensive UI callback performs synchronous I/O, process waiting, repeated
  whole-document traversal, or unbounded geometry work.
- Human and AI paths use the same background execution, progress, cancellation,
  caching, and document-integrity contracts.
- Large-document stress tests show no deadlock, corruption, stale apply, lost
  rollback, or unsafe cross-thread object access.
- The repository's full required test and packaging gates pass.
- The remaining measured latency is attributable only to bounded UI application
  or external operations that are isolated, cancellable where possible, and
  truthfully visible to the user.

