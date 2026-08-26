# Canonical Architecture — VibeCADAero High-Fidelity Extension, Pass 03 Correction 01

**Frozen host:** `halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`

## 1. Architectural statement

VibeCADAero is a governed multi-fidelity aerodynamics subsystem inside VibeCAD. It does not replace VibeCAD, the Native modeling system, VibeScript, or the existing low-order Aero functions. It adds progressively higher-fidelity aerodynamic evidence while preserving the host's authority, revision, intent, artifact and honesty semantics.

The architecture is intentionally split into **host-owned infrastructure** and **Aero-owned engineering semantics**.

### Host-owned

- document identity and structural revision;
- Native mutation authorization and receipts;
- generic Native preview list/apply/reject operator control;
- frozen Native tool surface and held-session behavior;
- user-explicit intent preservation;
- generic artifact provenance vocabulary (`exact`, `derived`, `presentation`);
- generic evidence distinctions such as `not_solved`, `model_unqualified`, direct `measured` evidence;
- safe document-thread mutation boundaries;
- increasingly strong refusal of mutation through raw `/v1/run` execution;
- **target host-owned domain-neutral Analysis Job Runtime**, extracted non-destructively from Native Background + detached FEM seeds.

### Aero-owned

- resolved aerodynamic geometry/configuration identity;
- body/solver coordinate frames and reference quantities;
- aerodynamic cases;
- solver and domain adapters;
- aerodynamic case/job payload semantics consumed by the host Analysis Runtime;
- force/moment/coefficient semantics;
- surface/volume field correspondence;
- solver qualification envelopes;
- moving-body/unsteady/propulsion/6DOF/FSI models;
- multi-fidelity refinement and engineering evidence history.

## 2. Canonical dataflow

```text
Authoritative VibeCAD document
        │
        ├─ host document UID + Native structural revision
        ├─ user-explicit intent snapshot
        └─ exact Native B-rep / semantic objects
                     │
                     ▼
             Aero geometry resolver
        frame + references + geometry fingerprint
                     │
          geometry readiness assessment
                     │
                     ▼
                  AeroCase
          case hash + method request
                     │
          ┌──────────┴───────────┐
          ▼                      ▼
      Solver router          Compute router
  NF/VLM/LBM/OpenFOAM     local/Kaggle/future HPC
          └──────────┬───────────┘
                     ▼
             Frozen solver input
      exact/derived artifact lineage + SHA-256
                     │
                     ▼
              Detached execution
       progress / cancel / reconnect / logs
                     │
                     ▼
                 CFDResult
           solver_finished != qualified
                     │
          ┌──────────┴──────────────┐
          ▼                         ▼
   qualification lookup       attachment guard
 build/model/envelope      native/geometry/case/input
          │                         │
          └──────────┬──────────────┘
                     ▼
       current result OR stale historical evidence
                     │
            AeroResults / fields / UI
                     │
           refinement / JSBSim / 6DOF
```

## 3. Three coordinated state systems

### 3.1 Host Native mutation state

Owns document structural revision, Native preview authorization, apply/reject, receipts and mutation evidence. Aero must not duplicate this for new CAD-changing features.

### 3.2 Host Analysis Job state

The **target architecture requires one host-owned domain-neutral Analysis Job Runtime**. It is to be extracted from the current `NativeBackgroundManager` orchestration and detached FEM execution mechanics without changing existing FEM behavior first.

The host owns job identity, lifecycle, provider execution, progress/cancel/timeout, durable metadata, artifact manifests, restart/reconnect, source-currentness orchestration and publication scheduling. Aero owns aerodynamic cases, dependency fingerprints, solver preparation/parsing and engineering evidence.

`AeroJobStore` is transitional/reference only and must not become production authority.

Submission authority, execution authority and publication authority are deliberately separate. A durable host job persists exact provenance and immutable artifacts, **not standing permission to mutate CAD**. Initial FEM migration preserves the current original `NativeCallTicket`/global-revision publication semantics exactly. Durable Aero/CFD later persists an inert `PublicationDescriptor`; when results are ready it rebinds exact `Document.Uid`, obtains an Aero `CurrentnessReport`, checks replay/artifact provenance, and acquires fresh Native publication authority on the document thread. Missing source becomes `AWAITING_SOURCE`; unavailable publication context becomes `AWAITING_PUBLICATION`; relevant drift becomes stale/quarantined historical evidence. See `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`.

### 3.3 Aero engineering-evidence state

Owns aerodynamic case identity, geometry fingerprint, solver input identity, result artifacts, qualification evidence, fields and current-vs-historical attachment.

These are deliberately different. A CFD job is not a Native preview; a numerical result is not a CAD mutation receipt; a successful process is not solver qualification.

## 4. Native repair convergence

The live `AeroPreview` geometry fingerprint remains useful. The current parallel authorization mechanism should be treated as compatibility debt.

