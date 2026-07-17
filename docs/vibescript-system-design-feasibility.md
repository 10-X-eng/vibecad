# VibeScript system-design feasibility

## Scope and method

This assessment asks two different questions:

1. Can VibeCAD/VibeScript generate the component geometry required by a complete product?
2. Can the current architecture preserve a reliable, revisable product definition across components, assemblies, analysis, drawings, manufacturing, and project reopen?

The distinction matters: geometry generation can be feasible while product-scale development remains structurally unsafe. Source references below use repository-relative paths and line numbers from the assessed revision.

**Implementation update (2026-07-17):** VibeScript now follows the selected-engine setting into supported native workbench surfaces, with BIM disabled by default and available through an explicit preference. ChatGPT subscription turns now use the same mixed native-plus-VibeScript surfaces as API-key providers. Sections 2 and 3 incorporate that implementation; the native-object identity findings in section 1 are unchanged.

### Classification vocabulary

- **Hard contract** — behavior explicitly validated or rejected by the current runtime/tool contract. A caller cannot rely on contrary behavior without changing that contract.
- **Current implementation choice** — behavior performed by the present code, but not inherently required by the external model concept.
- **Missing test** — high-risk behavior for which the inspected tests provide no end-to-end assertion. This is not evidence that the behavior works.

## 1. VibeScript product-scale boundary

### 1.1 Unit of authority and ownership

One VibeScript model is a source-defined, revisioned unit identified by a persistent 32-character `model_id`. Its accepted native representation is one newly created `App::Part` container carrying the model id, source, JSON parameters, content revision, runtime version, and an output-key-to-object-name map (`src/Mod/VibeCAD/VibeCADVibeScript.py:1553-1588`). The revision is derived from source, parameters, and expected outputs; create and update preparation carries the same model id while computing a new revision (`src/Mod/VibeCAD/VibeCADVibeScript.py:1470-1505`).

The model owns more than the final solids. After execution, every top-level document object created by that run is added to the model container; objects already recursively contained by another new object are not added twice (`src/Mod/VibeCAD/VibeCADVibeScript.py:1534-1536,1566-1572`). This can include bodies, sketches, PartDesign features, and a bound `App::VarSet`: the authoring API deliberately persists parameter algebra through an `App::VarSet` (`src/Mod/VibeCAD/vibescript_api.py:140-145,200-213`). Only the container and named result objects receive the model-id property directly (`src/Mod/VibeCAD/VibeCADVibeScript.py:1574-1588`), but deletion expands tagged roots through recursive containment for `App::Part` and `PartDesign::Body` roots (`src/Mod/VibeCAD/VibeCADScriptedOwnership.py:10-34,62-70`).

**Boundary:** a model is therefore one source/revision/parameter/output contract plus the complete native object subtree created by that execution. It is not, in this contract, a BOM, assembly, requirement set, configuration, analysis case, drawing package, or cross-document product structure.

### 1.2 Permitted outputs

The runtime requires source to assign a non-empty `result` dictionary whose keys exactly equal `expected_outputs` in the same order (`src/Mod/VibeCAD/vibescript_executor.py:456-473`). Each value must be a document object exposing `Shape`, recompute cleanly, have a valid shape, contain **exactly one solid**, and pass the deep OCCT validity check when available (`src/Mod/VibeCAD/vibescript_executor.py:475-523`). The instruction to return separate physical components as separate named outputs is explicit at lines 500-504. The engine additionally rejects any result object that was not created by the current run (`src/Mod/VibeCAD/VibeCADVibeScript.py:1534-1551`). `expected_outputs` is non-empty and bounded to 64 keys (`src/Mod/VibeCAD/VibeCADVibeScript.py:38-40,369-404`).

Consequences:

- A model may create a rich native PartDesign feature tree and may expose multiple physical components, but every published output is exactly one valid solid.
- A compound or multi-solid assembly cannot be one published output. It must be split into named one-solid outputs or represented outside the VibeScript output contract.
- Existing document objects cannot be adopted as outputs, even if geometrically valid.
- The output map persists logical keys and current object names, not stable object UUIDs or a reconciliation map (`src/Mod/VibeCAD/VibeCADVibeScript.py:1574-1588`).

