# Recovered Advanced VibeCAD / VibeCADAero roadmap

Recovered 2026-08-25 from the VibeCAD project files, repository history, and the ChatGPT recovery task **Find Advanced VibeCAD Work**.

## Second-pass preservation status

The corrected Pass 03 Correction 01 package has now been recovered byte-for-byte and preserved with this roadmap. The detailed no-loss reconstruction is in `VIBECADAERO_SECOND_PASS_PRESERVATION_SUPPLEMENT.md`, and the complete 110-file source package is preserved as `VibeCADAero_Reconciliation_Pass_03_Correction_01_df07a5e.zip` with SHA-256 `AB0E315D811F5FD77D0D4FA9220E5511481C57AA8AA65128F23D4475030915ED`.

The supplement is authoritative for details omitted here. In particular, it preserves the host/job/domain/provider/Native authority split, submission-versus-execution-versus-publication authorization, three independent state axes, atomic cancel/publication rules, `Document.Uid` recovery behavior, FEM-first strangler migration, protected API/import/package surfaces, exact case/frame/readiness/evidence contracts, the full 21-step implementation sequence, release gates, risks, stop conditions, UI surfaces, and all retained moving/unsteady/6-DOF/FSI/refinement scope.

## What the big plan actually was

The full program was the August 20 Advanced VibeCAD/VibeCADAero workstream against `halthinks/vibecad`. Its goal was a governed, multi-fidelity CAD-to-aerodynamics environment that extends the existing VibeCAD authority model instead of replacing it.

The intended stack covered:

- editable VibeCAD/FreeCAD geometry and VibeScript authoring;
- low-order aerodynamic analysis;
- GPU LBM / FluidX3D;
- OpenFOAM through the existing FreeCAD/CfdOF path;
- detached local execution plus remote/Kaggle execution;
- pressure and flow-field visualization;
- solver qualification and high-Reynolds-number work;
- moving bodies and propulsion;
- unsteady aerodynamics, 6-DOF, and FSI;
- provenance, evidence ceilings, and controlled refinement.

This is distinct from the smaller `PLAN.md`, `PLAN_V2.md`, `PLAN_V3.md`, `PLAN_V4.md`, and `PLAN_50.md` files. Those are implementation slices and host-honesty prerequisites that fed into the larger Advanced Aero program.

## Ten-stage executive grouping

This is the short executive grouping. It must not replace the exact dependency-ordered sequence in the second-pass supplement. The detailed sequence starts with live reconciliation and FEM characterization, isolates cancellation and process-tree correctness, extracts the host runtime behind compatibility facades, adds persistence and fresh publication authority in separate phases, then admits Aero as the second runtime client before solver/UI/physics expansion.

1. Close `/v1/aero` Native revision propagation.
2. Move Aero onto the host preview, evidence, and artifact semantics.
3. Establish geometry readiness, coordinate frames, and source correspondence.
4. Generalize detached execution into the host-owned VibeCAD Analysis Runtime.
5. Complete OpenFOAM/CfdOF integration.
6. Complete vendored FluidX3D integration.
7. Build the common field UI and Kaggle/remote execution and routing.
8. Perform solver qualification and high-Reynolds-number work.
9. Add moving bodies, propulsion, unsteady aero, 6-DOF, and FSI.
10. Add advanced diagnostics and controlled refinement.

## Reconciled current state

The repository history shows that the program is no longer parked at stage 1:

- PR #81, `4f9681965`, merged the Native Aero Analysis Runtime work.
- Its implementation commits include Aero runtime lifecycle helpers, stale-ticket rejection, routing Native Aero solves through the Analysis Runtime, regression coverage, and preservation of synchronous solving.
- PR #82, `31ea810db`, then reconciled the CAD-honesty host lineage into current `main`.

Therefore:

- Stage 1 is at least substantially implemented.
- Stage 2 has substantial host-honesty and artifact/evidence infrastructure beneath it, though an acceptance audit is still required before calling the whole stage closed.
- Stage 4 has a concrete merged implementation and tests, but must still be checked against the original project-file acceptance criteria.
- Stages 3 and 5-10 remain the main roadmap body unless later evidence proves otherwise.

### Current Step 7 checkpoint: installed FEM document lifecycle

The current daily checkpoint adds a deliberately narrow Step 7 stabilization repair for the installed FEM execution route:

- a running FEM job may publish after the exact source document is saved, closed, and reopened only when the active live document retains the captured `Document.Uid`;
- publication still uses the original Native mutation ticket and global structural revision checks;
- closed sources, switched documents, same-name replacement documents with a different UID, changed solver state, changed `History`, and changed runtime publication preferences all fail closed as stale;
- the live solver target and result importer are rebound only after those currentness checks pass;
- the behavior is covered in the Native route, the human GUI route, unit/integration tests, and an installed Windows command-line host;
- the installed proof uses deterministic synthetic solver result fields to exercise lifecycle and publication behavior. It does **not** claim physical solver/backend correctness or qualification.

