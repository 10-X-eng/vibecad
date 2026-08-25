# FEA Tool Sharpening

## Objective

Make a correct engineering study the obvious workflow for a person or model that
knows analysis, not VibeCAD. Start with working Elmer and OpenFOAM foundations;
then sharpen the human and AI surfaces against real solver runs.

## Rules

1. Use ordinary outcome requests. Never add tool names, call order, corrective
   steering, forced calls, retries, or benchmark-specific hints.
2. Prefer correctness over speed. Give local models enough time to finish.
3. Publish only state that changes the next decision: active study, exact
   geometry and selections, materials, physics, mesh, solver availability, jobs,
   results, stable identities, and revision.
4. Keep descriptions short and affirmative. Put validation in schemas and make
   failures name the rejected field, accepted values, relevant state, and repair.
5. Give each engineering action one obvious, precisely typed contract. Use
   natural names and units, stable references, scalable collections, and no
   undocumented limits or duplicate legacy paths.
6. Do not encode special cases or geometric heuristics. Correct the shared
   production contract used by both the ribbon and AI.
7. Return only what changed, the durable result identities, solver/job status,
   the next usable state, and exact diagnostics. Read-only calls never advance
   structural revision.
8. Benchmark from an inspected, immutable fixture with no target study, mesh,
   solver input, or results. Work on a copy and save before shutdown.
9. Judge saved artifacts and solver traces, not assistant prose. Count rejected
   and corrected calls. Independently verify geometry, mesh, boundary coverage,
   solver convergence, result import, units, tree ownership, and reopen behavior.
10. Tune with unsteered Qwen first, then GPT-5.6 Terra at high reasoning. Model
    limitations are acceptable only after the tool and context contracts are as
    clear and compact as we can make them.

## Delivery order

1. Make Elmer and OpenFOAM installed, discoverable, runnable, cancellable, and
   packageable.
2. Give the Analysis ribbon a study-first path: choose physics, assign domain and
   materials, set boundaries, mesh, solve, inspect results.
3. Prepare the computer-fan CFD fixture. Improve geometry with GPT-5.6 Sol at
   xhigh only if independent inspection shows the source is not simulation-ready.
4. Exercise multiple reusable structural, thermal, fluid, coupled, and scale
   cases so no contract is tuned to the fan alone.
5. Keep a running evidence log with prompts, provider/model settings, tool traces,
   artifacts, deterministic checks, failures, and production corrections.

## Acceptance

A workflow is sharp when both a new user and an unsteered model can start from the
same prepared geometry, create the intended study without guessing VibeCAD
internals, run the real selected solver, understand failures, inspect meaningful
results, save and reopen the document, and repeat the task without rejected tool
calls caused by VibeCAD's contract.

## Evidence log

### Analyze provider state

- A blank Analyze turn previously serialized 2,068 bytes of empty FEM
  collections. Its decision state is now 210 bytes.
- One declared steady-fluid study with no assignments previously serialized
  4,618 bytes, including duplicate analysis records and empty state for every
  FEM family. The same exact study now serializes 786 bytes: one study target,
  its intent, and its readiness blockers.
- The durable document snapshot, ribbon state, and human Study Setup data are
  unchanged. Compaction occurs only after the full state has authorized the
  provider surface.

### Analyze operation surface

- A declared steady-fluid study with no assignments previously advertised
  31,186 bytes of family schemas. Exact study-state projection now advertises
  23,254 bytes: creation operations remain, while edits without a live target
  are absent.
- The production GUI gate creates an analysis, continues on the new setup
  surface, declares steady fluid physics, and continues again on the fluid
  surface. Its frozen dispatch contract matches the provider-visible contract
  at each turn.
- Registered capabilities and human ribbon actions are unchanged. The provider
  projection is rebuilt between turns from durable physics, inventory counts,
  exact target kinds, and explicit truncation state.
- A trial that expanded every multi-operation family into top-level exact
  `oneOf` branches made the local model serialize nested objects as strings. It
  was removed rather than weakening runtime validation to accept malformed
  calls.
- The compiled inventory is 104,896 bytes. A live fluid study publishes 23,974
  bytes and 33 tools. Its focused `analyze.faces` tool reads a bounded page of
  exact current FaceN names, surface kinds, areas, centers, bounds, and planar
  normals without inflating unrelated operations.

