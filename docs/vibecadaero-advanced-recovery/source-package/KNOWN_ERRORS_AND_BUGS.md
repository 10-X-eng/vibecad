# Known Errors, Bugs, and Reconciliation Hazards — Pass 03 Correction 01

## A. Historical design/code errors retained from Pass 01

1. Multiple “canonical” dumps omitted previously accepted capabilities.
2. Proposed replacement `AeroResults` classes conflicted with live `AeroReport` authority.
3. Standalone LBM engines bypassed current stamps/public API.
4. Early drafts invented/unverified FluidX3D Python APIs.
5. Early FluidX3D CLI flags did not match the real `main_setup` architecture.
6. Some force extraction examples lacked a physically meaningful freestream/boundary setup.
7. Lattice/SI time conversion was previously fragile or incorrect.
8. Reference area/length/density/axes were scattered or hard-coded.
9. Force sign conventions drifted between examples.
10. Torque about center of mass was mislabeled as moment about an arbitrary Aero reference.
11. STL physical scaling was underspecified.
12. Kaggle execution examples were dummy/sleep-style mocks.
13. Kaggle weekly quota was incorrectly treated as a hard-coded constant.
14. Legacy Kaggle auth assumptions were stale.
15. CfdOF imports/API examples were previously stale/underspecified.
16. Surface mesh and OpenFOAM fluid volume were conflated.
17. Unknown Gmsh element fallback could corrupt topology.
18. Binary-STL behavior was asserted without a pinned contract.
19. Mesh→solid examples overpromised automatic repair.
20. Reduced dynamic-stall equations were labeled “full Leishman–Beddoes.”
21. Pitch-rate input was underused.
22. Scalar/vectorized dynamic-stall paths diverged physically.
23. Shared airfoil parameters were mutated across sections.
24. Semi/full-span integration was ambiguous.
25. Strip-theory density was hard-coded.
26. Unsteady coupling could erase caller initial state.
27. Prescribed pitch lost pitch rate.
28. Naïve RK use could double-advance stateful aero internals.
29. “Full 6-DOF” label overstated incomplete lateral/control aerodynamics.
30. Pressure coloring lacked triangle→source-face correspondence.
31. Results lacked artifact hashes/provenance.
32. Remote jobs lacked durable lifecycle/state/reconnect semantics.

## B. Pass 01 artifact defects found while building Pass 02

### B1. Test command was not self-contained

`TEST_OUTPUT.txt` recorded passing tests, but direct `pytest -q` from the overlay failed collection unless the caller set `PYTHONPATH`. Pass 02 adds `tests/conftest.py` so the package test command reproduces the result without hidden environment state.

### B2. Validation counts drifted

Pass 01 documents variously said 6, 9 and 10 passing tests as corrections accumulated. Pass 02 has one regenerated validation report and one test output.

### B3. Informational checkbox wording drifted

Some Pass 01 generated/reference files retained “I have read this third-party notice.” after the canonical choice became exactly “I understand.” Pass 02 regenerates the code reference from source and removes correction addenda from the canonical surface.

### B4. Obsolete commercial-profile vendor policy survived

An old `VENDOR_POLICY.md` still described “non-commercial VibeCAD” and “commercial VibeCAD” profiles despite later corrections. Pass 02 rewrites vendor docs so FluidX3D restrictions remain component-specific and no hypothetical VibeCAD/Aero commercial profile exists.

## C. Live upstream ↔ target conflicts at `d0a933e`

### C1. `/v1/aero` does not thread host Native revision into repairs

`VibeCADAero.propose_repairs()` / `apply_repairs()` accept `native_revision`, but current `VibeCADAgentControl.aero_command()` calls them without it. The host Native structural revision therefore is not bound into this external Aero repair route.

### C2. Aero repair preview is a parallel authorization record

`AeroRepairPreview` has its own geometry fingerprint/optional Native revision. Geometry identity is useful, but CAD mutation authorization should converge on host Native preview/receipt semantics.

### C3. Host preview store is unbounded

`_DocumentRecord.previews` is a plain dictionary. Consumed/stale entries remain stored. The new pending-preview view filters them but does not reclaim them.

