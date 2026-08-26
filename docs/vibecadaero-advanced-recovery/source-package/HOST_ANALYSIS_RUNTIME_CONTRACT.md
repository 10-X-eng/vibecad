# VibeCAD Host Analysis Runtime — Canonical Contract

**Status:** target architecture for implementation after a fresh upstream freeze  
**Frozen design anchor:** `df07a5e82ec2fb31515e10b33822253d69d496ff`

## 1. Ownership law

The durable ownership rule is:

> **VibeCAD owns jobs. Domains own engineering meaning. Compute providers own execution location. Native owns CAD mutation. Evidence/qualification owns claims.**

No layer may silently absorb another layer's authority.

### VibeCAD Analysis Runtime owns

- globally unique job identity;
- lifecycle state and transition validity;
- immutable prepared-input manifests;
- generic dependency snapshots;
- provider submission/reconnection identity;
- local/remote execution orchestration;
- progress, cancellation and timeout;
- generic logs and execution receipts;
- immutable artifact manifests and content hashes;
- durable job metadata/restart recovery;
- source-currentness validation orchestration;
- publication scheduling onto the document thread;
- quarantine/currentness status;
- structured generic failures.

### Engineering domain owns

For FEM, Aero, thermal, EM, acoustics, optimization, etc.:

- what state is relevant to the computation;
- how live document/domain state becomes immutable solver inputs;
- solver-specific configuration;
- what dependencies make a result current or stale;
- how outputs are parsed;
- what result objects/evidence mean;
- what validation/qualification is required;
- how a successful result is represented in its domain;
- what `NativeMutationDraft` or equivalent publication draft is appropriate.

### Compute provider owns

- where/how a prepared immutable bundle executes;
- provider capability description;
- submission/launch;
- provider job ID;
- status/progress transport;
- cancellation transport when supported;
- reconnect semantics when supported;
- output/log retrieval;
- provider-specific execution receipt.

Providers do **not** choose physics and do **not** decide whether a model is qualified.

### Native mutation authority owns

- document-thread transaction boundaries;
- recompute/validation;
- created/changed/deleted object receipts;
- structural revision changes;
- rollback on publication failure.

The Analysis Runtime MUST NOT bypass `NativeMutationBoundary` or directly mutate a FreeCAD document from a worker.

### Submission/execution/publication authority separation

A durable analysis job carries **provenance, not standing CAD mutation permission**.  The host distinguishes:

- `SubmissionAuthorization`: proves the exact job/prepared work was legitimately created from an exact source state;
- `ExecutionAuthority`: permits only execution/collection of that sealed work;
- `PublicationAuthorization`: a fresh document-thread Native authorization for the exact completed job after source rebind and domain currentness validation.

Do not serialize a live `NativeRuntimeContext`, `NativeCallTicket`, reauthorization callback, transaction handle, FreeCAD object, or Python closure as future authority. The original ticket/idempotency identifiers may be retained as inert provenance only. Current FEM migration preserves its existing original-ticket/global-revision publication behavior first; the durable publication coordinator is an additive later host capability required before production long-running Aero CFD. See `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`.

### Evidence / qualification owns

- `not_solved` vs solved;
- `model_unqualified` vs qualified;
- benchmark qualification envelope;
- measured vs derived vs presentation evidence;
- engineering claim ceilings.

`returncode == 0` means execution completed. It never means model qualified.

---

## 2. Core contracts

The exact implementation language may change, but the semantic contracts below are canonical.

### 2.1 `PreparedAnalysis`

An immutable description of one prepared computation.

Required concepts:

```text
analysis_id             immutable UUID
schema_version          host contract version
domain                  e.g. fem | aero | thermal
adapter_id              exact domain adapter implementation
adapter_version         implementation/version identity
created_at              UTC
source_document_uid     stable document identity, never UI label
source_summary          informational only
dependency_snapshot     immutable dependency records
input_manifest          immutable artifact manifest
execution_spec          provider-neutral execution request
expected_outputs        declared output expectations
publication_descriptor  domain-owned opaque, serializable publication recipe/identity
provenance              software/solver/config hashes and human-readable summary
```

