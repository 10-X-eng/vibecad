# Native Tool Sharpening Results

This is the chronological evidence log for the unchanged cases in
`native-tool-sharpening-benchmark.md`. It records model behavior before deciding
what to change. A rejected call remains a failure even when the model recovers.

Local rollout files and CAD artifacts are retained outside the repository. The
identifiers and artifact names below are sufficient to find them in the benchmark
evidence directory without committing machine-specific paths.

## Qwen annular-plate transition case

Prompt, unchanged between the before/after runs:

> Create one 12 mm thick annular plate centered at the origin with an 80 mm
> outside diameter and a 32 mm through-hole. Keep one valid solid Body.

### Run QAP-01 — 2026-08-17

Configuration:

- Provider path: Codex app-server using Ollama's OpenAI-compatible Responses API.
- Model: `qwen3.5:9b`, high reasoning, 65,536-token context.
- CAD artifact: `qwen-sketch-annular-plate-run-1.FCStd`.
- Initial Modeling rollout: `01a01261-7e22-73b1-8131-fe9418bf1d98`.
- Initial Sketching rollout: `01a01263-d601-7b63-afee-b3c1e69f71d8`.
- First Modeling continuation: `01a01267-e1e6-7b80-82b5-3318b531242a`.

Accepted first attempts:

1. `model.sketch` created and opened the XY-plane Sketch.
2. `sketch.draw_circle` created the 40 mm outside radius.
3. `sketch.draw_circle` created the 16 mm hole radius.
4. `sketch.finish` returned to Modeling.

Verified artifact:

- The saved partial FCStd contains exactly one Sketch with two concentric Circles
  of radii 40 and 16 mm.
- The Sketch bounds are 80 x 80 mm. No duplicate Sketch, accidental feature, or
  solid is present in the persisted document.
- The Sketch calls had no rejected payloads and needed no inspection round trip.

Failure after the Sketch transition:

- Every return to Modeling started a new managed Codex thread even though the
  published Modeling schemas had the identical
  `2e52d9a89354b22154a3459991df894f8dadb49cf42c1743a320bdc0c985c5d7`
  hash.
- Without its earlier Modeling history, Qwen repeatedly reopened the complete
  Sketch, then tried malformed generic `model.feature` and `model.part` payloads
  instead of the exact `model.extrude` tool.
- The run reached its 1,500-second allowance with no solid. Cancellation saved
  the partial FCStd, closed the document on the GUI thread, and exited without
  the former teardown segmentation fault.

Classification:

- Sketch tool selection, contracts, execution, and compact results: **pass**.
- Runner timeout and partial-artifact handling: **pass**.
- Cross-work continuity: **fail**.
- Root platform defect: the managed Codex thread identity included Native's
  volatile `surface_id`. That identifier contains the ribbon revision, so every
  successful Sketch open or close changed the thread key even when the immutable
  tool schema was unchanged.

Correction under comparison:

- Managed thread identity now uses the conversation, engine, and immutable
  schema hash. Workbench presentation and volatile surface revision no longer
  discard history.
- Tool authorization remains frozen and revalidated per turn. Different schema
  hashes still receive different threads, so this does not permit in-turn tool
  swapping.
- QAP-02 reruns the exact prompt without steering or forced tool selection.

### Run QAP-02 — 2026-08-17

Configuration was unchanged from QAP-01. The artifact is
`qwen-sketch-annular-plate-run-2.FCStd`.

Thread evidence:

- Initial Modeling thread: `01a0127a-0942-73d2-b23a-5b04226bf27a`.
- Sketching thread: `01a0127b-0507-7cb0-bbf9-ef6e5317fa28`.
- On return to Modeling, the app-server received `thread/resume` for the original
  `01a0127a-0942-73d2-b23a-5b04226bf27a` thread. No replacement Modeling thread
  was created.
- The continuation state named the exact existing `Sketch`, reported two closed
  wires, zero open wires, `valid=true`, and `solid_feature_ready=true`.

Geometry evidence:

- Sketch creation again succeeded on the first attempt with exact radii 40 and
  16 mm.
- The saved partial FCStd again contains only that correct two-circle Sketch and
  the timeline. Its bounds are 80 x 80 mm; no accidental object survived.

Modeling evidence:

- Qwen did not select `model.extrude` after the transition.
- It made 13 rejected calls across the overlapping `model.feature`, `model.part`,
  and `model.structure` contracts. Attempts included an annular `prism`, a Part
  `cylinder`, generic profile extrusion with misplaced profile/result fields,
  duplicate Part circles, and an invalid attempt to separate the correct Sketch.
- Every rejection was atomic. No rejected call changed the document.
- The 1,500-second timeout cancelled the provider, saved the partial document,
  closed the GUI document, and exited without a segmentation fault.

Classification:

- Managed cross-work continuity: **pass**.
- Sketch selection, payloads, runtime geometry, result clarity, and finish
  transition: **pass**.
- Modeling selection: **fail**. The generic `model.feature` description was only
  `Create a Body`, while `model.part` said only `Create standalone Part geometry`.
  Both broad contracts overlap the exact extrusion intent and expose large
  optional-field schemas.
