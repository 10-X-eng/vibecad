# Reconciliation Ledger — Pass 03 Correction 01

**Frozen upstream:** `df07a5e82ec2fb31515e10b33822253d69d496ff`

## Disposition key

- **CANONICAL** — active target behavior/architecture.
- **MERGED** — valid idea preserved inside a stronger/current abstraction.
- **SUPERSEDED** — historical proposal replaced by a more correct current design; rationale retained.
- **RESEARCH-DEFERRED** — accepted scope requiring deeper implementation/qualification; not deleted.

| ID | Topic | Disposition | Pass 02 resolution |
| --- | --- | --- | --- |
| R01 | VibeCADAero remains one subsystem of VibeCAD | CANONICAL | High-fidelity work extends current Aero; no second Aero product/workbench. |
| R02 | Existing NeuralFoil section solve | CANONICAL | Retained as rapid Level-1 solver. |
| R03 | Existing AeroSandbox/VLM/AeroBuildup | CANONICAL | Retained as rapid 3-D solver/stability source. |
| R04 | Existing momentum-theory hover | CANONICAL | Retained, clearly labeled non-CFD. |
| R05 | Existing JSBSim export | CANONICAL | Remains production flight-dynamics path. |
| R06 | Existing `VibeCADAero.py` public helper | CANONICAL | High-fidelity APIs extend it rather than bypass it. |
| R07 | Existing `AeroResults.py` | CANONICAL | Additive high-fidelity provenance/field/job properties. |
| R08 | Existing `AeroStamp.py` | CANONICAL | Method-specific claims required; generic “not CFD” text must evolve. |
| R09 | Existing Aero repair propose/apply | MERGED | Preserve semantics; converge authorization with host Native revision/preview system. |
| R10 | `AeroPreview.geometry_revision()` | CANONICAL | Useful Aero-specific engineering identity; keep even when host revision is added. |
| R11 | Independent Aero mutation authorization store | SUPERSEDED | Host Native state now owns CAD mutation authority; compatibility record may remain temporarily. |
| R12 | Long-running CFD job stored as Native preview | SUPERSEDED | Native previews are for CAD mutations. Long-running engineering work belongs to the target host Analysis Runtime. |
| R13 | Host Native structural revision | CANONICAL | Capture with every case/job and use for result attachment/stale checks. |
| R14 | `/v1/aero` repair path without host revision | SUPERSEDED | Thread live host revision into propose/apply. |
| R15 | Native pending previews | CANONICAL | Reuse for common mutation visibility once Aero CAD previews migrate. |
| R16 | Unbounded host preview record retention | SUPERSEDED TARGET | Upstream bug/hazard; add finite cleanup/restart semantics before heavy Aero use. |
| R17 | Solver vs compute provider separation | CANONICAL | Physics backend and execution location remain orthogonal. |
| R18 | Canonical `AeroCase` / `CFDResult` | CANONICAL | Retained and extended with job/revision/qualification identity. |
| R19 | Artifact hashes/provenance | CANONICAL | Geometry/mesh/case/result/field artifacts remain content-identified. |
| R20 | CAD/body/solver frame contract | CANONICAL | Explicit +X forward,+Y right,+Z down body convention; per-solver transform. |
| R21 | Explicit moment reference | CANONICAL | Required for moment coefficients and body↔solver translation. |
| R22 | FluidX3D vendored/default backend | CANONICAL | Planned under VibeCADAero vendor tree; external bridge override also supported. |
| R23 | “Commercial VibeCAD/Aero profile” | SUPERSEDED | FluidX3D terms are component-specific; no product-wide profile invented. |
| R24 | Purpose/license runtime policing | SUPERSEDED | Rejected by product direction; documentation is sufficient. |
| R25 | One first-use Aero notice | CANONICAL | Informational only; exact checkbox “I understand.”; local unversioned bit. |
| R26 | Repeat notice after updates/license changes | SUPERSEDED | Normally never shown again after first acknowledgement. |
| R27 | “I agree” checkbox | SUPERSEDED | Use “I understand.” only. |
| R28 | Restrictions on CAD output/design ownership | SUPERSEDED | Third-party component terms do not become VibeCAD/Aero design licensing. |
| R29 | Published FluidX3D commercial agreement assumed | SUPERSEDED | No standard public agreement/deployment model is published at this pass; do not invent terms. |
| R30 | FluidX3D external-only backend | SUPERSEDED | External is override; vendored/default remains target. |
| R31 | FluidX3D verified source APIs | CANONICAL | `LBM::run`, force/torque, voxelization, Units remain source anchor. |
| R32 | Unverified `fluidx3d.Config()` Python API | SUPERSEDED | Use verified process/C++ bridge until an API is actually verified. |
| R33 | Hard-coded 30 h/week Kaggle quota | SUPERSEDED | Query live quota; forecasting uses measured telemetry. |
| R34 | Kaggle as solver | SUPERSEDED | Kaggle is compute provider only. |
| R35 | Kaggle private zero-click after onboarding | CANONICAL | Retained with durable job lifecycle, privacy visibility and reconnect. |
| R36 | Advanced Kaggle forecasting | RESEARCH-DEFERRED | Live quota + measured history → estimate/uncertainty; never provider guarantee. |
| R37 | CfdOF as OpenFOAM seam | CANONICAL | Use current FreeCAD-native case-authoring APIs. |
| R38 | Reimplement all OpenFOAM case authoring | SUPERSEDED | Higher-level external-aero templates wrap CfdOF. |
| R39 | OpenFOAM external-aero RANS path | CANONICAL | First conventional CFD qualification path. |
| R40 | OpenFOAM URANS/compressible/transition/rotating templates | RESEARCH-DEFERRED | Explicitly retained in physics ladder. |
| R41 | Gmsh optional mesh bridge | CANONICAL | Explicit supported element conversion only. |
| R42 | Unknown Gmsh surface topology fallback | SUPERSEDED | Reject unknown types; do not corrupt topology. |
| R43 | Pressure/Cp painting to CAD | CANONICAL | Requires source-face correspondence and geometry hash. |
| R44 | Full interactive FluidX3D visualization in Aero | RESEARCH-DEFERRED | Retained; choose robust process/view integration rather than delete capability. |
| R45 | High-Re FluidX3D | RESEARCH-DEFERRED | Requires explicit operator/SGS/wall/domain/refinement qualification matrix. |
| R46 | Moving/rotating multi-body geometry | RESEARCH-DEFERRED | First-class motion/body/feedback architecture retained. |
| R47 | Propulsion-airframe interaction | RESEARCH-DEFERRED | Common propulsion state and actuator/resolved fidelity ladder required. |
| R48 | Dynamic stall | CANONICAL TARGET | Retained but formulation naming must match what is truly implemented/validated. |
| R49 | Scalar/vector dynamic-stall parity | CANONICAL | Same equations/state semantics required. |
| R50 | Strip/blade element model | CANONICAL TARGET | Explicit span convention, density and immutable per-section params. |
| R51 | Pitch/plunge unsteady coupling | CANONICAL TARGET | No hidden reset; prescribed motion includes rates. |
| R52 | Full 6-DOF rigid-body equations | CANONICAL REFERENCE | Internal solver is verification/reference; JSBSim production. |
| R53 | “Full vehicle aero” with zero lateral/control model | SUPERSEDED | Complete force provider still required. |
| R54 | Aeroelastic/FSI | RESEARCH-DEFERRED | Structural mapping/coupling/validation workstream retained. |
| R55 | Wake/mid/far-field drag decomposition | RESEARCH-DEFERRED | Best fit primarily after OpenFOAM field path matures. |
| R56 | Solver qualification records | CANONICAL | Added explicit versioned benchmark/envelope reference contract. |
| R57 | Successful run automatically qualified | SUPERSEDED | Execution success and model qualification separate. |
| R58 | Deterministic/explainable auto routing | CANONICAL | Added reference `AeroRouting.py`. |
| R59 | Opaque auto backend choice | SUPERSEDED | Selected/rejected candidates and reasons are visible. |
| R60 | Persistent/background job lifecycle | CANONICAL TARGET | Host-owned Analysis Runtime; `AeroJobStore.py` retained only as transitional/reference model. |
| R61 | Stale result discarded as failure | SUPERSEDED | Preserve as historical evidence; only active attachment is blocked. |
| R62 | Content-addressed artifact/cache lifecycle | CANONICAL TARGET | Add references, pinning, cleanup, migration, portable bundles. |
| R63 | Refinement/knowledge accumulation loop | CANONICAL TARGET | Low-order→high-fidelity→refinement→qualification→governed reuse. |
| R64 | New modules must be CMake-enumerated | CANONICAL | Required by current upstream packaging style. |
| R65 | NumPy<2 ABI constraint | CANONICAL UNTIL MIGRATED | Preserve until FreeCAD compiled-extension ABI is deliberately changed. |
| R66 | Separate legacy AeroWorkspace as primary UI | SUPERSEDED | Existing native Aero ribbon/control surface remains primary. |
| R67 | Pass corrections as accumulating addenda | SUPERSEDED | Pass 02 integrates corrections into canonical docs; history kept separately. |
| R68 | Hidden PYTHONPATH required for package tests | SUPERSEDED | Pass 02 test harness is self-contained; bare `pytest -q` is acceptance command. |

