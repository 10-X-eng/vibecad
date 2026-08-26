# VibeCAD Governed Engineering Architecture whitepaper evaluation

**Evaluation status:** completed against source baseline
`93500486c1515eac2ee98121e16a96a3038c0299` on 2026-08-26

**Disposition:** adopt the thesis and the expansion direction, but do not adopt
the submitted capability statuses or Programs A-Q verbatim.

## 1. Executive verdict

The paper identifies the right product: VibeCAD should be a governed parametric
engineering host in which AI proposes and explains work while deterministic
Native, VibeScript, Analysis, Assembly, Manufacture, Robot, and domain adapters
retain authority over engineering state, execution, verification, and
publication.

The paper is strongest when it insists on:

- structural currentness instead of conversational freshness;
- sealed inputs and explicit dependencies for detached computation;
- provider/physics separation;
- commit-time publication authority;
- evidence-bounded claims;
- one governed mutation path rather than provider-specific CAD APIs;
- durable provenance across CAD, analysis, and manufacturing;
- assembly and robot semantics remaining in their owning domains.

The paper is not yet safe to use as an implementation roadmap for three
reasons:

1. Its repeated `Implemented` labels collapse verified foundations, partial
   integrations, compatibility slices, and complete product capabilities into
   one status.
2. Programs J through Q overlook substantial current Manufacture, Assembly,
   component-interface, and Robot implementation and would create duplicate
   owners if read literally.
3. Its external-agent authority claim is stricter than the current product.
   `/v1/native` does reuse the frozen Native dispatcher and held Native
   sessions, but the compatibility `/v1/run` route still executes Python in the
   VibeCAD process. Source-text guards reject several obvious CAD mutation
   forms; they are not a capability sandbox.

The corrected implementation sequence and acceptance gates are defined in the
[VibeCAD governed engineering roadmap](vibecad-governed-engineering-roadmap.md).

## 2. Method and status vocabulary

The submitted 84-section paper was compared with the current repository,
existing canonical roadmaps/specifications, source registration, public
schemas, focused tests, and relevant primary standards. A source file or test
proves only its bounded contract; it does not prove release readiness or every
claim made by a section heading.

| Evaluation status | Meaning |
| --- | --- |
| **Verified foundation** | Current source and tests support the stated bounded capability. Later productization may remain. |
| **Partial** | A real implementation exists, but the paper's full claim or required cross-domain behavior is not complete. |
| **Design-ready** | The need, owner, dependencies, and acceptance boundary are clear enough for an implementation tranche. |
| **Unverified** | The audit did not find enough current evidence to make the claim. |
| **Reframe** | The proposed program is useful only after it is narrowed to extend an existing owner. |
| **Reject as duplicate** | A literal implementation would create a second authority for state already owned elsewhere. |

This evaluation does not certify release packaging, production security,
solver accuracy, manufacturing safety, robot safety, or real-world fitness.

## 3. Corrected current-capability boundary

