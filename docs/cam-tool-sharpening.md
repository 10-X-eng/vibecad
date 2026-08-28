# CAM: Native production workflow and tool sharpening

## Purpose

VibeCAD CAM must help a person turn one or more models into trustworthy machine
programs. Humans choose the Manufacture ribbon and remain in control of the
document. Native assistance uses the same production services as the human CAM
commands. The assistant should be able to inspect the current manufacturing
state, explain what is missing, perform requested work, verify the result, and
return a useful next state without requiring the user to understand VibeCAD's
internal object model.

This effort is Native-only. Existing CAM VibeScript behavior remains untouched
for compatibility, but it is not expanded or used as the production CAM path.
CAM is stateful, interactive, machine-dependent work; forcing it into a complete
VibeScript program would create a second implementation and a worse user flow.

## Product rules

1. Human and AI actions call the same production operations.
2. Humans choose the ribbon. Provider tools refresh between turns from exact
   durable document state.
3. The model receives the fewest tools and words that make every currently valid
   choice obvious. Tool availability never depends on prompt classification.
4. Tools are not forced, and ordinary prompts are not supplemented with hidden
   CAM steering.
5. No benchmark-specific behavior, model-specific exception, silent guessing,
   or success claim without verified evidence is allowed.
6. Correctness, recoverability, and machining safety take priority over response
   speed.
7. Expensive geometry, path generation, simulation, verification, and posting do
   not run on the Qt document thread without a demonstrated technical necessity.
8. Read operations do not mutate the document, selection, visibility, History,
   or structural revision.
9. Mutations are atomic, undoable, stale-safe, and stable after save/reopen. A
   failed operation leaves no partial Job, ToolBit, controller, or path feature.
10. Public removals, renames, and incompatible schema changes require owner
    approval. Additive production improvements do not.

## Multi-setup architecture

CAM is not a singleton workflow. One document may contain:

- independent Jobs for unrelated parts;
- several setups for different sides of one workpiece;
- different machines or postprocessors;
- alternative process plans for the same model;
- multiple stocks, fixtures, WCS assignments, tools, and operation sequences;
- related setups connected by an explicit remaining-stock handoff; and
- unrelated setups with no shared lifecycle at all.

The architecture must therefore work for zero, one, ten, or hundreds of setups.

- A Job is a setup aggregate, not a document-wide CAM container. Every stock,
  fixture, WCS, controller, operation, verification result, and posted artifact
  has exactly one owning setup unless it is an immutable library definition.
- The document has no authoritative `active setup` singleton. Human selection may
  focus one setup for a turn; it never changes ownership or creates an implicit
  dependency.
- Every mutation targets an explicit Job/setup identity. The executor never
  guesses a target from “the active Job.”
- Human selection is a turn-start disambiguation signal, not hidden mutation
  authority. Selection of a Job or one of its owned resources scopes the provider
  to that setup. With no selection, a sole setup is a convenience fallback; many
  setups produce a bounded catalog and require an explicit target.
- Setup enumeration is paged and searchable. Context truncation never makes later
  setups unsupported.
- Readiness, stock, machine, tools, operations, verification, and post state are
  calculated per setup, not aggregated into one document-wide answer.
- Each setup owns its operation order and tool controllers. Library ToolBits may
  be shared definitions, but mutable controller state is setup-owned.
- Related setups use explicit links. A remaining-stock result from Setup A may be
  adopted by Setup B only through a declared handoff with exact source state.
  Document order, labels, and geometric similarity never imply a relationship.
- Explicit setup relationships form an acyclic process graph. One setup may feed
  several later setups, and an unrelated setup remains a separate graph root.
  Recomputing a setup invalidates only its own derived state and declared
  downstream consumers.
- Posting targets one explicit setup, selected operations, or an explicit ordered
  process plan. It never silently posts every Job in the document.
- Background jobs and caches are keyed by setup identity and exact source state,
  so work on one setup cannot validate or invalidate another accidentally.
- Live context carries every active CAM job and its setup resource scope instead
  of collapsing several running setups into one document-wide status. A selected
  busy setup exposes status and safe reads; an unrelated setup remains usable.
- The tree presents every setup as a separate expandable manufacturing unit with
  its model, stock, fixtures, WCS, tools, operations, verification, and outputs.

The Analyze ribbon currently contains assumptions that make multiple studies and
their meshes awkward or effectively singular. The reusable selection/scoping
design developed here must be applicable to many FEM studies, meshes, solvers,
and results. FEM correction is a separate coherent follow-up PR; the first CAM PR
must not mix unrelated FEM changes into its review surface.

## Baseline surface