- Final solid/STEP: **incomplete**.

Correction after the immutable run:

- `model.extrude` now says exactly that it extrudes one closed Sketch into a new
  Part Design Body.
- `model.feature` now identifies its advanced Part Design scope and points finite
  Sketch extrusion to `model.extrude`.
- `model.part` now identifies standalone Part/reference geometry and explicitly
  distinguishes it from a Part Design Body.
- These are general contract boundaries; the user prompt and tool-selection
  behavior remain unforced.

### Run QAP-03 — 2026-08-17

Configuration and prompt were unchanged. The saved artifact is
`qwen-sketch-annular-plate-run-5.FCStd`.

Observed behavior on the pre-correction surface:

- Qwen's first modeling choice was the natural annular primitive: it supplied
  `outer_radius_mm=40` and `inner_radius_mm=16` as a `ring`. The generic
  `model.feature` contract rejected those fields because it advertised only its
  unrelated primitive union.
- Qwen moved to Sketching and eventually retained exactly two centered circles,
  radii 40 and 16 mm. The saved Sketch has two closed wires, an 80 x 80 mm
  bound, and no stray objects or constraints.
- Five captured Sketch calls failed: coincidence supplied `selection` where the
  merged schema required `target`; point creation supplied line fields; a point
  used the invalid `whole` position; a standalone point used a non-`start`
  position; and an already geometric coincidence was rejected as redundant.
- The correct profile survived atomically, but no solid was created before the
  allowance expired.

Classification:

- Natural primitive selection: **pass**; published primitive contract: **fail**.
- Final Sketch geometry and cleanup: **pass**; first-attempt Sketch calls:
  **fail**.
- Root Sketch defect: `sketch.constrain` flattened the incompatible coincidence,
  perpendicular, tangent, and symmetry target grammars into one permissive
  object. The field list was technically present but did not give the model one
  obvious call shape.
- Final solid/STEP: **incomplete**.

Corrections under comparison in QAP-04:

- `model.primitive` now owns the nine human Part Design primitives with one
  compact operation field map; `tube` directly expresses an annular solid.
- Coincidence, perpendicular, tangent, and symmetry now have separate focused
  tools with their own exact `target` forms. Selection-based constraints remain
  grouped in `sketch.constrain`.
- The human Point command now has `sketch.draw_point`; it is no longer hidden
  behind the contradictory `sketch.draw_line` name.
- The post-correction real Sketch GUI gate publishes 39 tools in 56,289 bytes
  and executes the focused coincidence route against a real Sketch. The
  complete ribbon-surface GUI gate also passes.

### Run QAP-04 — 2026-08-17

The prompt and 1,500-second allowance were unchanged. The saved partial artifact
is `qwen-sketch-annular-plate-run-6.FCStd`. This immutable process loaded the
38-tool Sketch surface after focused constraints but before `sketch.draw_point`
was separated.

Sketch evidence:

- Qwen created one Sketch and drew exactly two centered circles with radii 40
  and 16 mm. The resulting profile has two closed wires, zero open wires, and an
  80 mm outside bound.
- Every Sketch mutation was accepted. There are no Sketch failure diagnostics
  in the rollout.
- Qwen unnecessarily reopened the already face-buildable Sketch once, made no
  change, and finished it again. Cross-work thread resumption remained stable.

Modeling evidence:

- Qwen did not use `model.extrude`. It eventually created exact 80 x 80 x 12 mm
  and 32 x 32 x 12 mm cylinder Bodies, then failed to subtract the inner Body.
- The artifact contains the correct Sketch and two valid solid Bodies, not the
  requested single annular Body.
- Nine Modeling calls were rejected: the wrong inspection subtype, a Part-circle
  payload, `cylinder` with two-radius fields, and six attempts across incompatible
  boolean forms or feature-versus-Body targets.
- The timeout cancelled the provider, saved the partial document, closed the GUI
  document, and exited without a teardown crash.

Classification:

- Focused Sketch contracts and runtime: **pass for this case**.
- Sketch-to-Model transition continuity: **pass**; unnecessary transition count:
  **one**.
- Modeling primitive and boolean selection/contracts: **fail**.
- Final solid/STEP: **incomplete**.

The earliest remaining failures are now on the Modeling surface. This run does
not establish full Sketcher completion by itself; the unrelated rounded-profile
case must still pass on the final 39-tool Sketch surface before larger-model and
manual sign-off.

## Qwen rounded-plate development case

Prompt, unchanged in every run:

> Create one 10 mm thick plate from a 140 mm by 96 mm rounded rectangle centered
> at the origin with 18 mm corner radii. Add one axis-aligned elliptical
> through-hole at the origin with a 30 mm major radius and a 20 mm minor radius.
> Keep one valid solid Body.

This development case exercises Modeling to Sketching to Modeling before the
ten-case corpus. It is not a substitute for a corpus result.

### Run QRP-06 — 2026-08-17