It MUST contain only serializable primitive data and immutable artifact references. No live FreeCAD/Qt/Python domain object is durable state.

### 2.2 `DependencySnapshot`

A domain contributes the exact dependencies whose change affects result currentness.

Each dependency record contains at least:

```text
key
kind
canonical_digest or stable reference
human_summary
required_for_current_attachment
```

Examples for Aero:

- structural revision when relevant;
- aerodynamic geometry revision/hash;
- selected body/object identity;
- `AeroConfig` canonical digest;
- CAD→body transform;
- reference area/span/chord/reference point;
- atmosphere state;
- boundary conditions;
- mesh-generation configuration;
- solver version/configuration;
- moving-body/propulsion state.

Examples for FEM initially preserve the **existing exact behavior**:

- prepared solver state hash;
- exact History operations tuple/identity;
- result-retention preference;
- current FEM solver-specific invariants.

Do not improve FEM dependency granularity during the extraction PRs. First preserve behavior. Granularity changes are separate later work.

### 2.3 `ExecutionSpec`

Provider-neutral execution request:

```text
provider_id
entrypoint/command descriptor
arguments
environment keys/values allowed to persist
runtime-only secret references (never secret values)
working-set requirements
resource hints
timeout
expected exit semantics
remote-portability declaration
```

The local provider MUST continue to execute argv directly, never through a shell by default.

### 2.4 `ArtifactManifest`

Every immutable input/output artifact has:

```text
artifact_id
role                  input | output | log | checkpoint | visualization | receipt
media_type
relative/logical name
byte_count
sha256
storage reference
exactness class       exact | derived | presentation where applicable
producer identity
created_at
```

Large files are referenced, not embedded into job JSON or FCStd.

The host must reject traversal, unsafe symlinks, inconsistent hashes, or unsafe archive extraction.

### 2.5 `AnalysisJob`

The host-owned durable lifecycle record:

```text
job_id
analysis_id
domain
provider_id
provider_job_id
state
progress
status_message
created/updated/started/completed timestamps
attempt
execution_receipt
output_manifest
failure
currentness/publication status
```

Domain-specific aerodynamic/FEM content is referenced by `analysis_id`/payload identity, not promoted into the generic scheduler schema.

---

## 3. State model: three axes, not one overloaded status

A single `SUCCEEDED` flag is insufficient. Keep three related but distinct state concepts.

### 3.1 Execution state

```text
PREPARING
  -> PREPARED
  -> QUEUED
  -> SUBMITTING (remote/provider optional)
  -> RUNNING
  -> COLLECTING
  -> SOLVED
```

Terminal execution alternatives:

```text
FAILED
CANCELLED
TIMED_OUT
CAPABILITY_UNAVAILABLE
ORPHANED
```

`SOLVED` means expected execution completed sufficiently for domain parsing/validation to proceed.

### 3.2 Publication/currentness state

```text
UNVALIDATED
  -> VALIDATING_SOURCE
  -> CURRENT
  -> PUBLISHING
  -> PUBLISHED
```

or:

```text
VALIDATING_SOURCE
  -> STALE
  -> QUARANTINED
```

or:

```text
PUBLISHING
  -> PUBLISH_FAILED
```

A successful stale solve is not a failed computation. Its result and provenance are retained as immutable historical/quarantined evidence and are not silently attached as current.

### 3.3 Engineering evidence state

Domain/evidence-owned, e.g.:

```text
not_solved
model_unqualified
qualified for envelope X
measured
derived
presentation
```

The host runtime records these facts but does not invent them.

---

## 4. Threading contract

This is non-negotiable for FreeCAD integrity.

### Document/main thread may

