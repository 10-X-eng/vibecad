# Host Analysis Runtime — Persistence, Recovery, and Reattachment

## Current-state truth

At the frozen baseline, Native Background is an in-memory service. A process crash or application restart loses executable manager state. Therefore durable recovery is a **target capability**, not something this package claims already works.

## Two-record architecture

### Durable `JobDescriptor`

Persist only inert, serializable facts:

- schema version;
- job ID;
- capability/domain/action identity;
- provider name + provider implementation/version;
- solver/backend name + version/implementation identity;
- document name + document UID + captured revision seed;
- stable target refs/UIDs needed for rebind;
- domain-owned input fingerprint/hash;
- immutable prepared-input manifest and content hashes;
- provider external job ID where applicable;
- lifecycle status/phase snapshot;
- created/updated/start/end timestamps;
- cancellation-requested timestamp;
- timeout/deadline metadata;
- bounded log/event references;
- artifact manifest + checksums;
- execution receipt;
- structured error/failure kind;
- publication/currentness disposition;
- cleanup disposition.

### Ephemeral `JobRuntimeHandle`

Never persist:

- FreeCAD `Document` or `DocumentObject` instances;
- Python callables/closures;
- futures;
- threads;
- locks;
- `threading.Event` objects;
- `Popen` instances;
- open file descriptors/pipes;
- GUI objects;
- provider client objects containing secrets;
- plaintext credentials/tokens;
- unbounded stdout buffers.

These are reconstructed or declared non-recoverable after restart.

## Recovery classification

On host start, every non-terminal durable job is reconciled by provider capability.

### Local process with no authoritative reattach identity

Do not pretend it resumed.

- mark compatibility-visible status `failed`;
- structured `failure_kind = host_interrupted`;
- retain sealed input/output artifacts already present;
- do not automatically import outputs into CAD;
- clean orphan resources if ownership can be proven;
- user/agent may explicitly resubmit from immutable inputs.

### Durable remote/provider job with authoritative external ID

A provider may support reconnect:

1. query authoritative provider status;
2. reconcile logs/artifacts/checksums;
3. if still running, restore monitoring only;
4. if complete, retrieve/seal outputs;
5. before any CAD publication, rebind exact document targets on main thread;
6. rerun runtime-context and domain-currentness validation;
7. attach only if still current and policy permits;
8. otherwise retain result as historical/quarantined evidence.

Provider reconnectability never waives CAD currentness.

## Artifact sealing

Provider output becomes eligible for parsing/publication only after an immutable artifact receipt is created. Minimum receipt:

- artifact logical role;
- path/content-store reference;
- byte size;
- cryptographic hash;
- producing job ID;
- provider execution identity;
- solver/backend identity;
- input-manifest hash;
- creation/retrieval time.

This separates “files happened to exist in a temp directory” from evidence that can safely survive runtime restarts.

## Persistence transaction ordering

Critical lifecycle transitions should be durably recorded in an order that cannot create false success:

1. persist prepared descriptor before provider submission;
2. persist provider external ID immediately after successful submission;
3. persist output artifact manifest before publication;
4. acquire/persist commit intent/gate before document mutation where the storage backend supports it;
5. publish in the CAD transaction;
6. persist publication receipt;
7. only then persist terminal success.

On ambiguous crash boundaries, recover conservatively: verify document receipt/currentness rather than assuming success.

## Data migration

Durable schema is versioned from day one. A schema migration must be able to read the immediately previous format and must never deserialize executable Python objects. Unknown future fields are preserved or safely ignored according to explicit schema policy.

## Retention

Separate retention classes:

- job metadata: durable/auditable;
- bounded logs: policy-driven;
- input/output artifacts: content-addressed, reference-counted/pinned;
- transient work directories: disposable after sealed artifacts and domain commit requirements are satisfied.

Deletion of a temp workdir must not destroy the only copy of evidence referenced by a completed result.

## Durable authority boundary

Persistence stores a `PublicationDescriptor` and original submission identity as inert provenance. It does **not** serialize `NativeRuntimeContext`, a live/reusable `NativeCallTicket`, reauthorization callbacks, transaction handles or FreeCAD/Qt objects. After restart, provider/job recovery and CAD publication are separate operations. Publication requires exact source rebind, domain currentness and fresh Native publication authorization. Otherwise the completed result remains `AWAITING_SOURCE`, `AWAITING_PUBLICATION` or stale/quarantined.

See `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md`.
