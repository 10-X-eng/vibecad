# VibeCADAero second-pass preservation supplement

Recovered and consolidated on 2026-08-25. This document is a no-loss supplement to `RECOVERED_ADVANCED_VIBECAD_ROADMAP.md`. It preserves the detailed definitions that were flattened or omitted from the first recovery summary.

This is a preservation record, not permission to patch the active VibeCAD repository. The recovered reference package repeatedly requires a fresh live-source freeze and reconciliation before implementation.

## 1. Preservation baseline and chain of custody

The strongest recovered source is the corrected package:

- package: `VibeCADAero_Reconciliation_Pass_03_Correction_01_df07a5e.zip`;
- package SHA-256: `AB0E315D811F5FD77D0D4FA9220E5511481C57AA8AA65128F23D4475030915ED`;
- frozen VibeCAD design source: `halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`;
- Pass 02 comparison baseline: `d0a933e40005b4affe9303f27d1eae5cd36eb030`;
- recorded Pass 03 delta: 41 commits ahead, 0 behind, 50 changed files;
- FluidX3D source pin: `ProjectPhysX/FluidX3D@8986874e626e0aebd317ab16c420b39e30dfa273`;
- CfdOF source pin: `a90f60c2313ceba09c236c81f0693d93357d1614`;
- recovered package contents: 110 files, including 49 top-level planning, validation, risk, policy, and migration documents plus a proposed overlay, tests, schema, bridge source, vendor manifest, and authoritative FluidX3D license copy;
- validation recorded by the package: 45 reference-overlay tests passed. This proves the reference semantics exercised by those tests, not production integration into live VibeCAD.

The byte-for-byte recovered package is preserved beside this document. The archive is the ultimate reference if a summary below is ever ambiguous.

## 2. Corrections to the first recovered roadmap

The earlier ten-stage list is a useful executive grouping, but it is not the package's exact implementation sequence. The corrected plan defines a 21-step, dependency-ordered program with separate correctness, migration, persistence, publication-authority, solver, UI, qualification, and coupled-physics phases.

The following points must not be lost:

1. A host-owned VibeCAD Analysis Runtime is a prerequisite, not an optional later cleanup.
2. That runtime is extracted by a characterization-first strangler migration from existing Native Background and detached FEM code.
3. Existing FEM must be the first production client and the behavioral oracle. Aero may become the second client only after FEM parity, burn-in, and rollback gates pass.
4. Shadowing is observation only. It must never launch both legacy and new solvers or create two publication owners for one real job.
5. Durable persistence is a later, separate change after in-memory FEM parity.
6. Durable publication authority is another separate change after persistence. A stored job carries provenance, not standing permission to mutate CAD.
7. Solver selection and compute-provider selection are separate decisions. A provider must never silently change the requested physics method.
8. Execution success, result publication/currentness, and engineering qualification are three distinct state systems.
9. CFD jobs are not Native mutation previews. CAD-changing Aero repair proposals must converge onto host Native preview/apply/reject authority, while long-running analysis jobs remain host Analysis Runtime jobs.
10. The one-time FluidX3D-related notice is informational only. It must not become a use-purpose detector, entitlement gate, commercial/military classifier, telemetry event, or output-ownership rule.

## 3. Canonical ownership and authority law

The recovered architecture states the ownership rule as:

> VibeCAD owns jobs. Domains own engineering meaning. Compute providers own execution location. Native owns CAD mutation. Evidence and qualification own claims.

### VibeCAD host owns

- globally unique analysis and job identity;
- lifecycle state and valid transitions;
- immutable prepared-input manifests;
- generic dependency snapshots supplied by domains;
- provider submission and reconnect identity;
- local and remote execution orchestration;
- progress, cancellation, timeout, bounded logs, and generic failures;
- immutable artifact manifests and hashes;
- durable job metadata and restart recovery;
- source-currentness validation orchestration;
- publication scheduling onto the document thread;
- quarantine/currentness disposition and generic execution receipts.

### Engineering domains own

For FEM, Aero, thermal, EM, acoustics, optimization, and future domains:

- which source state is relevant to the computation;
- conversion of live document/domain state into immutable solver inputs;
- domain dependency fingerprints and currentness meaning;
- solver-specific configuration and physics;
- output parsing and result meaning;
- publication-draft construction;
- qualification, validation, and claim ceilings;
- domain-specific cleanup and result representation.

### Compute providers own

- where and how sealed work executes;
- capability description;
- local launch or remote submission;
- provider job identity;
- status, progress, cancellation transport, reconnect, collection, logs, and provider execution receipts.

Providers do not choose physics, mesh policy, turbulence model, qualification state, or CAD publication eligibility.

### Native mutation authority owns

- document-thread transactions;
- recompute and postcondition validation;
- created, changed, and deleted object receipts;
- structural revision changes;
- rollback on publication failure.

No worker, provider, solver adapter, or Analysis Runtime component may mutate a live FreeCAD document directly.

### Evidence and qualification own

- `not_solved` versus solved;
- `model_unqualified` versus a qualified model/envelope;
- measured, derived, and presentation distinctions;
- benchmark scope and engineering claim ceilings.

`returncode == 0` is never qualification.

## 4. Three distinct authorities for long-running work

The corrected plan separates authority across time.

### SubmissionAuthorization

Submission authorizes creation of one exact immutable prepared analysis from a captured source state. Persisted submission data may contain document UID, captured revisions/dependencies, prepared-input manifest, origin capability/action, and inert receipt/idempotency identifiers.

It must not persist a live `NativeRuntimeContext`, executable `NativeCallTicket`, reauthorization callback, FreeCAD object, Qt object, transaction handle, Python closure, provider secret, or agent/session token.

### ExecutionAuthority