These are **hard contracts**, not merely gaps in the helper library.

### 1.3 How source and parameter changes are accepted

Create validates the model name, source, parameters, and expected output list, rejects duplicate model labels, assigns a new model id, and computes a content revision (`src/Mod/VibeCAD/VibeCADVibeScript.py:1331-1368,1470-1505`). Updates first resolve the existing project artifact/native model and require the caller's `expected_revision` to match the current working revision, providing optimistic concurrency protection (`src/Mod/VibeCAD/VibeCADVibeScript.py:1369-1439`). The supported mutations are:

- exact source replacements, optionally with a parameter merge patch;
- a parameter merge patch alone;
- full replacement of source, parameters, and expected outputs; or
- an editor rebuild of an already staged source (`src/Mod/VibeCAD/VibeCADVibeScript.py:1440-1468`).

Preparation computes the new revision and persists a working candidate before native execution (`src/Mod/VibeCAD/VibeCADVibeScript.py:1470-1506`). Acceptance is synchronous and terminal. Inside one document transaction it deletes the prior accepted object subtree, executes source, enforces the output contract, creates/tags the replacement container and outputs, recomputes, mirrors accepted artifacts, and commits (`src/Mod/VibeCAD/VibeCADVibeScript.py:1615-1681`; `src/Mod/VibeCAD/vibescript_executor.py:697-726,752-776`). Any execution, contract, or acceptance error aborts the transaction, restoring the prior document state (`src/Mod/VibeCAD/vibescript_executor.py:785-805`).

The engine test verifies that a combined source edit and parameter retirement/addition reaches the executor and commits in one update transaction (`src/Mod/VibeCAD/vibecad_tests/test_vibescript_engine.py:805-855`). Thus native geometry acceptance is atomic. The working artifact and accepted geometry are distinct states: preparation may stage a candidate before execution, while only a successful transaction becomes the accepted native model.

### 1.4 Identity and downstream-reference result

There are two different identities:

- **Logical model identity survives:** update retains `model_id`, uses a stale-revision guard, and records a new content revision.
- **Native object identity does not survive:** before running an accepted update, the engine deletes all objects owned by the prior model (`src/Mod/VibeCAD/VibeCADVibeScript.py:1651-1660`), then requires all outputs to have been newly created in that run (`src/Mod/VibeCAD/VibeCADVibeScript.py:1534-1551`) and creates a new container (`src/Mod/VibeCAD/VibeCADVibeScript.py:1553-1555`). The update regression test explicitly expects prior owned objects to be removed and only one replacement container to remain (`src/Mod/VibeCAD/vibecad_tests/test_vibescript_engine.py:775-803`).

Therefore output keys can remain logically stable, and internal names may coincidentally be reused, but neither is object-identity preservation. There is no object-by-object reconciliation, persistent per-feature id, or external-reference retargeting step in the inspected acceptance path.

**Downstream-link verdict:** reliable survival of assembly joints, drawings, FEM references, CAM references, expressions, or `App::Link` targets that point into a regenerated model is **not guaranteed and should currently be treated as not surviving regeneration**. The transaction protects the old model on failure; it does not preserve references after a successful replacement. None of the inspected VibeScript engine/executor/contract tests creates a downstream external link, updates the model, and proves that the link remains valid and semantically attached. The closest update test only asserts deletion and replacement-container count (`src/Mod/VibeCAD/vibecad_tests/test_vibescript_engine.py:775-803`).

### 1.5 Limits and confidence classification