At the start of this effort, the default human Manufacture ribbon contained 60
actions and had seven optional actions. Composite buttons and related actions
resolved to 27 Native provider families. The human-owned `workspace.switch`
family was removed before dispatch,
so the model received 26 tools on a default CAM turn:

- seven shared document, state, view, inspection, background, save, and undo
  families;
- fourteen CAM families for Jobs, properties, inspection, tools, programs,
  operations, modification, simulation, posting, templates, and output; and
- five robot path, sequence, motion, and export families.

Twenty-six tools with large operation unions are unnecessary at every stage.
Manufacture needs the exact-state provider projection already used by Analyze,
but it must be designed for many selected or explicitly targeted setups rather
than one study or one active Job.

## Progressive Native surface

The human ribbon remains complete. Only the provider surface is projected.
Projection is derived from installed capabilities, exact document objects,
explicit relationships, readiness, and current human selection.

| Current state | Provider emphasis |
|---|---|
| No setup | Inspect selected models, enumerate setups, create setup, search tool catalog, shared view/save/undo. |
| Setup selected but incomplete | Read/edit setup, stock, placement, WCS, fixture, machine, post, tool, and readiness. |
| Setup ready for operations | Applicable operation creation plus setup/tool editing. Operation families whose real prerequisites are absent remain unavailable. |
| Operation selected | Read/edit/recompute/reorder/activate/remove that operation and expose applicable dress-ups. |
| Valid paths exist | Inspect commands, simulate, verify, compare remaining stock, and retain setup/operation correction tools. |
| Setup is post-ready | Preview and post the explicit setup or selected operations while retaining all relevant correction tools. |
| Selected setup has background work active | Job status/cancel plus current state; setup-targeted reads and mutations return after atomic publication. |
| Native simulator open | Exact simulation close plus non-mutating setup inspection; document mutations remain hidden until the task closes. |
| Multiple setups selected | Only operations with a real multi-setup contract appear. Single-setup mutations require an explicit setup target. |

This is not a rigid wizard. Relevant earlier operations remain available, and
the surface can expand or contract as document state changes. Exact projection
removes impossible or irrelevant choices; it does not decide what the user wants.

## Human workflow

The primary human path is:

1. Select one or more manufacturable models and create a setup.
2. Choose a machine and postprocessor.
3. Define stock, workholding, setup orientation, origin, and WCS.
4. Search or create tools and configure setup-owned controllers.
5. Create and edit operations.
6. Generate paths in the background with visible progress and cancellation.
7. Inspect and simulate stock removal.
8. Review readiness, collisions, gouges, limits, units, feeds, and cycle time.
9. Preview the exact posted program and authorize its output.

The GUI should explain missing facts in manufacturing language and link directly
to the relevant editor. The assistant may complete requested work, but it should
ask for genuinely unavailable physical facts—such as the actual machine, holder,
workholding, stock, material, or cutting data—instead of inventing them.

## Production services

### Setup and stock

One shared service supports human dialogs and Native bindings for models, stock
type and placement, setup orientation, WCS/origin, fixtures, machine, postprocessor,
units, output defaults, and explicit inter-setup stock handoff. It supports create,
read, update, recompute, suppress/activate, delete, and ordered process-plan use.

### Tools and cutting data

Tool catalog search works by machining purpose and exact dimensions rather than
requiring a model to page blindly through filesystem-shaped assets. ToolBit,
holder, controller, offsets, spindle, coolant, feed, rapid, and machine/material
limits remain distinct. Deterministic calculations may derive values only from
explicit physical inputs and report the source; missing cutting data is not guessed.

### Operations

Every operation supports the lifecycle its human editor supports: inspect current
editable state, create, edit geometry and parameters, choose a tool, recompute,
reorder, activate/suppress, and remove. Geometry selectors use current objects and
subelements supplied by inspection or selection. Runtime must not depend on an
unpublished GUI selection or task-panel side effect.

Path-generating Native operations capture the exact setup on the document
thread, save one private FCStd snapshot there, and perform path computation in an
isolated `FreeCADCmd` process. One bounded, authenticated path artifact is
published in a short document transaction after exact live-state revalidation.
Cancellation remains available until publication starts. Exact unchanged work
uses a bounded in-session cache. Setup expressions and native presentation are
restored during publication; undo/redo and save/reopen retain the same path and
persistent operation settings. Array, copy-path, and start-point edits do not
generate paths and remain immediate atomic graph edits.

