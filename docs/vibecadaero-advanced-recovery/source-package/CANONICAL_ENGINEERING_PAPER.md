# VibeCADAero: A Governed Multi-Fidelity Aerodynamics and Simulation Architecture

**Reconciliation Pass 03**  
**Frozen upstream:** `halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`  
**Status:** canonical target paper and builder design; upstream remains untouched

## Abstract

The supplied design history began with a compelling objective: a CAD model should be capable of running its own wind-tunnel-style analysis. Geometry creation, case declaration, voxelization or meshing, fluid solution, aerodynamic force extraction, and pressure visualization should form one continuous engineering workflow rather than a chain of manual exports into unrelated tools. During the discussion this objective expanded into local GPU Lattice Boltzmann Method (LBM) computation, Kaggle accelerator offload, OpenFOAM/CfdOF integration, high-Reynolds-number analysis, moving geometry, mesh conversion, dynamic stall, strip theory, unsteady coupling, six-degree-of-freedom (6-DOF) dynamics, and advanced drag decomposition.

The discussion also accumulated contradictions. Multiple later code dumps called themselves canonical while omitting accepted capabilities; some APIs were assumed rather than verified; result and configuration classes were proposed that would replace richer objects already present in the live repository; Kaggle quota and job behavior were mocked; force and coordinate conventions drifted; scalar and vectorized dynamic-stall implementations no longer represented the same model; and “full 6-DOF” was used for a rigid-body model whose lateral aerodynamics were zero.

This paper reconciles those ideas against the frozen live VibeCAD upstream. The resulting architecture is not a new Aero subsystem. It is an extension of the existing VibeCADAero authority, configuration, reporting, evidence, repair, ribbon, JSBSim, packaging and test infrastructure. The central architectural advance is the separation of **solver semantics** from **compute location**, backed by immutable case/result/artifact contracts and method-specific evidence. The complete accepted capability remains in scope; implementation phases are dependency and validation order, not scope deletion.

---

## 1. Existing system: what VibeCADAero already is

The frozen upstream is significantly more mature than the early design conversation assumed. VibeCADAero already includes a real public orchestration module, geometry/configuration resolution, coordinate mapping, NeuralFoil section analysis, AeroSandbox AeroBuildup/VLM analysis, stability derivatives, hover/cruise engineering estimates, a FreeCAD `AeroReport`, report/assistant output, bounded repair proposals, non-destructive preview/apply behavior, JSBSim export, native ribbon commands, build manifests and dedicated tests.

This matters because a clean architecture must build *through* those seams. Replacing `AeroResults.py` with a new dataclass, adding a second independent preferences system, or creating a separate Aero workbench would not be additive; it would fragment a working product.

The canonical direction is therefore:

> **VibeCADAero becomes a governed multi-fidelity aerodynamic reasoning, analysis and simulation domain whose low-order solvers, high-fidelity CFD solvers, unsteady engineering models, remote compute providers, field artifacts and flight-dynamics outputs share one authority, one configuration lineage, one evidence system and one result provenance model.**

---

## 2. Product objective

The complete target experience is a user or agent being able to request, from the active CAD context:

```text
build / select vehicle geometry
        ↓
resolve geometry, mass, frame and reference quantities
        ↓
declare aerodynamic case
        ↓
select solver automatically or explicitly
        ↓
select execution location automatically or explicitly
        ↓
mesh / voxelize / write case
        ↓
solve
        ↓
collect forces, moments, coefficients, convergence and fields
        ↓
paint pressure/Cp and inspect flow
        ↓
compare fidelity levels / validate
        ↓
feed governed results into reports, repair proposals, JSBSim or later fitting
```

The user should not need to understand whether one result was produced by a low-order local Python solver, an external OpenCL LBM executable, a CfdOF-authored OpenFOAM case, or a remote accelerator. They *should* always be able to inspect that provenance.

Zero-click after onboarding is a UX requirement, not permission to hide engineering state. Remote-upload destination, current provider quota when available, case settings, result provenance and evidence ceiling must remain visible and auditable. Third-party terms remain accessible through the informational notices; they are not converted into runtime purpose controls.

---

## 3. Fidelity is a ladder, not a replacement strategy

### 3.1 Level 0 — authoritative geometry/configuration state

All analysis begins from the same resolved VibeCAD geometry and metadata. The existing `AeroConfig` and `CadAeroFrame` are preserved. The high-fidelity extension adds explicit atmosphere, frame/origin transforms, geometry hash and mesh provenance.

The project should never reach a state in which NeuralFoil uses one chord, OpenFOAM uses a different manually typed reference length, FluidX3D uses an STL maximum dimension, and JSBSim uses a third value without the report making those differences explicit.

### 3.2 Level 1 — NeuralFoil

NeuralFoil remains the rapid section model. It is valuable for airfoil behavior, polar priors, quick checks and inputs to later reduced-order/unsteady models.

### 3.3 Level 2 — AeroSandbox AeroBuildup/VLM

The live AeroSandbox path remains the rapid 3-D model. Its stability derivative path is particularly important because those derivatives can later support better 6-DOF force providers and JSBSim model fitting.

### 3.4 Level 3 — stateful unsteady engineering models

Steady polars cannot describe rapid pitch, rotor/blade kinematics or dynamic stall. A stateful dynamic-stall/strip model therefore sits between low-order steady analysis and CFD. Its purpose is fast time-domain engineering prediction, not replacement of CFD.

The earlier code is retained conceptually but corrected in status: it was not a complete validated Leishman–Beddoes implementation. The overlay names the present code a reduced engineering model and makes the canonical target a literature-calibrated dynamic-stall model of explicitly documented formulation, validated against published oscillating-airfoil data. Model naming must follow what is actually implemented and validated.