| Limit or behavior | Classification | Evidence and product-scale implication |
|---|---|---|
| Published outputs are a non-empty exact ordered mapping, at most 64 entries. | **Hard contract** | `VibeCADVibeScript.py:38-40,369-404`; `vibescript_executor.py:462-473`. Product structures larger than 64 published components require multiple models or a different boundary. |
| Every published output is one valid solid. | **Hard contract** | `vibescript_executor.py:481-523`. Assemblies/compounds are not single VibeScript outputs. |
| Every output must be created in the current execution. | **Hard contract** | `VibeCADVibeScript.py:1534-1551`. Existing objects cannot be adopted or retained as outputs. |
| One model owns the complete newly created native subtree under a replacement `App::Part`. | **Current implementation choice** | `VibeCADVibeScript.py:1553-1588`; `VibeCADScriptedOwnership.py:10-70`. This is convenient for cleanup but makes the regeneration blast radius the whole model. |
| Updates delete the prior subtree and recreate it wholesale. | **Current implementation choice constrained by a hard output-newness contract** | `VibeCADVibeScript.py:1624-1626,1654-1660`; update test at `test_vibescript_engine.py:775-803`. |
| Source/parameter/output changes use a content revision and stale-write guard. | **Hard contract** | `VibeCADVibeScript.py:1428-1439,1470-1505`. Logical model revisions are controlled even though native object identities are not. |
| A failed run restores the prior document state. | **Hard contract, tested** | `vibescript_executor.py:710-726,785-805`; transaction sequence in `test_vibescript_engine.py:805-855`. This protects failed edits, not successful downstream-link continuity. |
| Container, body, feature, and sketch object identities survive successful regeneration. | **They do not survive in the current implementation** | Prior owned closure is removed and a new container/output set is mandatory. This is deterministic replacement, not reconciliation. |
| External downstream links are retargeted or proven valid after regeneration. | **Missing feature and missing test** | No reconciliation step exists in `VibeCADVibeScript.py:1527-1681`; the inspected update test stops at replacement (`test_vibescript_engine.py:775-803`). Product-scale use must assume breakage until an integration test proves otherwise. |
| Topological references inside or outside the model remain semantically stable after shape changes. | **Missing test** | Edge-query helpers avoid scripts hardcoding names (`vibescript_api.py:8-16`), but no inspected test proves downstream face/edge references survive a model revision. Geometric selectors improve regeneration robustness inside the script; they do not provide external persistent topology. |

### 1.6 Product-scale conclusion from this boundary

VibeScript is a capable **component geometry generator** with atomic acceptance, revision guards, native parametric feature trees, multiple named one-solid outputs, and strong shape postconditions. Its current regeneration unit is nevertheless too coarse to be a reliable product backbone: successful change preserves the logical model id but replaces the native objects that downstream product artifacts would reference. Until stable per-component/per-feature identities and downstream reconciliation are implemented and tested, the safe architecture is to treat accepted VibeScript outputs as replaceable geometry snapshots, not durable product nodes.

## 2. Complete-system workflow matrix

### 2.1 Verdict rubric

Ordered strongest to weakest:

- **Doable** — an existing tool path covers the step end-to-end today, with test or contract evidence.
- **Likely** — a complete tool path exists; remaining friction is operational (manual workbench switching, external binaries, GUI hand-off), not structural.
- **Probable** — a partial tool path exists; the step works for bounded cases but is fragile at product scale, unproven across revisions, or requires workarounds.
- **Possible** — no dedicated tool path; the step is achievable only outside the assistant surface or collides with a structural gap identified in section 1.

Every verdict below is tied to a named tool path or an explicit gap.

### 2.2 Provider-surface architecture (context for every cell)

The tool surface a model sees is computed per turn from the active workbench. Part Design preserves its engine-specific surface. On other supported workbenches, `_surface_tool_names` combines `CORE_PROVIDER_TOOLS`, the native workbench pack, and VibeScript only when VibeScript is the selected Part Design engine. BIM requires the `VibeScriptOnBIMEnabled` opt-in; Test, None, and unknown workbenches do not gain VibeScript (`src/Mod/VibeCAD/VibeCADSession.py`; packs in `src/Mod/VibeCAD/VibeCADWorkbenchTools.py`). Consequences that apply to every scenario:

- **One active workbench per turn remains.** The user still chooses the domain by changing workbenches, but a VibeScript-selected turn can now combine component generation with that workbench's Assembly, FEM, TechDraw, CAM, Material, Spreadsheet, Mesh, or other native tools. Switching domains still starts a new surface on the next turn.
- **API-key providers (OpenAI/Anthropic)** receive the complete active mixed surface directly.
- **ChatGPT subscription** receives a validated, hashed, frozen turn-start declaration of the same complete surface. Native tools may coexist with no more than one scripted engine. Dynamic declarations stay fixed for the turn, while every attempted call is re-authorized against the live active surface; a tool removed by workbench or edit-state drift is rejected and the current available-tool list is returned.
- **Engine exclusivity is explicit.** Selecting build123d, OpenSCAD, or native Part Design prevents VibeScript from joining other workbench surfaces. Existing Part Design engine surfaces remain unchanged.

### 2.3 Tool-path evidence per workflow dimension

**Requirements.** Intent Memory stores validated, provenance-backed entries with categories including `requirement`, `constraint`, `interface`, `verification`, and `open_question`, guarded by a content revision (`src/Mod/VibeCAD/VibeCADIntentMemory.py:22-41,59-80`). It works in both provider modes, including ChatGPT subscription (`VibeCADSession.py:1481-1493`). But entries are free text keyed by category and turn provenance: nothing links a requirement to a CAD object, a joint, or an analysis result, and nothing enforces or verifies one. Requirements capture is real; requirements *traceability* is a structural gap.

**Component geometry.** The VibeScript path assessed in section 1: atomic acceptance, revision guards, up to 64 one-solid outputs per model, multiple models per document (§1.1-1.3). This is the strongest dimension.

**Assembly and motion.** The Assembly pack provides `create_assembly`, `insert_component`, `ground_component`, `create_joint`, `solve`, `list_structure` (`VibeCADWorkbenchTools.py:113-120`). Components are `App::Link` occurrences of same-document objects — insert resolves the source via the *active document* only (`src/Mod/VibeCAD/tool_impl/service/assembly_insert_component.py:72-87`); nested assemblies are supported via `Assembly::AssemblyLink` (`assembly_insert_component.py:105-109`). Accepted component types include `App::Part`, so a VibeScript model container is insertable (`assembly_insert_component.py:198-225`). Joint coverage is broad — fixed, revolute, cylindrical, slider, ball, distance, parallel, perpendicular, angle, rack-pinion, screw, gears, belt (`src/Mod/VibeCAD/tool_impl/service/assembly_create_joint.py:15-29`) — and the solver reports explicit verdicts (`src/Mod/VibeCAD/tool_impl/service/assembly_solve.py:69-98`). Two structural caveats: (a) joints attach to exact component subelement names (`assembly_create_joint.py:88-136`), and (b) VibeScript regeneration deletes the linked container (§1.4), so links and joints into a regenerated model dangle. Assembly is therefore safe only over *frozen* geometry.

**Iteration.** The defining gap. Revising a VibeScript component after an assembly, drawing, or analysis references it replaces the native objects wholesale with no reconciliation and no test coverage (§1.4-1.5). Any "change the wheel diameter after the mower is assembled" workflow must currently be treated as destructive: delete dependents, regenerate, re-insert, re-joint, re-analyze.

**Materials.** `material.list_materials`/`apply_material` assign physical material cards used by FEM; `set_appearance` is display-only (`VibeCADWorkbenchTools.py:314-326`). Assignments live on objects, so regeneration discards them with the replaced subtree.

**BOM.** No BOM tool exists anywhere in the packs. The nearest path is a hand-built `Spreadsheet` sheet (`VibeCADWorkbenchTools.py:99-103,437-446`) with no extraction from assembly structure and no linkage to model revisions. Structural gap.

**Analysis.** The FEM pack covers analysis creation, library materials, constraints on exact subelements, Gmsh meshing, and CalculiX solving with von-Mises/displacement reporting; it fails with instructions when the external binaries are missing (`VibeCADWorkbenchTools.py:279-302`). Regression tests exist (`src/Mod/VibeCAD/vibecad_tests/test_fem_geometry_tool_regressions.py`). This is single-solid, single-document structural analysis: no assembly-level load paths, no CFD, no thermal, and constraint references share the subelement-name fragility above.

