# Host Analysis Runtime — Submission, Execution, and Publication Authority

**Status:** Canonical target contract for Pass 03 Correction 01.  This is an architectural migration requirement, not a claim that upstream VibeCAD already implements the durable form described here.

## 1. Why this contract exists

A long-running engineering solve creates an authority problem that is easy to get wrong.

Current detached FEM execution is deliberately strict: a Native call begins against one exact live document state, the solver runs detached, and publication later reuses the original Native mutation context.  The existing Native ticket contains the exact document UID, capability identity, expected structural revision, and an idempotency token.  That is excellent fail-closed behavior for today's bounded FEM workflow.

It is **not** a safe durable authorization model for a CFD job that may run for hours, survive a document switch or application restart, or return after unrelated structural edits.  A live `NativeRuntimeContext`, callback closure, `NativeCallTicket`, agent turn, transaction object, or FreeCAD object must never be serialized and treated as a standing permission to mutate future CAD state.

The solution is to separate three authorities that are currently temporally adjacent but are conceptually different.

> **Submission authorizes creation of exact immutable work. Execution authorizes compute only. Publication is freshly authorized against the exact current document and exact completed job.**

No one of those authorities implies the other two.

---

## 2. The three-authority model

### 2.1 SubmissionAuthorization

A `SubmissionAuthorization` proves that a legitimate VibeCAD capability created the analysis job from an exact source state.

It records durable **provenance**, not a reusable future mutation capability.

Minimum durable fields:

```text
submission_id
job_id / analysis_id
domain_id
adapter_id + adapter_version
originating_capability
originating_operation
action/surface identity where applicable
source_document_uid
submission_structural_revision
submission_dependency_snapshot_id
prepared_input_manifest_id
submission timestamp
originating call/receipt/idempotency identifiers as inert provenance if useful
```

It MUST NOT persist:

```text
NativeRuntimeContext object
NativeCallTicket as executable authority
reauthorize_turn callback
FreeCAD document/object references
Qt objects
transaction handles
Python closures
provider credentials
agent/session tokens
```

If the original Native ticket/idempotency token is retained in a record, it is historical provenance only.  It cannot be replayed after restart to authorize CAD mutation.

### 2.2 ExecutionAuthority

`ExecutionAuthority` belongs to the host Analysis Runtime and selected compute provider.  It is limited to the immutable prepared work identified by the submission.

It may:

- read the sealed input bundle;
- launch/submit the declared provider operation;
- poll progress;
- collect bounded logs;
- request cancellation;
- reconnect to a provider job when supported;
- collect and hash immutable outputs;
- run FreeCAD-independent parsing/numerical post-processing.

It MUST NOT:

- mutate CAD;
- reinterpret a different analysis as the submitted analysis;
- change solver physics/configuration after the input identity is sealed;
- attach result objects to a document;
- create a fresh engineering claim solely because a process exited successfully.

Execution can therefore continue while the source document is inactive or closed, provided the provider and shutdown policy support it.  The resulting artifacts are evidence tied to the original immutable analysis, not authority over any currently open document.

### 2.3 PublicationAuthorization

Publication requires a **fresh host authorization** when completed output is ready to attach to CAD.

The publication coordinator must establish all of the following before a mutation begins:

1. the exact persisted job/submission identity is known;
2. immutable output artifacts pass manifest/hash validation;
3. the exact source document can be rebound unambiguously using host document identity;
4. the domain adapter resolves the intended current targets in that document;
5. the domain supplies a `CurrentnessReport` comparing current engineering dependencies with the frozen dependency snapshot;
6. the publication recipe/adapter version is known and compatible;
7. no successful publication receipt already exists for this exact publication identity;
8. current Native host authority can issue/authorize a **new mutation transaction/ticket** for this publication action;
9. publication executes on the document thread through the existing Native mutation/transaction boundary;
10. postconditions and receipt creation succeed atomically or the document mutation is rolled back.

A fresh publication authorization is **not a bypass around the Native system**.  It is narrower than arbitrary mutation because it may only publish an already-authorized, completed, provenance-matching host job using its registered domain publication adapter.