### 3.5 Level 4 — vendored GPU LBM

FluidX3D is a strong technical match for the original “CAD file runs its own wind tunnel” idea: Cartesian voxelization, GPU/CPU OpenCL execution and source-level force-field APIs make rapid volumetric flow around complex geometry plausible.

However, two facts govern integration.

First, VibeCAD must use verified current APIs. The canonical adapter therefore uses an external process around verified C++ methods rather than the unverified Python `Config()` interface assumed in earlier drafts.

Second, FluidX3D's current license is not a generic permissive open-source license. It contains commercial, military, AI-source-training and other conditions. VibeCAD nevertheless vendors FluidX3D as the normal LBM backend, preserving the upstream license and origin documentation, and also supports an explicitly configured external bridge override. VibeCAD does not infer or police usage categories; users and distributors are responsible for reading and following the documented requirements. The replaceable interface still allows additional LBM/GPU solvers without changing the case/result model.

### 3.6 Level 5 — OpenFOAM through CfdOF

OpenFOAM is the natural high-fidelity conventional CFD path. CfdOF already solves much of the FreeCAD-native case-authoring problem: analysis objects, mesh objects, physical models, boundary conditions, case writing and solver integration.

The correct VibeCAD strategy is therefore not to recreate a second CfdOF. It is to provide a higher-level VibeCADAero external-aero template and result adapter around CfdOF's current APIs, while maintaining a stable solver-neutral `AeroCase` and `CFDResult` above it.

For a high-Re UAV case, OpenFOAM/CfdOF should become the reference path for RANS and, where justified, LES/DES-style analyses. LBM remains complementary for rapid GPU feedback and specialized workflows.

### 3.7 Level 6 — diagnostic decomposition

Total drag alone is insufficient for advanced design work. The accepted roadmap retains:

- surface pressure/shear integration;
- wake momentum-deficit cross-checks;
- induced/profile/wave/spurious drag decomposition;
- mid-field/far-field formulations;
- convergence/refinement uncertainty;
- cross-solver disagreement analysis.

These are not MVP decorations. They are advanced diagnostics whose implementation depends on reliable field extraction and validation infrastructure.

---

## 4. The crucial separation: solver vs. compute provider

A major flaw in the early design was treating “Kaggle LBM” as if Kaggle were part of the physics solver. This makes routing, provenance and future growth harder.

The canonical architecture separates two questions:

1. **What physics/model should solve this case?**
   - NeuralFoil
   - AeroSandbox/VLM
   - dynamic stall/strip theory
   - FluidX3D LBM
   - OpenFOAM
   - future solver

2. **Where should a prepared solver job execute?**
   - in-process/bundled Python where appropriate
   - local external process
   - Kaggle accelerator
   - future SSH/Slurm/HPC/managed worker

The solver writes a deterministic job bundle. The compute provider executes that bundle and returns an execution receipt. The solver parses its own output into the canonical result schema.

This gives VibeCAD a stable path to automatic routing without allowing cloud-specific details to contaminate aerodynamic semantics.

---

## 5. Kaggle: native remote compute without fake guarantees

The user requirement is valid: after one-time setup, heavy compatible jobs should be able to use Kaggle accelerators without manual notebook editing.

The earlier implementation was not adequate. It hard-coded approximately thirty hours, pretended to decrement remaining quota locally, generated dummy force values, and left kernel push/output commented out.

The canonical provider instead uses the current Kaggle CLI as the service compatibility boundary:

```text
query live quota
    ↓
prepare private kernel metadata + solver bundle
    ↓
kernels push (accelerator selected)
    ↓
kernels status polling
    ↓
kernels output download
    ↓
verify expected result artifact/hash/schema
```

No fixed quota amount belongs in VibeCAD. A future scheduler can forecast expected consumption using measured telemetry, but forecasting must never replace actual quota querying.

Kaggle also cannot magically make every solver portable. A locally compiled FluidX3D executable is not remotely executable merely because its job was routed to Kaggle. The solver adapter must explicitly prepare a Kaggle-runnable bundle: source/build instructions or a permitted artifact, dependencies and an entrypoint. That remote portability is a solver capability advertised to routing policy.

---

## 6. Geometry, meshing and solver correspondence

### 6.1 Geometry is an artifact, not just a filename

Every solver geometry should carry:

- source document revision;
- selected object names;
- source units;
- solver units;
- tessellation parameters;
- content hash;
- coordinate frame;
- transform/origin provenance.

A cached CFD result is reusable only when all relevant geometry/case/solver inputs match.

### 6.2 MeshPart path

The fastest VibeCAD→LBM path uses the existing FreeCAD geometry kernel plus MeshPart tessellation to emit an STL. The exact file writer mode is verified at runtime rather than assumed.

### 6.3 Gmsh path

Gmsh is retained as an optional meshing tool. Its initial canonical role is controlled surface meshing/conversion, with later extension to volume/preprocessing tasks where appropriate.

The converter is topology-aware. Linear triangles pass through; quads split deterministically; 6-node quadratic triangles subdivide to four linear triangles using mid-edge nodes. Unknown higher-order topology is rejected rather than guessed.

### 6.4 OpenFOAM fluid region

The aircraft surface is not the OpenFOAM volume mesh. External aerodynamics requires a fluid domain around the body, far-field/inlet/outlet boundaries, refinement regions and near-wall strategy.

Automatic external-domain generation is therefore a real subsystem with its own acceptance tests:

- bounding box and configurable upstream/downstream/lateral clearances;
- boolean fluid-region construction;
- robust face classification after topology changes;
- wake refinement region;
- near-body/surface refinement;
- optional boundary layers;
- mesh quality gates.

This subsystem should reuse CfdOF's mesh objects rather than outputting ad-hoc OpenFOAM dictionaries whenever CfdOF can represent the requirement.

---

## 7. Coordinate systems are a first-class engineering contract

Many numerical “bugs” in aero systems are actually frame bugs.

The canonical external contract uses aircraft body axes:

- `+X`: forward
- `+Y`: right
- `+Z`: down

The existing VibeCAD CAD frame is not silently assumed to be this frame. The live `CadAeroFrame` already captures important current model orientation and must be the source of truth when the builder adds a full 3-D CAD→body transform.

The canonical result stores body-axis force and moment. Aerodynamic coefficients are derived from an explicit basis:

- drag direction from freestream;
- lift direction from configured “up” projected perpendicular to freestream;
- side direction completing an orthogonal basis.

This makes angled flows and sideslip valid without changing backend-specific formulas.

Moment reference requires equivalent care. FluidX3D can return torque about an object center; OpenFOAM forceCoeffs can use a configured center of rotation; VibeCAD reports have an aerodynamic reference point. Those are not interchangeable. Pass 01 deliberately withholds FluidX3D moment coefficients until that transform is explicit.

---

## 8. Forces, coefficients and validation

For dimensional force `F`, freestream density `ρ`, speed `U` and reference area `S`, the usual nondimensional force coefficients use dynamic pressure:

```text
q = 1/2 ρ U²
C_D = D / (q S)
C_L = L / (q S)
C_S = Y / (q S)
```

Pitch moment uses reference chord `c`; roll/yaw moments conventionally use appropriate span/reference lengths.

The important architectural decision is that these calculations occur from the canonical case/result contract, not independently inside each solver bridge with hard-coded values. This makes LBM and OpenFOAM directly comparable.

A result is not “validated” merely because it contains a plausible coefficient. Validation includes, as applicable:

1. solver process success;
2. residual or statistical convergence;
3. transient removal and averaging window;
4. mesh/lattice refinement;
5. domain-size independence;
6. time-step/lattice Mach/stability checks;
7. surface vs. wake force agreement;
8. cross-solver comparison;
9. experimental/published benchmark comparison;
10. uncertainty estimate.

---

## 9. Pressure and field visualization

The original demo's most compelling feature is not just a drag number. It is the ability to see the flow result on the CAD model.

That requires a correspondence problem to be solved correctly. A pressure value from a voxel boundary cell or OpenFOAM surface element cannot be assigned to “Face7” merely because the arrays have similar lengths.

The canonical pipeline records source-face identity during tessellation or builds a robust geometric correspondence after solve. A surface field carries:

- geometry hash;
- triangle/element ID;
- source CAD face ID;
- area;
- pressure/Cp/shear.

Values can then be area-weighted back to original CAD faces. For higher-resolution display, the viewer may render the solver surface mesh directly while retaining source-face picking.

Volume field visualization should be a consumer of solver artifacts, not a child window owned by FluidX3D. That decoupling supports local and remote jobs equally and avoids brittle cross-process Qt/OpenGL ownership.

---

## 10. Dynamic stall and strip theory

The dynamic-stall capability remains important for rapid maneuvers, rotors, propellers, VTOL transition and aeroelastic coupling.

The reconciled engineering model corrects several prior defects:

- pitch rate contributes explicitly;
- parameters are immutable per section;
- scalar and vectorized paths implement the same equations;
- section chord is not mutated through a shared parameter object;
- semi-span mirroring is explicit;
- atmosphere is configurable;
- model identity says “engineering/reduced,” not “full validated LB.”

The canonical research path then upgrades this model by calibrating time constants/separation/vortex behavior from airfoil-specific static/dynamic data and validating hysteresis loops against published oscillating-airfoil experiments.

A powerful later integration is to use NeuralFoil/AeroSandbox results as low-order priors while CFD or experimental results calibrate dynamic parameters. This makes VibeCAD not only a solver launcher but a governed model-refinement system.

---

## 11. Unsteady coupling and aeroelasticity

Pitch/plunge coupling is retained as the first explicit two-way unsteady dynamics example. The reconciled code preserves caller initial conditions and requires prescribed position *and velocity* so the aerodynamic state receives the correct rates.

The deeper architecture should support partitioned coupling:

```text
structure/rigid state at t
        ↓
aero kinematics and geometry
        ↓
aerodynamic solve / reduced model
        ↓
forces/moments
        ↓
structural/rigid integration
        ↓
state at t+Δt
```

For stateful unsteady models, high-order integration requires checkpoint/restore or tightly coupled iteration. A naïve RK4 wrapper that advances the aerodynamic memory on every derivative evaluation is mathematically inconsistent.

Moving-mesh CFD and full fluid-structure interaction remain accepted targets. They enter through the same case/artifact/coupling interfaces after static cases and state synchronization are validated.

---

## 12. Six-degree-of-freedom flight dynamics

There are two distinct requirements:

1. full 6-DOF rigid-body equations;
2. a complete 6-DOF aerodynamic/propulsion/control force model.

The old discussion conflated them. The pass-01 internal simulator implements the first honestly. Its current strip provider supplies longitudinal aerodynamics and thrust but explicitly reports that lateral aerodynamics and control derivatives are absent.

The live upstream's JSBSim integration remains the stronger production path. The internal solver is valuable for:

- deterministic unit tests;
- frame/sign verification;
- coupling experiments;
- reduced model benchmarking;
- cross-checking exported JSBSim models.