### Rectangular-duct CFD baseline

- The first unsteered Qwen 3.5 9B run started from one inspected 200 x 60 x
  40 mm fluid-domain solid and this ordinary request: “Set up and run a steady
  incompressible air-flow analysis through the supplied rectangular duct. Flow
  travels in +X at 5 m/s, the outlet is at 0 Pa gauge, and the remaining faces
  are no-slip walls. Use a suitable mesh, solve it, and report the pressure drop
  and maximum velocity.” It reached the 1,800-second limit after 90 tool calls
  without producing a mesh, complete boundaries, solver, or results.
- Its saved FCStd contains the unchanged fluid domain, two analyses, one initial
  velocity, and one fluid material. The trace repeatedly guessed face names and
  state hashes because the provider exposed source topology but no exact FaceN
  identities or geometric properties. It also mixed operation-specific fields
  in the compact fluid, mesh, model, and inspection families.
- A second run tested exact top-level operation branches and was worse: nested
  analysis and reference objects were emitted as strings. Its saved artifact
  contains only two analyses and one OpenFOAM solver. This isolates schema
  expansion as a model-facing regression, so those branches were removed.
- A third run used the unchanged prompt and immutable source with the focused
  face reader. The model successfully read all six exact faces and created one
  study, initial velocity, outlet pressure, fluid material, and OpenFOAM solver.
  It still timed out before meshing. The trace isolates three shared contract
  defects: provider state records are not ready-to-use exact targets, mutation
  results expose the next analysis target inconsistently, and the multi-operation
  mesh/inspection families invite fields from the wrong operation. The saved
  artifact is retained for the next production correction.

### Production corrections

- Analyze now publishes only operations applicable to the exact current study
  between turns. A fully conformal shared solid domain omits redundant
  connection creation; separate solids and existing connections retain the
  connection tool.
- Whole-domain shared and separate solid creation is available through the same
  production operation used by the ribbon and assistant. Shared creation uses a
  conformal Boolean-fragment CompSolid while preserving the source geometry.
- Repeated face references stored by FreeCAD in one `PropertyLinkSubList` entry
  are flattened into exact endpoints for create, inspect, and edit. This covers
  connections between two faces of one analysis domain without a special case.
- CalculiX result ranges now carry explicit pipeline provenance: the modern
  CalculiX pipeline remains in FreeCAD engineering units (mm and MPa), while the
  legacy CcxTools importer is converted from SI. Empty result graphs are
  rejected.
- Load values use the same numeric canonicalization for preflight, document
  storage, readback, and postcondition verification.
- High-entity provider state was exercised with 256 solids. Exact assignments,
  contextual tool transitions, save, and reopen remained usable while the
  provider state contracted from 16,900 to 5,368 bytes after study completion.

### Real solver gates

- A three-solid conformal bridge gate generated a real Gmsh mesh, ran CalculiX,
  imported displacement and stress, and reopened with all three source solids
  represented by one stable analysis domain.
- A two-material bimetal gate ran coupled thermal-mechanical CalculiX and
  reopened with temperature and displacement fields in exact units.
- ElmerGrid and ElmerSolver run as detached processes, import a real result,
  rerun without blocking the interface, and preserve the result after reopen.
- OpenFOAM Foundation 14 runs both laminar and k-omega SST cases in the shared
  background lifecycle. Imported pressure, velocity, turbulence, oriented
  boundary flux, continuity, GFA, EFA, and discharge coefficient survive
  save/reopen and use the same result state for the ribbon and assistant.

### Unsteered cross-model acceptance

- Qwen 3.5 9B and GPT-5.6 Terra high received the same ordinary bridge request
  with no tool names, call order, retries, or benchmark instructions. Both chose
  a shared conformal domain, applied steel, support, deck pressure, gravity,
  meshing, and a real solver from the contextual surface.
- The final Terra artifact reopened with 10,203 result points and 5,647 cells.
  Its exact ranges were 0.738 mm maximum displacement and 628.5 MPa maximum von
  Mises stress. The final Qwen artifact independently completed a real Elmer
  solve and reopened with imported results.