Execution authority permits only execution and collection of the sealed prepared work. It may launch, poll, cancel, reconnect, download, hash, and perform FreeCAD-independent parsing. It cannot change the analysis, mutate CAD, attach result objects, or manufacture a qualification claim.

### PublicationAuthorization

Publication requires a fresh, narrow host authorization for the exact completed job. Before CAD mutation, the publication coordinator must prove:

1. exact persisted job/submission identity;
2. validated immutable output manifests and hashes;
3. unambiguous rebind of the exact source document;
4. domain resolution of intended current targets;
5. a domain `CurrentnessReport` against frozen dependencies;
6. known compatible publication adapter/recipe version;
7. no existing successful publication receipt for the same publication identity;
8. fresh current Native authorization for this exact publication;
9. document-thread mutation through the existing Native transaction boundary;
10. successful postconditions and atomic receipt creation, otherwise rollback.

Initial FEM migration preserves its existing original-ticket/global-structural-revision publication behavior. The durable publication coordinator is added independently and proven before long-running Aero CFD depends on it.

## 5. Three independent state axes

These states must not be collapsed into one `SUCCEEDED` flag.

### Execution state

Canonical target vocabulary includes:

`PREPARING -> PREPARED -> QUEUED -> SUBMITTING -> RUNNING -> COLLECTING -> SOLVED`

Provider-specific/internal stages may also include `UPLOADING`, `SUBMITTED`, `CANCELLING`, `DOWNLOADING`, and `PARSING`.

Terminal alternatives include:

`FAILED`, `CANCELLED`, `TIMED_OUT`, `CAPABILITY_UNAVAILABLE`, and `ORPHANED`.

`SOLVED` means declared execution and required output collection/integrity parsing completed. It does not mean published or qualified.

### Publication and currentness state

Canonical vocabulary:

`UNVALIDATED`, `VALIDATING_SOURCE`, `AWAITING_SOURCE`, `AWAITING_PUBLICATION`, `CURRENT`, `STALE`, `QUARANTINED`, `PUBLISHING`, `PUBLISHED`, and `PUBLICATION_FAILED`.

Laws:

- solved does not imply published;
- published does not imply qualified;
- stale does not imply solver failure;
- a closed/missing source is `AWAITING_SOURCE`, not a fabricated failure;
- no valid fresh host publication context is `AWAITING_PUBLICATION`, not an authority workaround;
- relevant dependency drift produces stale/quarantined historical evidence;
- failed publication preserves immutable solver outputs.

### Engineering evidence state

The package defines:

- `evidence_waiting` with claim ceiling `not_solved` for a prepared case with no accepted result;
- `capability_unavailable` when the dependency or route is unavailable;
- `failed` for execution, collection, parsing, or validation failure;
- `model_unqualified` for a parseable completed numerical result without a matching qualification record;
- `model_qualified` only when exact build/model/settings and requested envelope are covered by versioned benchmark evidence;
- `measured` only for direct authoritative measurement, never screenshots or CFD output.

No state implies airworthiness.

## 6. Atomic cancellation and publication law

The frozen Native Background implementation contains a known check-to-commit race: cancellation can be accepted while a document-thread callback is between its final cancellation check and transition to `committing/finalizing`.

The target runtime therefore needs one linearizable operation equivalent to:

`try_begin_publication(job_id) -> ACQUIRED | CANCELLED | NOT_READY`

Under one lifecycle lock/transaction it must load the exact job/attempt, reject terminal or ineligible work, observe cancellation, terminalize if cancellation won, or assign exactly one publication owner and enter a non-cancellable publication critical section before releasing the lock.

Forbidden outcome: cancellation returns accepted and the job later mutates CAD.

Acquiring the lifecycle gate is not itself CAD authority. Provenance validation, source rebind, domain currentness, replay detection, fresh Native authorization, document-thread transaction, postconditions, and one publication receipt still follow.

## 7. Document identity, Save/close/reopen, and recovery

The host identity seam is `document_uid(document)`, sourced from FreeCAD `Document.Uid`. The plan explicitly says not to invent a second document-ID system during extraction.

Before automatic durable reattachment, characterize `Document.Uid` through:

- Save;
- Save As in the same live document;
- Save Copy or copied-on-disk reopen;
- clone/duplicate operations;
- close/reopen;
- autosave/recovery restoration;
- import into a new document;
- simultaneous copies;
- duplicate labels and paths.

Paths, labels, project names, and visual/content similarity are informational only and never attachment authority.

Target behavior:

- switching active documents does not redirect publication;
- closing the source may allow detached execution to continue;
- completed output with no exact source becomes `AWAITING_SOURCE`;
- reopening is a new binding event followed by exact UID and dependency revalidation;
- relevant changes produce stale/quarantined evidence;
- unrelated changes may eventually be tolerated for Aero only when domain-scoped dependencies prove irrelevance;
- initial FEM remains globally strict until a separately approved behavior change;
- duplicate callback, reconnect, UI retry, or crash recovery returns the existing publication receipt instead of creating a second result graph.

On application restart:

- completed/published jobs reopen as historical records;
- reconnect-capable remote jobs use their authoritative provider ID;
- local jobs without authoritative process reattachment become compatibility-visible failed with structured `host_interrupted`, or `ORPHANED` internally;
- leftover files or PIDs never prove success;
- outputs require manifest/hash validation;
- no job is republished merely because a document with the same name/path opens.

## 8. Durable records, artifacts, and crash consistency

The target uses two kinds of records.

### Durable JobDescriptor

Compact serializable data may include:

- job, analysis, attempt, domain, adapter, solver, and provider identity;
- exact source document UID and dependency snapshot;
- immutable input/output manifests and hashes;
- provider external job ID;
- state, timestamps, cancellation/deadline data;
- bounded log/event references;
- execution receipt and structured failure;
- publication/currentness and cleanup disposition;
- inert `PublicationDescriptor` with publication identity, adapter version, exact output-manifest identity, result identity, and source/dependency provenance.