| Whitepaper capability | Corrected status | Current evidence | Honest boundary |
| --- | --- | --- | --- |
| Governed Native CAD | **Verified foundation** | `VibeCADNativeDispatch.py`, `VibeCADNativeState.py`, capability schemas, Native tests | This proves the current governed Native surface, not that every CAD mutation route is governed. |
| Structural revisions | **Verified foundation** | Native state store, observers, structural-property classification, revision-conflict tests | Revision scope is host-defined and must continue to be audited as new state owners are added. |
| Stale Native-call rejection | **Verified foundation** | `NativeCallTicket`, expected revision checks, conflict errors | Dependency-scoped staleness outside Native remains domain/runtime work. |
| Idempotent Native-result memory | **Verified foundation** | Bounded verified-result and receipt memory keyed by idempotency token | Memory is process/document scoped and bounded; it is not a durable cross-restart receipt ledger. |
| Structured mutation receipts | **Verified foundation** | `NativeOperationReceipt` with exact object identities and revision bounds | The receipt is not yet the unified provenance model proposed by Program E. |
| Preview/apply staged mutation | **Partial** | Ten allowlisted families in `NATIVE_PREVIEW_FAMILIES`, stale/consume/apply/reject tests | It is not a complete consequential-mutation census. Manufacture, Assembly, Robot, export, and external side-effect policies need explicit classification. |
| Human-controlled authoring mode | **Verified foundation** | `VibeCADAuthoringMode.py`, project metadata, authority-conflict handling | New automation must preserve explicit mode/authority state and cannot silently reset it. |
| Frozen provider tool surface | **Verified foundation** | modeling-surface freeze and schema-digest behavior | The frozen model tool set does not restrict separate privileged local automation routes. |
| Domain-neutral Analysis contracts | **Verified foundation** | `analysis_contracts.py` and installed public facades | Contract presence does not imply durable storage, remote portability, or complete result semantics. |
| Input sealing | **Partial** | bounded sealed-directory manifest and hash checks in `analysis_artifacts.py` | The complete immutable artifact store, archive defenses, recovery, retention, and garbage collection remain open. |
| Dependency identity/currentness | **Partial** | `DependencyRecord`, `DependencySnapshot`, `CurrentnessReport`, FEM/Aero consumers | The host vocabulary exists, but every domain has not converged on complete dependency-scoped publication checks. |
| Detached Analysis orchestration | **Verified foundation for the in-memory slice** | `analysis_runtime.py`, lifecycle/cancellation/commit-gate tests | The runtime explicitly stores jobs in memory and does not recover across restart. |
| Local execution provider | **Verified foundation** | `LocalProcessProvider`, shared bounded process sequence | Reconnect is explicitly unsupported; cross-platform descendant cleanup remains a release gate. |
| Multi-solver provider migration | **Verified foundation for current FEM adapters** | CalculiX, Elmer, Z88, and Mystran use the host local provider | Full legacy/host A/B parity and long-running stabilization are still partial in the Aero roadmap. |
| Commit/publication gate | **Verified foundation for current in-memory paths** | atomic cancellation-versus-commit ordering and document-thread commit | Durable replay, restart ambiguity, exact-source publication, and idempotent recovery remain unimplemented. |
| Bounded process infrastructure | **Partial** | shell-free command sequence, time/log bounds, cancellation | Complete Windows and POSIX process-tree ownership and orphan cleanup are not yet closed. |
| Local external-agent API | **Partial** | loopback bearer token, `/v1/context`, `/v1/native`, `/v1/prompt`, screenshots, tests | `/v1/run` remains a privileged Python compatibility route. The system must not claim dispatcher exclusivity while it exists. |
| Persistent external Native session | **Verified foundation for a live process** | held session identity, 256-call budget, idle expiry, explicit close, shared undo scope | The session is process-local continuity, not restart-persistent workflow or engineering state. |
| Architecture-level CI | **Verified foundation** | source-bound architecture, packaging, and capability tests plus build workflows | CI coverage is evidence for named invariants only; it is not proof that Programs A-Q are complete. |

## 4. Evaluation of Programs A-Q

