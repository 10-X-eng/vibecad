# Builder Handoff — VibeCADAero Reconciliation Pass 03 Correction 01

## Mission

Implement the full high-fidelity Aero architecture **into the then-current VibeCAD**, reusing host Native/evidence/job patterns that now exist and preserving all current low-order Aero functionality.

## Frozen design baseline

`halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`

Do not code against this SHA blindly if upstream has moved. Reconcile first.

## Builder rules

- Do not delete current VibeCADAero capabilities.
- Do not create a parallel Aero workbench/product.
- Do not bypass `VibeCADAero.py`/Native authority with raw exec.
- Do not create a second generic preview Apply/Reject controller.
- Do not make CFD jobs Native previews.
- Do not equate solver completion with qualification.
- Do not equate exact B-rep with watertight/CFD-ready/manufacturable.
- Do not generalize FluidX3D component restrictions to VibeCAD/Aero.
- Do not add purpose/license enforcement controls; keep the single informational `I understand.` notice.
- Do not silently drop advanced scope because it is later in dependency order.
- Do not implement production Aero scheduling/persistence as a parallel runtime.
- Do not rewrite working FEM while extracting host mechanics.
- Do not combine process extraction, persistence migration, concurrency expansion and solver-physics changes in one PR.
- Do not let worker threads touch live FreeCAD objects.
- Do not move existing FEM input writers off the current thread boundary during generic extraction.
- Do not shadow-test by launching legacy and new solver processes together. Shadowing is observation only.
- Do not use document label/path as durable result-attachment authority.
- Do not delete compatibility modules merely because repository-local imports disappear.
- Do not mark restarted jobs successful from leftover files/PIDs without execution evidence.
- Do not permit duplicate result publication after retry/reconnect/callback replay.

## Execution sequence

### Step 0 — live re-reconciliation

Freeze current `main`; compare from Pass-03 SHA. Re-read VibeCADAero, Native preview control/state/dispatch, `VibeCADNativeBackground*`, detached FEM execution/process/state, mutation boundary, artifact/evidence seams and build files. If upstream has already generalized any host-runtime seam, adopt that live work instead of replaying this design mechanically.

### Step 1 — characterize current FEM/background behavior (no runtime change)

Add the tests in `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md`. Capture existing process, input digest, command/env, stale checks, result graph/History, receipts, `native.job`, cancel/timeout and platform behavior, including **golden normalized lifecycle traces**. **This step adds no capability.**

### Step 2 — introduce host Analysis contracts/facades with old implementation underneath

Add domain-neutral `VibeCADAnalysis*` contracts/ports in the host. Keep existing public modules/functions/actions. Add an **observation-only shadow trace** if useful, but never run a second solver or second publication path. No persistence and no solver behavior change.

### Step 3 — extract local process mechanics only

Move generic process execution from detached FEM to `LocalProcessProvider` (or live naming equivalent) behind the old facade. Preserve source-verified argv/env/cwd, polling, timeout/cancel, direct-process cleanup and error compatibility. Characterize descendant process-tree behavior first; if child leakage is confirmed, harden it in the dedicated correctness slice before making the new provider canonical. Do not touch FEM state or result import.

### Step 4 — extract artifact/input sealing only

Generalize safe detached workspace + hashing/manifests behind compatibility behavior. Preserve current FEM digest semantics while introducing richer manifests additively. No scheduler/persistence change.

### Step 5 — extract generic orchestration behind NativeBackground

Use `NativeBackgroundManager` as the compatibility surface. Internally route prepare/worker/commit through the generic Analysis Runtime while preserving one active job/document and current `native.job` behavior.

### Step 6 — migrate FEM one solver path at a time

CalculiX first, then other paths according to live test coverage. Every migration must prove identical input/command/env/currentness/result graph/History/receipt behavior. Existing FEM remains the oracle.

### Step 7 — stabilization interval

Run/merge the host runtime with FEM before Aero depends on it. Resolve lifecycle/threading defects here without simultaneously debugging CFD.

### Step 8 — add durable host job metadata/artifact persistence separately

Introduce versioned transactional local persistence and restart/orphan/reconnect semantics. Keep large artifacts outside FCStd. Do not change solver physics or concurrency in this step.

### Step 8A — add durable publication authority separately

After persistence is stable, implement the publication coordinator in `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`. Persist inert submission/publication descriptors only. Rebind exact `Document.Uid`, validate exact job/artifact provenance and domain dependencies, acquire fresh Native mutation authority, and make publication replay-idempotent. Keep current FEM publication semantics unchanged during this step; prove the coordinator independently before Aero depends on it.

### Step 9 — close the Aero/Native repair revision gap independently