### Ephemeral JobRuntimeHandle

Never persist:

- FreeCAD documents/objects;
- Python callables or closures;
- futures, threads, locks, events, or `Popen` objects;
- file descriptors and GUI objects;
- provider clients carrying secrets;
- plaintext credentials/tokens;
- unbounded output buffers.

Recommended durability target is a versioned transactional SQLite database in VibeCAD per-user application data plus a content-addressed/immutable artifact directory. FCStd stores only compact references and engineering evidence, not multi-gigabyte CFD/FEM artifacts.

Crash-safe ordering is:

1. persist the prepared descriptor before provider submission;
2. persist provider external ID immediately after successful submission;
3. seal and persist output artifact manifest before publication;
4. persist publication intent/gate where supported;
5. commit the CAD transaction;
6. persist the publication receipt;
7. only then persist terminal success.

On an ambiguous crash boundary, inspect durable receipt/currentness evidence; never assume success.

Artifacts require role, logical name/storage reference, media type, byte count, SHA-256, producer/job/provider/solver identity, input-manifest correlation, exactness class where applicable, and creation/retrieval time. Reject path traversal, unsafe symlinks, unsafe archive extraction, hash mismatch, and unbounded bundles.

## 9. Local process-control requirements

The existing helper controls only the direct `Popen`; descendant MPI ranks, wrappers, and helper processes may survive. This is a known hazard to characterize and, if reproduced, fix independently before provider extraction.

The target `LocalProcessProvider` owns the complete launched process tree:

- POSIX: dedicated session/process group, graceful group signal, bounded hard kill, root reap, reader shutdown, and child verification where feasible;
- Windows: isolated process group plus a Windows Job Object or equivalent reliable descendant ownership, solver-supported graceful signal, bounded tree termination fallback, stream drain/join, and handle closure.

Cancellation, timeout, and host shutdown remain distinct facts. Timeout records timeout even if cleanup succeeds; cancellation records user/host intent and provider acknowledgment; shutdown follows explicit provider survival/reconnect policy.

Logs are bounded/streamed, secrets are redacted before persistence, and cleanup is effect-idempotent: stop resources, close readers/handles, seal artifacts, release provider resources, then remove disposable workspaces only when no retained evidence depends on them.

## 10. Solver ladder and provider separation

The defined multi-fidelity ladder is:

1. Level 0 — geometry, mass, and configuration truth;
2. Level 1 — NeuralFoil;
3. Level 2 — AeroSandbox lifting-line/VLM;
4. Level 3 — engineering unsteady, strip, dynamic-stall, and hover models;
5. Level 4 — FluidX3D high-throughput GPU LBM;
6. Level 5 — OpenFOAM through CfdOF;
7. Level 6 — diagnostics, decomposition, comparison, and post-processing.

Solver/backend means the physics/model. Provider means where sealed work runs.

Defined providers:

- in-process/local for appropriate low-order work;
- detached local CPU/GPU process;
- Kaggle remote compute;
- future HPC/remote providers.

The provider contract conceptually exposes `capabilities`, `submit`, `poll`, `cancel`, `reconnect`, `collect`, and `cleanup`. Capability flags explicitly state reconnect, cancellation, accelerator, log-streaming, and size-limit support.

Auto-routing must be deterministic and explainable. Eligibility uses actual availability, qualification, portability, resource estimates, and live quota/device data. Unknown quota remains unknown. No permanent `30 hours/week`, T4, P100, or other accelerator assumption is architecture.

## 11. Canonical aerodynamic case/result contracts

The reference schema version is `vibecad.aero.cfd/1`.

### Coordinate and force contract

- body axes: `+X` forward, `+Y` right, `+Z` down;
- freestream is expressed in body axes in m/s;
- every solver records body-to-solver transform and origin;
- forces and moments normalize back into body axes;
- drag/lift/side are projected onto an explicit aerodynamic basis derived from freestream and configured lift-up vector;
- no backend may silently assume `Fx = drag` or `Fz = lift`;
- force/moment reference point must be explicit;
- coefficient references explicitly include density, velocity, `Sref`, length/chord, span, and moment reference;
- no hard-coded biplane reference policy may leak into the generic contract.

### Reference data structures

The recovered reference overlay defines:

- `Vector3`;
- `AeroBasis`;
- `ReferenceQuantities(area_m2, length_m, span_m, moment_reference_body_m, area_definition)`;
- `FlowConditions(freestream_body_mps, density_kg_m3, dynamic_viscosity_pa_s, temperature_k, static_pressure_pa, turbulence_intensity, turbulence_length_scale_m)`;
- `Artifact(path, sha256, media_type, size_bytes, role, metadata)`;
- `GeometryArtifact(artifact, geometry_revision, source_object_names, source_units, solver_units, tessellation tolerances)`;
- `SolverSpec(backend, model, backend_version, settings)`;
- `ComputeSpec(provider, accelerator, settings)`;
- `AeroCase(case_id, geometry, flow, references, solver, compute, lift_up_body, metadata, schema_version)`;
- `ForceMoment`, `Coefficients`, `Diagnostics`, `CFDResult`, `PreparedJob`, and `ExecutionReceipt`.

The production `AeroCase` identity must also bind document UID, captured host Native revision, Aero geometry revision/hash, exact input geometry artifact hash, atmosphere/reference quantities, backend/model/build/settings, and provider request.

The JSON result schema requires `schema_version`, `case_id`, `solver_backend`, `compute_provider`, `state`, and `method`, with optional solver version, evidence/claim fields, force/moment, coefficients, diagnostics, and hashed artifact records.

Large fields stay outside scalar FreeCAD properties. FCStd receives small structured references/manifests.

