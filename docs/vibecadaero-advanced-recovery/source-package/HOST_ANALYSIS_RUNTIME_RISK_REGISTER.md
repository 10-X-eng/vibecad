# Host Analysis Runtime — Risk Register

This register treats the runtime extraction as foundational VibeCAD work. “Mitigation” means architectural/testing containment, not user-purpose controls.

| Risk | Severity | Failure mode | Containment / acceptance criterion |
|---|---|---|---|
| Big-bang regression | Critical | FEM or Native execution breaks while multiple responsibilities move together | small PR sequence; characterization first; one concern/PR; compatibility facades |
| Worker touches live FreeCAD | Critical | crash/corruption/race/non-deterministic document state | hard threading contract; test instrumentation; only prepare/revalidate/publish on document thread |
| Partial publication | Critical | solver result objects partly inserted after failure | publication only through existing mutation transaction; rollback tests |
| Wrong-current result | Critical | expensive CFD/FEM result attached to changed source | immutable dependency snapshot + document-thread revalidation + stale quarantine |
| Over-invalidation | High | unrelated edit invalidates expensive analysis | domain-contributed dependencies; do not use only global revision long-term |
| Under-invalidation | Critical | relevant geometry/config change not detected | domain dependency schema + hashes + explicit currentness report + tests |
| FEM behavior drift | Critical | inputs/commands/results differ due “cleanup” during extraction | golden parity tests; no solver improvements in extraction PR |
| Public API break | High | agents/ribbon/tests lose `native.job`/error compatibility | facades preserve schemas/action names/error mappings |
| Result qualification conflation | Critical | successful process reported as validated engineering truth | execution/publication/evidence state axes separate; qualification domain-owned |
| Job persistence corrupts FCStd | High | large artifacts or runtime DB coupled to document format | job metadata/artifacts outside FCStd; only compact refs/evidence in document |
| Restart false-success | Critical | stale files on disk treated as completed job | explicit ORPHANED/reconnect; manifest/hash validation; never infer from filename |
| Provider credential leak | Critical | secrets stored in manifests/logs | runtime-only injection; redaction; no secret persistence |
| Unsafe bundle/archive | Critical | traversal/symlink overwrite | seal/extract validation; bounds; safe paths |
| Child process leak | High | solver/GPU workers survive cancel/timeout | preserve existing child-tree cleanup, test Windows/Linux |
| Log/memory growth | High | long CFD job exhausts UI/process memory | bounded status tails + external log artifact + streaming |
| Disk exhaustion | High | CFD fields/checkpoints fill drive | explicit artifact accounting/retention; no hidden destructive GC; surface storage state |
| Concurrency regression | High | multiple jobs race result publication/resources | preserve one job/doc initially; concurrency is separate feature |
| Save-As identity confusion | Critical | result attaches to wrong cloned document | stable document/source IDs + dependency hashes; labels never authority |
| Old module removal breaks plugins/tests | High | downstream imports fail | compatibility re-exports; repo/import audit; deletion only after window |
| CMake omission | Medium | new runtime works source-tree but not installed package | explicit registration tests/install smoke |
| Platform-specific regression | High | Windows/macOS/Linux process behavior diverges | platform CI/manual matrix; preserve flags/path semantics during extraction |
| Remote provider semantics contaminate solver | High | solver logic becomes Kaggle/cloud-specific | provider/solver contracts separate; routing explicit |
| Aero shapes host abstraction prematurely | High | generic runtime encodes CFD-specific assumptions | FEM proves runtime first; generic layer cannot import Aero |
| FEM semantics over-generalized | High | `SOLVER_SPECS`/History behavior weakened | keep solver state/domain adapter FEM-owned |
| Hidden license/purpose enforcement creeps into runtime | High | general VibeCAD execution becomes coupled to third-party terms | explicit non-goal; runtime has zero purpose/license concepts; notices remain informational |
| Data schema migration loss | High | persistent job history lost/corrupt after upgrade | persistence introduced later; schema version; transactional migration; backup/rollback strategy |
| Duplicate solver during shadow migration | Critical | parity mode doubles expensive work and creates competing result owners | observation-only shadow traces; exactly one process/publication authority |
| Duplicate result publication | Critical | retry/reconnect/late callback creates a second result graph | stable publication identity + receipt + idempotency tests |
| Save-As/clone misattachment | Critical | completed result attaches to wrong document that shares label/path | path informational only; exact source/dependency revalidation; awaiting-source state |
| Crash-state fabrication | Critical | leftover files/PID interpreted as successful job | evidence-based reconnect/orphan classification; artifact hash validation |
| Installed package omission | High | source tests pass but packaged VibeCAD lacks new runtime modules | CMake/install-tree smoke on supported packaging paths |
| Downstream macro/plugin break | High | old Python import path removed despite external users | thin compatibility re-exports; deletion only after audit/window |
| State callback regression | High | duplicate/late provider events reopen terminal state | validated monotonic transition table; dedupe/replay tests |
| Non-idempotent output collection | High | reconnect copies/parses output repeatedly with divergent state | immutable output manifest + repeat-safe collect/parse semantics |
| Cancel accepted during check/commit window | Critical | current phase/event checks may permit accepted cancel before phase advances, then commit continues | dedicated race characterization + isolated atomic-gate fix before extraction oracle |
| Descendant process survives direct-parent stop | High | solver/MPI/helper remains after cancel/timeout on some platforms | characterize synthetic child tree per platform; isolated process-ownership fix if confirmed |