| Program | Disposition | Corrected interpretation |
| --- | --- | --- |
| A — Persistent Analysis Jobs | **Adopt; Design-ready** | Already owned by VibeCADAero host Steps 8 and 8A. Add versioned transactional job metadata, immutable artifacts, fault-injected recovery, and fresh publication authority. |
| B — Remote Execution Provider | **Adopt after A** | Implement one real provider only after durable identity, reconnect, artifact validation, credential boundaries, cancellation semantics, and replay-safe publication exist. |
| C — Analysis Result Envelope | **Adopt as a thin host contract** | Add a versioned common envelope around domain payloads. Do not flatten domain-specific fields or equate provider success with verification/publication. |
| D — Verification Findings | **Adopt as a common vocabulary** | Define stable finding/rule/source identities, severity, verdict, affected engineering identities, evidence, remediation, and claim ceiling. Keep domain taxonomies extensible. |
| E — Unified Provenance | **Adopt and broaden** | Model entities, activities, agents, usage, generation, derivation, association, and publication. Hashes are identity evidence, not the entire provenance model. |
| F — Preview Coverage Audit | **Adopt immediately** | Build a complete capability/operation policy census. Preserve read-only, safe immediate, preview-required, confirmation-required, and external-side-effect distinctions. |
| G — Preview Evidence | **Adopt after the census** | Add bounded geometry/effect summaries only where useful. A preview remains non-authoritative, expiring, dependency-bound state. |
| H — Workflow DAG | **Adopt after A, C, D, and E** | Build a durable orchestration layer that references jobs; do not turn one Analysis job into an implicit workflow engine. |
| I — Optimization | **Adopt after H and F/G** | Candidates must use governed design mutation, immutable inputs, explicit variables/objectives/constraints, bounded budgets, complete provenance, and no automatic publication without policy. |
| J — Manufacturing Jobs | **Reframe** | VibeCAD already has Native Manufacture jobs, operations, tools, post-processing, simulation, retained simulation results, templates, and exports. Add runtime-backed detached tasks and evidence without replacing the Manufacture graph. |
| K — Canonical Assembly Graph | **Reject as a new owner; extend Assembly** | Native Assembly objects and the existing joint/occurrence graph remain canonical. Consolidate stable identities and missing edge semantics there; do not create an Analysis-owned graph. |
| L — Component Interfaces | **Reframe as an extension** | `component.interface` and named Assembly connectors already exist. Expand the interface taxonomy and stability rules in the Assembly/component owner. |
| M — Joint Inference | **Adopt as propose-only assistance** | Infer ranked candidates from existing interfaces, but require explicit acceptance through the Assembly joint authoring path. Inference never creates an authoritative joint by itself. |
| N — Assembly Validation | **Continue the existing specification** | Solve diagnostics and rigid solved-state mechanism checks exist. Continue continuous-motion certification, flexible-subassembly coverage, fit, stable identity, and Part Design verification in `assembly-mechanism-integration-spec.md`. |
| O — Assembly Sequencing | **Adopt after K/L/N consolidation** | Exploded views and playback are useful presentation foundations but are not accessibility, insertion, precedence, or collision-verified sequence proof. |
| P — Service and Disassembly | **Adopt after O** | Build target-specific removal planning over the same validated graph and sequence engine; report minimum removal sets as bounded optimization results, not guaranteed shop procedures. |
| Q — Robot Task Projection | **Reframe as an adapter** | Robot setup, trajectories, waypoint editing, simulation, and KUKA export already exist. Project verified assembly steps into robot intent, then leave reachability, motion planning, controls, and cell safety to the Robot domain and downstream systems. |

## 5. Important corrections the roadmap must preserve

### 5.1 One identity is not enough

The paper lists useful IDs but does not define their non-interchangeability.
The implementation must keep at least these identities distinct:

- `analysis_id`: durable host identity for one prepared engineering analysis;
- host attempt ID: one execution attempt under an analysis;
- provider job ID: provider-owned reconnect/cancel identity;
- event ID: duplicate-detection identity for one lifecycle event;
- trace ID/span ID: diagnostic correlation across calls;
- result ID: immutable result-envelope identity;
- publication receipt ID: idempotent document/publication mutation identity;
- workflow ID/node ID/run ID: orchestration definition and execution identity;
- source document UID and structural/dependency revisions.

Provider IDs and trace IDs must never become document identity or publication
authority.

### 5.2 Provenance is a graph, not metadata decoration