The prompt, model, context window, and 1,500-second allowance were unchanged.
The saved partial artifact is `qwen-sketch-rounded-ellipse-run-6.FCStd`. The only
contract difference from QRP-05 was that a frozen single-purpose tool could omit
its redundant operation discriminator.

Verified CAD progress:

- Qwen again produced the exact 140 x 96 mm rounded profile, four 18 mm corner
  arcs, and centered 30 x 20 mm Ellipse without a rejected Sketch call.
- `model.extrude` was accepted without `operation="extrude"` and created one
  valid solid Body with 140 x 96 x 10 mm bounds, 11 faces, and volume
  112,769.204276 mm3.
- The extrusion consumed both closed profile wires. The Ellipse is already a
  through-hole in that solid; the measured volume matches the rounded-rectangle
  area minus the elliptical area, multiplied by 10 mm.
- This is the first unchanged QRP run to create the complete requested solid.
  The previously observed redundant-operation failure is therefore corrected in
  a real Qwen workflow, not only by a dispatcher test.

Remaining behavior after completion:

- Qwen did not stop after the complete solid. It attempted to create another
  face-supported hole Sketch, first using `Faces@1`. `model.sketch` correctly
  requires FreeCAD's `Face1` spelling and rejected that call atomically.
- The diagnostic's `valid_example` returned `subelement="value"`, which does not
  satisfy its own `^Face[1-9][0-9]*$` schema. This is a platform diagnostic bug.
- Qwen recovered and created `Sketch001` on a face with the same 30 x 20 mm
  Ellipse. The allowance expired before that redundant second profile was used or
  removed. The extra active profile left the otherwise correct Body hidden, so
  the runner did not export STEP.
- No production string or model-state field contains the `Faces@1` spelling;
  that value was model-generated rather than copied from VibeCAD context.

Classification:

- Final Sketch tool selection, calls, and exact geometry: **pass**.
- Single-purpose operation inference and exact extrusion: **pass**.
- Requested solid geometry: **pass**, persisted in the FCStd.
- Model stopping behavior and Modeling result clarity: **fail**; the model did
  not recognize that a two-wire profile extrusion had already created the hole.
- Structured diagnostic example: **fail**; regex-constrained string examples
  must satisfy their published pattern.
- Timeout cleanup: **pass**; the partial artifact was saved and VibeCAD exited
  without hanging or crashing.

### Run QRP-05 — 2026-08-17

The prompt and 1,500-second allowance were unchanged. The saved partial artifact
is `qwen-sketch-rounded-ellipse-run-5.FCStd`. This run loaded the final focused
Sketch surface: 39 tools, including separate Point, Coincident, Perpendicular,
Tangent, and Symmetric tools.

Verified Sketch evidence:

- Qwen selected the existing dedicated rounded-rectangle and ellipse tools. No
  Sketch call was rejected.
- The persisted Sketch contains the exact 140 x 96 mm rounded rectangle with
  four 18 mm corner arcs and the centered, axis-aligned 30 x 20 mm ellipse.
- Its real OpenCASCADE shape has two closed wires, nine edges, zero open wires,
  140 x 96 mm bounds, and is valid. The document contains no duplicate Sketch or
  accidental solid.
- The Sketch has 15 total geometry records because FreeCAD retains six generated
  construction helpers. Its nine public profile edges are the intended eight-edge
  rounded rectangle and one Ellipse.
- Sketch finish returned to Modeling with `valid=true`, two closed wires, zero
  open wires, and `solid_feature_ready=true`. The original Modeling thread was
  resumed.

Rejected Modeling calls after the clean transition:

1. `model.part` received a regular-polygon definition with a `plane` field. The
   broad standalone-Part contract rejected that unrelated payload.
2. `model.surface` received `boundaries`, `closed`, and `style`, none of which
   belong to its advertised operation.
3. Qwen then selected the exact `model.extrude` tool and supplied its natural
   profile and length fields, but omitted `operation="extrude"`. The dispatcher
   rejected the call even though the tool name already uniquely identifies that
   operation.

Classification:

- Sketch tool selection, payloads, execution, geometry, compact results, and
  work transition: **pass** for this second, topologically different case.
- Final solid/STEP: **incomplete**. The fixed allowance expired in Modeling.
- Earliest directly observed platform defect: **redundant single-purpose tool
  discriminator**. Requiring `operation="extrude"` after selecting
  `model.extrude` adds no information and turned the correct tool choice into a
  rejected call.
- Timeout handling: **pass**. Cancellation saved the partial document, closed it
  on the GUI thread, and exited without a hang or segmentation fault.

Correction under comparison:

- A frozen tool that exposes exactly one operation may omit the redundant
  `operation` argument. The dispatcher infers only that frozen operation before
  exact validation and execution. Explicit operation values remain accepted;
  multi-operation tools still require an explicit discriminator.

### Run QRP-04 — 2026-08-17

Configuration:

- VibeCAD base: `1be8b67494ce1c6c579b89be0d568718c68830dd` plus the uncommitted
  `feature/native-tool-sharpening` worktree.