The defined target force-provider hierarchy combines validated longitudinal/lateral derivatives, control surfaces, propulsion, rotor effects and unsteady terms. CFD can inform coefficient surfaces or corrections, but fitting must remain provenance-aware and validated out of sample.

---

## 13. Evidence, honesty and non-destructive engineering

One of the strongest live-upstream design choices is the evidence/claim-ceiling layer. High-fidelity work must strengthen it, not bypass it.

The current generic claim text is correct for the existing low-order stack but becomes semantically wrong for a genuine CFD result because it says the output “is not CFD.” The correct redesign is method-specific:

```text
low-order result:
  method = neuralfoil / vlm / aerobuildup
  claim = low-order model; not CFD or flight test; not airworthy

CFD result:
  method = cfd:openfoam or cfd:fluidx3d:lbm
  claim = CFD model result; not flight test; not airworthiness evidence;
          qualification depends on convergence/validation status
```

A later validation framework can add evidence states such as `validated_against_benchmark` without ever implying airworthiness from software analysis alone.

Analysis remains non-destructive. CFD may generate repair proposals (“reduce junction separation,” “change incidence,” “increase clearance”) but geometry changes go through the existing proposal/preview/apply/reject governance.

---

## 14. Caching and controlled accumulation of engineering knowledge

Once every case and artifact is hashed, VibeCAD can safely reuse work and learn from prior analysis.

A cache key should include at minimum:

- geometry revision/hash;
- frame/transform version;
- case schema version;
- flow conditions;
- references;
- solver/model/version;
- solver settings;
- mesh/lattice settings.

Compute provider usually does not change the physical result definition, but provider/runtime versions and hardware should still be retained as provenance for reproducibility.

This architecture also permits controlled accumulation:

- validated mesh settings for geometry classes;
- runtime/performance telemetry;
- solver disagreement history;
- calibrated dynamic-stall parameters;
- known-good domain sizes;
- repair outcomes.

The system must distinguish “past result” from “truth.” Reuse is allowed only when input identity and validation ceiling support it.

---

## 15. Validation program

### 15.1 Contract/unit tests

- body/wind coefficient projection;
- unit conversion at boundaries;
- JSON round trip;
- artifact hash stability;
- scalar/vectorized dynamic-stall parity;
- quaternion norm/frame signs;
- no hidden reset of unsteady state.

### 15.2 FreeCAD integration tests

- CAD→body transform on known oriented primitives;
- reference point and area extraction;
- deterministic STL tessellation/hash for unchanged documents;
- TRI6 conversion topology;
- pressure face mapping;
- packaged CMake file inclusion;
- existing public API/honesty tests still pass.

### 15.3 FluidX3D validation

- compile bridge against pinned/current compatible FluidX3D;
- upstream force-field benchmark;
- simple creeping-flow/Stokes case where applicable;
- domain-size study;
- lattice refinement;
- boundary-condition study;
- force sign/unit test against analytic/reference result;
- geometry scaling test with a known-dimension STL;
- cross-check against OpenFOAM for selected low-speed cases.

### 15.4 OpenFOAM/CfdOF validation

- automated CfdOF analysis object creation;
- volume domain construction and boundary classification;
- mesh quality gate;
- forceCoeffs collector on at least two supported OpenFOAM variants;
- standard external-aero benchmark;
- y+ / wall-treatment policy;
- RANS model comparison where appropriate;
- mesh convergence/GCI-style uncertainty workflow.

### 15.5 Dynamic/unsteady validation

- static-limit convergence to the intended polar/model;
- oscillating airfoil dynamic-stall loops against published data;
- time-step convergence;
- scalar/vector parity;
- pitch/plunge benchmark and flutter/LCO cases where model scope permits.

### 15.6 6-DOF validation

- zero-force inertial motion;
- gravity sign in NED/body frames;
- torque-free angular momentum behavior;
- known constant-force/constant-moment solutions;
- JSBSim cross-comparison using identical mass/inertia/force models.

---

## 16. Dependency-ordered implementation program

These stages are **not** a scope reduction.

### Stage 1 — contracts and upstream seams

Merge case/result/artifact contracts, extend report/evidence schema, establish frame/reference rules, preserve all current tests.

### Stage 2 — geometry/mesh identity

Implement CAD→body transform, deterministic geometry artifact generation, source-face correspondence and optional Gmsh utilities.

### Stage 3 — first real high-fidelity solver paths

Integrate CfdOF/OpenFOAM and the vendored-default FluidX3D bridge, while retaining an external-bridge override. Establish real force/coefficient/convergence results with benchmark tests.

### Stage 4 — field results and visualization

Pressure/Cp/shear mapping, volume fields, slices/streamlines/vorticity/Q-criterion, common result viewer.

### Stage 5 — remote compute

Wire solver-specific Kaggle bundles through `AeroKaggle`, live quota, privacy controls, job persistence/cancellation/retry. Add performance telemetry and only then forecasting/routing policy.

### Stage 6 — unsteady engineering and flight coupling

Integrate calibrated dynamic stall/strip theory, pitch/plunge, 6-DOF reference solver and JSBSim comparison. Add validated lateral/control/propulsion providers.

### Stage 7 — advanced physics

High-Re LBM validation, moving bodies, dynamic mesh, FSI, rotating components, advanced OpenFOAM models.

### Stage 8 — advanced diagnostics/refinement

Wake surveys, mid/far-field drag decomposition, uncertainty quantification, solver disagreement analysis, controlled model calibration and repair/refinement loops.

---

## 17. What should remain private/optional vs. core

Core VibeCADAero should contain:

- contracts;
- configuration/frame integration;
- solver/provider registry;
- result/evidence integration;
- mesh/artifact interfaces;
- optional dependency detection;
- OpenFOAM/CfdOF adapter code where license-compatible;
- Kaggle CLI adapter;
- dynamic/unsteady reference models;
- tests and documentation.

FluidX3D source and the VibeCAD bridge are vendored with the normal VibeCAD product distribution. An external FluidX3D bridge remains supported as a normal override. The bundled FluidX3D license and third-party notices state the applicable use and redistribution requirements; VibeCAD does not classify the user or automatically switch/exclude backends based on purpose.

---

## 18. Native authority is a platform seam — Pass 02 finding strengthened by Pass 03

Between Pass 01 and the Pass 02 frozen upstream, VibeCAD advanced its Native mutation architecture substantially. The current host store now maintains a document structural revision, one-shot mutation previews, stale-revision rejection, mutation receipts, bounded verified call memory, and a dispatcher view of pending previews across a broad family of model operations. This is no longer a local convention for one tool; it is a host-level authority seam.

Aero must therefore distinguish **three different state machines** instead of building one oversized replacement:

1. **Native CAD mutation authority** — owned by `VibeCADNativeState` / `VibeCADNativeDispatch`; it governs CAD-changing operations and stale structural revisions.
2. **Aero engineering evidence** — owned by VibeCADAero case/result/report/stamp objects; it governs what was solved, with what geometry and method, and what claims are justified.
3. **Long-running job lifecycle** — owned by an Aero job store; it governs local or remote computation that may outlive a Native turn or the FreeCAD process.

These state machines intersect but must not be conflated. A CFD job is **not** a Native mutation preview. It may continue and remain valuable evidence after the CAD changes. When it finishes, its captured Native revision and Aero geometry hash decide whether it can become the current result or must remain historical/stale evidence. Conversely, any later CAD-changing Aero repair still belongs under host mutation authority.

### 18.1 Existing Aero repair preview must converge with host authority

The live repository still has its older `AeroRepairPreview` object, keyed to an Aero geometry fingerprint and an optional `native_revision`. That fingerprint is useful and should remain as evidence identity. What should not remain long-term is a second independent mutation-authorization system.

Pass 02 changed the integration target; Pass 03 strengthens it because the host now also owns generic preview apply/reject controls and user-explicit intent preservation:

- retain `AeroPreview.geometry_revision()` or an evolved geometry-content hash;
- capture the host Native structural revision at proposal time;
- thread that revision through every `/v1/aero` repair proposal/apply path;
- where practical, migrate CAD-changing Aero proposals onto the host preview/receipt mechanism;
- preserve compatibility for existing documents while the migration occurs;
- reject silent application when either the host revision or relevant geometry identity changed.

The live `/v1/aero` route currently calls `propose_repairs()` and `apply_repairs()` without supplying the optional Native revision even though the public Aero functions can accept it. That is a concrete integration gap to close before high-fidelity result-driven repairs are added.

### 18.2 Upstream preview-store lifecycle hazard

The current host `_DocumentRecord.previews` container is an ordinary dictionary. Consumed previews are marked consumed but remain stored; stale previews also remain stored. `list_mutation_previews()` hides consumed entries but does not bound or remove them. The persisted Native-state export stores receipts but not previews.

That is acceptable for short development sessions but becomes a lifecycle hazard if Aero or future agent workflows create many proposals. The recommended upstream fix is **engineering hygiene**, not a policy control: add explicit finite preview retention/cleanup semantics and make restart behavior deliberate. Aero must not depend on indefinitely retained in-memory preview records.

---

## 19. Persistent Aero job fabric

Pass 01 had `PreparedJob` and `ExecutionReceipt`, but that only described a synchronous call boundary. The complete product requires a durable job fabric.

A canonical `AeroJobRecord` captures:

- stable `job_id` and `case_id`;
- solver backend and compute provider;
- document identity;
- captured host Native revision;
- captured Aero geometry revision/hash;
- provider job id when remote;
- work/result paths and immutable artifact references;
- attempts and timestamps;
- progress where the provider can supply it;
- terminal error/cancellation state;
- small metadata, never huge field arrays.

The lifecycle is explicit: `prepared → queued/uploading/submitted → running → downloading → parsing → succeeded`, with failure, cancellation and orphan/recovery paths. Pass 03 Correction 01 makes the end state explicit: `AeroJobStore.py` is **transitional/reference only**. VibeCAD shall own one domain-neutral Analysis Job Runtime, extracted non-destructively from the existing Native Background orchestration and detached FEM execution paths. FEM must prove observational parity before Aero becomes the second production client.

Restart is not failure. On restart, VibeCAD should reconnect to provider jobs when possible, mark unreachable remote jobs as orphaned rather than falsely failed, and allow retry/recovery without duplicating an already-running provider job.

A result that finishes after CAD changes remains valid **historical evidence for the exact captured case**. It simply cannot silently overwrite the active Aero report for changed geometry.

### 19.1 Durable jobs carry provenance, not mutation authority

The host runtime must not serialize the submission-time `NativeRuntimeContext`, `NativeCallTicket`, callbacks, FreeCAD objects or transactions and replay them later as permission to attach results. Current FEM intentionally remains stricter during migration: its existing original-ticket/global structural revision + FEM exact-state checks are preserved first as the parity oracle.

