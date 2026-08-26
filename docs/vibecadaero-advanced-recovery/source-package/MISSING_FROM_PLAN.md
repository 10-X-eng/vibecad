# What Is Still Missing — Canonical Pass 03 Correction 01 Gap Register

Pass 03 closes or narrows several Pass-02 infrastructure gaps because upstream implemented them. This file lists only what remains materially incomplete, while recording newly closed gaps at the end.

## P0 — integration correctness

### 1. Bind `/v1/aero` repair proposal/application to the host structural revision

Still missing. The existing Aero API supports `native_revision`; the external route must supply it from the same host state used by `/v1/native`.

### 2. Converge Aero CAD mutation authorization onto the host preview/apply/reject path

Host generic preview operator controls now exist. Aero must migrate CAD-changing repairs without losing the Aero geometry fingerprint or evidence payload.

### 3. Resolve Native preview retention/restart semantics

The host store still keeps preview records in memory after consume/reject and outstanding previews are not a durable job mechanism. Bound/cleanup policy remains a host quality task.

### 4. Make Aero stamps/results/context method-aware

`AeroStamp.py`, `AeroResults.py` and `VibeCADAeroContext.py` are still shaped around low-order solves. They need solver-finished, qualification, artifact/provenance, case ID, geometry revision, solver version, settings hash and field references.

## P1 — solver and job foundation

### 5. Implement the host Analysis Runtime migration specified by Correction 01

Live FEM and Native Background provide the working seeds. The architecture is no longer ambiguous: characterize current behavior, extract one domain-neutral host runtime behind compatibility facades, prove it with FEM, then make Aero the second client. **The implementation is still missing upstream; the plan is now complete enough to execute non-destructively.**

### 5A. Prove non-destructive cutover, rollback and compatibility

Still missing upstream: golden characterization traces, observation-only shadowing, per-solver cutover, publication idempotency, downstream-import compatibility, installed-package parity, and explicit rollback routes. These are now specified in `HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md`.

### 5B. Implement durable identity/restart/recovery semantics

Still missing upstream: Save-As/close/reopen behavior, `AWAITING_SOURCE`, provider reconnect/orphan classification, crash-consistent metadata/artifact promotion, persistent job schema migration, idempotent collection/publication and retry-attempt lineage. See `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`.

### 5C. Implement durable publication authorization without persisting standing mutation permission

Still missing upstream: explicit separation of submission provenance, detached execution authority and fresh publication authority for jobs that outlive a Native turn/document activity. Current FEM keeps its original ticket/global-revision behavior during extraction. Durable Aero later needs inert `PublicationDescriptor` persistence, exact `Document.Uid` rebind, domain `CurrentnessReport`, replay-idempotent receipt and fresh Native mutation authorization. See `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`.

### 6. Complete conventional external-aero OpenFOAM/CfdOF path

Still missing: domain creation, BCs, turbulence/transition selection, mesh policy, solve, force/coefficients, residuals, field import and qualification.

### 7. Complete vendored FluidX3D integration

Still missing in live upstream: vendored source/build integration, VibeCAD bridge build, geometry/domain setup, real force/torque/field result path, result provenance and benchmark qualification.

### 8. Build high-Re FluidX3D qualification matrix

Collision/stability/SGS/wall treatment, lattice-Mach limits, Re envelopes, domain/blockage rules, averaging, grid convergence and benchmark suite remain to be specified/validated in live code.

### 9. Solver-neutral case/artifact cache

Content-addressed cases, frozen inputs, results, fields, migrations, retention and garbage collection remain missing.

## P2 — user experience and remote compute

### 10. Full CFD UI

Case setup, geometry readiness, mesh preview, run/status/cancel, residual/history plots, solver comparisons, current-vs-stale results and result history remain to be built into the existing Aero ribbon/panel surface.

### 11. Interactive field experience

Cp/pressure/velocity/vorticity/Q, slices, probes, clipping, streamlines, transient playback and the intended interactive FluidX3D experience remain missing.

### 12. Kaggle execution hardening and forecasting

Live quota query, observed throughput history, job-size/resource estimate, scheduler explanation, reconnect/download/log handling and accelerator discovery remain. Do not assume a fixed weekly-hours number or a fixed GPU model.

## P3 — advanced physics retained as canonical scope

### 13. Moving/rotating multi-body CFD
### 14. Propulsion–airframe interaction
### 15. Full 6DOF aerodynamic/control/propulsion provider
### 16. Aeroelasticity / FSI and flutter qualification
### 17. High-fidelity dynamic stall implementation/validation
### 18. Wake/mid/far-field drag-source diagnostics
### 19. Uncertainty/convergence reporting
### 20. Multi-fidelity refinement and controlled engineering-knowledge accumulation

None of these are deleted or relegated to an undefined “eventually.” They remain dependency-ordered target capabilities.

## Gaps materially closed/narrowed by upstream since Pass 02

- Generic Native preview list/apply/reject operator control: **CLOSED upstream**.
- Preservation check for user-explicit intent during preview apply: **CLOSED upstream**.
- Held Native session status/idle behavior: **CLOSED upstream**.
- Exact/derived/presentation artifact vocabulary: **CLOSED upstream for host; Aero adoption remains**.
- `not_solved` vs `model_unqualified` solver evidence distinction: **CLOSED upstream for FEM; Aero adoption remains**.
- Detached solver input hashing/stale-before-attach pattern: **CLOSED as a proven FEM pattern; host-generalization implementation remains open but is now fully specified by Correction 01**.
- Raw `/v1/run` as a mutation pathway: **actively closed upstream; do not restore it**.

## Host-runtime items closed by Correction-01 deepening

The earlier plan did not fully specify cancellation linearizability, process-tree ownership, durable-vs-ephemeral state, document close/reopen recovery, restart classification, public compatibility, or rollback gates. These are now explicit in the `HOST_RUNTIME_*` documents and `DESTRUCTIVE_CHANGE_AUDIT.md`.

Still not claimed complete: live FreeCAD integration, supported-platform process-tree implementation, durable storage backend choice, real provider reconnect tests, FEM A/B parity, or upstream migration. Those remain implementation work after a fresh freeze and explicit authorization.