---

## 3. Why the original Native ticket is preserved for FEM first

The initial generic-runtime extraction MUST preserve current FEM behavior exactly.

Today the long-running solver publication closes over the `NativeCallTicket` that existed when the FEM command was submitted.  `NativeMutationRunner` later authorizes that ticket, including its original global expected structural revision.  This means any structural revision drift that the host recognizes can prevent publication.  That behavior is part of the current FEM correctness/compatibility contract.

Therefore:

- **FEM migration phase:** keep the original ticket/context/global-revision publication path unchanged behind the new generic execution plumbing.
- **Durable publication phase:** introduce the new `PublicationDescriptor` + fresh publication authorization as an additive host capability after FEM parity.
- **Aero production phase:** use the durable publication model for long-running CFD only after that host capability is proven.
- **Optional later FEM refinement:** FEM may migrate to the durable publication coordinator only in a separate behavior-change pass with its own parity/compatibility evidence.

This sequencing prevents a genericization from accidentally broadening what FEM results are allowed to attach to.

---

## 4. PublicationDescriptor

The job database should persist an inert, serializable `PublicationDescriptor`, conceptually:

```text
publication_id
job_id
analysis_id
submission_id
domain_id
publication_adapter_id
publication_adapter_version
source_document_uid
source_target_descriptors[]
frozen_dependency_snapshot_id
expected_output_manifest_id
result_identity
publication_schema_version
original capability/action identity as provenance
```

It intentionally does **not** contain live authority.

The descriptor says *what result may be considered for publication and where it originated*.  The host still has to create the fresh publication authority when the exact document is available.

### Publication identity

A deterministic or otherwise stable publication identity must make replay detectable.  At minimum it binds:

```text
analysis/job identity
successful execution attempt
validated output manifest
publication adapter + version
intended source document/domain target identity
publication recipe/result identity
```

Reconnect, duplicate provider callbacks, UI retry, restart recovery, or agent retry must resolve to the same publication identity and return an existing receipt rather than create a duplicate result graph.

---

## 5. Currentness is domain-owned; authorization remains host-owned

The host must not hard-code CFD or FEM dependency meaning.

The domain contributes a structured `CurrentnessReport`, for example:

```text
current: true | false | unknown
source_resolved: true | false
changed_dependencies[]
missing_dependencies[]
ambiguous_dependencies[]
current_dependency_snapshot_id
frozen_dependency_snapshot_id
recommended_disposition:
    publish | await_source | await_publication | stale | quarantine
```

For Aero, relevant dependencies can include geometry hash, analyzed object identity, transforms/reference point, AeroConfig, atmosphere, reference dimensions, mesh/domain settings, solver/model/build, and moving/propulsion state.

For current FEM compatibility, the domain remains responsible for the existing exact solver-state hash, exact History tuple, and result-retention preference checks.

A host structural revision remains valuable provenance.  It may be an actual dependency for operations whose meaning depends on all structural state.  It must not become the sole permanent validity rule for every high-cost CFD analysis simply because it is convenient.

---

## 6. Publication state is separate from execution state

Execution success and CAD publication are independent axes.

A provider can complete successfully while publication waits for the source document or becomes stale.

Recommended publication states:

```text
UNVALIDATED
VALIDATING_SOURCE
AWAITING_SOURCE
AWAITING_PUBLICATION
CURRENT
STALE
QUARANTINED
PUBLISHING
PUBLISHED
PUBLICATION_FAILED
```

Key laws:

- `SOLVED/COMPLETED` does not imply `PUBLISHED`.
- `PUBLISHED` does not imply `QUALIFIED`.
- `STALE` does not imply solver failure.
- absence of the source document yields `AWAITING_SOURCE`, not fabricated failure.
- inability to establish current Native publication authority yields `AWAITING_PUBLICATION`, not an unsafe workaround.
- changed relevant dependencies yield `STALE` and normally `QUARANTINED` historical evidence.
- a publication transaction failure yields `PUBLICATION_FAILED` while immutable solver outputs remain preserved.

---

## 7. Document close, switch, reopen, and restart

### Active document switches during execution