This checkpoint does not close Step 7, Step 8, or Step 8A. Installed POSIX evidence, physical solver/backend execution, durable restart/orphan recovery, fresh publication authorization, replay-idempotent durable receipts, and the remaining lifecycle/leak burn-in are still required.

### Queued Step 8 checkpoint: receipt-bound domain verification recovery

The current dependency-ordered checkpoint extends the durable runtime after immutable provider-output admission without crossing into publication authority:

- every returned artifact is read again from host-owned content-addressed storage and checked against the exact attempt-bound output manifest before any domain verifier runs;
- the domain verifier receives the exact persisted analysis, domain, adapter, source-document UID, dependency digest, provider-attempt identity, manifest digest, immutable descriptors, and ephemeral local object paths;
- the verifier must return the existing bounded, secret-screened `EngineeringResultEnvelope` and finding contracts, bound to the exact domain, adapter, source, dependency, attempt, and artifact hashes and byte counts;
- the host persists a bounded, canonical, write-once verification receipt while state is still `verifying`, then advances only to `waiting_to_publish`;
- a crash after receipt persistence but before the phase transition resumes without rerunning the domain verifier or duplicating evidence;
- missing immutable storage or a temporarily unavailable verifier preserves `verifying` for truthful retry, while content drift or a mismatched verifier result fails closed as explicit terminal evidence;
- the coordinator has no document, CAD-mutation, qualification-promotion, scheduling, or publication-authorization input.

This is fixture evidence only. It does not wire a production FEM or Aero verifier, a real authenticated remote provider, network or portable-bundle transport, document rebind/currentness publication checks, or Native mutation authority. It therefore advances Step 8 but does not close Step 8 or Step 8A.

### Current queued Step 8A checkpoint: receipt-bound publication recovery

The next dependency-ordered checkpoint preserves the compatibility publication API and adds a stricter path for verified results:

- one canonical publication descriptor binds the exact latest attempt, domain, adapter and version, source-document UID, frozen dependency digest, provider-attempt identity, output-manifest digest, result identity, and canonical result digest;
- fresh authorization binds the hash of that complete descriptor rather than a loose result label;
- before source rebind or mutation, every verification-receipt artifact is checked again under the content-addressed store lock, with missing storage left retryable and byte, type, or symlink drift recorded as terminal integrity failure;
- exact source rebind, domain currentness, and adapter compatibility must pass before compare-and-swap ownership persists bounded intent, authorization, currentness, verification-receipt identity, and live artifact references;
- the document callback receives the canonical verified result plus ephemeral immutable artifact paths, but no persisted callback, live document object, or authority token is introduced;
- successful postconditions produce bounded, secret-screened, path-free mutation evidence and a write-once publication receipt before terminal success;
- a crash after that receipt but before the terminal transition finalizes without remutation, while ownership with no receipt remains outcome-unknown and is never replayed automatically.

This checkpoint uses an inert document fixture. It does not yet provide real `Document.Uid` rebind, domain-owned FEM/Aero publication-draft adapters, Native document-thread transaction and rollback wiring, installed-host process-crash evidence, or physical solver/importer parity. It advances Step 8A but does not close Step 8A.

## What remains, in practical execution order

### A. Acceptance audit of already-landed foundation

- Reconcile PRs #81 and #82 against the original Advanced Aero contracts.
- Verify `/v1/aero` revision propagation end to end, not merely unit-level behavior.
- Verify preview/evidence/artifact semantics and stale-result rejection in the live host.
- Verify the shared Analysis Runtime is truly host-owned and reusable, not an Aero-only duplicate.

### B. Geometry readiness and frame/source correspondence

- Define admissible geometry states for each fidelity level.
- Bind body, lifting-surface, control-surface, propulsion, and reference-frame identities to the exact CAD revision.
- Record unit, axis, origin, transform, tessellation, and source-object correspondence.
- Reject stale or ambiguous geometry before meshing or solving.

### C. OpenFOAM/CfdOF completion

- Complete the native CfdOF/OpenFOAM execution path.
- Preserve case, mesh, boundary-condition, solver-version, process, and artifact provenance.
- Extract real result fields and failure diagnostics.
- Keep solver success below qualification or airworthiness claims.

### D. Vendored FluidX3D completion

- Finish the vendored build and packaging boundary.
- Replace launch-only or placeholder behavior with real field/result extraction.
- Bind GPU/device, lattice, scaling, boundary, iteration, and convergence facts to the result.
- Add deterministic fixtures, timeout/cancellation, stale-result rejection, and cleanup tests.

### E. Common field UI and remote/Kaggle execution

- Normalize local OpenFOAM and FluidX3D outputs into a common field/result contract.
- Add pressure, velocity, vorticity, stream/field, slice, and time-step views with exact provenance.
- Implement real Kaggle package upload, run submission, polling, artifact download, verification, and failure recovery.
- Route jobs by capability and qualification without silently changing fidelity.

### F. Solver qualification and high-Re work