## 12. Geometry readiness and source correspondence

Artifact exactness and CFD readiness are independent.

Canonical readiness ladder:

1. `unknown`;
2. `brep_accepted`;
3. `surface_closed`;
4. `surface_watertight`;
5. `fluid_domain_ready`;
6. `mesh_ready`;
7. `solver_input_frozen`.

Required evidence by stage:

- B-rep accepted: source object and revision identity, validity/non-null checks, units/transforms, exact artifact hash;
- surface closed/watertight: tessellation method/tolerances, free-edge/non-manifold/intersection checks, orientation/normals, component policy;
- fluid-domain ready: separate domain artifact, recorded extents, unambiguous inlet/outlet/farfield/wall roles, blockage checks;
- mesh ready: mesher/version/settings, quality metrics, near-wall/refinement policy, source-face correspondence;
- solver input frozen: immutable tree hash, geometry/case/native revision, solver build/model/settings, immutable work directory or equivalent sealed artifact.

Native B-rep/STEP is `exact`; STL/OBJ/surface mesh, OpenFOAM volume mesh, FluidX3D voxel grid, CFD fields/results, and VTK/VTM are `derived`; screenshots/renders/animation frames are `presentation`.

Every derived artifact identifies source geometry, case, settings, conversion, and producer hashes. Returned surface/field data preserves triangle or mesh-part to source-face/object correspondence where possible, so stale fields cannot be painted onto changed CAD.

Readiness does not imply manufacturability, flight testing, or airworthiness.

## 13. Aero result, stamp, and assistant-context requirements

`AeroStamp.py` must become method-aware rather than globally saying Aero is “not CFD.” Required fields include:

- `evidence_state`;
- `claim_ceiling`;
- `method`;
- `solver_finished`;
- `model_qualified`;
- `not_airworthy`;
- solver/version/model/settings hash;
- qualification ID when applicable;
- case ID, geometry revision, and result artifact reference.

Existing low-order results remain `model_unqualified` unless separately qualified.

`AeroResults.py` remains the durable engineering result authority and is extended additively with:

- case ID/hash;
- solver/backend/model/version;
- compute provider and provider job ID;
- captured Native revision and Aero geometry revision;
- frozen-input hash and artifact provenance;
- qualification ID/state;
- convergence/residual summary;
- force/moment vector and reference point;
- coefficient references;
- field artifact references;
- current/stale attachment state;
- uncertainty/grid-convergence summary.

`VibeCADAeroContext.py` remains bounded and exposes current result summary, method/evidence/qualification, current/stale status, job progress, geometry readiness, available field artifacts, and reasons for unavailability/unqualification. It must not inline huge fields or solver traces.

## 14. Native repair convergence

Immediate correction for `/v1/aero` repair propose/apply:

1. resolve active document;
2. resolve authoritative `document_uid`;
3. get `current_revision(document_uid)` from the host Native document state store;
4. pass that revision into `VibeCADAero.propose_repairs(..., native_revision=...)` and `apply_repairs`;
5. never trust a client-supplied revision as authoritative.

Target CAD-repair flow:

1. Aero proposes a domain repair payload with evidence;
2. host Native preview stores authorization arguments;
3. host UI/dispatcher exposes Apply/Reject;
4. host stale and user-explicit-intent checks run;
5. Aero domain code performs the exact requested repair;
6. Native records the mutation receipt;
7. Aero recomputes evidence.

Do not add a second generic Aero Apply/Reject controller. Preserve `AeroPreview` only as a compatibility seam until convergence is safe. Preview lifetime/persistence/retention remains an explicit open design item; the existing preview store is unbounded and outstanding previews are not exported/restored.

## 15. Exact first-use FluidX3D notice contract

The remembered backend is **FluidX3D**.

Canonical behavior:

- show once, on the first entry into VibeCADAero;
- title: `Third-Party Software Notice`;
- checkbox text exactly: `I understand.`;
- action: `Continue`;
- persist one local unversioned boolean;
- reference implementation preference group: `User parameter:BaseApp/Preferences/Mod/VibeCADAero`;
- reference key: `ThirdPartyNoticesAcknowledged`;
- normally never show again, including after product/backend/license-document updates;
- never transmit the flag;
- the bit records only that the notice was seen.

It is not:

- an `I agree` contract;
- a purpose or intended-use declaration;
- a commercial/military/research classification;
- telemetry;
- an entitlement or compliance check;
- a solver-selection rule;
- a restriction on VibeCAD, VibeCADAero, unrelated backends, user-created designs, or output ownership.

FluidX3D terms remain component-specific and authoritative in its included license. The reviewed source-available license contains commercial-use, military-use, AI-training, attribution/alteration, publication/source, citation, and license-retention conditions. The public project did not publish a standard commercial agreement, price, EULA, redistribution grant, or deployment model at the recovered pass, so VibeCAD must not invent one.

The corrected vendor policy is:

- vendor pinned source under `src/Mod/VibeCADAero/vendor/FluidX3D/`;
- preserve upstream source, license, origin, and readable notices;
- build/package a VibeCAD-owned bridge against the pinned API;
- allow an explicitly configured external bridge override;
- normal run does not auto-download solver source;
- no purpose-of-use questions, per-run legal prompts, or product-wide use profiles;
- re-vendoring freezes a new commit, rechecks API/build/license docs, rebuilds the bridge, reruns unit/scale/force/torque/domain/refinement/field/packaging tests, and updates the pin.

The package also records an earlier commercial-build correction: commercial distributions exclude/disable the vendored payload by default unless compatible commercial permission permits bundling; separately installed FluidX3D may be used under the actual granted terms. That distribution choice does not turn runtime job logic into a license/purpose enforcement system.

## 16. FluidX3D completion requirements

The FluidX3D path is not complete merely because a process launches.