### Immediate integration

The live external `/v1/aero` route must resolve and pass the host structural revision into `VibeCADAero.propose_repairs()` and `apply_repairs()`.

### Target integration

For CAD-changing Aero repair operations:

1. Aero calculates the proposed domain-specific repair and geometry evidence.
2. Host Native preview authority records the proposal/expected structural revision.
3. Host generic Apply/Reject UI or Native call owns user authorization.
4. Host verifies stale revision and preserves `user_explicit` intent.
5. Aero applies the exact stored repair payload on the document thread.
6. Host records mutation receipt.
7. Aero recomputes and creates new aerodynamic evidence from the changed geometry.

## 5. Solver ladder

The ladder remains additive.

### Level 0 — geometry, mass, configuration

Current `AeroConfig`, `AeroMass`, geometry and exact host measurements.

### Level 1 — NeuralFoil

Fast section coefficients. Remains valuable for interactive analysis and initialization.

### Level 2 — AeroSandbox AeroBuildup / VLM

Current low-order 3D airplane/tailsitter path. Remains canonical.

### Level 3 — unsteady reduced engineering models

Dynamic-stall, strip/blade-element and time-domain coupling. Must remain explicitly qualified by model scope; do not call the current reduced reference a complete Leishman–Beddoes implementation.

### Level 4 — vendored FluidX3D LBM

GPU/OpenCL high-fidelity path. Vendored/default solver component in the planned Aero distribution, with external bridge override. Requires explicit physical scale, domain/boundary setup, solver version/build identity, force/torque/field extraction and benchmark qualification.

### Level 5 — OpenFOAM through CfdOF

Conventional CFD path for external aerodynamics, RANS/URANS/compressible/transition/rotating capabilities and advanced diagnostics as validated.

### Level 6 — diagnostic/decomposition post-processing

Wake, mid-field/far-field drag-source analysis, convergence and uncertainty diagnostics. OpenFOAM is the natural first backend for thermodynamic drag decomposition; LBM remains surface/momentum-exchange primary unless separately validated.

## 6. Solver vs. compute provider

Solver answers **what physics/model is executed**. Compute provider answers **where the prepared job runs**.

Solvers:

- NeuralFoil
- AeroSandbox/VLM
- FluidX3D
- OpenFOAM/CfdOF
- unsteady engineering models

Compute providers:

- in-process/local CPU for low-order work;
- local detached process/GPU;
- Kaggle notebook execution;
- future remote/HPC providers.

No provider may silently alter the aerodynamic method. A Kaggle FluidX3D case and local FluidX3D case must share a case/result contract and identify any build/config differences explicitly.

## 7. Detached execution architecture

Pass 03 adopts the invariants now demonstrated by live detached FEM:

- prepare all solver inputs before worker launch;
- use a detached work directory;
- reject unsafe input paths/symlinks;
- hash the frozen input tree;
- record solver executable/build and environment;
- bound input files/bytes/timeouts;
- worker process never directly mutates FreeCAD;
- progress and cancellation are explicit;
- attach results only after revalidating source state;
- discard/retain stale result artifacts without promoting them to current evidence.

The end state is no longer conditional. **VibeCAD shall generalize these mechanics into the host Analysis Job Runtime.** `AeroJobStore.py` is retained only as a transitional/reference model until the host runtime is proven with FEM and Aero migrates onto it.

The extraction must preserve the current public Native/FEM behavior through compatibility facades. `VibeCADNativeAnalyzeSolverState.py` remains FEM-specific; `NativeMutationBoundary` remains publication authority; no worker thread may mutate FreeCAD.

## 8. Evidence model

### Prepared != solved

`evidence_state=evidence_waiting`, `claim_ceiling=not_solved`.

### Solver completed != qualified

A parsed FluidX3D/OpenFOAM result is initially `model_unqualified` unless an exact solver qualification applies.

### Qualification

A qualification record is bound to:

- backend;
- solver build/version;
- model/collision/turbulence settings;
- benchmark source;
- benchmark geometry/settings hashes;
- tolerances;
- Reynolds/Mach/alpha/geometry envelope.

Only a matching record can promote a completed result to Aero's qualified-model state. Qualification still does not imply airworthiness.

### Measurement

Direct measurement from authoritative CAD geometry is `measured`. Numerical CFD is not a measurement; screenshots are never measurements.

## 9. Artifact taxonomy

Reuse the host vocabulary.

### Exact

Native B-rep, STEP or equivalent exact CAD representation.

### Derived

STL/OBJ, surface mesh, volume mesh, voxel grid, CFD field, numerical solver result, VTK/VTM outputs.

### Presentation

Screenshots, renders, animation frames.

Every derived artifact should identify its source geometry/case/settings hash. Artifact class does not imply model qualification.

## 10. Geometry readiness