- inspect live FreeCAD domain objects;
- resolve stable object identities;
- run domain `prepare()` that requires FreeCAD APIs;
- create/finalize immutable input files;
- capture dependency snapshot;
- produce publication drafts;
- revalidate live dependencies;
- run Native mutation transaction/publication.

### Worker threads/processes may

- hash sealed files;
- upload/download;
- spawn external processes;
- poll providers;
- stream bounded logs;
- parse immutable output files when parsing is FreeCAD-independent;
- perform pure numerical postprocessing.

### Worker threads/processes MUST NOT

- hold live FreeCAD object references as durable job state;
- mutate a document;
- recompute a document;
- manipulate Qt GUI state;
- create result objects directly in the live document.

If a parser requires FreeCAD, separate file parsing from document publication and run the FreeCAD-dependent phase on the document thread.

---

## 5. Provider contract

Minimum provider surface:

```text
describe_capabilities()
submit_or_launch(prepared_analysis)
status(provider_job_id)
cancel(provider_job_id)
collect(provider_job_id)
reconnect(provider_job_id)
```

Capabilities explicitly advertise:

- local vs remote;
- reconnect support;
- cancel support;
- accelerator types if known;
- log streaming;
- max input/output sizes when known;
- execution environment identity;
- portable bundle requirements.

Initial provider implementation is `LocalProcessProvider`, extracted from the proven current process runner without behavioral change.

Kaggle becomes a later provider, not part of the extraction PR. Future SSH/Slurm/cloud providers use the same contract.

---

## 6. Domain adapter contract

A domain adapter conceptually supplies:

```text
prepare(live_domain_state) -> PreparedAnalysis
validate_dependencies(snapshot, live_domain_state) -> CurrentnessReport
parse(outputs) -> immutable domain result
build_publication(result, currentness) -> NativeMutationDraft/domain publication draft
qualify(result, qualification_registry) -> evidence state
```

The runtime never calls arbitrary solver/domain methods by reflection. Adapters are registered explicitly.

FEM adapters initially wrap the existing CalculiX/Elmer/Mystran/Z88 builders/importers. Aero adapters later cover FluidX3D/OpenFOAM and can also use the host runtime for other external high-fidelity solvers.

---

## 7. Persistence contract

Durability is added only after behavior-preserving extraction proves stable.

Recommended target:

- standard-library SQLite job metadata database in VibeCAD per-user app-data;
- versioned schema and explicit migrations;
- atomic transactions;
- content-addressed or immutable artifact directory alongside app data/cache;
- FCStd stores only small analysis/result references and engineering evidence needed in the document;
- provider credentials/tokens are never persisted in the job DB or artifact manifest;
- remote provider IDs may be persisted so jobs can reconnect after restart.

Do not store multi-gigabyte CFD/FEM artifacts inside FCStd.

### Durable publication descriptor

Persistence stores an inert `PublicationDescriptor` containing exact job/analysis/submission identity, source `document_uid`, domain publication adapter/version, frozen dependency snapshot, validated output-manifest identity and result/publication identity. It does **not** store executable Native authority. After restart or a document reopen, publication must establish a fresh host context and fresh Native mutation authorization; otherwise the result remains `AWAITING_SOURCE` or `AWAITING_PUBLICATION`.

### Restart semantics

On restart:

- completed/published jobs reopen as historical records;
- remote jobs with reconnect-capable providers attempt explicit reconnect;
- local child processes that cannot be proven/reconnected become `ORPHANED`, never fabricated as complete;
- output directories found on disk are not automatically trusted—manifest/hash validation is required;
- jobs whose document is absent remain unattached until/if a matching source can be resolved;
- no job is automatically republished merely because a document with the same display name opens.

---

## 8. Compatibility facade contract

Existing VibeCAD APIs are preserved while internals move.

At minimum the implementation must preserve:

- `native.job` action names and current request/response shape during migration;
- current job-creation response plus `native.job status/cancel` behavior;
- existing FEM `analyze.solver_execution` operation `run` semantics;
- current `NATIVE_ANALYZE_*` and background error compatibility for existing clients;
- existing FEM result object graph and History insertion;
- mutation receipts;
- current one-active-job-per-document rule during extraction;
- current GUI/ribbon/agent routes.

Old Python modules remain as compatibility facades/re-exports for at least the migration window. Internal ownership may move without breaking imports.

---

## 9. Generic failure taxonomy

Internally the host runtime should normalize failures such as:

```text
PREPARE_FAILED
INPUT_INTEGRITY_FAILED
PROVIDER_UNAVAILABLE
SUBMISSION_FAILED
LAUNCH_FAILED
TIMEOUT
CANCELLED
PROCESS_FAILED
COLLECTION_FAILED
OUTPUT_MISSING
OUTPUT_CORRUPT
PARSE_FAILED
SOURCE_STALE
PUBLISH_FAILED
ARTIFACT_INTEGRITY_FAILED
RECONNECT_FAILED
ORPHANED
```

Compatibility facades map these to existing public/native error codes where current users/tests depend on them. Do not break current error contracts merely to make naming prettier.

---

## 10. Explicit non-goals

The host Analysis Runtime has **zero** knowledge of:

- commercial/non-commercial use;
- military/civil use;
- FluidX3D license classification;
- user purpose;
- legal eligibility;
- airworthiness decisions;
- solver qualification by return code.

Third-party terms remain informational/documented as already established for Aero. Engineering integrity checks—hashes, staleness, source identity—exist solely to prevent incorrect result attachment and corruption.

## 15. Hard semantic boundary added by deepening

The runtime SHALL treat a domain's input fingerprint/currentness descriptor as opaque evidence. For FEM this means `VibeCADNativeAnalyzeSolverState` semantics—including solver settings, analysis ownership, result roots, History role/owner/children, suppression and implementation identity—remain FEM-owned.

The runtime SHALL NOT infer, normalize, weaken, or replace these dependencies.

Cancellation/publication must be linearizable: a single atomic lifecycle operation decides whether cancellation wins or the job enters non-cancellable `committing`. An accepted cancellation can never later publish to CAD.

Persistence is descriptor-based. Raw FreeCAD objects and executable Python/runtime handles are forbidden in durable records.

See `HOST_RUNTIME_STATE_MACHINE.md`, `HOST_RUNTIME_PERSISTENCE_AND_RECOVERY.md`, and `HOST_RUNTIME_COMPATIBILITY_CONTRACT.md`.


## 11. Publication identity and idempotency

A job/result publication must have a stable identity sufficient to detect replay. Reconnect, UI retry, duplicated completion callbacks, or crash recovery must not create a second result graph for the same exact publication. The host stores/queries a publication receipt, while the domain owns the result identity and Native owns the commit transaction.

## 12. Document identity and reattachment

Document labels and paths are informational only. In-session publication uses the exact host document/session identity. Durable restart reattachment requires unambiguous source identity plus domain dependency revalidation; otherwise a completed result remains `AWAITING_SOURCE` or stale/quarantined. Do not add a new persistent FreeCAD document property during extraction unless a canonical host identity already exists at implementation time.

## 13. Callback/race semantics

Cancellation request, confirmed cancellation, execution completion and publication are distinct facts. Provider events can be duplicated or late. State transitions are validated/monotonic and publication has one owning transaction. Existing FEM race behavior is characterized and preserved before any intentional semantic improvement.

## 14. Crash-consistency contract

Persistent metadata never claims an artifact before the immutable bytes/manifests are safely promoted. A publication receipt is not durable success until the Native mutation transaction has succeeded. Restart never infers success from a leftover file or PID.

## 15. Compatibility and packaging contract

Existing modules/actions/errors remain available through facades during migration. New core runtime modules are explicitly registered in the live build/install system and have no import-time dependencies on Aero or optional solver/provider packages. Installed-tree import tests are required.