Required work:

- pin and vendor the real source;
- build the VibeCAD bridge against verified source APIs and explicit compile-time options;
- preserve an external bridge override;
- define stable freestream, body, domain, and boundary conditions;
- require explicit physical geometry scale and CAD/body/solver coordinate transform;
- use correct FluidX3D Units conversion for time, force, and torque;
- return dimensional body-axis force/moment or enough transform metadata to normalize it;
- distinguish torque about center of mass from moment about the requested Aero reference;
- export volume/surface fields with source mapping and provenance;
- bind GPU/device, lattice, resolution, domain, transient/sample/averaging, model, build, and settings facts;
- produce deterministic fixtures and tests for scale, units, sign, timeout, cancellation, cleanup, stale attachment, and fields;
- qualify high-Re use with collision model, stability, SGS/turbulence treatment, wall treatment, lattice Mach/Re envelope, blockage/domain sensitivity, averaging, grid convergence, and benchmark evidence.

The reference bridge uses SI job inputs, explicit STL physical size, domain/resolution controls, transient/sample controls, and a result location. Reference executable resolution checks explicit solver setting/environment override before vendored `bin` candidates. These are reference semantics, not proof the bridge is production-integrated.

## 17. OpenFOAM/CfdOF completion requirements

Required work:

- complete the normal FreeCAD CfdOF/OpenFOAM analysis path;
- create/associate the fluid domain and body surfaces;
- define boundary conditions and turbulence/model choices;
- write a real case and execute through the host provider/runtime;
- parse force, moment, coefficient, residual, convergence, and field outputs;
- preserve mesh, domain, case, solver build/version, process, and artifact provenance;
- preserve coordinate/frame/reference transforms;
- publish only after currentness and Native authorization;
- retain solver success below qualification and airworthiness claims;
- qualify with benchmark and mesh/grid/domain/model evidence.

## 18. Kaggle and remote-provider requirements

Kaggle is a compute provider, never a solver.

The defined route requires:

- current credentialed CLI/service behavior;
- explicitly prepared portable private notebook/kernel bundles;
- live quota and accelerator/machine-shape discovery;
- job-size and throughput estimates labeled as estimates;
- explainable route selection;
- submission, provider job ID persistence, status polling, bounded logs, reconnect, output download, checksum verification, failure recovery, and cancellation where supported;
- no attempt to treat a local absolute FluidX3D/OpenFOAM executable path as remotely runnable;
- no fixed weekly quota or permanent GPU model assumption;
- remote completion followed by artifact verification, domain parsing, exact source/currentness revalidation, and document-thread Native publication.

Remote providers come after the local provider, durable host job identity, persistence/recovery, and publication authority are stable.

## 19. Required UI surfaces

The complete UI target includes:

- the one-time informational notice;
- case setup and exact geometry/readiness panel;
- selected solver and provider with auto-route explanation;
- mesh and fluid-domain preview;
- background job list, progress, status, cancel, reconnect, and recovery state;
- bounded logs, convergence, residuals, and diagnostics;
- current versus stale/historical result timeline;
- solver comparison and discrepancy views;
- qualification/benchmark details and envelope applicability;
- common field viewer for `Cp`, pressure, velocity, vorticity, Q-criterion, clips, slices, probes, streamlines, and transient playback;
- exact field/mesh/source correspondence and provenance;
- host Native Apply/Reject for CAD-changing repair previews.

UI convenience must not collapse state axes or silently attach stale fields.

## 20. Dynamic, moving, coupled, and refinement scope retained

These capabilities remain defined scope and were not optional embellishments:

- moving bodies and control surfaces;
- rigid-body motion laws and moving-boundary/revoxelization behavior;
- rotor/propeller fidelity ladder and propulsion interaction;
- engineering unsteady models, strip theory, dynamic stall, and hover;
- complete 6-DOF with lateral, control, propulsion, wind, and gust providers;
- JSBSim retained as production 6-DOF authority unless explicitly changed; internal rigid-body equations are verification/reference;
- aeroelasticity and FSI with structural authority, field/mesh mapping, partitioned coupling, timestep ownership, relaxation, convergence, and flutter validation;
- wake, mid-field, far-field, decomposition, uncertainty, and grid-convergence diagnostics;
- cross-fidelity disagreement localization;
- parameter, mesh, model, and fidelity refinement workflows;
- controlled engineering-knowledge accumulation from validated/qualified evidence only.

The refinement loop is:

`low-order -> high-fidelity -> disagreement localization -> refine mesh/model -> re-solve -> qualification/uncertainty -> reusable evidence`.

Stale, failed, or unqualified results remain inspectable history and are never silently promoted.

## 21. Exact dependency-ordered implementation sequence

This is the detailed sequence preserved by `BUILDER_HANDOFF.md`.

### Step 0 — live re-reconciliation

Freeze then-current `main`, compare from Pass 03, and reread Aero, Native preview/control/state/dispatch, background/FEM execution/state/process, mutation authority, artifacts/evidence, tests, and build files. Adopt newer upstream work where it already owns a seam.

### Step 1 — characterize current FEM/background behavior

Add executable characterization and golden normalized lifecycle traces for process, input digest, command/environment, stale checks, result graph/History, receipts, public job APIs, cancellation/timeout, and platform behavior. No new runtime capability.

### Step 2 — introduce host Analysis contracts/facades

Add domain-neutral host contracts while the old implementation remains underneath. Preserve all public modules/functions/actions. Optional shadow trace is observation-only.

### Step 3 — extract local process mechanics

Move process execution behind `LocalProcessProvider`, preserving argv, environment, cwd, polling, timeout, cancellation, cleanup, and error compatibility. Fix descendant-process ownership only in its isolated correctness slice.

### Step 4 — extract input/artifact sealing