After persistence is stable, VibeCAD adds a separate publication coordinator. It persists inert `SubmissionAuthorization`/`PublicationDescriptor` records tying the exact job/attempt/output manifest to the source `Document.Uid`, domain adapter/version and frozen dependency snapshot. When a durable Aero result is ready, the host rebinds the exact source document, asks Aero for a dependency `CurrentnessReport`, detects prior publication receipts, and obtains **fresh Native mutation authorization** for that exact completed job. If the source is closed it waits as `AWAITING_SOURCE`; if publication authority cannot yet be established it waits as `AWAITING_PUBLICATION`; relevant engineering drift produces stale/quarantined evidence rather than mutation.

This solves both failure modes: a stale serialized Native ticket can never become standing permission, while an unrelated host edit does not have to invalidate a four-hour CFD solve once Aero's exact dependency model is proven. Domain currentness does not itself authorize mutation; Native still owns the final transaction, postconditions and receipt.

---

## 20. Explainable solver and compute routing

`auto` must become a deterministic decision, not a hidden conditional inside each adapter. The routing layer evaluates candidates that have already reported:

- solver capability for the requested physics;
- qualification status for the requested regime;
- local/remote availability;
- estimated memory and wall time when known;
- live provider quota and forecast fit when known;
- requested fidelity/latency preferences;
- privacy/upload choice for remote compute.

Every routing decision returns the selected route and the reasons alternatives were rejected. Unknown quantities remain `unknown`; VibeCAD must not invent quota, runtime or accuracy.

The reference `AeroRouting.py` demonstrates deterministic selection semantics. A later production router will be richer, but its result must remain inspectable by the UI, script surface and agent.

---

## 21. Solver qualification is versioned evidence

A successful solver execution is not the same as a qualified model. Pass 02 added, and Pass 03 retains, an explicit `SolverQualification` reference contract tying qualification to:

- backend and exact version/build;
- model/turbulence/collision formulation;
- benchmark identity and source;
- geometry and settings hashes;
- expected observables and tolerances;
- an applicability envelope such as Re, Mach, angle of attack and geometry class.

This allows the evidence system to distinguish:

- capability unavailable;
- numerically completed but unqualified;
- qualified inside a tested envelope;
- extrapolation outside the envelope;
- failed validation.

Qualification never turns CFD into airworthiness evidence. It makes the numerical claim precise.

---

## 22. Complete capability still missing from the implementation plan

The accepted target remains larger than the current overlay. Pass 03 keeps these workstreams explicit rather than hiding them under “advanced later”:

### 22.1 Native/agent control surface

High-fidelity operations need exact public actions for case preparation, case inspection, launch, status, cancel, retry/reconnect, result inspection, active-result selection and field visualization. CAD-changing follow-up actions use host Native revision/preview semantics. Read/compute actions must not mutate CAD merely by being queried.

### 22.2 Aero UI and interactive field experience

The complete UX includes a case panel, geometry/domain preview, mesh/voxel preview, solver/resource decision explanation, run/progress/cancel/reconnect, convergence/time-history views, result history, field selector, surface pressure/Cp/shear, slices, probes, streamlines, vorticity/Q-criterion and time-dependent playback. FluidX3D's real-time visualization capability should be integrated through the most robust FreeCAD-compatible presentation path rather than reduced to an offline scalar report.

### 22.3 Kaggle resource prediction and scheduling

Live quota replaces the old hard-coded weekly assumption. The complete capability additionally learns **performance telemetry**, not engineering truth: measured upload time, startup latency, cells/step throughput, job wall time and accelerator consumption. A transparent estimator can then predict whether a compatible job fits current quota and compare local vs. remote cost/time. Estimates are labeled estimates and never treated as provider guarantees.

### 22.4 FluidX3D high-Re qualification

The plan must specify and validate the chosen collision/operator options, SGS/turbulence treatment, wall treatment, lattice-Mach limits, domain blockage criteria, resolution rules, transient/averaging windows, convergence/stationarity checks, boundary families and refinement studies. “High-Re capable” is a qualification target, not a label granted by compilation.

### 22.5 OpenFOAM physics ladder

The external-aero template must define supported incompressible/compressible regimes, steady RANS vs. URANS selection, turbulence/transition models, near-wall/y+ policy, temporal/numerical schemes, rotating/actuator regions, convergence criteria, mesh-quality rejection criteria and supported post-processing. Cases outside a qualified template remain explicit expert/custom cases.

### 22.6 Moving and rotating geometry

Moving-body work requires first-class `RigidBody`, `MotionLaw`, `MovingSurface`, frame/origin and force-feedback contracts; timestep synchronization; revoxelization or moving-boundary behavior; and provenance for time-dependent geometry. This workstream covers control surfaces, tilting assemblies, propellers/rotors and VTOL transitions rather than treating “moving mesh” as a single boolean.

### 22.7 Propulsion–airframe interaction

The solver ladder needs common propulsion state and force/moment interfaces covering the current momentum-theory baseline, actuator-disk/actuator-line approaches, propeller/rotor maps, wake/propwash effects on lifting surfaces, motor/prop operating state and resolved rotating geometry at the highest fidelity level. The same propulsion semantics must feed CFD, unsteady models, JSBSim and reference 6-DOF paths consistently.

### 22.8 Complete 6-DOF aerodynamic provider

The rigid-body integrator is only one part of full vehicle simulation. Production 6-DOF needs lateral-directional aerodynamics, control effects, propulsion, wind/gust state, mass/inertia authority and clear aggregation of every component force/moment. JSBSim remains the production FDM authority while the internal solver is a verification/reference implementation.

### 22.9 Aeroelasticity / FSI

The roadmap retains modal and higher-fidelity coupling. It needs structural authority, aero↔structural mapping, partitioned iteration, relaxation, timestep synchronization, convergence/divergence behavior, modal reduction, flutter/LCO benchmarks and provenance for transferred fields.