### C4. Outstanding Native previews are not persisted

Host `export_document()` persists revision/baseline/receipts, not outstanding previews. Restart semantics are therefore asymmetric and need explicit design before depending on previews for long-lived workflows.

### C5. `AeroStamp` still globally says current Aero is “not CFD”

Correct for current low-order results, wrong once an actual CFD method is the source. Make claims method-specific without raising the airworthiness ceiling.

### C6. `AeroResults` remains low-order-oriented

It lacks case/job IDs, solver/provider provenance, geometry/mesh hashes, convergence, qualification, stale/current status and field references.

### C7. `VibeCADAeroContext` remains low-order-oriented

Assistant context currently exposes bounded low-order coefficients and fixed `model_unqualified/not_airworthy` semantics. It needs bounded job/CFD/provenance/field summaries later.

### C8. `AeroConfig.reference_area_m2` remains configuration-specific

Generalize explicit reference policy without breaking existing biplane defaults.

### C9. Cross-backend transform/origin/moment reference is not yet one live contract

The overlay provides it, but upstream integration still needs one authoritative resolved transform.

### C10. Atmosphere is still split between constants and future case state

Resolve one case atmosphere shared by all backends.

### C11. New files require explicit CMake registration

Current VibeCAD/VibeCADAero build enumerates runtime/tests/resources. Missing enumeration can pass source tests but fail packaged product.

### C12. NumPy ABI constraint must be respected

Current `requirements-aero.txt` keeps NumPy <2 for bundled FreeCAD extension compatibility. Do not “modernize” it casually.

## D. External dependency constraints

### D1. FluidX3D licensing is component-specific

FluidX3D's current source-available license includes use/redistribution conditions. VibeCAD documents them for that component; it does not generalize them to Aero/VibeCAD or add purpose detectors. No standardized public commercial agreement/deployment model exists at this pass.

### D2. FluidX3D source pin did not move

Pass 02 recheck: still `8986874e626e0aebd317ab16c420b39e30dfa273`.

### D3. CfdOF source pin did not move

Pass 02 recheck: still `a90f60c2313ceba09c236c81f0693d93357d1614`.

### D4. Kaggle remains a volatile compute service

Use current CLI/status/quota behavior, persist provider IDs, and keep forecasts explicitly estimated.

## E. Scope hazards to avoid

- Do not turn implementation stages into scope deletion.
- Do not call a solver/model “full” or “qualified” before evidence exists.
- Do not let remote-provider logic leak into solver physics contracts.
- Do not let solver-specific frames leak into global force conventions.
- Do not let stale results silently become current.
- Do not store long-running jobs in the Native mutation-preview store.
- Do not create product-use/license enforcement machinery; documentation + one informational notice is the chosen product behavior.
- Do not overwrite a newer active upstream with this frozen overlay.

## F. Pass 03 findings — frozen `df07a5e`

### F1. `/v1/aero` repair host-revision propagation remains incomplete

The host Native platform has become materially stronger, increasing the importance of closing this gap. Do not let Aero repair application be the older exception to host stale-revision semantics.

### F2. Parallel Aero repair preview authority is now more obviously technical debt

`AeroPreview` still provides useful geometry identity, but the host now owns generic list/apply/reject and user-explicit preservation. Treat Aero's current preview object as a compatibility seam, not a precedent for new mutation systems.

### F3. Preview records still lack a finite lifecycle bound

Consume/reject marks records but the underlying host preview dictionary remains a retained record set. Outstanding previews are not exported/restored as durable work. This does not block normal short-lived Native use, but it makes the store inappropriate for CFD job persistence and is a general long-document memory/restart hazard.

### F4. `AeroJobStore` must not become a second generic scheduler

Pass 02's reference job store was useful to formalize a lifecycle. It must now be treated as **transitional/reference only**. The real target is one VibeCAD Analysis Runtime extracted non-destructively from Native Background + detached FEM. FEM proves parity first; Aero must not become a parallel scheduler.

### F5. Exact artifact class can be misread as engineering readiness