- Provider path: Codex app-server using Ollama's OpenAI-compatible Responses API.
- Model: `qwen3.5:9b`, high reasoning, 65,536-token context.
- CAD artifact: `qwen-sketch-rounded-ellipse-run-4.FCStd`.
- Modeling rollout: `01a01245-88d9-7bf1-a61b-fe3f47b7b8c4`.
- Sketching rollout: `01a01248-e99a-7e60-b6de-362d1e9a2442`.
- First Modeling continuation: `01a0124e-4568-77d2-abd7-8b1ea06ee586`.
- Inspection-only Sketching continuation: `01a0124f-a17a-71f0-9429-9318cef8a7e2`.
- Second Modeling continuation: `01a01256-ac0c-7ea3-a575-e0e613008650`.
- Duplicate Sketching continuation: `01a01258-7652-7ec0-8836-80cdb793483f`.

Accepted first attempts:

1. `model.sketch` created the reusable XY-plane Sketch.
2. `sketch.draw_rounded_rectangle` created the requested 140 x 96 mm outer
   loop with 18 mm corner radii.
3. `sketch.draw_ellipse` created the requested centered 30 x 20 mm ellipse.
4. `sketch.finish` returned to Modeling.

Verified sharpened Sketch results:

- The rounded Rectangle result contained its eight public boundary identities,
  profile readiness, solver state, counts, and revision. It omitted 19 generated
  constraint records, two construction helpers, and echoed dimensions.
- The Ellipse result contained one public Ellipse identity, profile readiness,
  solver state, counts, and revision. It omitted four internal geometries and
  four internal constraints.
- Both calls were accepted on their first attempts and produced two closed,
  face-buildable wires with zero open wires.

Continuation behavior:

- Modeling received the original request, the exact existing Sketch, two closed
  wires, `valid=true`, and `solid_feature_ready=true`, plus the explicit event
  text: `Continue the current design from its existing document state. Do not
  repeat completed operations.`
- `model.extrude` was present with the exact finite-length contract.
- Qwen nevertheless reopened the complete Sketch, called `state.read`, made no
  mutation, and left again.
- On the next Modeling continuation, Qwen created and opened `Sketch001` instead
  of extruding `Sketch`.

Classification:

- Sketch tool selection, calls, runtime geometry, and compact results: **pass**.
- CAD-work transition context: **pass**; the frozen state and continuation event
  were complete and truthful.
- Cross-work continuity: **fail**; each return to Modeling used a new managed
  thread because the thread key included the changed ribbon revision. The model
  therefore saw current state but lost its earlier Modeling reasoning and tool
  history.
- Final artifact: **incomplete**; no solid Body or STEP export was produced before
  the fixed 1,500-second allowance expired. The then-current runner only saved on
  success, so the retained FCStd contains the initial document rather than the
  in-memory Sketch; the immutable tool rollouts are the geometry evidence for this
  run.
- Benchmark infrastructure: **fail**; exiting while the timed-out provider worker
  was still active caused a Qt/Python-console teardown segmentation fault.

Runner correction after the immutable run:

- Timeout now requests provider cancellation and waits for the provider worker.
- The document is closed on the GUI thread before application exit.
- Partial documents are recomputed and saved before failure teardown.
- A forced three-second timeout reproduced the cancellation path and exited with
  the expected `TimeoutError` without a segmentation fault.

No tool-selection heuristic or case-specific prompt change is justified by this
run. The same-thread correction is tested with an unchanged prompt before any
larger-model comparison.

### Run QRP-03 — 2026-08-17

Configuration:

- VibeCAD base: `1be8b67494ce1c6c579b89be0d568718c68830dd` plus the uncommitted
  `feature/native-tool-sharpening` worktree.
- Provider path: Codex app-server using Ollama's OpenAI-compatible Responses API.
- Model: `qwen3.5:9b`, high reasoning, 65,536-token context.
- CAD artifact: `qwen-sketch-rounded-ellipse-run-3.FCStd`.
- Modeling rollout: `01a0121b-e3bf-7f31-a6eb-19565b89cdb3`.
- Sketching rollout: `01a0121f-0ffe-71f2-9349-d04238883895`.
- Modeling continuation rollout: `01a01223-d0d3-7513-9f6c-063bc0f103f0`.

Accepted first attempts:

1. `model.sketch` created the reusable XY-plane Sketch.
2. `sketch.open` opened that exact Sketch.
3. `sketch.draw_rounded_rectangle` created the 140 x 96 mm outer loop with
   18 mm corner radii.
4. `sketch.draw_ellipse` created the centered 30 x 20 mm axis-aligned ellipse.
5. `sketch.finish` closed edit mode and requested Modeling.

Verified transition state:

- The Modeling continuation reported one valid Sketch with two closed wires,
  zero open wires, nine profile edges, and `solid_feature_ready=true`.
- Qwen selected extrusion next. It did not reopen the completed Sketch. This is
  the first unchanged run that clears the prior repeated-Sketch transition defect.

Rejected Modeling calls observed after that transition:

1. `model.feature`: put `height_mm` and `profile` inside the generic feature
   definition and encoded `result` as a string.
2. `model.feature`: retried `height_mm` inside the definition.
3. `model.feature`: followed the returned incomplete example but used `Wire` as
   a region identifier and still encoded `result` as a string.
4. `model.feature`: retried region `1`, which also violated the undocumented
   `InternalFaceN` identity contract.
5. `model.part`: attempted the alternate extrusion tool with a placement field
   that its contract does not accept.
6. `model.feature`: retried region `1` and encoded `result` as a string.
7. `model.feature`: selected `InternalFace1` but still encoded `result` as a string.

Classification:

- Sketch creation and work transition: **pass** for this case.
- Final artifact: **incomplete**; no solid Body was produced.
- The fixed 1,200-second allowance expired, followed by a shutdown segmentation
  fault while the provider worker was still active.
- Earliest remaining platform defect: **Modeling schema**.
- Evidence: the generic profile tool combines extrusion, revolution, loft,
  sweep, and helix; it buries extrusion distance under a multi-level extent
  structure; its generated valid example omits the required extent and emits an
  invalid placeholder for a regex-constrained region. The model's repeated
  `height_mm` payloads also show that the published contract does not match the
  natural CAD expression of a 10 mm extrusion.

Next unchanged-run requirement:

- Give extrusion an exact, natural contract and a fully valid diagnostic example.
- Rerun this exact prompt. Acceptance requires the first extrusion attempt to be
  valid, one visible solid Body, the requested 140 x 96 x 10 mm outer bounds,
  the elliptical through-hole, a saved FCStd artifact, and a non-empty STEP export.

### Run QRP-02 — 2026-08-17

- CAD artifact: `qwen-sketch-rounded-ellipse-run-2.FCStd`.
- Rounded rectangle and ellipse were accepted on their first attempts.
- `sketch.finish` successfully returned to Modeling.
- Modeling did not know whether the finished Sketch was usable, so Qwen reopened
  and inspected it, finished it again, returned to Modeling, and reopened it again.
- Classification: **false state plus transition**. Modeling published counts and
  attachment mode but omitted exact profile validity and solid-feature readiness.

### Run QRP-01 — 2026-08-17

- CAD artifact: `qwen-sketch-rounded-ellipse-run-1.FCStd`.
- The dedicated rounded-rectangle tool was selected correctly.
- The combined ellipse contract led Qwen to mix center-ellipse and
  three-point-ellipse fields. It recovered after one rejected call.
- `sketch.finish` succeeded, but the host did not recognize the provider-facing
  finish name as a Modeling transition.
- Classification: **Sketching schema plus transition**.

## Qwen Modeling annular-plate case

Prompt, unchanged between runs:

> Create one 12 mm thick annular plate centered at the origin with an 80 mm
> outside diameter and a 32 mm through-hole. Keep one valid solid Body.

### Run QMP-01 — 2026-08-17

The retained artifacts are `qwen-model-annular-plate-run-1.FCStd`, `.step`, and
`.png`.

- Qwen selected `model.primitive` with `operation="tube"` on its first call.
- The resulting Body is one valid solid with exact outer radius 40 mm, inner
  radius 16 mm, height 12 mm, and volume 50,667.606317 mm3.
- Four mass-property calls incorrectly used singular `target` where the exact
  operation accepted only a `targets` array. Qwen eventually used the published
  list form, but execution then failed because the Design Body's solid-bearing
  Compound has no direct `CenterOfMass` attribute.
- Qwen completed from the mutation result and a fitted view, but five of seven
  total calls were rejected.

Correction under comparison:

- Mass properties accepts the natural singular `target` while retaining the
  existing bounded `targets` list.
- Solid-bearing Compounds compute their exact volume center from the contained
  solids instead of assuming a direct shape attribute.

### Run QMP-02 — 2026-08-17

The prompt and Qwen configuration were unchanged. The partial artifact is
`qwen-model-annular-plate-run-2.FCStd`.

- This sample did not recognize the unexplained `tube` enum value. It created an
  outer Cylinder Body and a second inner Cylinder Body instead.
- Five Boolean attempts followed. Two payloads mixed the section, combine, and
  split contracts; two valid combine-cut calls failed at runtime with `Design
  Combine preview differs from its result Body`; one valid section call failed
  while copying line material.
- Qwen then created an unused Sketch and made two invalid Hole calls with typed
  objects encoded as strings. The fixed 1,500-second allowance expired after 10
  calls, eight rejected calls, two Cylinder Bodies, and no annular result.
- Cancellation saved the partial FCStd, closed VibeCAD, and exited without a
  crash or hang.

Classification:

- `model.primitive` payload correctness when selected: **pass**.
- Direct primitive selection stability: **fail**. The provider named `tube` but
  never stated that it is the hollow-cylinder/annular-plate primitive.
- Boolean schema comprehension: **fail after the wrong primitive choice**.
- Valid combine-cut runtime: **fail**, retained as the next independent Modeling
  defect.