## Highest-risk implementation boundaries

### 1. Document-thread boundary

Treat violations as release blockers. A “thread-safe enough” FreeCAD object reference is not an acceptable durable job payload.

### 2. Publication boundary

The runtime may determine that publication is eligible; it does not directly perform arbitrary document mutation. Existing Native transaction authority remains the final mutation mechanism.

### 3. Currentness boundary

Stale is not failed. Successful stale results must remain inspectable and attributable to their original source.

### 4. Compatibility boundary

Refactoring internal ownership does not justify changing existing agent/native API behavior. Public changes are separate product decisions.

### 5. Persistence boundary

Do not introduce SQLite/artifact migration in the same change that extracts process execution. Persistence has crash-recovery/data-loss risk and deserves its own review.

## Deepening risk additions

| ID | Risk | Severity | Required mitigation / gate |
|---|---|---:|---|
| HR-11 | Cancellation accepted in the gap before commit phase transition, followed by CAD mutation | Critical | Atomic commit gate under the same lifecycle lock; linearizability tests. |
| HR-12 | Solver descendants survive cancel/timeout because only direct `Popen` is killed | High | Provider-owned POSIX process group / Windows Job Object or equivalent; synthetic parent+child tests. |
| HR-13 | Durable recovery serializes live FreeCAD/Python objects | Critical | Separate inert durable descriptor from ephemeral runtime handle; schema tests forbid executable objects. |
| HR-14 | Host restart falsely reports/resumes a local process it cannot prove is the same execution | High | `host_interrupted` recovery classification; no automatic CAD publication; explicit resubmit. |
| HR-15 | Remote reconnect publishes old output into changed/reopened document | Critical | Authoritative external job ID + artifact hashes + exact document/domain currentness revalidation. |
| HR-16 | Generic runtime weakens FEM stale-result identity | Critical | FEM fingerprint is opaque domain evidence; parity tests compare hashes/result currentness. |
| HR-17 | Same-name reopened document receives old results | Critical | Document UID identity remains mandatory in initial migration. |
| HR-18 | Cleanup races/double-callbacks delete needed artifacts or overwrite terminal state | High | Idempotent cleanup + terminalization; sealed artifacts before disposable workspace removal. |
| HR-19 | Refactor breaks existing `analyze.solver_execution` / `native.job` clients | High | Compatibility facade; JSON/error/action/transaction parity; internal rollback flag. |
| HR-20 | Persistence scope expands refactor before execution boundary is proven | Medium | Stage durability after FEM adapter A/B parity. |

## R19 — persisted job accidentally becomes standing CAD mutation authority

**Risk:** a durable implementation serializes or reconstructs the original `NativeCallTicket`, `NativeRuntimeContext`, callback closure or equivalent and treats it as future authorization after restart/document drift.

**Impact:** stale or replayed authorization could mutate a document outside the original Native context.

**Mitigation:** persist only inert submission/publication descriptors; rebind exact `Document.Uid`; validate exact job/artifact/domain currentness; obtain fresh Native publication authorization; publication receipt is replay-idempotent. Initial FEM keeps current ticket semantics in-process until this new coordinator is separately proven.

## R20 — global structural revision over-invalidates long CFD

**Risk:** reusing the current FEM ticket/global-revision rule for all future CFD marks expensive results stale after unrelated document edits.

**Impact:** needless recomputation and unusable durable workflows.

**Mitigation:** preserve FEM behavior first, then allow Aero adapter to contribute exact domain dependency fingerprints/currentness to the new publication coordinator. Host structural revision remains provenance and may still be a dependency where appropriate.

## R21 — domain currentness becomes a Native-authority bypass

**Risk:** because Aero says its dependencies are current, a runtime publishes without fresh host mutation authorization.

**Impact:** domain code bypasses VibeCAD transaction/revision/receipt authority.

**Mitigation:** currentness answers only engineering applicability. It never grants mutation authority. Fresh host publication authorization + Native transaction/postcondition/receipt remain mandatory.