The common envelope should use a VibeCAD profile of the concepts in
[W3C PROV-DM](https://www.w3.org/TR/prov-dm/): engineering inputs and outputs
are entities; mutation, preparation, execution, verification, and publication
are activities; humans, models, host code, solvers, and providers are agents
with explicit roles. Usage, generation, derivation, association, and delegation
connect them. VibeCAD does not need to adopt RDF as its storage format to use
this model correctly.

### 5.3 Event identity, tracing, and durable identity are separate

The [CloudEvents core specification](https://github.com/cloudevents/spec/blob/ce%40stable/cloudevents/spec.md)
provides a useful duplicate rule based on `source` plus `id`, while
[W3C Trace Context](https://www.w3.org/TR/trace-context/) provides interoperable
request correlation. VibeCAD should borrow those semantics but keep both
separate from `analysis_id`, provider job identity, and publication receipts.

### 5.4 Portable artifacts require descriptors and semantics

The [OCI content descriptor model](https://specs.opencontainers.org/image-spec/descriptor/)
demonstrates the minimum safe reference shape: media type, digest, and byte
size. A VibeCAD portable bundle additionally needs a versioned semantic
manifest, declared entry points, platform/runtime requirements, units, source
dependencies, expected outputs, bounds, and signature/trust policy. Adopting
descriptor concepts does not require packaging every job as a container image.

### 5.5 Findings should be common without erasing domains

[SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html)
is a useful reference for tool/rule identity, results, locations, artifacts,
invocations, and taxonomies. VibeCAD findings need engineering identities,
units, verdicts, evidence levels, currentness, and claim ceilings that SARIF
does not define. The roadmap therefore uses SARIF as a design reference, not as
the VibeCAD public schema.

### 5.6 Persistence technology is an implementation decision

SQLite is a credible local metadata-store candidate because its official
[write-ahead logging documentation](https://www.sqlite.org/wal.html) describes
atomic commit/rollback and concurrent readers. It must still pass a VibeCAD
spike covering Windows packaging, locking, power-loss fault injection, schema
migration, backup/copy behavior, WAL checkpointing, and corruption recovery.
The roadmap does not pre-approve SQLite or WAL mode.

### 5.7 Local automation needs an explicit privilege model

The current agent surface has two materially different paths:

| Path | Current authority |
| --- | --- |
| `/v1/native` | Frozen Native capability schemas, structural revision checks, receipts, held session limits, and Native undo scope. |
| `/v1/run` | Python execution inside the VibeCAD process with source-text checks for several obvious CAD mutations. |

The second path can be useful for compatibility and non-CAD utility work, but
it is a privileged escape hatch. The roadmap requires one explicit decision:
retain and clearly label it, constrain it with a real capability boundary, or
deprecate it through a separately approved compatibility migration. A string
filter is not a security boundary, and this documentation change does not
authorize removal of the route.

## 6. Missing requirements added by the evaluation

The following requirements were absent or too implicit in the submitted paper
and are mandatory in the canonical roadmap:

- schema migration and unknown-version refusal for every durable record;
- crash-point fault injection before and after each metadata, artifact, and
  publication transition;
- one-writer/locking rules and behavior when two VibeCAD processes open the
  same project;
- retention, pinning, quotas, cleanup, and evidence-aware garbage collection;
- secret redaction and a rule that credentials never enter manifests,
  provenance, logs, document objects, or portable bundles;
- untrusted archive, symlink, traversal, decompression-bomb, and oversized
  artifact defenses;
- provider callback authentication, replay resistance, duplicate/out-of-order
  event handling, polling backoff, and quota/rate-limit behavior;
- explicit distinction between retrying an attempt and creating a new
  engineering analysis;
- compatibility behavior for old jobs/results after schema or provider
  upgrades;
- exact cancellation semantics before launch, during execution, during
  collection, while waiting to publish, and after publication begins;
- workflow cycle rejection, deterministic ready-node ordering, bounded
  fan-out, resumability, and node-level publication policy;
- optimization budgets, deterministic seeds where applicable, duplicate
  candidate detection, failed/indeterminate candidate handling, and human
  approval before accepted-state mutation;
- assembly-sequence claims that distinguish sampled motion, conservative
  continuous proof, collision, and indeterminate accessibility;
- robot projection units, frame conventions, tool/TCP identity, tolerance,
  and explicit downstream validation status;
- installed-tree imports, packaging manifests, upgrades, and platform matrices
  for every new public facade and runtime dependency.

## 7. Final decision

Adopt the paper as a strategic architecture statement after applying this
evaluation. Do not use its Section 57 status list as the repository baseline,
and do not create Programs J-Q as independent greenfield subsystems. The
canonical implementation authority is the companion roadmap, current source,
and the existing domain specifications it references.