- Establish benchmark geometries and independently checkable reference cases.
- Separate numerical completion, convergence, validation, and qualification.
- Record mesh/grid independence, timestep sensitivity, domain/boundary sensitivity, and model limits.
- Add high-Reynolds-number regimes only where the selected solver/model and evidence support them.

### G. Dynamic and coupled physics

- Moving bodies and control surfaces.
- Propulsion and actuator coupling.
- Unsteady aerodynamic analysis.
- 6-DOF vehicle dynamics.
- Fluid-structure interaction.
- Explicit coupling assumptions, timestep ownership, convergence/failure handling, and result provenance.

### H. Advanced diagnostics and controlled refinement

- Cross-fidelity comparison and discrepancy diagnostics.
- Evidence-driven mesh/model/fidelity refinement proposals.
- User-controlled acceptance of expensive or design-mutating refinement.
- No automatic promotion from a visually plausible field or solver exit code to validated, qualified, manufacturable, or airworthy.

## Supporting local plans that must not be lost

- `PLAN.md`: CAD-honesty waves W0-W6, including Native preview/apply, stamps, intent dispositions, one mutation owner, and Aero repair as a proposal.
- `PLAN_V2.md`: industry-grade Aero wrapper slices: real config, mass/flight-card honesty, stale-safe Native receipts, named Aero agent control, visual fence, ribbon parity/reject, and frozen Native digest.
- `PLAN_V3.md`: host CAD-honesty assembly after Aero #12, especially fail-closed Intent Memory.
- `PLAN_V4.md`: `/v1/native` Bot equality through the one Native dispatcher.
- `PLAN_50.md`: the 50-slice approved path covering Native preview families, UI, Bot session behavior, export/measure claims, FEM/CAM ceilings, and one-writer rules.
- `AERO_RIBBON_AND_TOOL_GAPS.md`: ribbon/tool parity and explicit Analyze, Section, VLM, JSBSim, Report, propose/apply/reject repair behavior.

## Solver/backend with the remembered acknowledgment checkbox

The exact backend is **FluidX3D**. It was never EIRENE. The ChatGPT Library contains the original `AERO_FIRST_USE_INFORMATIONAL_NOTICE.md`, dated August 19, which defines the remembered control exactly.

The required behavior is:

- show the informational notice once, on the user's first entry into VibeCADAero;
- label the acknowledgment **I understand.** and then allow **Continue**;
- after the user checks it, persist one local unversioned boolean;
- never show the notice again during normal use or after updates;
- do not treat the flag as telemetry;
- do not let the flag affect solver eligibility, licensing, product behavior, or output ownership.

The recommended notice explains that VibeCADAero can use third-party software under terms separate from VibeCAD; FluidX3D is one such solver; and FluidX3D's own terms include commercial- and military-use restrictions. Those component-specific terms do **not** make VibeCAD or VibeCADAero non-commercial, do not apply to unrelated Aero backends or features, and do not change ownership of CAD designs created in VibeCAD.

The acknowledgment is explicitly **not**:

- an "I agree" license acceptance;
- a declaration of intended use;
- a commercial, military, or research classification;
- an entitlement or compliance check;
- a restriction on VibeCAD or VibeCADAero generally;
- a restriction on ownership of user-created CAD designs;
- a solver-selection control;
- a recurring prompt.

The separate `CORRECTION_01_FLUIDX3D_VENDOR_POLICY.md`, also dated August 19, establishes the packaging rule:

- non-commercial VibeCAD vendors a pinned FluidX3D source in `src/Mod/VibeCADAero/vendor/FluidX3D/`, preserves upstream license/origin, builds the bridge against it, and packages the bridge with the product;
- the commercial build excludes/disables that vendored payload by default and requires a separately installed FluidX3D backend with compatible commercial permission, unless later permission allows commercial bundling;
- military-use restrictions remain independently governed by FluidX3D's terms;
- there is no runtime auto-download; vendoring is a source/release engineering operation;
- the solver abstraction remains unchanged, with AeroLBM behind the solver interface.

The complete recovered gate contract is preserved separately in `RECOVERED_FLUIDX3D_FIRST_USE_GATE.md`.

## Source locations

- Local project-plan layer: the parent project directory containing this repository
- Canonical repository: this VibeCAD repository
- ChatGPT recovery task: `6a8bd711-f984-83e8-a6c6-c3945a812a41` — **Find Advanced VibeCAD Work**
- Original work task: `6a86d2f4-0640-83e8-9f0f-c59783d141fd` — **VibeCAD Aero Work advanced**
- ChatGPT Library source: `AERO_FIRST_USE_INFORMATIONAL_NOTICE.md` — August 19, 2.19 KB
- ChatGPT Library source: `CORRECTION_01_FLUIDX3D_VENDOR_POLICY.md` — August 19, 1.53 KB
- Recovered corrected package: `VibeCADAero_Reconciliation_Pass_03_Correction_01_df07a5e.zip`
- Detailed second-pass preservation: `VIBECADAERO_SECOND_PASS_PRESERVATION_SUPPLEMENT.md`