Exactness and readiness are independent. Canonical ladder:

`unknown → brep_accepted → surface_closed → surface_watertight → fluid_domain_ready → mesh_ready → solver_input_frozen`

No readiness state implies manufacturability or airworthiness.

## 11. Frames and reference quantities

Canonical body axes remain:

- +X forward
- +Y right
- +Z down

Every solver adapter must resolve explicit body↔solver frame/origin transforms. Force and moment results must include the reference point. Coefficient conversion uses explicit density, velocity, reference area, chord/span and moment reference. Do not scatter hard-coded biplane area/chord assumptions across adapters.

## 12. Geometry/mesh/field correspondence

For pressure/Cp/field painting:

- retain geometry SHA and source document revision;
- retain source-face identity through tessellation/meshing where possible;
- carry triangle/solver-surface → source CAD face mapping;
- aggregate fields with defined weighting;
- reject mapping when geometry identity no longer matches.

Length/order guesses are not acceptable provenance.

## 13. Routing

`auto` must be deterministic and explainable. Candidate selection considers:

- method suitability;
- qualification status;
- availability;
- requested fidelity;
- known memory/wall-time estimates;
- live/estimated compute quota where relevant;
- user-explicit preference.

Unknown values remain unknown rather than guessed. Kaggle accelerator type is runtime/provider data; current docs show accelerator assumptions can change.

## 14. UI/control surface

One Aero domain, one native ribbon/panel family. Required surfaces:

- first-use informational notice once (`I understand.`);
- case setup and geometry readiness;
- solver/backend selection with explainable auto route;
- mesh/domain preview;
- background job/status/cancel/reconnect;
- residual/history/convergence;
- current vs stale result history;
- field selector/slices/probes/clipping/streamlines/playback;
- solver comparison/qualification details;
- repair proposal through host Apply/Reject semantics;
- same authoritative commands accessible to in-app assistant and external `/v1/aero` control.

## 15. Moving bodies / propulsion / FSI

Retained canonical scope:

- rigid/moving surfaces and motion laws;
- revoxelization/moving boundaries;
- rotor/propeller actuator/full-geometry fidelity ladder;
- force/torque feedback;
- control surfaces and VTOL transitions;
- structural/modal coupling and field transfer;
- partitioned FSI iteration/relaxation/convergence;
- flutter qualification.

## 16. 6DOF

Internal rigid-body equations remain a verification/reference implementation; JSBSim remains the production flight-dynamics authority unless separately changed. Complete 6DOF requires lateral-directional/control/propulsion/wind/gust force providers, not merely rigid-body kinematics.

## 17. Refinement and engineering knowledge

The canonical loop is:

`low-order prediction → high-fidelity solve → disagreement localization → mesh/model refinement → re-solve → qualification/uncertainty evaluation → reusable evidence`

Only validated/qualified evidence can be promoted into controlled reusable engineering knowledge. Failed/stale/unqualified runs remain valuable history but cannot silently become calibration truth.

## 18. Third-party information principle

Component-specific terms stay component-specific. The first-use notice is informational, once-only, exactly **I understand.** There is no VibeCAD/Aero purpose detector, product-wide purpose/use profile, runtime compliance gate or restriction on CAD design ownership.


## 16. Correction 01 — non-destructive host-runtime migration law

The host Analysis Runtime is a **VibeCAD architectural migration**, not an Aero implementation shortcut.

Implementation order is mandatory in spirit:

1. characterize current FEM/background behavior without changing it;
2. introduce pure host contracts/facades;
3. extract local process mechanics while preserving old module/function behavior;
4. extract input/artifact sealing primitives while preserving current FEM digests;
5. introduce generic runtime orchestration behind the `NativeBackgroundManager` compatibility surface;
6. migrate FEM one solver path at a time and prove result/history/receipt parity;
7. stabilize before adding another engineering client;
8. add durable persistence as a separate reviewed change;
9. make Aero the second domain client;
10. add Kaggle/remote providers only after local/durable host semantics are stable;
11. delete old duplicate internals only after import/API audits and a compatibility window.

At no point may generic runtime extraction simultaneously redesign FEM physics, change current public `native.job` semantics, expand job concurrency and introduce persistent schema migration. Those are intentionally separated risk domains.

See `HOST_ANALYSIS_RUNTIME_CONTRACT.md`, `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md`, `NON_DESTRUCTIVE_MIGRATION_MATRIX.md`, and `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md`.


### Non-destructive runtime cutover invariant

Parity is established with **observation-only shadow traces**, never by running the legacy and generic solver paths simultaneously. Exactly one path owns process execution and exactly one Native transaction owns publication for each real job. Cutover is per responsibility/per solver and remains revertible behind compatibility facades. Durable reattachment uses exact domain dependencies rather than document labels/paths; publication is idempotent against retries/reconnect/callback replay. See `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md` and `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`.