**Drawings.** TechDraw pack: page, projected views, dimensions, annotations (`VibeCADWorkbenchTools.py:130-136,463-479`). Views reference 3D objects by name, so drawings of a VibeScript model die on regeneration. Adequate for per-part drawings of frozen geometry; there is no drawing-set, title-block revision, or sheet-numbering management.

**Manufacturing.** CAM pack creates jobs, tools, and operations, but G-code postprocessing to files is explicitly left to the user in the GUI (`VibeCADWorkbenchTools.py:249-263`). Mesh/MeshPart can tessellate solids for printing (`VibeCADWorkbenchTools.py:150-153,339-351`), but no pack exposes an STL/3MF export tool. Molding, sheet-metal, and weldment preparation have no tool path at all.

**Persistence.** The project store is keyed to the active CAD document: project identity derives from the saved file path (or session id when unsaved), and the manifest, conversation threads, `design.md`, and Intent Memory live in one per-document folder (`src/Mod/VibeCAD/VibeCADProject.py:1-9,95-118,708-725`). Reopening a saved document resumes the project and its memory. There is no multi-document product: no cross-document links, no product-level version pinning, no component-document dependency graph.

### 2.4 Scenario matrix (API-key provider)

Scenario profiles: **robot mower** (~30-80 parts, wheel/blade motion, molded deck), **go-kart** (~100-200 parts, tube frame, steering, drivetrain), **3D printer** (~200-500 parts, linear motion, belts, many purchased parts), **drone mothership** (~500+ parts, nested sub-assemblies, aero surfaces, multi-team scale).

| Dimension | Robot mower | Go-kart | 3D printer | Drone mothership |
|---|---|---|---|---|
| Requirements | **Probable** — captured in Intent Memory, no traceability to geometry/verification (§2.3) | **Probable** — same | **Probable** — same | **Probable** — same; scale amplifies the missing product graph |
| Component geometry | **Doable** — housings, blades, wheels well inside the VibeScript contract (§1.2) | **Likely** — tube-frame sweeps and drivetrain parts feasible; weldment semantics absent | **Likely** — many small parts; >64 outputs forces multi-model decomposition (§1.2) | **Probable** — lofted aero solids strain the one-solid script path; Surface pack is a separate manual workbench |
| Assembly / motion | **Probable** — revolute wheels/blade via joint set; frozen-geometry caveat (§2.3) | **Probable** — rack-pinion steering and gears exist as joint types; part count raises solver/reference risk | **Probable** — slider + belt joints exist; hundreds of occurrences untested | **Possible** — nesting is supported (`Assembly::AssemblyLink`) but single-document insertion and identity fragility make this scale structurally unsafe |
| Iteration | **Possible** — regeneration severs joints/drawings/FEM (§1.4); recovery is manual teardown | **Possible** — same | **Possible** — same, multiplied by part count | **Possible** — same; worst case |
| Materials | **Likely** — apply cards once geometry is frozen | **Likely** — same | **Likely** — same | **Probable** — assignments lost per regeneration across many models |
| BOM | **Possible** — no tool; manual spreadsheet only | **Possible** — same | **Possible** — same; purchased-part metadata has no home | **Possible** — same |
| Analysis | **Likely** — single-part FEM (blade, deck) with external binaries; tested path | **Probable** — frame analysis is assembly-level load paths, which FEM pack does not model | **Probable** — single-bracket FEM fine; motion/resonance analysis absent | **Possible** — aero/CFD and system-level structural analysis have no tool path |
| Drawings | **Likely** — per-part TechDraw pages on frozen geometry | **Likely** — same; larger sheet count is manual | **Probable** — hundreds of sheets, no drawing-set management | **Probable** — same, plus assembly drawings of nested structures untested |
| Manufacturing | **Possible** — molded deck has no mold/DFM path; CAM for machined parts ends before G-code files | **Probable** — machined hubs/brackets via CAM (GUI postprocess); tube cutting/welding absent | **Probable** — printed parts blocked on missing mesh-export tool; CAM for machined parts partial | **Possible** — composite/aero manufacturing entirely out of scope |
| Persistence | **Likely** — one document, one project folder, resumable conversation + memory | **Likely** — same, at the comfort limit of one document | **Probable** — one document holding 200-500 parts plus assembly is untested and unpartitionable | **Possible** — multi-document product structure does not exist (§2.3) |