- Mass-property correction: not exercised because this run never produced the
  requested result.

Correction under comparison in QMP-03:

- `model.primitive` now states, in one sentence, that `tube` is the direct Body
  primitive for a hollow cylinder or annular plate. No prompt or orchestration
  change is made.

### Run QMP-03 — 2026-08-18

The prompt and Qwen configuration were unchanged. The retained artifacts are
`qwen-model-annular-plate-run-3.FCStd`, `.step`, and `.png`.

- Qwen selected `model.primitive` with `operation="tube"` immediately.
- The mutation succeeded on its first attempt and produced the exact one-solid
  Body: outer radius 40 mm, inner radius 16 mm, height 12 mm, and volume
  50,667.606317 mm3.
- Qwen saved the document and stopped. The complete run used two accepted calls,
  zero rejected calls, no CAD-work transition, and no corrective steering.
- Compared with QMP-02, the single accurate sentence removed two unnecessary
  Bodies, eight rejected calls, a 1,500-second timeout, and an unfinished result.

Classification:

- Direct Body primitive selection, contract, mutation, result, and completion:
  **pass**.
- The direct primitive wording correction is accepted as a measured Modeling
  improvement.
- The valid combine-cut runtime failure observed in QMP-02 remains open and is
  tested with a case that genuinely requires a Boolean rather than by forcing a
  Boolean into this prompt.

## Qwen Modeling centered-Boolean case

Prompt, unchanged between runs:

> Create one 60 x 60 x 12 mm square plate centered at the origin. Build a
> 20 mm diameter vertical cutting cylinder through its center as a separate
> Body, subtract it from the plate, and keep only the resulting solid Body.

The deterministic result is one Body with bounds `[-30,30] x [-30,30] x
[-6,6]` mm and volume 39,430.088815692245 mm3. The strict runner also requires
zero rejected calls; a plausible screenshot or one visible Body is not enough.

### Runs QMB-01 through QMB-04 — 2026-08-18

- QMB-01 created an origin-based Box, so the cut removed only one corner of the
  plate. The visual and measured result disproved the model's completion claim.
- QMB-02 gained explicit placement and produced the exact solid after one
  feature-versus-Body Boolean target error.
- QMB-03 exposed a false transform description: the tool said it could move a
  Body although its implemented operations were scale and pattern. Qwen
  recovered, but retained two visible Bodies.
- QMB-04 made placement mandatory and exposed another contract failure: the
  compact primitive union did not make the selected kind's dimensions required.
  Qwen eventually made an origin-based plate and a quarter-hole. The old runner
  falsely accepted it because it checked only the count of visible Bodies.

Corrections established by these immutable runs:

- Body mutation results now report exact bounds.
- Transform describes only the operations it implements.
- The live runner verifies exact final volume and optimal bounds and can require
  zero failed calls.
- Primitive placement is expressed as a geometric center, independent of the
  host primitive's local origin convention.
- Boolean combine now calls the kept input `source_body`, matching its actual
  behavior.

### Run QMB-05 — 2026-08-18

- Center-based placement produced the exact final volume and bounds.
- The shared primitive shape object still exposed dimensions belonging to other
  kinds. Qwen first supplied wedge coordinates for a Box, then tube radii for a
  Cylinder.
- Boolean's nested definition was once encoded as a string. Qwen recovered and
  completed the correct solid, but the strict oracle rejected the run for three
  failed calls.

### Run QMB-06 — 2026-08-18

- Replacing the shared shape object with a nested exact `oneOf` removed the
  cross-kind field ambiguity but introduced a more basic model-facing problem.
- Qwen first sent `shape="box"`, then encoded the complete shape object as a JSON
  string. Both calls were rejected before mutation. It correctly moved to a
  Sketch fallback, and the run was stopped at that transition because the
  primitive contract had already failed the zero-error criterion.

Correction introduced for QMB-07:

- Primitive kind is now the top-level operation, beside its dimensions and
  `center_mm`; there is no nested shape value to serialize.
- The operation field states each kind's exact required and optional fields.
  Dispatch revalidates the selected operation against its original closed
  schema before mutation.
- The complete live ribbon surface remains below the unchanged 64 KiB Model
  budget and publishes all 533 human actions.

### Runs QMB-07 and QMB-08 — 2026-08-18

- QMB-07 selected the new flat Box operation correctly on its first attempt and
  created exact centered bounds. The process was stopped before its second call
  while separating that rollout from a delayed QMB-06 continuation, so it is an
  operationally aborted sample rather than acceptance evidence.
- QMB-08 again created the exact centered Box and a centered, vertical 10 mm
  radius cutting Cylinder on their first calls.
- Its first Boolean used the correct Cylinder Body but supplied the Box feature
  as `source_body`. The primitive result exposed the created Body inside a
  one-item `bodies` collection while separately calling the parametric feature
  `operation`; that name made the wrong identity look actionable.
- The target-type diagnostic named `PartDesign::Body`, Qwen corrected to `Body`,
  and the Boolean produced the exact requested volume and bounds. The strict
  zero-failure oracle correctly rejected the otherwise accurate result.

