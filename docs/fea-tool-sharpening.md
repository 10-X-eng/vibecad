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