Thread real host revision into `/v1/aero` repair propose/apply and converge CAD-changing Aero authorization onto host preview/apply/reject. Preserve Aero geometry fingerprint as domain evidence.

### Step 10 — host-aligned Aero evidence, geometry readiness and frames

Integrate `not_solved`, `model_unqualified`, qualification, exact/derived/presentation, current/stale; complete CAD↔body↔solver frames, references, geometry readiness and correspondence.

### Step 11 — Aero becomes the second host Analysis Runtime client

Implement Aero adapters for case preparation, dependency snapshot/currentness, result parsing and publication. At this point `AeroJobStore` is no longer production authority; retain only compatibility/migration reading if needed.

### Step 12 — OpenFOAM/CfdOF complete baseline

Build the first complete conventional external-aero case end-to-end through the host runtime: geometry → domain → mesh → BC/model → solve → convergence → forces/coefficients → fields → evidence. Qualify against benchmark cases.

### Step 13 — vendored FluidX3D complete baseline

Integrate pinned vendor source/build/bridge through the same host runtime and provider contract. Validate scale/domain/boundaries/force/torque/fields/qualification. Keep external bridge override.

### Step 14 — common field/result viewer

Unify high-fidelity fields under Aero's domain result UI while host jobs/artifacts remain generic.

### Step 15 — explainable routing + Kaggle provider

Add Kaggle only after local provider + durable host job identity/reconnect are stable. Solver routing remains separate from provider selection.

### Step 16 — qualification engine and high-Re FluidX3D

Versioned benchmark registry/envelope, then collision/SGS/wall/domain/averaging/grid-convergence qualification.

### Step 17 — moving geometry and propulsion interaction

Rigid bodies, motion laws, revoxelization/moving boundaries, rotor/prop fidelity ladder and feedback.

### Step 18 — unsteady / complete 6DOF

Validated dynamic-stall/strip models, lateral/control/propulsion/gust providers, JSBSim production path retained.

### Step 19 — aeroelasticity / FSI

Structural authority, mapping, partitioned coupling, timestep/relaxation/convergence and flutter validation.

### Step 20 — advanced diagnostics and refinement

Wake/mid/far-field, uncertainty/grid convergence, parameter/refinement workflows and controlled engineering-knowledge accumulation built as compositions of host jobs rather than new schedulers.

## Required evidence after every implementation step

- exact upstream SHA;
- changed file list;
- tests actually run;
- solver/runtime versions;
- artifacts/hashes where relevant;
- known limitations/qualification state;
- no claim inflation.

## Stop conditions

Stop the integration slice (not the project) and reconcile if:

- upstream changes the relevant host seam while coding;
- a solver API assumption proves false;
- test evidence contradicts the plan;
- geometry/frame/reference identity is ambiguous;
- a result cannot be tied to source/case/version/settings;
- the implementation would require bypassing host authority;
- an extraction step changes existing FEM observable behavior without an explicitly separate approved behavior-change scope;
- a worker would require live FreeCAD object access;
- a durable schema migration has no rollback/recovery story.

## Mandatory pre-write host-runtime checklist

Before modifying upstream for the host Analysis Runtime:

1. Freeze live `main`; do not implement directly from `df07a5e…`.
2. Reconcile the current CMake manifest and every extraction-boundary file against this package.
3. Read `DESTRUCTIVE_CHANGE_AUDIT.md` and treat its DO-NOT-EXTRACT list as hard architecture law.
4. Add missing characterization tests first.
5. Preserve `analyze.solver_execution` and `native.job` public compatibility.
6. Implement the atomic commit gate before routing real FEM jobs through the new manager.
7. Harden local process-tree ownership before claiming robust cancellation/timeout.
8. Keep durable descriptors free of FreeCAD/Python live runtime objects.
9. Retain a legacy FEM rollback route until A/B parity and rollback exercise pass.
10. Do not wire Aero until FEM is green through the extracted runtime.

The exact staged sequence is `HOST_RUNTIME_EXTRACTION_SEQUENCE.md`; release gates are `HOST_RUNTIME_REGRESSION_GATES.md`.


## Mandatory migration companion documents

- `SOURCE_VERIFIED_HOST_RUNTIME_BASELINE.md`
- `HOST_ANALYSIS_RUNTIME_CONTRACT.md`
- `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md`
- `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md`
- `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`
- `HOST_ANALYSIS_RUNTIME_ARCHITECTURAL_DECISIONS.md`
- `NON_DESTRUCTIVE_MIGRATION_MATRIX.md`
- `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md`
- `HOST_ANALYSIS_RUNTIME_RISK_REGISTER.md`