The first proven vertical slice contains facing, pocket, profile, and drilling.
After owner validation, the same lifecycle is applied separately to helix,
adaptive, slot, thread milling, engraving, deburring, V-carve, 3D pocket,
copy/array, probe, custom program commands, and each dress-up. Optional surface,
waterline, rotary, 3+2, multi-axis, CAMotics, and robot workflows are later
capability milestones with truthful runtime availability.

### Verification and posting

Path validity is not proof of machining safety. Verification reports its actual
coverage: stock removal, remaining stock, tool/holder/fixture collisions, gouges,
rapid clearance, machine travel/rotation limits, unsupported commands, units,
feeds, spindle state, coolant, and per-operation/total cycle time. Unsupported
checks are named rather than treated as passed.

Posting is one coherent human flow: select explicit setup/operations and fixtures,
review machine and post, run lightweight sanity checks, preview deterministic
G-code and files, optionally inspect/edit, and authorize output. Generation occurs
in the background so the dialog and application remain responsive.

## Upstream product signals

FreeCAD's current CAM backlog identifies recurring needs that should inform
VibeCAD priorities without blindly copying every patch:

- [New Job UI improvements](https://github.com/FreeCAD/FreeCAD/issues/30194):
  understandable unit warnings and safer feed-unit presentation.
- [Postprocessing dialog design](https://github.com/FreeCAD/FreeCAD/discussions/28428):
  one integrated overview for machine, fixtures, operations, sanity findings,
  cycle time, G-code, and output instead of sequential modal dialogs.
- [Headless operation creation failure](https://github.com/FreeCAD/FreeCAD/issues/31849):
  operation factories must not depend on hidden GUI selection or retain broken
  partial objects.
- [Rapid-filter path safety](https://github.com/FreeCAD/FreeCAD/pull/31731): path
  optimization must preserve intentional retract, clearance, and endpoint motion.
- [Many-tool-controller opening performance](https://github.com/FreeCAD/FreeCAD/issues/32195):
  repeated global reference work can turn seconds into minutes.
- [Opaque stock preselection](https://github.com/FreeCAD/FreeCAD/issues/32167):
  stock presentation must not make underlying model geometry hard to select.
- [Machine and machine-based post support](https://github.com/FreeCAD/FreeCAD/pull/32132):
  machine capabilities and post behavior must be first-class.
- [Simulation epic](https://github.com/FreeCAD/FreeCAD/pull/31244) and
  [multi-axis simulation](https://github.com/FreeCAD/FreeCAD/issues/29758):
  simulation needs better UX and must eventually account for the machine
  kinematic chain.
- [3+2 rest machining](https://github.com/FreeCAD/FreeCAD/issues/30204): cleared
  material cannot be represented as one Z-axis cylinder assumption across
  differently oriented operations.

Upstream changes are reviewed before local implementation. A current upstream fix
may be adopted or adapted; VibeCAD should not create conflicting parallel behavior.

## Tool-sharpening method

### Ordinary prompts

Benchmarks use requests a normal person would make, such as “set this bracket up
for my three-axis mill and machine the top pocket and holes.” They do not name
tools, payload fields, operation order, selection syntax, or hidden platform rules.
Each run starts from a known fresh fixture and its exact preconditions are recorded.

### Qwen: minimum usability probe

Local Qwen receives whatever time the available hardware requires. It is used to
expose unclear names, schemas, descriptions, state, returns, and error recovery.
Qwen is not the machining-taste oracle and the platform is never shaped around a
single small model's preferences.

A Qwen case passes the tool-use gate when it:

- selects semantically appropriate tools;
- produces schema-valid arguments without payload coaching;
- can inspect the information required for its next call;
- completes mutations without runtime or postcondition failure;
- understands the truthful result and limitations; and
- can correct a rejected request using the returned repair information.

The part may still use a mediocre strategy, conservative parameters, awkward
operation order, or unattractive labels. Those are model taste unless the tool
surface prevented a reasonable choice.

### Failure classification

Every unsuccessful run is classified before changing code:

| Class | Evidence | Platform response |
|---|---|---|
| Tool discovery defect | A relevant operation is absent, misleadingly named, duplicated, or buried behind unrelated choices. | Correct state projection or tool shape. |
| Schema defect | A reasonable interpretation is schema-valid but runtime-invalid, required fields are ambiguous, or natural equivalent values are needlessly rejected. | Align schema, runtime, and human semantics. |
| Context defect | Required current facts cannot be discovered, are stale, are attached to the wrong setup, or are drowned in irrelevant state. | Correct bounded inspection and setup-scoped context. |
| Runtime defect | Correct call crashes, hangs, partially mutates, corrupts History, or differs from the human command. | Fix the shared production implementation. |
| Result defect | Tool reports success but geometry, path, setup identity, verification, or output is wrong or misleading. | Fix postconditions and production behavior. |
| Capability gap | The requested machining method is genuinely not implemented or installed. | Report the exact limitation; schedule a capability milestone if in scope. |
| Model taste/ability | Tools execute correctly and expose adequate choices, but the model chooses an inferior yet valid process. | Do not hard-code or steer; compare with Terra and record the model limit. |
| Fixture defect | Benchmark starts with prior Jobs/results, wrong stock, missing geometry, or contradictory requirements. | Repair the fixture and rerun; do not change production. |

Repeated Qwen preference alone is not evidence for a product change. A synonym or
parameter form is accepted when it is natural for users and preserves one clear
meaning, not merely because Qwen emitted it once. No hidden retry, forced call,
prompt recipe, or benchmark detector is added to manufacture success.

### Terra: stronger independent validation

GPT-5.6 Terra through the subscription path runs the same ordinary prompt against
a fresh identical fixture at high reasoning effort. Terra provides a better-taste
comparison, not implementation instructions.

- If Qwen and Terra fail at the same contract or runtime boundary, platform-defect
  confidence is high.
- If Terra succeeds and Qwen calls the tools correctly but chooses a worse valid
  strategy, the result is a model limitation.
- If Terra finds a different natural payload interpretation, the contract is
  reviewed for genuine ambiguity.
- Model feedback may describe what was unclear on the published surface. Models
  are not asked to design code they cannot see.

### Validation evidence

- Independent setup gates create three Jobs for the same model without replacing
  or cross-mutating one another.
- Related-setup gates support sibling branches from one retained result, reject
  stale predecessors, and preserve links through undo, redo, and save/reopen.
- PathSimulator produces its visual preview Mesh and a separate exact solid
  representation of the simulated height field. Follow-up setups consume the
  solid representation; they do not attempt to repair the render Mesh.
- Retained simulation checks exact detached cutter sweeps against the protected
  setup model off the document thread. Collision-free and deliberate-gouge gates
  prove both outcomes; result objects retain bounded collision evidence and name
  holder and fixture checks as unavailable rather than passing them implicitly.
  Bundled, user, and add-on machine definitions are all usable on a fresh install.
  For a configured machine, verification compares exact toolpath extents with
  each supported axis travel span. It reports definite span violations, while
  separately identifying absolute machine-position checking as unavailable until
  the setup has a measured machine-coordinate work offset. Rapid sweeps are
  checked against protected model geometry and identify current-stock clearance
  as unavailable. Cycle time uses the existing CAM Path estimator with exact
  setup feeds and rapids and reports feed-speed fallback or missing operation data
  explicitly.
- The Manufacture simulator menu exposes a human retained-result command backed
  by the same preflight, background worker, commit, and verifier as Native mode.
  With several Jobs it is inactive until selection identifies exactly one setup;
  the compiled gate proves it does not fall back to a document-wide active Job.
- On a two-operation bracket, Qwen selected retained-stock simulation, polled its
  background job, and created a follow-up setup with no failed calls. Terra/high
  independently retained the stock and created the follow-up setup with no failed
  calls. Both saved documents reopened with two Jobs and valid retained stock.
- Exact operation targets now accept the bounded setup-summary identity and the
  full `read_setup` or operation-result identity, then normalize to the complete
  toolpath state. Simulation, retained-stock verification, CAMotics, selected
  posting, copies, arrays, and dress-ups all consume the same public target.
- An ordinary Qwen bracket run exposed eight failed calls, including an impossible
  read-to-simulation identity mismatch and a false model-source mutation after
  Pocket generation. After production fixes, the identical prompt produced a real
  Pocket, completed interactive simulation, posted NC, and saved successfully with
  five failed calls. One was a reasonable optional Pocket label and is now
  accepted. The other four were inconsistent retries on schemas Qwen had already
  used successfully. Terra/high then completed the same prompt with zero failed
  calls: exact ToolBit and controller updates, machine and post setup, labeled
  Pocket, drilling, retained-stock simulation, validation, posting, save, and
  reopen evidence with two active valid operations.
- Pocket, drilling, and whole-model 3D Surface gates execute the production
  Native call through the background manager and isolated worker. The GUI event
  loop remains live, setup-scoped progress appears in Manufacture context,
  independent Jobs keep separate ownership, and generated paths and computed
  operation properties survive rollback, undo/redo, recompute, save, and reopen.
  Existing lifecycle gates for every supported path strategy still pass through
  the shared headless operation boundary.
- The interactive GL gate proves the open simulator publishes its exact task ID
  and scopes the next Native turn to close plus non-mutating inspection. A live
  Terra/high run opened, inspected, and closed that task without an impossible
  mutation being advertised or attempted.
- Live background reruns exposed a publication race when a busy setup still
  advertised reads bound to its pre-publication state. The selected busy surface
  now contains only current state and background status/cancel. Terra/high then
  created a Pocket and Drilling operation from the fresh bracket with zero failed
  calls; both paths remained active and valid after save/reopen.
- Qwen completed the same corrected surface without the former busy-read trap,
  but repeatedly encoded an explicit geometry array as text and selected invalid
  or redundant geometry. Terra used the identical schemas correctly, so those
  remaining failures are recorded as the small model's structured-output and
  machining-plan limit rather than accepted through a looser production contract.

## Reusable benchmark corpus

The corpus contains happy and unhappy paths and never tunes around one part:

1. A prismatic bracket using facing, pocket, outside profile, and drilling.
2. A hole plate using spot/drill/counterbore/countersink/thread workflows.
3. A pocket with islands using roughing, rest machining, and finish passes.
4. A thin-wall part using tabs, leads, ramps, and safe finish allowance.
5. Engraved text and V-carved geometry with disconnected paths.
6. A sculpted mold using 3D pocket, waterline, and surface finishing.
7. A rotary shaft when the installed machine and runtime support it.
8. One workpiece with two explicitly related setups and remaining-stock handoff.
9. Several unrelated Jobs using different machines, units, and posts in one
   document to prove non-singleton behavior.
10. A large document with many setups, tools, and operations to measure opening,
    scoping, caching, cancellation, and provider-context size.
11. Invalid or unsupported cases: broken geometry, impossible cutter, missing
    holder or stock, unsafe depth, fixture collision, wrong WCS, unavailable post,
    stale state, and unsupported machine kinematics.

Metric and imperial cases are both required. Generated programs are compared with
the exact internal motion and, where supported, parsed or backplotted to ensure the
post did not change the intended path.

## Delivery milestones and stopping gates

### Milestone 1: Native CAM foundation

- Complete human/action/provider parity inventory.
- Add multi-setup exact-state provider projection.
- Establish shared setup targeting and background execution contracts.
- Measure context and schema size at zero, one, and many setups.

**Gate:** architecture review before writing every operation integration.

### Milestone 2: complete three-axis vertical slice

- Job/setup, stock, WCS, fixtures, machine, post, and tool workflows.
- Facing, pocket, profile, and drilling with complete edit lifecycle.
- Integrated inspection, simulation/readiness, preview, and post.
- Human and AI paths produce equivalent saved document state.

**Gate:** owner completes a real local Job and inspects the output before the
architecture is multiplied across the ribbon.

### Milestone 3: ribbon completion

- Apply the proven lifecycle independently to remaining standard operations,
  program commands, copying, and every dress-up.
- Run the full multi-setup corpus through Qwen and Terra.

**Gate:** no unresolved production/tool defect in supported three-axis workflows.

### Milestone 4: advanced CAM

- Optional OCL 3D strategies, rotary, 3+2, rest machining, multi-axis simulation,
  CAMotics, and robot overlap, each enabled only when its runtime is real.

Advanced CAM may require separate issues and PRs. It is not allowed to delay a
high-quality three-axis package indefinitely or be advertised before verification
can represent its actual kinematics.

### FEM multi-study follow-up

After the multi-resource targeting/scoping pattern is proven, open and implement a
separate FEM correction for multiple independent or related studies, meshes,
solvers, and result sets. It must remove singleton assumptions without changing
CAM delivery or mixing the two workbenches in one review.

## Completion criteria

The sharpening effort is complete only when:

- many independent and related setups work without ambiguity or cross-mutation;
- the model normally sees only tools applicable to the exact selected setup state;
- ordinary Qwen prompts use supported tools without call-shape or runtime errors;
- Terra independently validates the same workflows with no platform-specific
  coaching;
- every supported operation has a human and Native create/edit/recovery path;
- long work remains responsive, cancellable, cached, and stale-safe;
- inspection, simulation, verification, and posting are evidence-backed;
- undo and save/reopen preserve every setup and relationship;
- posted motion agrees with the verified internal path;
- errors identify the affected setup, object, field, actual value, expected value,
  and one direct correction; and
- documentation lets a new user complete a Job without already knowing FreeCAD
  CAM internals.

The objective is not to make Qwen a brilliant machinist. The objective is to make
VibeCAD CAM so coherent that even a weak model can operate every supported tool
correctly, while stronger models and human operators receive a best-of-breed CAM
workflow rather than additional platform complexity.