## Pass 03 dispositions

| ID | Topic | Disposition | Pass 03 resolution |
|---|---|---|---|
| P3-01 | Aero-owned generic preview apply/reject broker | SUPERSEDED | Live host now has generic pending-preview list/apply/reject and in-app commands. Reuse host. |
| P3-02 | Host user-explicit intent preservation on preview apply | CANONICAL HOST SEAM | Aero repair apply should participate rather than duplicate. |
| P3-03 | Pass-02 `AeroJobStore` as an independent scheduler | SUPERSEDED AS TARGET | `AeroJobStore` is transitional/reference only. Extract one VibeCAD host Analysis Runtime from Native Background + detached FEM; FEM first client, Aero second. |
| P3-04 | FEM detached execution pattern for CFD | CANONICAL PATTERN | Freeze/hash inputs, detached workdir, progress/cancel, stale-before-attach, worker does not mutate document. |
| P3-05 | Host artifact classes exact/derived/presentation | CANONICAL | Adopt for Aero artifacts. Keep geometry readiness separate. |
| P3-06 | Solver completed == model qualified | SUPERSEDED | Completion produces `model_unqualified` unless exact qualification evidence applies. |
| P3-07 | Exact B-rep == CFD-ready/manufacturable | SUPERSEDED | Add independent geometry-readiness ladder. |
| P3-08 | `/v1/run` as Aero/CAD escape hatch | SUPERSEDED | Use `/v1/aero` and registered Native/domain capabilities only. |
| P3-09 | Native session as CFD job lifetime | SUPERSEDED | Detached jobs outlive agent Native sessions. |
| P3-10 | Full high-fidelity scope | CANONICAL | Unchanged: high-Re, moving bodies, interactive fields, remote compute, 6DOF, FSI, diagnostics/refinement remain target scope. |