The solver may continue detached.  Publication does not jump to the new active document.

### Source document closes

The solver may continue if provider policy permits.  On completion, publication becomes `AWAITING_SOURCE`.

### Candidate source document reopens

The host resolves the exact VibeCAD `document_uid` (`Document.Uid`) and the domain revalidates dependencies.  Same filename, label, or path is insufficient.

The implementation must characterize `Document.Uid` behavior across Save, Save As, Save Copy/clone, close/reopen, recovery and imported/copied files before automatic durable reattachment is enabled.  VibeCAD already has this host identity; do not invent a second document-ID system during extraction.

If identity is ambiguous or cannot be proven, remain `AWAITING_SOURCE` and require an explicit, safely revalidated reattachment workflow rather than guessing.

### Application restart

The runtime reloads only inert durable records.  No serialized callback or Native ticket becomes live again.  Execution/provider recovery occurs first; publication is separately rebound later.

---

## 8. Atomic publication and cancellation interaction

Cancellation and publication have two separate gates:

1. **execution lifecycle gate** — determines whether cancellation won before the job becomes eligible to publish;
2. **Native publication transaction** — determines whether CAD mutation commits successfully.

An accepted cancellation before the lifecycle commit/publication gate can never later publish.

Once the lifecycle gate grants publication ownership, a late user cancellation does not asynchronously tear down an active CAD transaction.  Native transaction failure/rollback semantics control that critical section.

The current source has a narrow cancel-vs-commit transition window that must be characterized and corrected in a dedicated host-correctness slice before genericization relies on the behavior.  The migration must not conceal that fix inside a broad refactor.

---

## 9. Publication coordinator API boundary

The eventual host service can conceptually expose internal operations like:

```text
register_submission(...)-> SubmissionReceipt
mark_execution_complete(...)
resolve_publication(job_id, document_uid) -> CurrentnessReport
request_publication(job_id) -> PublicationDisposition
publish(job_id, fresh_host_context) -> PublicationReceipt
lookup_publication(publication_id) -> PublicationReceipt | None
```

This is an internal architecture shape, not a requirement to rename the current public Native tools.

During migration the existing public APIs stay stable:

- FEM job creation remains `analyze.solver_execution` / `run`;
- job control remains `native.job` / `status` or `cancel`;
- no invented `native.job start`, `clear`, or generic public mutation endpoint is required.

---

## 10. Security/integrity boundary without product policing

These controls are engineering-integrity controls only:

- exact job/provenance binding;
- immutable artifact hashes;
- currentness validation;
- exact source-document rebind;
- replay/idempotency prevention;
- Native transaction authority;
- bounded provider execution.

They are **not** purpose-of-use, commercial-use, military-use, license, ownership, or user-output enforcement.  Third-party license requirements remain documentation/informational notices as separately defined by the Aero plan.

---

## 11. Migration acceptance criteria

The durable publication architecture is not considered ready for Aero until all of these are demonstrated:

- current FEM behavior remains unchanged through the initial execution extraction;
- no live Native context/ticket/callback is serialized as future authority;
- completed detached work can remain safely `AWAITING_SOURCE` without mutation;
- restart can reload a job without manufacturing publication authority;
- exact document rebind never uses label/path alone;
- relevant domain drift produces stale/quarantined evidence rather than attachment;
- unrelated Aero-irrelevant edits can eventually be shown not to over-invalidate CFD once domain-scoped currentness is enabled;
- duplicate completion/reconnect/retry produces one publication receipt/result graph;
- publication still goes through Native mutation transaction/postcondition/receipt logic;
- failed publication leaves the CAD document transactionally unchanged and preserves immutable solver artifacts;
- accepted cancellation and successful publication are mutually exclusive under the atomic lifecycle gate.

---

## 12. Canonical rule

> **A durable VibeCAD analysis job never carries standing permission to mutate CAD.  It carries immutable provenance.  When results are ready, the host re-establishes the exact document, asks the domain whether the solved engineering state is still current, then obtains fresh Native publication authority for that exact completed job.**

That rule is what lets FEM stay safe while Aero/CFD becomes genuinely durable.