Correction under comparison in QMB-09:

- Focused primitive results now return one top-level `body` identity and one
  separately named `feature`, followed by the Body's bounds, volume, and solid
  count. The misleading one-item collection and `operation` feature name are no
  longer exposed by `model.primitive`.

### Run QMB-09 — 2026-08-18

- This independent Qwen sample copied the nested `definition` pattern used by
  broader neighboring Modeling tools into `model.primitive`. The flat schema
  rejected it and supplied an exact valid example.
- The corrected retry reached the first draft of the focused result adapter.
  That adapter incorrectly returned an `ok` field even though Native dispatch
  owns the success envelope, so dispatch rejected the handler result contract.
- The sample was stopped because its zero-failure criterion was already lost.
  The adapter error was removed before another run.

General correction under comparison in QMB-10:

- The two most common human primitives now have sole-purpose `model.box` and
  `model.cylinder` tools. Their closed flat schemas contain only dimensions,
  center, optional rotation, and label; neither accepts a `definition` object.
- The remaining sphere, cone, ellipsoid, torus, prism, wedge, and tube actions
  remain in `model.primitive`.
- Shared required/optional fields are no longer repeated inside generated
  operation descriptions. This preserved the unchanged 64 KiB Model surface
  ceiling while publishing exactly 28 tools and all 533 human actions.

### Run QMB-10 — 2026-08-18

The retained artifacts are `qwen-model-boolean-plate-run-10.FCStd`, `.step`, and
`.png`.

- Qwen selected `model.box`, `model.cylinder`, and `model.boolean` directly.
- All three calls succeeded on their first attempt. There were zero rejected
  calls, no Sketch transition, no inspection detour, and no corrective prompt.
- The Box result reported exact bounds `[-30,30] x [-30,30] x [-6,6]` mm and
  volume 43,200 mm3. The Cylinder had exact radius 10 mm and centered bounds
  `[-10,10] x [-10,10] x [-12,12]` mm.
- Boolean used `source_body=Body`, `tool_bodies=[Body001]`, and
  `keep_tools=false` on its first call.
- The strict final oracle verified one visible solid Body, exact volume
  39,430.088815692245 mm3, exact centered bounds, a valid STEP export, and the
  saved visual through-hole.

Classification:

- Focused Box/Cylinder selection, payloads, execution, and result identities:
  **pass**.
- Design Body Boolean selection, target identities, execution, and completion:
  **pass for this case**.
- The primitive/Boolean checkpoint is accepted. Modeling still needs varied
  revolution/dress-up and Part-workflow cases before its surface is frozen.

## Qwen Modeling all-edge fillet case

Prompt:

> Create one 40 x 30 x 20 mm Box Body centered at the origin, then fillet every
> edge with a 3 mm radius. Keep one valid solid Body.

### Run QMF-01 — 2026-08-18

The retained artifacts are `qwen-model-fillet-box-run-1.FCStd`, `.step`, and
`.png`.

- Qwen selected `model.box` directly and created exact centered bounds
  `[-20,20] x [-15,15] x [-10,10]` mm with volume 24,000 mm3.
- It then selected `model.dressup` with `operation=fillet`, radius 3 mm, and the
  exact `all_edges` selection on `Body`.
- The fillet call succeeded on its first attempt. The resulting one-solid Body
  retained the exact centered bounds and measured 23,340.84937505542 mm3, which
  matches the analytic rounded-box volume.
- The complete trace was `model.box`, `model.dressup`, and `document.save`:
  three accepted calls, zero rejected calls, no transition, no inspection
  detour, and no corrective prompt.
- The strict runner exported valid FCStd, STEP, and screenshot artifacts. The
  screenshot visibly confirms all twelve rounded edges.

Classification:

- Focused Box selection and result: **pass**.
- All-edge fillet selection, contract, runtime, and completion: **pass**.
- This varied case independently confirms that QMB-10 was not a Boolean-specific
  selection fluke. Revolution and standalone Part workflows remain separate
  Modeling checkpoints.

## Terra Modeling validation

### Centered-Boolean run before the Inspection rename — 2026-08-18

The first `gpt-5.6-terra` subscription run at high reasoning created the exact
plate, cutter, and Boolean result with correct first-attempt calls. It then made
one unnecessary rejected inspection call:

- `inspect.query(operation="visual_result", target=Body)` was a natural reading
  of that operation name, but the operation accepts only an existing
  `Inspection::Feature` produced by the human Visual Inspection command.
- Terra recovered with `view.control(capture_objects)` and saved the correct
  geometry, but the strict zero-failure oracle rejected the run.

Correction:

- The human action now maps to `inspection_result`. The description remains
  explicit that it reads statistics from an Inspection result. The general view
  capture tool is unchanged.

### Centered-Boolean run after the Inspection rename — 2026-08-18

The retained artifacts are `terra-model-boolean-plate-run-2.FCStd`, `.step`, and
`.png`.

- Terra selected the exact `model.box`, `model.cylinder`, and `model.boolean`
  calls on its first attempts.
