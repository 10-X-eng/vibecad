# Native Tool Sharpening Benchmark

## Purpose

This corpus measures whether VibeCAD's Native assistant can complete varied
mechanical-engineering work from ordinary user requests. It is designed to catch
overfitting to one part, one tool family, or one model.

The benchmark is not a prompt-writing contest. User prompts state the desired
artifact and engineering requirements. They must not name VibeCAD tools, prescribe
an operation sequence, tell the model to inspect instead of calculate, or explain
how the application works.

CADGenBench supplies public generation and editing inputs and a tool-independent
geometry evaluation approach. VibeCAD adds workflow cases for capabilities outside
CADGenBench: parameters, assemblies, analysis, manufacturing, drawings, and BOMs.

Sources:

- [CADGenBench repository](https://github.com/huggingface/cadgenbench)
- [Public CADGenBench inputs](https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data)
- [CADGenBench leaderboard](https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench)

## Fixed run rules

Every comparison uses the same ten cases and the exact prompt text below.

1. Start from the case's declared clean document or immutable input fixture.
2. Use the same VibeCAD build, model, reasoning effort, and model context limit for
   every case in one run.
3. Do not add corrective steering, tool hints, or hidden case-specific instructions.
4. Preserve every rollout, tool trace, final document, neutral export, and screenshot.
5. Evaluate the document with an external deterministic oracle. The assistant's
   final prose is never evidence that the artifact is correct.
6. Record every rejected call, corrected call, CAD-work transition, and unfinished
   turn. A recovered error still counts against first-call accuracy.
7. A system-prompt change must be general CAD guidance supported by failures in at
   least three distinct cases. Tool-local ambiguity belongs in that tool's name,
   description, schema, or result.
8. Never change a case prompt to make a failing implementation pass. Version a new
   corpus if a requirement itself is defective.
9. Set the wall-clock allowance from measured hardware throughput and keep it fixed
   within a comparison run. A harness timeout while the provider is still producing
   work is an infrastructure result, not a CAD failure.

## Ten-case corpus

### NTS-01 — Machined cover from drawing

- Input: CADGenBench sample `101/input.png` attached as the reference image.
- Surfaces: Modeling, Sketching.
- Exercises: drawing interpretation, irregular outer profile, bosses, bores, hole
  placement, pockets, fillets, and a single valid part.
- Prompt: `Reproduce the geometry as accurately as possible from the attached engineering drawing.`
- Oracle: CADGenBench validity, shape similarity, interface match, and topology
  metrics against its private ground truth; VibeCAD additionally requires an
  editable parametric document and one final solid Body.

### NTS-02 — Patterned cylindrical hub from drawing

- Input: CADGenBench sample `105/input.png` attached as the reference image.
- Surfaces: Modeling, Sketching.
- Exercises: rotational geometry, stepped bores, radial holes, repeated features,
  and dress-up features.
- Prompt: `Reproduce the geometry as accurately as possible from the attached engineering drawing.`
- Oracle: the CADGenBench geometry metrics plus one valid final solid Body and a
  retained editable feature history.

### NTS-03 — Formed sheet-metal bracket from drawing

- Input: CADGenBench sample `103/input.png` attached as the reference image.
- Surfaces: Modeling, Sketching.
- Exercises: sheet thickness, bends, reliefs, cutouts, holes, and repeated formed
  features. A failure caused by missing sheet-metal capability is a platform gap,
  not a model failure.
- Prompt: `Reproduce the geometry as accurately as possible from the attached engineering drawing.`
- Oracle: the CADGenBench geometry metrics, uniform specified thickness, valid
  bend topology, one connected result, and an editable formed-part history.

### NTS-04 — Replace end-face chamfers

- Input: CADGenBench sample `208/input.step` in a clean document.
- Surfaces: Modeling.
- Exercises: inspection of imported topology, exact edge targeting, removal or
  suppression of an existing treatment, and replacement dress-up geometry.
- Prompt: `On the end face furthest in -X, replace the chamfers on both concentric circular edges with 3 mm fillets.`
- Oracle: CADGenBench validity, shape similarity, interface match, and topology
  metrics; the unrelated imported geometry must remain unchanged.

### NTS-05 — Change an impeller pattern

- Input: CADGenBench sample `203/input.step` in a clean document.
- Surfaces: Modeling.
- Exercises: recognition of repeated geometry, stable targeting, patterned-feature
  editing, and preservation of the hub and blade form.
- Prompt: `Reduce the number of impeller blades from 7 to 5.`
- Oracle: exactly five blades, one connected valid result, unchanged hub/interface
  geometry, and the CADGenBench geometry metrics.

### NTS-06 — Parameter-driven mounting plate

- Input: clean document.
- Surfaces: Parameters, Modeling, Sketching.
- Exercises: named parameters, work switching, a closed outer profile with internal
  loops, rounded corners, extrusion, and a later dimensional update.
- Prompt: `Build a parametric mounting plate with its lower-left corner at X=0, Y=0 and its bottom at Z=0. Set PlateLength=80 mm, PlateWidth=50 mm, Thickness=8 mm, CornerRadius=5 mm, and HoleDiameter=6 mm. Put four through-holes at (10,10), (70,10), (70,40), and (10,40) mm. Then change PlateLength to 100 mm and move the two +X hole centers to X=90 mm. Keep one solid Body and report its final overall dimensions, volume, validity, and solid count.`
- Oracle: named editable parameters with the final values; bounds of
  `100 x 50 x 8 mm`; four diameter-6 through-holes at the final coordinates;
  corner radius 5 mm; one valid solid; volume read independently from the final
  document.

### NTS-07 — Pin-and-clevis assembly

- Input: clean document with immutable `clevis.FCStd` and `pin.FCStd` component
  fixtures available to the assistant.
- Surfaces: Modeling, Assembly.
- Exercises: component reuse, occurrence creation, work switching, axis selection,
  fixed and revolute joints, and motion inspection.
- Prompt: `Create an assembly from the supplied clevis and pin. Fix the clevis, place the pin through both clevis ears, and give the pin its intended rotational freedom without axial sliding. Confirm the remaining degrees of freedom and check for unintended interference.`
- Oracle: exactly one occurrence of each supplied definition; fixed clevis; pin
  axis coincident with both clevis bores; one rotational degree of freedom; no
  axial degree of freedom; no unintended solid intersection.

### NTS-08 — Static analysis of a loaded bracket

- Input: immutable `analysis-bracket.FCStd` fixture containing a single bracket
  Body with two mounting holes and a named loaded face.
- Surfaces: Analysis and Mesh.
- Exercises: material assignment, supports, load creation, meshing, solver setup,
  background execution, result reading, and work switching.
- Prompt: `Analyze the supplied bracket as 6061-T6 aluminum. Fix both cylindrical mounting-hole faces and apply a total 1000 N load in -Z uniformly over the named loaded face. Use a suitable solid mesh, solve a linear static case, and report maximum displacement and von Mises stress with their locations.`
- Oracle: correct material; exact constrained and loaded faces; total load and
  direction; valid volume mesh; completed linear-static solve; finite displacement
  and stress fields; reported extrema equal the stored solver results.

### NTS-09 — CAM for a four-hole plate

- Input: immutable `cam-plate.FCStd` fixture and a declared stock/tool library.
- Surfaces: Manufacturing.
- Exercises: stock, job, tool selection, facing, profiling, drilling, ordering,
  simulation, and postprocessing.
- Prompt: `Prepare the supplied plate for a 3-axis mill. Face the top, drill all four through-holes, and profile the outside while leaving four 0.5 mm holding tabs. Use the supplied aluminum stock and tool library, simulate the job, and produce the configured machine program.`
- Oracle: one valid CAM job; correct stock and setup; four hole locations; complete
  top facing; outside profile with four tabs; no simulated fixture collision or
  remaining material in required cut regions; non-empty postprocessed program.

### NTS-10 — Assembly drawing and BOM

- Input: immutable `drawing-assembly.FCStd` fixture with a small multi-component
  mechanical assembly and part metadata.
- Surfaces: Drawing and Assembly.
- Exercises: drawing-page creation, projected views, dimensions, balloons, BOM
  generation, quantity aggregation, and tree publication.
- Prompt: `Create an A3 mechanical drawing for the supplied assembly with front, right, section, and isometric views. Dimension the functional mounting and fit features, add item balloons, and include a BOM with item number, part number, description, material, and quantity. Export the finished drawing to PDF.`
- Oracle: one A3 page; required views and a valid section; dimensions attached to
  the intended geometry; balloon-to-row agreement; BOM rows aggregated by component
  definition with correct quantities and metadata; expandable BOM in the model tree;
  non-empty PDF export.

## Metrics

Record these values per case and in aggregate:

| Metric | Meaning |
| --- | --- |
| Artifact pass | The case-specific deterministic oracle passes. |
| First-call accuracy | Accepted calls divided by all first attempts at an intended action. |
| Recovery accuracy | Failed actions later corrected without user steering. |
| Completion | The assistant reaches the requested final artifact and answer. |
| False completion | The assistant claims success while the oracle fails. This is always a critical failure. |
| CAD-work transitions | Requested transitions, successful transitions, and unnecessary transitions. |
| Non-CAD calls | Provider-owned planning or harness calls made before or between VibeCAD actions. |
| Context bytes | System instructions, active state, conversation, and tool schemas measured separately for every turn. |
| Token usage | Input, cached input, reasoning, and output tokens where the provider reports them. |
| Latency | Total case time and time to each accepted mutation. |
| Tool-result grounding | Reported dimensions, volumes, counts, solver values, and manufacturing facts match the document. |

The primary score is cases passed without user steering. Token count is a constraint,
not the goal: a shorter surface is better only when it preserves or improves artifact
pass rate and first-call accuracy.

## Run record

Create one immutable record for each corpus run. At minimum, record:

```text
Run ID:
VibeCAD commit/build:
Provider and access method:
Model:
Reasoning effort:
Context limit:
Case fixture version:

Case | Artifact pass | First-call accuracy | Recovered errors | False completion | Transitions | Non-CAD calls | Context bytes | Tokens | Elapsed
NTS-01 |
NTS-02 |
NTS-03 |
NTS-04 |
NTS-05 |
NTS-06 |
NTS-07 |
NTS-08 |
NTS-09 |
NTS-10 |
```

Below the table, list each failure by its first causal category and link the exact
rollout, final document, neutral export, screenshot, and oracle output. Never replace
the original record after a fix; add a new run so progress and regressions remain
visible.

## Tuning discipline

Classify every failure before changing code:

- **False state:** VibeCAD supplied incorrect or missing live document facts.
- **Tool selection:** names or descriptions made the wrong capability look correct.
- **Schema:** the chosen capability's required payload was ambiguous or needlessly
  difficult to express.
- **Runtime:** a valid request failed or produced the wrong document mutation.
- **Result:** the tool succeeded but omitted the exact identity or fact needed next.
- **Transition:** the next CAD work surface was wrong, stale, or not carried forward.
- **Model limit:** the surface was correct and concise but the model still failed.

Fix the earliest demonstrated platform defect. Re-run the unchanged failing case,
then the full unchanged corpus. A local improvement that regresses another case is
not accepted.

The shared system prompt should contain only durable cross-platform behavior: honor
the current request, preserve unrelated design identity/history, use exact returned
state, recover from a failed operation before dependent work, and verify requested
engineering outcomes before claiming completion. Geometry recipes, tool names,
work-surface instructions, and case-specific warnings belong elsewhere.

## Required runner work

The current Ollama live acceptance runner assumes exactly one visible solid Body and
one STEP export. Replace that single hardcoded acceptance condition with a case
manifest and case-specific read-only oracle. The runner must select provider, model,
reasoning effort, and context limit through environment variables, preserve all
evidence, and never inject retries or corrective steering. CADGenBench candidates
should retain its standard `output.step` layout so the public local sanity check and
leaderboard evaluator can consume them directly.