Generalize safe detached workspaces and manifests while preserving the exact FEM digest behavior additively. No scheduler or persistence change.

### Step 5 — extract orchestration behind NativeBackground

Keep `NativeBackgroundManager` as compatibility surface while generic prepare/worker/commit orchestration moves underneath. Preserve one active job per document and current `native.job` behavior.

### Step 6 — migrate FEM one solver at a time

CalculiX first, then Elmer/Mystran/Z88 according to live support/coverage. Prove identical inputs, command/environment, currentness, result graph, History, receipt, and errors. Legacy FEM remains oracle and rollback route.

### Step 7 — stabilization interval

Run and merge the host runtime with FEM before Aero depends on it. Resolve lifecycle/threading defects without concurrently debugging CFD.

Current checkpoint status: the installed FEM route now has an explicit, bounded exact-source reopen repair. A saved/closed/reopened document may resume publication only when the active live document has the captured `Document.Uid` and the captured solver state, `History` identity, keep-results setting, and runtime publication preferences are unchanged. The captured solver/result-importer references are then rebound to the live objects, but publication still passes through the original Native mutation ticket and global structural revision checks. Closed sources, switched documents, same-name replacements with a different UID, and every tested state drift reject as stale. The same behavior is exercised through the Native route, the human GUI route, and an installed Windows command-line integration gate.

The installed lifecycle gate uses deterministic synthetic result fields and therefore proves lifecycle/currentness/publication behavior only. It does not prove physical solver/backend correctness, POSIX installation behavior, durable restart/orphan recovery, fresh publication authorization, replay-idempotent durable receipts, or complete leak/orphan burn-in. Step 7 remains partial, and Steps 8 and 8A remain separate required work.

### Step 8 — add durable host metadata/artifact persistence

Versioned transactional local persistence and restart/orphan/reconnect semantics. Keep large artifacts outside FCStd. Do not change physics or concurrency here.

### Step 8A — add durable publication authority

Implement inert publication descriptors, exact `Document.Uid` rebind, provenance/currentness checks, fresh Native publication authorization, and replay-idempotent receipts. Keep existing FEM publication semantics unchanged while proving this independently.

### Step 9 — close the Aero/Native repair revision gap

Thread actual host revision into `/v1/aero` propose/apply and converge CAD repair authorization onto host preview/apply/reject.

### Step 10 — integrate Aero evidence, readiness, frames, and correspondence

Adopt host evidence/artifact taxonomy; complete CAD/body/solver frames, reference quantities, geometry readiness, source correspondence, and current/stale semantics.

### Step 11 — make Aero the second Analysis Runtime client

Implement Aero prepare, dependency snapshot, currentness, parse, publication draft, and qualification adapters. Preserve solver/provider separation.

### Step 12 — complete OpenFOAM/CfdOF baseline

Real domain, boundary conditions, turbulence/model, meshing, solve, result/field collection, publication, and qualification evidence.

### Step 13 — complete vendored FluidX3D baseline

Real vendored build/bridge, geometry scale, domain/boundaries, force/torque, fields, provenance, and benchmark fixtures; preserve external override.

### Step 14 — build the common field/result viewer

Unify high-fidelity result visualization while generic jobs/artifacts remain host-owned.

### Step 15 — add explainable routing and Kaggle provider

Only after local provider, durable job identity, reconnect/recovery, and publication safety are stable.

### Step 16 — qualification engine and high-Re FluidX3D

Versioned benchmark registry/envelopes followed by high-Re model, domain, averaging, stability, and grid-convergence work.

### Step 17 — moving geometry and propulsion interaction

Rigid bodies, motion laws, moving boundaries/revoxelization, rotor/prop fidelity, and feedback.

### Step 18 — unsteady and complete 6-DOF

Validated unsteady/strip/dynamic-stall models; lateral, control, propulsion, wind/gust providers; JSBSim production route retained.

### Step 19 — aeroelasticity and FSI

Structural authority, mapping, partitioned coupling, timestep/relaxation/convergence, and flutter validation.

### Step 20 — advanced diagnostics and controlled refinement

Wake/decomposition, uncertainty/grid convergence, cross-fidelity comparison, and refinement workflows composed from host jobs rather than a new scheduler.

## 22. Release gates that cannot be skipped

### Gate 0 — fresh source freeze

Freeze live main, diff from the recovered anchor, reread relevant code/tests/build registration, and update the drift record.

### Gate 1 — characterization

Record public API, process, digest, result graph, History, receipt, errors, timeout/cancel, document lifecycle, and platform behavior before extraction.

### Gate 1A — cancellation/commit race

Stress and reproduce the race. If real, fix it separately and re-baseline the oracle.

### Gate 1B — process-tree ownership

Test child-spawning processes on Windows and POSIX. If descendants survive, harden separately and re-baseline.

### Gate 2 — pure state-machine tests

Prove cancellation/publication linearizability, monotonic terminal state, replay safety, idempotent cleanup, and state-axis separation.

### Gate 3 — local provider tests

Prove direct argv execution without shell, environment/cwd preservation, bounded logs, timeout/cancel, process-tree cleanup, output sealing, unsafe-path rejection, and secret redaction.

### Gate 4 — document lifecycle tests

Same exact live source publishes; switched, closed, same-name replacement, different-UID reopen, or changed source does not. Any separately approved exact-UID reopen path must revalidate all captured currentness inputs before publication, and stale output remains attributable history.

Current checkpoint evidence covers exact-UID reopen plus refusal for a closed source, a switched document, a same-name/different-UID replacement, solver-state drift, `History` drift, and runtime-preference drift on installed Windows. Physical solver execution and installed POSIX coverage remain open, so Gate 4 is not yet closed.

### Gate 5 — FEM A/B parity

Compare solver files/hashes, command/environment, return behavior, result object graph, membership, History, state hashes, receipts, public JSON/errors, and cleanup.