- It then used recompute, validity, mass properties, isometric view, object
  capture, and save. Every call succeeded; it did not select
  `inspection_result` for the Body.
- The strict oracle verified one visible solid Body, exact centered bounds,
  volume 39,430.088815692245 mm3, valid FCStd and STEP artifacts, and zero
  rejected calls.

Classification:

- Qwen zero-error primitive/Boolean and all-edge fillet cases: **pass**.
- Terra-high zero-error primitive/Boolean case: **pass**.
- Focused Modeling contracts generalize across the local small model and the
  subscription frontier model. Revolution and standalone Part workflows remain
  before the overall Modeling surface can be frozen.

## Terra Sketch validation

### General rounded-plate run — 2026-08-17

The unchanged rounded-plate prompt was first given to `gpt-5.6-terra` at high
reasoning without requiring Sketch usage. The retained artifacts are
`terra-sketch-rounded-ellipse-run-1.FCStd`, `.step`, and `.png`.

- Terra completed a geometrically correct visible solid, but built it from Part
  primitives, transforms, and booleans instead of a Sketch.
- Nine of 39 calls were rejected. The failures exposed Modeling problems rather
  than Sketch problems: overlap between `model.part` and `model.primitive`, an
  unclear required `centered` transform argument, an unnatural boolean payload,
  and an `inspect.query` crash path for a Compound without `CenterOfMass`.
- The document contains 112 objects and 19 solid-bearing intermediates. This is
  valid completion evidence, but not acceptable parametric Modeling behavior and
  not evidence for the Sketch surface.

These failures are retained in the Modeling log. No Sketch contract was changed
from this run.

### Focused Sketch run before the angle correction — 2026-08-17

The focused prompt explicitly requested one XY-plane Sketch containing the same
rounded Rectangle and Ellipse, followed by one 10 mm extrusion. The retained
artifacts are `terra-sketch-rounded-ellipse-run-2.FCStd`, `.step`, and `.png`.

- Terra selected `model.sketch`, `sketch.open`, `sketch.batch`,
  `sketch.draw_ellipse`, `sketch.finish`, and `model.extrude` correctly.
- One `sketch.batch` call was rejected because a corner Arc ended at 360 degrees.
  The published schema incorrectly allowed values below 360 only, even though
  360 degrees is the natural and exact spelling for the positive X axis at the
  end of a 270-to-360-degree quarter Arc.
- Terra recovered and completed one exact 140 x 96 x 10 mm solid Body, then
  saved FCStd, STEP, and PNG artifacts.

Correction:

- Cyclic start/end/orientation angles now accept the closed interval 0 through
  360 degrees and normalize 360 to 0 internally.
- Arc sweeps remain strictly smaller than 360 degrees, so the correction does
  not permit a degenerate full-circle Arc.

### Focused Sketch run after the angle correction — 2026-08-17

The prompt, model, high reasoning effort, and tool-selection behavior were
unchanged. The retained artifacts are
`terra-sketch-rounded-ellipse-run-3.FCStd`, `.step`, and `.png`.

- All 13 tool calls succeeded; there were zero rejected calls.
- Terra chose the dedicated `sketch.draw_rounded_rectangle` and
  `sketch.draw_ellipse` tools directly, validated the Sketch, finished edit mode,
  extruded it, recomputed it, framed it, and saved it.
- The result is one valid 140 x 96 x 10 mm Body with the centered 30 x 20 mm
  elliptical through-hole.
- The packaged GUI gate publishes 39 Sketch tools in 55,662 bytes. The full
  ribbon gate publishes 533 unique operations without schema failure.

Classification:

- Final Qwen Sketch selection and exact geometry across the concentric-circle and
  rounded-Rectangle/Ellipse cases: **pass**.
- Final Terra focused Sketch workflow with no retry or steering: **pass**.
- Packaged Sketch provider surface and full ribbon registry: **pass**.
- Sketcher is ready for manual real-system validation. Modeling remains a
  separate unfinished sharpening stack and is not covered by this sign-off.

## New-document Native startup

The saved-document boundary remains intentional. The FCStd file establishes the
portable design identity before Native assistance creates machine-local
conversation or authority state; unsaved Native work is not a second persistence
path. While the document is unsaved, the chat input, Send action, attachments,
prompt starters, and conversation controls remain unavailable, and the panel
directs the user to save instead of reporting an unrelated ribbon-tool state.

The header now turns the previous multi-step startup into one guided action:

1. Create a document and choose Native.
2. Confirm manual control and complete the normal Save As dialog.
3. VibeCAD creates the initial conversation and enables the prompt automatically.

Cancelling Save As leaves VibeScript selected and creates no conversation. A
clean-profile GUI gate exercised both cancellation and successful Save As through
the real Qt dialog, then verified that the saved document entered Native mode
with exactly one active conversation and an enabled prompt.

## Corpus results

No ten-case corpus run has completed on this branch yet. Add immutable run tables
here after the rounded-plate development case reaches a valid exported solid and
the runner has case-specific deterministic oracles.
