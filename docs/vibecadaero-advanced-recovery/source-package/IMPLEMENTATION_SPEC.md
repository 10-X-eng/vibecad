# Implementation Specification — Reconciliation Pass 03 Correction 01 Builder Target

**Frozen source target:** `halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`

## 1. Rule of engagement

This is a design/reference package, not permission to patch the active repository. Immediately before implementation, freeze current upstream again and reconcile from the Pass-03 SHA. Preserve newer upstream work; do not wholesale replace current Native/Aero files.

## 1.1 Correction 01 implementation principle

The host Analysis Runtime is now an explicit **VibeCAD prerequisite**, not a future optional convergence. It must be created by strangler extraction from existing working host code, not by dropping a new framework into VibeCAD. Read `HOST_ANALYSIS_RUNTIME_CONTRACT.md` and `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md` before changing execution code.

## 2. Authorities to retain

| Live seam | Authority |
|---|---|
| `VibeCADAero.py` | Public Aero operation authority |
| `AeroConfig.py` | Aero config/resolved geometry/frame inputs |
| `AeroSolvers.py` | Existing low-order solver authority |
| `AeroResults.py` | Durable FreeCAD AeroReport authority |
| `AeroStamp.py` | Aero honesty/evidence stamp authority, to be generalized |
| `AeroPreview.py` | Current repair geometry fingerprint/compatibility preview |
| `VibeCADAeroContext.py` | Bounded assistant Aero context |
| `VibeCADNativeState.py` | Host structural revision/mutation receipt authority |
| `VibeCADNativeDispatch.py` | Frozen Native tool execution authority |
| `VibeCADNativePreviewControl.py` | Generic pending preview list/apply/reject |
| `VibeCADIntentMemory.py` | User-explicit intent preservation |
| host Native output/measure/analyze seams | Shared artifact/evidence vocabulary |
| `VibeCADNativeBackground*` | Existing host orchestration seed; preserve public behavior while extracting generic runtime |
| `VibeCADNativeAnalyzeSolverExecution*` | Existing detached FEM execution seed; split generic mechanics from FEM adapters without behavior drift |
| `VibeCADNativeMutation.py` | Final document mutation transaction/publication authority; not replaced by job runtime |

## 3. Pass-03 reference overlay

### `AeroCFDContracts.py`

Retain canonical case/result/frame/reference contracts. Add host provenance fields only additively.

Required `AeroCase` identity should include or be associated with:

- `case_id` / case SHA;
- document UID;
- captured host Native revision;
- Aero geometry revision/hash;
- exact input geometry artifact hash;
- flow/atmosphere/reference quantities;
- solver backend/model/version/settings;
- compute provider request.

### `AeroHostEvidence.py`

Adopt host-aligned semantics:

- `not_solved` for prepared case;
- `model_unqualified` after solver completion unless qualification applies;
- exact/derived/presentation artifact classes;
- measured only for direct authoritative measurement;
- airworthiness remains explicitly outside claim scope.

### `AeroGeometryReadiness.py`

Maintain readiness separately from artifact provenance. Required states:

`BREP_ACCEPTED`, `SURFACE_CLOSED`, `SURFACE_WATERTIGHT`, `FLUID_DOMAIN_READY`, `MESH_READY`, `SOLVER_INPUT_FROZEN`.

### `AeroNativeRepairBridge.py`

Reference transition seam until `aero.*` repair mutations are directly registered into host Native preview authority. It must preserve:

- document UID;
- host structural revision;
- Aero geometry revision;
- user-explicit intent fingerprint.

### `AeroDetachedExecution.py`

TRANSITIONAL reference only. Its generic invariants move into the target host Analysis Runtime; Aero must not own a permanent duplicate.

### `AeroJobStore.py`

Keep in this reference overlay only as a transitional lifecycle/domain-payload model. **Do not implement it as production scheduling/persistence authority.** Build the host Analysis Runtime first and make Aero a client after FEM parity.

### Existing solver adapters

Retain and integrate:

- `AeroLocalCompute.py`
- `AeroKaggle.py`
- `AeroLBM.py`
- FluidX3D `setup_vibecad.cpp`
- `AeroOpenFOAM.py`
- `openfoam_collect.py`
- `AeroMesh.py`
- `AeroFieldResults.py`
- dynamic/unsteady/6DOF modules.

## 4. Immediate live integration changes

### 4.1 `/v1/aero` Native revision plumbing

When handling `propose_repairs`:

1. resolve active document;
2. resolve host `document_uid`;
3. get `current_revision(document_uid)` from `service.native_document_state_store()`;
4. pass that revision into `VibeCADAero.propose_repairs(..., native_revision=...)`.

When handling `apply_repairs`, repeat current revision resolution and pass it. Do not trust a client-supplied revision as authoritative.

### 4.2 Host preview convergence

Do not add a second generic Aero Apply/Reject command set. Target migration:

- Aero proposes domain repair payload;
- host Native preview stores authorization arguments;
- host UI/dispatcher applies/rejects;
- host stale and user-explicit checks run;
- Aero domain code performs exact requested repair;
- host receipt records mutation;
- Aero recomputes evidence.

Until this migration is safe, preserve `AeroPreview` compatibility behavior and bind it to actual host revision.

### 4.3 Do not couple CFD job lifetime to Native sessions

Current external Native sessions can idle-close. CFD must continue independently. A solver result can be stale/historical after session/document changes and still be valid evidence for the captured case.

## 5. Host Analysis Runtime migration — prerequisite to production high-fidelity jobs

Do not implement FluidX3D/OpenFOAM/Kaggle production execution by extending `AeroJobStore`. First perform the non-destructive host migration defined in `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md`.

The host runtime must provide the proven pattern for all domains:

1. domain prepares on document thread and resolves case/source dependencies;
2. seal detached immutable input artifacts;
3. reject symlink/path/archive escapes and hash content;
4. persist/track host-owned job identity and lifecycle;
5. provider executes off document thread with bounded environment/timeouts;
6. progress/cancel/log state is generic host state;
7. solver/domain parser produces immutable result artifacts;
8. return to document thread for dependency revalidation;
9. for current FEM migration, preserve the original ticket/global-revision publication behavior exactly;
10. for durable Aero/CFD, establish a fresh provenance-bound `PublicationAuthorization` for the exact completed job—never deserialize a stale Native context/ticket as authority;
11. if current and authorized, domain builds the publication draft and existing Native mutation authority commits it; otherwise retain output as `AWAITING_SOURCE`, `AWAITING_PUBLICATION`, or stale/quarantined evidence;
10. if stale, retain successful output as quarantined/historical evidence rather than attach or discard;
11. domain qualification stamps engineering evidence separately from execution success.

### 5.1 Extraction constraint

`VibeCADNativeAnalyzeSolverState.py` and FEM solver builders/importers remain FEM-specific. Generic code is extracted only from physics-neutral orchestration/process/artifact responsibilities.

### 5.2 Compatibility constraint

Existing `native.job` schema/actions, FEM result graph/History, error compatibility, one-active-job-per-document behavior, cancellation/timeout semantics and Native receipts remain unchanged during extraction.

### 5.3 Persistence constraint

Durable SQLite/artifact persistence is a **separate later PR** after in-memory FEM parity. Do not combine persistence migration with process extraction.

Durable publication authority is also a separate host evolution after persistence. Persist only inert publication descriptors/provenance; **never persist a live `NativeCallTicket`, `NativeRuntimeContext`, callback closure or transaction object as future CAD authority.** Exact source rebind + domain currentness + fresh Native publication authorization are required. See `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`.

### 5.4 Aero adoption gate

Aero may become the second client only after the parity gates in `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md` pass. Remote/Kaggle providers come after local provider + durable host job identity are stable.

## 6. AeroStamp target

Replace one generic low-order claim sentence with method-aware stamps.

Required fields:

- `evidence_state`
- `claim_ceiling`
- `method`
- `solver_finished`
- `model_qualified`
- `not_airworthy`
- solver/version/model/settings hash when relevant
- qualification ID when relevant
- case ID / geometry revision / result artifact reference.

Existing low-order analysis remains `model_unqualified` unless separately qualified.

## 7. AeroResults additive properties

Do not replace the FeaturePython object. Add properties/structured references for:

- case ID/hash;
- solver/backend/model/version;
- compute provider/provider job ID;
- captured Native revision;
- Aero geometry revision;
- frozen-input hash;
- artifact provenance;
- qualification ID/state;
- convergence/residual summary;
- force/moment vector and reference point;
- coefficient references;
- field artifact references;
- stale/current attachment state;
- uncertainty/grid-convergence summary where available.

Keep large fields outside scalar FreeCAD properties; store references/manifests rather than bloating the document.

## 8. VibeCADAeroContext target

Assistant context must remain bounded. Expose:

- current Aero result summary;
- method/evidence/qualification;
- current/stale status;
- job summary and progress;
- geometry readiness;
- available field/result artifacts;
- reason when capability unavailable/unqualified.