### 22.10 Artifact/cache lifecycle

Content hashes are necessary but not sufficient. Production needs content-addressed artifact layout, document references, retention/pinning, cleanup of unreferenced intermediates, corruption checks, schema migration, portable case bundles and a clear distinction between small document metadata and potentially huge solver fields.

### 22.11 Refinement loop

The solver ladder should become an executable refinement architecture: low-order prediction → high-fidelity case → disagreement localization → mesh/model refinement → re-solve → qualification comparison → governed reuse. Failed or unqualified runs remain evidence but never silently become calibration truth.

---

## 23. Third-party component notice principle

VibeCAD and VibeCADAero retain their own project license and are not characterized by the use restrictions of one solver dependency. Third-party terms remain attached to the component to which they apply.

FluidX3D is the planned vendored/default LBM backend, with an explicit external bridge override also supported. Its current upstream license text is shipped/readable alongside the component. At this reconciliation point the public FluidX3D license does not permit commercial FluidX3D use without explicit permission from its copyright owner; the official project does not publish a standardized commercial agreement, price or deployment model. VibeCAD therefore documents that fact and does not invent hypothetical commercial terms or architecture.

On the first entry to Aero, one purely informational **Third-Party Software Notice** may be shown. It says that Aero can use third-party components with separate terms, links the detailed notices, and contains exactly:

> **I understand.**

The local acknowledgement records only that the informational notice was seen. It is unversioned, normally never shown again, is not transmitted, does not classify intended use, does not control solver eligibility, and does not change ownership or licensing of CAD designs created in VibeCAD.

---

## 24. Pass 03: host convergence changes the implementation boundary

Between the Pass-02 frozen SHA `d0a933e40005b4affe9303f27d1eae5cd36eb030` and Pass-03 SHA `df07a5e82ec2fb31515e10b33822253d69d496ff`, VibeCAD advanced 41 commits across 50 files. The important changes are architectural rather than aerodynamic: the host now implements more of the control/evidence machinery that Aero had been planning independently.

### 24.1 Generic preview controls now belong to the host

The current host has generic pending-preview list/apply/reject behavior and in-app preview commands. It applies the exact proposal stored by the dispatcher, refuses stale previews, defaults automatic apply off, and can verify that `user_explicit` intent remains preserved.

Therefore VibeCADAero should not build:

- its own generic preview list UI;
- a second Apply/Reject broker;
- an independent generic intent-preservation mechanism;
- a new long-lived Aero mutation authorization store.

The current `AeroRepairPreview` remains useful for aerodynamic geometry identity and compatibility, but authorization should converge onto host Native authority.

### 24.2 Detached FEM proves the right compute semantics

The live FEM execution path now freezes input artifacts into a detached directory, bounds and hashes those inputs, runs external solver processes with timeout/progress/cancellation, and revalidates the exact source solver/History/retention state before attaching results. It also separates solver completion from model qualification.

This is close to the CFD execution architecture required here, but the current code is FEM-specific. Correction 01 resolves the ownership gap with a **non-destructive host extraction**:

- characterize and preserve the existing FEM behavior first;
- extract physics-neutral process/artifact/orchestration mechanics behind compatibility facades;
- keep FEM solver state/builders/importers domain-specific;
- prove FEM parity one solver path at a time;
- make the resulting VibeCAD Analysis Runtime the one host job authority;
- make Aero the second domain client only after that parity is demonstrated.

### 24.3 Artifact exactness and geometry readiness are distinct

The host now uses `exact` for STEP/native B-rep artifacts, `derived` for mesh/STL artifacts, and `presentation` for screenshots. This is a useful shared vocabulary, but it answers only provenance.

Aero additionally requires a readiness ladder:

`brep_accepted → surface_closed → surface_watertight → fluid_domain_ready → mesh_ready → solver_input_frozen`

An exact B-rep is not automatically watertight for CFD, mesh-ready, manufacturable or airworthy. A derived mesh can be a correct solver artifact when its derivation is auditable.

### 24.4 Evidence must describe the actual epistemic state

The host now demonstrates a useful progression:

- prepared analysis: `evidence_waiting`, `not_solved`;
- completed numerical solve without qualification: `model_unqualified`;
- direct authoritative geometry measurement: `measured`;
- screenshot: presentation-only, not measurement.

Aero should adopt this rather than maintain a single generic low-order claim string. FluidX3D/OpenFOAM completion is not qualification. A qualification record must match the exact solver build/model/settings and requested operating envelope.

## 25. Long-running jobs must outlive Native agent sessions

The current external Native channel can hold a session and closes idle sessions after a bounded interval. This is appropriate for interactive CAD mutation, but it is the wrong lifetime for CFD.

A CFD job therefore captures a document/case snapshot and runs independently. Session closure does not cancel the computation. When results return, the attachment decision is based on frozen input hash, case hash, Native revision and Aero geometry revision—not on whether the originating agent session still exists.

This distinction is essential for Kaggle and large local/OpenFOAM runs, and it prevents a control-plane timeout from becoming accidental physics-job cancellation.

## 26. Refinement architecture becomes stronger with host evidence semantics

The governed refinement loop can now use a shared evidence vocabulary:

1. low-order result produces a method-specific, usually unqualified model prediction;
2. high-fidelity case is prepared (`not_solved`);
3. detached solve completes (`model_unqualified` unless covered by qualification);
4. result is attached current or retained stale by exact identity comparison;
5. disagreement is localized by coefficient/field/flow regime;
6. mesh/model/solver settings are refined explicitly;
7. qualification/convergence evidence is updated;
8. only validated evidence is eligible for controlled reuse.