### 2.5 ChatGPT-subscription parity and transport boundary

Subscription mode now receives the same active native-plus-VibeScript capability surface as API-key mode:

| Dimension | Subscription verdict |
|---|---|
| Requirements | **Probable** — Intent Memory compiles through the same project path |
| Component geometry | **Doable/Likely** — VibeScript tools are available whenever VibeScript is selected and the workbench permits them |
| Assembly/motion, materials, analysis, drawings, manufacturing | **Same as API-key mode** — the active native workbench pack and VibeScript coexist in the declared turn surface |
| BIM | **Native BIM tools by default**; mixed BIM plus VibeScript only after `VibeScriptOnBIMEnabled` is explicitly enabled |
| Persistence | **Likely** — the project store is provider-independent |

A subscription turn cannot add a newly surfaced tool declaration after it starts. If the live workbench or edit state removes a declared tool, execution fails closed and reports the current available set. If a later state adds a tool that was not in the snapshot, the model can use it on the next turn. This is a transport integrity boundary, not a domain-capability restriction.

### 2.6 Matrix conclusion

No scenario earns Doable beyond component geometry. The binding constraints are identical across providers and grow with scale: (1) the iteration column is Possible everywhere — the §1.4 identity gap poisons every downstream dimension; (2) BOM and requirements traceability have no tool path; and (3) the single-document product model caps scale around the go-kart level. Cross-workbench VibeScript access removes a provider/tool-availability barrier, but it does not repair regenerated object identity.

## 3. Feasibility verdict and closure roadmap

### 3.1 Verdict

The two questions from the scope get two different answers.

**Geometry feasibility — YES.** VibeCAD/VibeScript can generate the component geometry required by complete products today. The contract is strong and tested: atomic acceptance inside one document transaction, content-revision guards against stale writes, native parametric feature trees with persisted parameter algebra, up to 64 named one-solid outputs per model, multiple models per document, and deep OCCT validity postconditions on every published solid (§1.1–1.3). For the four assessed scenarios, component geometry rates Doable/Likely everywhere except the lofted aero surfaces of the drone mothership (Probable). A user who wants *parts* gets them reliably, in both API-key and ChatGPT-subscription modes.

**Reliable complete-system design feasibility — NO, not in the current architecture.** No scenario in §2.4 earns better than Probable on any dimension past component geometry, and the iteration column — the essence of engineering a product — is Possible (the weakest verdict) in all four scenarios. The proximate cause is singular and structural: a successful VibeScript update deletes and recreates the model's entire native subtree with no reconciliation (§1.4), so every downstream artifact that references the model — assembly links, joints, material assignments, TechDraw views, FEM constraints, CAM sources — must be presumed severed by any component revision. Around that core gap sit two compounding constraints: no BOM or requirements-traceability tool path exists at all, and the product lives in one document with no cross-document structure. Provider surface parity is no longer one of the blockers.

**Subscription-mode verdict:** ChatGPT subscription and API-key users now have the same workbench capability model. The subscription transport freezes and hashes declarations at turn start, but live authorization and current-tool feedback preserve workbench safety during the turn.

**Practical ceiling today:** a disciplined user with either provider mode can take a robot-mower-class product from requirements notes through frozen-geometry assembly, single-part FEM, and per-part drawings — provided components are finalized *before* being referenced downstream, and any later component change is treated as a manual teardown-and-rebuild of its dependents. Above roughly go-kart complexity, the single-document model and identity fragility make the workflow unsafe rather than merely laborious.

### 3.2 Highest-leverage blockers, in priority order

