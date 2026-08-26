
# Aero Job Reuse Decision — Pass 03 Correction 01

## Decision: the host runtime is now a required architecture target

Pass 03 was correct that Aero must not operate a second generic scheduler, but too conservative in waiting for a future generic host service to appear.

The deeper source review shows that VibeCAD already has the two necessary seeds:

- `VibeCADNativeBackground*` — host prepare/worker/commit orchestration, job IDs, progress/status/cancel, one-active-job-per-document policy and `native.job` surface;
- `VibeCADNativeAnalyzeSolverExecution*` — detached FEM input generation, safe input hashing, process execution, timeout/cancel of the direct owned process, source revalidation and transaction-safe publication. Descendant process-tree behavior remains a characterization item, not a proven current guarantee.

The target therefore is:

> **Extract one domain-neutral VibeCAD Analysis Job Runtime from those existing working paths. Make FEM its first compatibility-proven client. Make Aero its second client.**

## `AeroJobStore` disposition

`AeroJobStore.py` in this reconciliation overlay is **TRANSITIONAL / REFERENCE ONLY**.

Keep it for now because it records accepted lifecycle ideas and keeps the overlay tests/self-contained design reproducible. Do **not** evolve it into production scheduling, process execution, durable provider orchestration or generic artifact persistence.

Once the host runtime exists, Aero contributes a domain payload containing things such as:

- Aero case ID/hash;
- aerodynamic geometry identity;
- solver/model/build/settings;
- provider routing decision;
- Aero dependency fingerprints;
- result/evidence/qualification references.

The host contributes:

- job ID/lifecycle;
- queue/execution/recovery;
- provider job ID;
- progress/cancel/timeout;
- artifact manifests;
- persistence/restart;
- publication/currentness orchestration.

## FEM-first proving rule

Aero MUST NOT be the first real client used to validate the generic runtime.

Before Aero depends on it, the refactor must demonstrate observational parity for existing FEM:

- identical solver inputs and command/environment semantics;
- identical current input digest behavior during compatibility phase;
- identical cancel/timeout/process-tree behavior;
- identical stale-source refusal;
- identical result object graph/History behavior;
- identical Native mutation receipts;
- compatible `native.job` public surface and errors.

## Why this is safer than “keep Aero separate for now”

Keeping a second Aero store/runtime temporarily feels conservative, but it encourages divergence in exactly the hard areas: crash recovery, artifact identity, process cancellation, source-currentness, provider reconnect and publication. The safer strategy is to make the host seam real **before** Aero production execution depends on it, while retaining old FEM facades until parity is proven.

## What remains separate

- CFD jobs are not Native mutation previews.
- Native preview/apply/reject remains CAD-mutation authorization.
- `NativeMutationBoundary` remains transaction/publication authority.
- FEM solver state remains FEM-specific.
- `AeroCase`, geometry/frame/reference semantics, solver adapters, result parsing and qualification remain Aero-specific.
- execution success remains separate from model qualification.

See the host-runtime migration documents for the exact contracts, PR sequence, rollback and parity gates.

## Durable publication authority

The host job runtime must not turn a persisted job into standing CAD mutation authority. Current FEM retains its existing submission-time ticket/global-revision publication behavior during extraction. Production long-running Aero later uses an inert `PublicationDescriptor` and a fresh host publication authorization after exact source `Document.Uid` rebind, Aero dependency-currentness validation and replay/idempotency checks. See `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`.