This creates accumulated engineering knowledge without converting every previous solver output into assumed truth.

## 27. Pass 03 implementation priority shift

The dependency order is now:

1. close `/v1/aero` Native revision propagation;
2. adopt host preview/evidence/artifact semantics in Aero;
3. establish geometry readiness/frame/source correspondence;
4. reuse detached-execution invariants and keep a solver-neutral Aero job domain record;
5. complete OpenFOAM/CfdOF baseline;
6. complete vendored FluidX3D baseline;
7. common field UI + remote execution/routing;
8. solver qualification/high-Re work;
9. moving bodies/propulsion/unsteady/6DOF/FSI;
10. advanced diagnostics/refinement.

This is dependency order, not scope reduction.

## 28. Conclusion
The discussion did not steer VibeCADAero away from its earlier purpose. It discovered a larger coherent purpose around it.

The live project already provides a governed low-order aerodynamic domain: geometry/config resolution, solver hierarchy, evidence/reporting, repair governance and flight-dynamics export. The accepted additions transform that domain into a multi-fidelity engineering environment in which CAD geometry can launch rapid low-order analysis, stateful unsteady models, GPU LBM and OpenFOAM; results can execute locally or remotely; fields can return to the CAD model; and every result remains traceable to geometry, solver, compute provider and validation state.

The critical lesson from the prior code history is that breadth is not the problem. **Unreconciled duplicate abstractions are the problem.** The canonical design therefore preserves the breadth while reducing the number of truths: one public Aero authority, one frame contract, one case/result contract, one evidence lineage and one builder roadmap that can be reconciled repeatedly as upstream evolves.



## 25. Correction 01 — the Host Analysis Runtime is a VibeCAD migration, not an Aero subsystem

The earlier plan correctly rejected a second Aero scheduler but stopped one step short of the real solution. The generic service should not be awaited; it should be deliberately extracted from infrastructure VibeCAD already has.

### 25.1 Two proven host seeds

At the frozen Pass-03 SHA, `VibeCADNativeBackground*` already supplies host job orchestration/status/cancel semantics and `VibeCADNativeAnalyzeSolverExecution*` already supplies detached FEM input hashing, safe subprocess behavior, cancellation/timeout, exact source revalidation and transaction-safe result publication. The engineering-domain state in `VibeCADNativeAnalyzeSolverState.py` is correctly FEM-specific.

The extraction boundary is therefore structural:

- move **job/process/artifact mechanics** toward `VibeCADAnalysis*` host infrastructure;
- keep **FEM meaning** in FEM adapters;
- keep **Aero meaning** in Aero adapters;
- keep **Native mutation authority** as the publication transaction boundary;
- keep **qualification/evidence** separate from execution success.

### 25.2 Non-destructive migration

The implementation must be a strangler refactor. First add characterization tests with zero runtime behavior change. Then add generic contracts/facades over the existing implementation. Then extract the process runner and artifact sealing separately. Then introduce runtime orchestration behind the current Native Background surface. Migrate FEM one solver at a time, prove parity, stabilize, and only then introduce durable persistence and Aero adoption.

Persistence, concurrency expansion, provider expansion and solver-physics improvements are intentionally separate changes. Combining those risk domains would make regressions hard to localize and rollback.

### 25.3 Three state axes

The generic runtime must not overload `success`:

1. **execution** — prepared/queued/running/solved/failed/cancelled/timed-out;
2. **publication/currentness** — validating/current/published or stale/quarantined;
3. **engineering evidence** — not-solved/model-unqualified/qualified/measured/derived/presentation as domain evidence allows.

A successful stale solver result is retained as immutable historical evidence; it is not silently attached to changed geometry and is not destroyed merely because it is stale.

### 25.4 FreeCAD safety boundary

Live FreeCAD object inspection/preparation and result publication occur on the document thread. Worker execution receives sealed immutable files/serializable snapshots, never durable live object references. Publication returns through the existing Native mutation transaction so partial document result graphs cannot survive a failed commit.

### 25.5 Precise dependencies

The host supports domain-contributed dependency fingerprints. FEM initially preserves its current exact solver/History/retention checks. Aero later contributes geometry/config/frame/reference/mesh/solver/atmosphere/BC dependencies so an unrelated document edit need not automatically invalidate hours of compute while any relevant change does.

### 25.6 Durable job architecture

Durable metadata should be implemented later as a versioned local transactional store (SQLite is the recommended default), with large solver artifacts outside FCStd and compact provenance/result references inside the document. Provider credentials are runtime-only. Remote jobs may reconnect by provider ID; unrecoverable local jobs become explicitly orphaned rather than fabricated as successful after restart.

### 25.7 Compatibility is part of correctness

Existing `native.job`, `/v1/native`, `/v1/aero`, FEM result graphs, History behavior, Native receipts and error compatibility are protected surfaces during extraction. Old modules remain facades/re-exports until import audits prove deletion safe.

The complete implementation-level specification is in `HOST_ANALYSIS_RUNTIME_CONTRACT.md`, `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md`, `NON_DESTRUCTIVE_MIGRATION_MATRIX.md`, `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md`, and `HOST_ANALYSIS_RUNTIME_RISK_REGISTER.md`.


### Non-destructive runtime cutover invariant

Parity is established with **observation-only shadow traces**, never by running the legacy and generic solver paths simultaneously. Exactly one path owns process execution and exactly one Native transaction owns publication for each real job. Cutover is per responsibility/per solver and remains revertible behind compatibility facades. Durable reattachment uses exact domain dependencies rather than document labels/paths; publication is idempotent against retries/reconnect/callback replay. See `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md` and `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`.