### Gate 6 — persistence/recovery fault injection

Test crash at queued, submitted-before-ID, running, output-sealed, waiting-to-commit, and receipt-written-before-terminal-success boundaries.

### Gate 6A — durable publication authority

Prove document UID semantics, inert persistence, awaiting-source/publication states, domain drift quarantine, fresh Native authorization, one receipt, rollback, preserved artifacts, and mutual exclusion of accepted cancellation and publication.

### Gate 7 — rollback exercise

Run representative cases through the extracted path, switch back to legacy, and prove no schema/CAD state prevents fallback.

### Gate 8 — Aero adoption

Only after FEM parity/burn-in, wire Aero to shared execution while preserving Aero domain authority and keeping physics out of the host runtime.

## 23. Protected compatibility surfaces

The migration must preserve:

- capability `analyze.solver_execution`;
- operation `run`;
- existing target schema and request modes;
- background response `job` and `next` shape;
- `native.job` actions `status` and `cancel`;
- current Native/Aero binding, dispatcher, ribbon, GUI, and agent routes;
- existing Native error families/codes and transaction behavior;
- one-active-job-per-document during extraction;
- FEM result object graph, prior-result replacement, History insertion, state hashes, and mutation receipts;
- installed-tree/CMake registration and downstream Python import paths;
- old modules as compatibility facades/re-exports during the migration window.

Do not invent `start`, `clear`, a renamed capability, or a raw `/v1/run` solve/mutation escape hatch. New global discovery/recovery APIs are additive and explicitly versioned.

The temporary internal switch may select `legacy_fem_execution` or `analysis_runtime_fem`; it is a rollback mechanism, not a permanent user product option.

## 24. Hard extraction boundary

The host runtime may extract only physics-neutral mechanics: IDs, lifecycle transitions, queue/concurrency policy, generic progress/log/failure records, cancellation and atomic publication gate, timeout plumbing, provider identity/interface, local process supervision, durable metadata, artifact manifests/hashes, restart reconciliation, and scheduling a domain-owned publication callback on the document thread.

The following remain FEM-owned and must not be generalized into the host core:

- `compute_solver_input_state()` and its semantic dependencies;
- solver settings and implementation identity;
- analysis membership/ownership;
- mesh validity and solver/mesh association;
- result-root selection/removal;
- `vibecad_fem_history` roles/ownership/children;
- CalculiX/Elmer/Mystran/Z88 preparation and result import;
- result-object state hashes/currentness;
- History `solve` semantics;
- FEM transaction names, visible errors, capability identity, and claim semantics.

Preparation that touches live FreeCAD/FEM state remains on the document thread and, in the frozen implementation, occurs before background submission. Do not move it into workers during extraction.

## 25. Known hazards and prior errors to keep visible

### Historical technical errors already rejected

- incomplete “canonical” dumps that dropped accepted capabilities;
- replacing live `AeroReport`/result authority with parallel classes;
- standalone LBM engines bypassing `VibeCADAero.py` and stamps;
- invented FluidX3D Python APIs or CLI flags incompatible with real `main_setup`;
- force extraction without meaningful freestream/boundaries;
- fragile lattice/SI time, force, and torque conversion;
- scattered/hard-coded reference quantities and sign conventions;
- center-of-mass torque mislabeled as arbitrary-reference moment;
- underspecified STL physical scale;
- dummy Kaggle sleep mocks and fixed quota/auth assumptions;
- stale CfdOF API examples;
- conflating surface meshes with OpenFOAM fluid volumes;
- corrupt fallback handling of unknown Gmsh elements;
- unpinned binary-STL claims and overpromised mesh-to-solid repair;
- reduced dynamic stall mislabeled as full Leishman-Beddoes;
- underused pitch rate, scalar/vector model drift, mutable shared airfoil parameters;
- ambiguous semi/full-span integration and hard-coded strip density;
- unsteady initial-state loss, lost pitch rate, and double-advancing stateful aerodynamics;
- incomplete lateral/control physics labeled full 6-DOF;
- pressure coloring without source-face correspondence;
- missing result hashes/provenance and non-durable remote lifecycle.

### Live/integration hazards recorded by Pass 03

- `/v1/aero` revision propagation incomplete;
- parallel Aero repair authorization remains technical debt;
- Native preview store unbounded and outstanding previews not persisted;
- `AeroStamp`, `AeroResults`, and `VibeCADAeroContext` remain low-order-shaped;
- reference area policy and atmosphere are not yet unified;
- cross-backend transform/origin/moment-reference is not one live authority;
- new source files can be omitted from CMake/installed product;
- NumPy `<2` ABI constraint exists for bundled FreeCAD compatibility;
- FluidX3D/CfdOF pins and Kaggle service assumptions may drift;
- big-bang framework rewrite is destructive;
- genericizing FEM semantic state is the wrong boundary;
- persistence and process extraction must not land together;
- global document revision alone eventually over-invalidates expensive CFD;
- duplicate execution is invalid parity;
- duplicate result publication needs replay protection;
- path/label is not durable attachment authority;
- restart cannot infer local-job truth from leftover files/PIDs;
- downstream imports and installed packaging are compatibility surfaces;
- cancellation/commit and direct-parent process-stop hazards require isolated fixes.

## 26. Explicit stop conditions

Stop the current integration slice and reconcile if:

- upstream changes a relevant host seam while coding;
- a solver API assumption proves false;
- test evidence contradicts the plan;
- geometry/frame/reference identity is ambiguous;
- a result cannot be tied to source, case, version, and settings;
- implementation would bypass host authority;
- extraction changes observable FEM behavior without a separate approved behavior-change scope;
- a worker would require live FreeCAD object access;
- a durable schema migration lacks rollback/recovery;
- qualification or model limits cannot support the proposed claim.