Do not inline solver traces or large fields.

## 9. Geometry and mesh requirements

### Exact geometry

Use current host exact artifact semantics for Native B-rep/STEP.

### Surface derivation

Record tessellation method/tolerance, source SHA/revision and face correspondence.

### Fluid domain

Domain generation is a separate artifact from body surface. Record extents, boundary roles and blockage rules.

### Meshing

Record mesher/version/settings, quality metrics, near-wall policy and topology correspondence. Unknown Gmsh surface element types remain rejected unless explicitly implemented.

### Voxelization

Require explicit physical scale and coordinate transform. Record lattice resolution/domain/units and moving-body identity.

## 10. FluidX3D implementation requirements

- vendor pinned source under third-party/vendor location;
- preserve upstream license and component notices;
- build VibeCAD bridge against pinned API;
- configure required compile-time options explicitly;
- establish stable freestream/domain/boundary conditions;
- use correct Units conversion for force/torque/time;
- return dimensional body-frame force/moment or enough transform metadata for VibeCAD to project it;
- return field artifacts with mapping/provenance;
- qualify high-Re settings by benchmark envelope;
- support moving/revoxelized geometry as later dependency-ordered implementation, not deleted scope.

No software use-purpose policing is part of the adapter.

## 11. OpenFOAM/CfdOF implementation requirements

Use current CfdOF API rather than rewriting case authoring. Complete and qualify:

- external-aero domain;
- incompressible RANS baseline;
- turbulence model/y+/near-wall policy;
- transient URANS path;
- transition/compressible/rotating paths when implemented;
- force/forceCoeffs parsing;
- residual/convergence summary;
- volume/surface field export;
- field→CAD mapping;
- solver/version/settings provenance;
- grid-convergence qualification.

## 12. Routing

Every auto route returns a rationale. Candidate eligibility includes availability, qualification and live resource data. Unknown quota remains unknown. Do not hard-code “30 hours/week” or a permanent Kaggle GPU type. Current provider docs are evidence that accelerator assumptions can change.

## 13. UI requirements

Integrate into existing Aero/native surfaces:

- first-use informational notice once;
- case/geometry-readiness panel;
- solver/provider route explanation;
- mesh/domain preview;
- background job list/status/cancel/reconnect;
- logs/convergence/residuals;
- stale/current result history;
- field controls (Cp/pressure/velocity/vorticity/Q, clips/slices/probes/streamlines/playback);
- qualification/benchmark details;
- host Apply/Reject for CAD repair previews.

## 14. Qualification program

Qualification is immutable/versioned evidence. At minimum:

- solver build SHA/version;
- model/turbulence/collision selection;
- benchmark source and geometry;
- settings hash;
- observable tolerances;
- Re/Mach/alpha/geometry envelope;
- mesh/grid convergence evidence;
- date/environment/hardware where relevant.

A successful run outside the envelope is still `model_unqualified`.

## 15. Test matrix

### Pure reference

- case/frame coefficient math;
- dynamic model scalar/vector parity;
- strip geometry semantics;
- rigid-body equations;
- mesh topology conversion;
- routing determinism;
- qualification envelope;
- one-time informational acknowledgement;
- host evidence/artifact mapping;
- geometry-readiness independence;
- Native repair stale/intent checks;
- detached input hashing/attachment guard.

### Host integration

- current upstream regression suite;
- `/v1/aero` revision propagation;
- host preview apply/reject + user-explicit preservation;
- Native session closure does not cancel CFD jobs;
- `/v1/run` cannot bypass Aero mutations.

### Solver integration

- FreeCAD geometry export/mesh mapping;
- FluidX3D compile/run/force/field benchmark;
- CfdOF case write/OpenFOAM run/parse;
- Kaggle credentialed private notebook flow and reconnect;
- current-vs-stale result attachment.

## 16. Definition of done

The complete canonical target is done only when the user can construct or select real geometry, obtain transparent multi-fidelity analyses locally or remotely, inspect qualification/convergence and live/archived fields, run moving/unsteady/flight/FSI workflows, and have every result tied back to exact source geometry/case/solver evidence without bypassing VibeCAD's host authority.


### Non-destructive runtime cutover invariant

Parity is established with **observation-only shadow traces**, never by running the legacy and generic solver paths simultaneously. Exactly one path owns process execution and exactly one Native transaction owns publication for each real job. Cutover is per responsibility/per solver and remains revertible behind compatibility facades. Durable reattachment uses exact domain dependencies rather than document labels/paths; publication is idempotent against retries/reconnect/callback replay. See `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md` and `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`.
