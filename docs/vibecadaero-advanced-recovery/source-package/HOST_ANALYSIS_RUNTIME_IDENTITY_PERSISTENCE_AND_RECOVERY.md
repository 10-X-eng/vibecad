# Host Analysis Runtime — Document Identity, Persistence, Restart, and Recovery

**Purpose:** define the hard lifecycle cases before durable CFD/FEM jobs are introduced.

## 1. Identity law

A document label or file path is never sufficient authority for result attachment.

The runtime distinguishes:

- **host document identity** — current VibeCAD uses `document_uid(document)`, sourced from FreeCAD `Document.Uid`, as the exact document identity; its Save/Save-As/copy/reopen behavior must be characterized before durable automatic reattachment is enabled;
- **source dependency identity** — domain-specific hashes/references describing the engineering state that was solved;
- **informational locator** — file path, label, project name; useful for UI, never sufficient for attachment;
- **durable analysis identity** — analysis/job IDs independent of whether the source document is currently open.

VibeCAD already has a canonical host identity seam: `document_uid(document)` reads `Document.Uid`. **Do not invent a second document-ID system during extraction.** Instead characterize `Document.Uid` across Save, Save As, Save Copy/clone, close/reopen, crash recovery and copied/imported files. If any case is ambiguous, durable publication remains `AWAITING_SOURCE` until an explicit safely revalidated binding exists.

## 2. Save, Save As, Save Copy, clone

### Save

No semantic change. A running job remains bound to its captured source dependencies; saving the file does not make stale data current or current data stale by itself.

### Save As in the same live document session

The path may change while the live document identity remains the same. Path change alone is not a stale condition. Domain dependencies decide currentness.

### Save Copy / document clone

A copied file/document must not inherit automatic publication authority for a running job merely because its contents or label look similar.

The result may be *eligible for explicit reattachment* only after exact dependency revalidation. Automatic attachment requires an unambiguous live source identity.

## 3. Document close while a job runs

Closing a document does not need to destroy an expensive detached computation.

Target behavior:

1. execution may continue detached if its provider can do so safely;
2. publication becomes impossible while no exact source document is open;
3. completed output is retained as immutable job/artifact state;
4. job publication state becomes `AWAITING_SOURCE` or equivalent, not `FAILED`;
5. reopening a candidate document triggers exact domain revalidation;
6. if exact/current, publication can be offered/performed through normal Native authority;
7. if different, result remains stale/quarantined historical evidence;
8. never publish into a different document solely because it has the same label/path.

Existing FEM behavior should first be characterized; this richer behavior is introduced only after durable persistence exists.

## 4. Application shutdown

The host runtime needs an explicit shutdown protocol.

### Local provider

- stop accepting new jobs;
- persist latest durable state if persistence exists;
- request cooperative cancellation for local jobs according to current product semantics;
- terminate owned child processes when required for safe shutdown;
- record whether termination was confirmed;
- never mark success merely because output files exist after restart.

### Remote provider

A UI/app shutdown is not necessarily a remote-job cancellation. If the user did not request cancel and the provider supports reconnect, persist the provider job identity and resume observation later.

The provider contract must declare whether a remote job survives client exit.

## 5. Restart classification

On startup, nonterminal persisted jobs are reclassified by evidence, never guessed.

Possible outcomes:

- `RECONNECTING` — provider has durable job identity and supports status reconnect;
- `RUNNING` — reconnect confirms still running;
- `COLLECTING` — provider confirms completed and outputs are available;
- `ORPHANED` — host cannot prove the process/provider state;
- `AWAITING_SOURCE` — computation/result exists but source document is not open;
- `STALE/QUARANTINED` — source reopened but dependencies do not match;
- `FAILED` — provider explicitly reports failure or artifact validation fails.

A local PID from a previous app process is not by itself sufficient proof of ownership or correctness after restart.

## 6. Persistent metadata design

Recommended default: a VibeCAD-owned SQLite database under application data, introduced only after in-memory parity.

Persist compact metadata:

- job/analysis ID;
- schema version;
- domain/adapter/provider identity;
- provider job ID;
- lifecycle timestamps/state/attempt;
- immutable dependency snapshot refs;
- artifact manifest refs;
- execution receipt/failure;
- publication/currentness state;
- bounded human-readable status.

Do not persist:

- live Python/Qt/FreeCAD objects;
- raw credentials/tokens;
- multi-GB fields inside SQLite blobs;
- whole solver logs when a file artifact suffices.

## 7. Crash consistency

State updates and artifact publication must survive abrupt termination without inventing success.

Rules:

- write artifacts to temporary names then fsync/atomic-rename where supported before manifest promotion;
- manifest hash is computed from final immutable bytes;
- job state referencing an artifact is committed only after the artifact is durable enough for the platform contract;
- publication receipt is committed after Native mutation succeeds, not before;
- database transitions are transactional;
- restart treats incomplete transitions as recovery cases;
- result importer must not consume partially written output as completed evidence.

## 8. Artifact identity and storage

Artifacts are content-addressed or at minimum content-hashed immutable records.

Separate:

- input bundle;
- solver output bundle;
- logs;
- parsed numerical result;
- visualization/field products;
- publication receipt.

Large CFD fields remain outside FCStd. The document stores compact references/provenance sufficient to explain what produced the result.

No hidden destructive cleanup. Storage accounting can surface size/age/reachability; deletion is an explicit lifecycle action with clear consequences.

## 9. Job retries and attempts

Retry is not mutation of history.

A retry creates a new **attempt** under the same analysis intent or a new analysis ID according to whether prepared inputs are identical.

- identical sealed inputs + same solver/provider spec may share `analysis_id` with incremented `attempt`;
- changed inputs/config create a new prepared analysis identity;
- previous failed/cancelled attempts remain inspectable;
- a successful retry does not erase the earlier failure;
- publication identifies the exact successful attempt/artifacts.

## 10. Idempotent collection and publication

Provider reconnect or UI retry may cause collection to be requested more than once.

- output collection validates immutable hashes and can be repeated safely;
- parsed result identity is deterministic for the same output bundle/version where practical;
- publication checks a unique publication receipt before creating result objects;
- if already published, return the existing receipt/result identity rather than duplicate objects.

## 11. Persistence schema evolution

Schema evolution is its own engineering concern.

Required fixtures:

- empty/new database;
- immediately prior schema;
- database with running/reconnecting jobs;
- stale/quarantined completed jobs;
- missing/corrupt artifact reference;
- interrupted migration fixture.

Migration failure must preserve recoverability. It must not touch FCStd data and must not delete artifacts to make the schema appear clean.

## 12. Aero-specific application

Aero currentness should be driven by aerodynamic dependencies, not merely file/revision equality:

- geometry identity/hash;
- relevant body/object selection;
- CAD↔body↔solver transforms and reference point;
- AeroConfig digest;
- reference area/span/chord;
- atmosphere/flow conditions;
- mesh/domain generation settings;
- solver/model/settings/build;
- moving-body/propulsion state where applicable.

A document may change in unrelated ways while an Aero result remains current. Conversely, a tiny relevant geometry/config change must make it stale.

## 13. FEM preservation rule

Do not replace current FEM `solver_still_exact`, History tuple, and `KeepResultsOnReRun` checks with the Aero dependency model during extraction. Preserve them exactly first. Any later refinement of FEM invalidation granularity is a separate behavior change with its own evidence.