## 27. Definition of done

The full target is complete only when a user can:

- construct or select real VibeCAD geometry;
- obtain transparent multi-fidelity analyses locally or remotely;
- inspect method, solver/provider, convergence, qualification, uncertainty, and current/stale state;
- inspect live and archived fields tied to exact source geometry;
- run moving, propulsion, unsteady, flight-dynamics, and FSI workflows;
- trace every result to exact source geometry, case, solver build, settings, provider execution, input/output artifacts, and publication receipt;
- do all of that without bypassing Native CAD authority or inflating evidence into qualification, manufacturability, or airworthiness.

## 28. Source-document index

The preserved archive includes these top-level records. Their names are retained here so future recovery can locate a definition rather than relying on memory.

### Canonical plan and handoff

- `README.md`
- `CANONICAL_ENGINEERING_PAPER.md`
- `CANONICAL_ARCHITECTURE.md`
- `CANONICAL_CODE_REFERENCE.md`
- `IMPLEMENTATION_SPEC.md`
- `BUILDER_HANDOFF.md`
- `MISSING_FROM_PLAN.md`
- `KNOWN_ERRORS_AND_BUGS.md`
- `RECONCILIATION_LEDGER.md`
- `CORRECTION_01_DEEPENING_LEDGER.md`
- `DIFF_FROM_PASS_01.md`
- `DIFF_FROM_PASS_02.md`

### Host Analysis Runtime contracts and migration

- `CORRECTION_01_HOST_ANALYSIS_RUNTIME.md`
- `HOST_ANALYSIS_RUNTIME_CONTRACT.md`
- `HOST_ANALYSIS_RUNTIME_ARCHITECTURAL_DECISIONS.md`
- `HOST_ANALYSIS_RUNTIME_MIGRATION_PLAN.md`
- `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md`
- `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`
- `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`
- `HOST_ANALYSIS_RUNTIME_RISK_REGISTER.md`
- `SOURCE_VERIFIED_HOST_RUNTIME_BASELINE.md`
- `DESTRUCTIVE_CHANGE_AUDIT.md`
- `NON_DESTRUCTIVE_MIGRATION_MATRIX.md`
- `FEM_CHARACTERIZATION_AND_PARITY_TEST_PLAN.md`
- `HOST_RUNTIME_COMPATIBILITY_CONTRACT.md`
- `HOST_RUNTIME_STATE_MACHINE.md`
- `HOST_RUNTIME_PROVIDER_CONTRACT.md`
- `HOST_RUNTIME_PROCESS_CONTROL.md`
- `HOST_RUNTIME_PERSISTENCE_AND_RECOVERY.md`
- `HOST_RUNTIME_DOCUMENT_LIFECYCLE.md`
- `HOST_RUNTIME_EXTRACTION_SEQUENCE.md`
- `HOST_RUNTIME_REGRESSION_GATES.md`

### Aero-specific definitions and policy

- `AERO_JOB_REUSE_DECISION.md`
- `AERO_FIRST_USE_INFORMATIONAL_NOTICE.md`
- `FLUIDX3D_COMMERCIAL_LICENSING_STATUS.md`
- `THIRD_PARTY_NOTICES.md`
- `POLICY_AND_RESTRICTION_PRINCIPLES.md`
- `GEOMETRY_READINESS_MODEL.md`
- `EVIDENCE_AND_ARTIFACT_TAXONOMY.md`
- `NATIVE_CONTROL_RECONCILIATION.md`

### Traceability and verification

- `UPSTREAM_BASELINE.md`
- `CURRENT_UPSTREAM_DRIFT_CHECK.md`
- `LIVE_DRIFT_AFTER_PASS_03.md`
- `SOURCE_TRACEABILITY.md`
- `RECHECK_PLAYBOOK.md`
- `VALIDATION_REPORT.md`
- `TEST_OUTPUT.txt`
- `SYNC_MANIFEST.json`
- `TREE.md`

### Proposed reference overlay

- canonical CFD contracts and result JSON schema;
- host-aligned evidence/readiness/repair/attachment reference modules;
- FluidX3D and OpenFOAM adapters;
- local and Kaggle providers;
- mesh, fields, routing, qualification, unsteady, strip, dynamic-stall, 6-DOF reference modules;
- FluidX3D C++ bridge, vendor manifest, policy, and license;
- reference host-runtime atomic-publication/state models;
- 45 reference tests.

## 29. No-loss handoff checklist

Before any future builder says the roadmap is “recovered,” confirm that the handoff still explicitly contains:

- FluidX3D and exact `I understand.` behavior/key/path;
- component-specific licensing and no runtime use-purpose policing;
- host/job/domain/provider/Native/evidence ownership split;
- submission, execution, and fresh publication authority;
- three state axes and all awaiting/stale/quarantine states;
- atomic cancel-versus-publication rule;
- exact document UID and Save/clone/reopen characterization;
- inert persistence and no serialized live authority;
- provider-owned process trees and idempotent cleanup;
- FEM-first strangler migration and observation-only shadowing;
- protected public APIs, compatibility facades, CMake/install-tree checks, and rollback switch;
- solver ladder and solver/provider separation;
- case/frame/reference contracts and explicit atmosphere;
- geometry-readiness ladder independent of artifact exactness;
- method-aware stamps, expanded results/context, and source-correspondent fields;
- OpenFOAM/CfdOF, vendored FluidX3D, Kaggle, common field UI, qualification/high-Re;
- moving/propulsion/unsteady/6-DOF/JSBSim/FSI scope;
- diagnostics/refinement and controlled evidence promotion;
- exact release gates, stop conditions, risks, and definition of done;
- the original corrected archive and its SHA-256.

If any of those disappears from a future summary, the summary is incomplete even if its shorter stage list sounds plausible.