1. **Regeneration identity loss (§1.4).** The single highest-leverage defect: it alone forces the iteration verdict to Possible in all four scenarios and contaminates six other dimensions (assembly, materials, analysis, drawings, manufacturing, persistence-across-revisions). Every other investment is capped until this is fixed. Evidence: `VibeCADVibeScript.py:1651-1660` (pre-update deletion), `1534-1551` (output-newness contract), `test_vibescript_engine.py:775-803` (test pins replacement, not continuity).
2. **No downstream reconciliation or even detection.** There is no dependency scan before deletion, no retargeting after recreation, and no warning to the caller that dependents exist. Even without full reconciliation, *detecting* dependents would convert silent breakage into a refusable, explainable operation.
3. **No product graph.** BOM has no tool path; Intent Memory captures requirements as free text with no links to objects, joints, analyses, or verification results (§2.3). Without a typed component/requirement/interface/verification structure, "complete system design" has no machine-checkable meaning in this codebase.
4. **Single-document orchestration ceiling.** No multi-document product structure or cross-document dependency graph exists. Workbench selection remains human-controlled, although selected VibeScript now accompanies each supported native pack. The document boundary, rather than tool availability, is the primary scale limit.
5. **Manufacturing exit gaps.** No mesh-export (STL/3MF) provider tool despite Mesh tessellation existing; CAM postprocessing to G-code files is explicitly a human GUI action (`VibeCADWorkbenchTools.py:249-263`; `RUNTIME_VERIFICATION.md` "Machine postprocessing remains a human-controlled CAM export action"). Cheap to close relative to the value.

### 3.3 Phased closure roadmap

Each phase has an explicit verification gate; a phase is not done until its gate tests exist and pass.

**Phase 0 — near-term reliability (make destruction visible, close cheap exits).**

- *P0.1 Dependency guard:* before the pre-update deletion in the acceptance path, walk `InList`/dependent objects of the owned subtree; if external dependents exist, fail the update with a structured error listing them (or proceed only with an explicit `force` acknowledgment). Converts blocker 2 from silent to explicit.
- *P0.2 Deterministic output naming:* reuse the prior object name per output key on regeneration. Precedent exists in this codebase — the OpenSCAD engine already keeps accepted output keys stable across edits, never recycles removed keys (`RUNTIME_VERIFICATION.md`, "Output identity"), so this is an implementation choice, not new research.
- *P0.3 Material re-application:* persist material-card assignments keyed by output key in the model artifact and re-apply after successful regeneration.
- *P0.4 Mesh export tool:* add an STL/3MF export provider tool to the Mesh pack, closing the 3D-printing exit (blocker 5).
- **Gate G0:** tests T1–T3 below pass; guardrail suite still green.

**Phase 1 — identity and reconciliation (fix blocker 1).**

- *P1.1 Reconciliation map:* on successful regeneration, emit an output-key → (old object, new object) map and retarget `App::Link` occurrences (including `Assembly::AssemblyLink`) to the replacement objects inside the same transaction.
- *P1.2 Joint re-attachment:* re-resolve joint attachments through geometric selectors (the `vibescript_api` edge/face query approach, `vibescript_api.py:8-16`) instead of exact subelement names, then re-run `assembly.solve` and report the verdict as part of the update result.
- *P1.3 View/constraint retargeting:* apply the same reconciliation to TechDraw view sources and FEM constraint references, with per-reference success/failure reporting.
- **Gate G1:** tests T4–T7 pass; the iteration column in §2.4 can be honestly upgraded from Possible to Probable/Likely.

**Phase 2 — product graph and scale (fix blockers 3–4).**