## Pass 03 Correction 01 dispositions

| ID | Topic | Disposition | Correction 01 resolution |
|---|---|---|---|
| C01-01 | Wait for a generic host job service to appear | SUPERSEDED | Specify and deliberately extract it from existing working host seeds. |
| C01-02 | One domain-neutral VibeCAD Analysis Job Runtime | CANONICAL TARGET | Host owns lifecycle/provider/artifact/recovery/currentness orchestration. |
| C01-03 | Native Background as disposable old implementation | SUPERSEDED | It is a compatibility/orchestration seed; preserve public behavior and evolve behind facade. |
| C01-04 | Detached FEM process/runtime as disposable old implementation | SUPERSEDED | It is the proven execution seed; extract physics-neutral mechanics while preserving FEM behavior. |
| C01-05 | Genericize `VibeCADNativeAnalyzeSolverState.py` | SUPERSEDED | FEM state/solver semantics remain FEM-owned. |
| C01-06 | Aero as first client proving generic runtime | SUPERSEDED | FEM must prove observational parity before Aero production adoption. |
| C01-07 | Big-bang FEM rewrite | SUPERSEDED | Characterization-first strangler migration in small revertible PRs. |
| C01-08 | Change concurrency during extraction | SUPERSEDED | Preserve one active job/document initially; concurrency is separate later feature. |
| C01-09 | Add persistence in process-extraction PR | SUPERSEDED | Persistence is separate later migration with schema/rollback/restart testing. |
| C01-10 | Worker thread touches live FreeCAD | SUPERSEDED | Prepare/revalidate/publish on document thread; worker only immutable files/data. |
| C01-11 | Job runtime replaces Native mutation transaction | SUPERSEDED | `NativeMutationBoundary` remains publication authority. |
| C01-12 | Successful stale solve is discarded/fails | SUPERSEDED | Preserve immutable quarantined/historical result; block current attachment only. |
| C01-13 | Whole document revision is sole future CFD dependency | MERGED / REFINED | Preserve current FEM exact rules first; host supports domain-contributed dependency fingerprints for future precision. |
| C01-14 | Execution success implies qualification | SUPERSEDED | Runtime execution, publication currentness and engineering evidence are separate axes. |
| C01-15 | Runtime purpose/license controls | SUPERSEDED | Host runtime has no purpose/license semantics; existing informational notices remain separate. |

## Correction 01 deepening dispositions

The detailed host-runtime dispositions D01–D20 are in `CORRECTION_01_DEEPENING_LEDGER.md`. They are additive to this ledger. Most importantly: FEM state identity remains domain-owned; public FEM/job seams stay stable; atomic cancel/commit and process-tree control are required correctness fixes; durability is staged after boundary parity; and no upstream implementation is authorized by this package.