Live host wording correctly backs away from “manufacturable solid.” Aero must likewise prevent exact B-rep/STEP from implying watertight CFD surface, valid fluid domain, mesh readiness, manufacturability or airworthiness.

### F6. Solver completion can still be over-read as model validation

Live FEM now explicitly stamps successful solver output as `model_unqualified`. Aero must adopt the same separation for FluidX3D/OpenFOAM until a matching versioned qualification envelope exists.

### F7. Kaggle accelerator assumptions can go stale

Current Kaggle CLI documentation warns that the default-image P100 route is not usable for normal GPU compute without a compatible stack. Routing must discover available accelerator/machine shape and treat T4/P100/etc. as runtime data, not hard-coded architecture.


### F8. A big-bang “generic analysis framework” rewrite would be destructive

The runtime touches threading, subprocesses, History/result publication, Native errors, agent APIs, build registration and eventually persistent data. Replacing the FEM path wholesale would make regression attribution and rollback poor. Characterization-first strangler extraction is mandatory.

### F9. Genericizing FEM state is the wrong extraction boundary

`VibeCADNativeAnalyzeSolverState.py` contains solver/property semantics that belong to FEM. Moving that into a generic job system would leak engineering-domain rules into host infrastructure. Extract process/orchestration/artifact mechanics, not solver meaning.

### F10. Persistence and process extraction must not land together

Persistent job metadata/recovery is desirable but introduces schema/crash/data-loss risk. It is a separate phase after in-memory behavioral parity, with explicit migration/rollback.

### F11. A global document revision alone is too blunt for expensive future CFD

Current FEM exactness rules are preserved first. The target host contract permits domain-contributed dependency fingerprints so unrelated edits do not necessarily invalidate an hours-long CFD job while relevant geometry/config changes always do.


### F12. Duplicate execution is not a valid parity technique

Running legacy and new solver paths simultaneously would double compute and create two publication owners. Parity shadowing must be read-only observation of one authoritative execution.

### F13. Result publication needs replay protection

A reconnect, retry, duplicated callback or UI request can otherwise create duplicate result graphs. Publication identity/receipt and idempotent collection are required before durable remote execution.

### F14. Document path/label cannot be durable attachment authority

Save-As, cloned documents and reopened files make label/path ambiguous. Durable jobs require exact source/dependency revalidation and an explicit awaiting-source/quarantine state when authority is uncertain.

### F15. Application restart cannot infer local-job truth from leftover files/PIDs

After a crash/restart, only provider/process ownership evidence plus artifact validation can classify a job. Unknown becomes orphaned/recovery state, never fabricated success.

### F16. Compatibility includes installed packaging and downstream Python imports

A source-tree-only refactor can pass tests while packaged VibeCAD omits modules or downstream macros break. Keep facades/re-exports and add installed-tree/CMake parity checks.


### F17. Current NativeBackground has a check-to-commit cancellation window

At the frozen source, the document-thread callback checks the cancellation event, validates, checks cancellation again, and only then changes the phase to `committing/finalizing`. `cancel()` accepts while the phase is still `waiting_to_commit`. This is a source-level race hazard: characterize it with a concurrency test and fix it separately if reproduced before treating the lifecycle as the generic-runtime oracle.

### F18. Current process stop helper controls the direct Popen only

The shared `stop_process()` calls terminate/kill/wait on the direct process. Whether descendant solver/MPI/helper processes survive is platform/process dependent and must be tested. If they do, process-tree hardening is a separate correctness change before provider extraction.

### K-P03C01-12 — Persisting submission-time Native authority as future publication authority

A long-running job must not serialize a `NativeRuntimeContext`, reusable `NativeCallTicket`, callback closure or transaction as standing durable mutation permission. Current FEM's in-process ticket semantics are preserved during extraction; durable Aero publication later uses inert provenance plus fresh Native publication authorization.

### K-P03C01-13 — Treating global structural revision as the permanent sole CFD currentness rule

The current FEM ticket/global-revision check is intentionally preserved for FEM parity, but using it forever for long CFD would over-invalidate results after unrelated structural edits. Aero needs domain-scoped dependency currentness **in addition to**, not instead of, host publication authority.