- *P2.1 Typed product structure:* components, interfaces, requirements, and verification records as first-class linked entities (extending Intent Memory's revision-guarded store) with links to model ids, joints, and analysis results — making requirements traceable and checkable.
- *P2.2 BOM extraction:* derive BOM from assembly occurrence structure plus per-component metadata (purchased-part fields included), exportable to Spreadsheet; regenerate on demand, pinned to model content revisions.
- *P2.3 Multi-document components:* component documents with dependency pinning and cross-document link resolution, lifting the single-document ceiling.
- *P2.4 Product orchestration:* build explicit cross-document component and dependency management. Workbench surfaces already combine the selected VibeScript engine with the active native domain; the remaining problem is coordinating durable product artifacts, not exposing more tools at once.
- **Gate G2:** tests T8–T10 pass.

### 3.4 Concrete success tests

| ID | Test (all as automated integration tests under `vibecad_tests/`) | Proves |
|---|---|---|
| T1 | Create model → insert into assembly → attempt update → structured dependent-listing error (no `force`) | P0.1 dependency guard |
| T2 | Apply material to output → update model → material card present on replacement object | P0.3 |
| T3 | Tessellate + export STL via provider tool → valid non-empty file | P0.4 |
| T4 | Create model → `App::Link` occurrence → update model → link resolves to replacement object, document recomputes cleanly | P1.1 |
| T5 | Assembly with revolute joint on model output → update model changing dimensions → joint re-attached, `assembly.solve` returns success verdict | P1.2 |
| T6 | TechDraw page with projected view of model output → update → view renders from replacement object | P1.3 |
| T7 | FEM constraint on model face → update → constraint re-resolved or explicit per-reference failure report | P1.3 |
| T8 | Requirement entry linked to model id and joint → query returns the full trace; deleting the joint flags the requirement unverified | P2.1 |
| T9 | 3-level nested assembly across ≥2 documents reopens with all links resolved | P2.3 |
| T10 | End-to-end mower scenario: ≥10 models, assembly with motion joints, one component revision, BOM regeneration, per-part drawing — no manual repair steps | Whole-roadmap acceptance |

### 3.5 Reconciliation with existing tests

**What existing tests actually prove (claims in this document that are test-backed):**

- *Engine/executor/contract behavior* — 247 targeted tests passed during §1 verification: atomic accept/rollback, revision guards, output contract enforcement, source policy. `RUNTIME_VERIFICATION.md` additionally documents transactional parity (open→commit / open→abort with no orphans) and OpenSCAD output-key stability as automated contracts.
- *Tool-surface and subscription guardrails* — `test_tool_surface_guardrails.py` and `test_codex_subscription.py` cover representative mixed workbenches, BIM default and opt-in behavior, single-engine enforcement, frozen schema/name integrity, and live call revocation.
- *Provider wiring* — `test_provider_subprocess.py` pins that VibeScript guidance and model summaries follow actual surfaced capability, remain absent when unavailable, and are not duplicated in Part Design. It does **not** prove downstream-reference survival.
- *FEM/geometry regressions* — `test_fem_geometry_tool_regressions.py` (21 tests) covers face-normal/angle measurement, query schema validation, and material-card search. It proves the query and material *lookup* paths, not FEM-reference survival across regeneration.

**Untested high-risk behavior (explicitly not proven anywhere in the inspected suites):**

1. Survival of any external reference (assembly link, joint, drawing view, FEM constraint, material assignment, expression) across a successful VibeScript regeneration — the central risk of this assessment, with zero coverage.
2. Assembly behavior at product scale: no test exercises more than a handful of occurrences, nested `AssemblyLink` reopen, or solver behavior over hundreds of joints.
3. Document save/reopen with a mixed population of VibeScript models plus assembly plus drawings — persistence verdicts in §2.4 above "one model" are extrapolation.
4. Multi-model documents near the 64-output or many-model regime claimed feasible for the 3D-printer scenario.
5. End-to-end FEM (Gmsh + CalculiX) is environment-dependent by design (`RUNTIME_VERIFICATION.md`, "Environment-Dependent Checks"); CI-level coverage stops before real solves.

**Bottom line.** VibeCAD today is a well-tested component-geometry system wrapped in a per-turn tool surface, with untested seams exactly where complete-system design lives. The verdict is not that system design is impossible — it is that it is currently *unprotected*: nothing detects, prevents, or repairs the reference breakage that product-scale iteration inevitably causes. Phase 0 makes the breakage visible, Phase 1 makes revision safe, Phase 2 makes the product — not just its parts — a first-class object. Until Gate G1 passes, complete-system work should treat every accepted VibeScript model as a frozen snapshot and every component revision as a planned teardown.
