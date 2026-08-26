# Non-Destructive Migration Matrix

**Purpose:** map each existing Pass-03 VibeCAD mechanism to the target Analysis Runtime without deleting or silently changing behavior.

| Existing mechanism at `df07a5e` | Existing responsibility | Target disposition | Must preserve during extraction | May evolve later |
|---|---|---|---|---|
| Original `NativeCallTicket` publication semantics | current FEM fail-closed publication against submission revision | **PRESERVE FIRST / EVOLVE LATER** | unchanged during extraction; durable publication coordinator added separately | FEM parity tests, then separate publication-authority tests |
| `Document.Uid` / `document_uid` | existing exact host document identity | **PRESERVE + CHARACTERIZE** | no second ID; characterize Save/SaveAs/copy/reopen/recovery | identity matrix before auto-reattach |
| Durable publication authority | not currently a persisted reusable permission | **ADD AS FRESH AUTHORIZATION** | inert `PublicationDescriptor`; exact source/currentness + fresh Native mutation authority | no serialized live ticket/context; idempotent receipt |
| `VibeCADNativeBackground.py` | in-memory jobs, prepare/commit worker, progress/cancel, one job/doc, bounded records | **MERGED into host runtime behind facade** | public behavior, one-job/doc, cancel/status semantics, current failure mapping | durable persistence, richer states, broader domain clients |
| `VibeCADNativeBackgroundRuntime.py` | document-scoped `native.job` status/cancel wrapper; job creation is performed by the domain capability (`analyze.solver_execution` operation `run` for FEM) | **PRESERVE as public compatibility surface** | action names/schema/response, current capability routing | later expose generic analysis actions only through separately reviewed API evolution |
| `VibeCADNativeBackgroundSchema.py` | Native tool schema | **PRESERVE** | existing JSON schema and enum behavior | additive versioned fields/actions only after compatibility review |
| `VibeCADNativeAnalyzeSolverExecutionProcess.py` | subprocess mechanics, timeout/cancel, output tails; current shared stop helper directly controls the launched `Popen` | **EXTRACT generic mechanics; retain facade** | argv/env/cwd, error behavior, tails, cancel/timeout, direct-process behavior | characterize descendants first; harden process-tree ownership separately if required; provider capabilities/richer logs |
| `VibeCADNativeAnalyzeSolverExecution.py` | FEM preparation, input digest, detached execution orchestration, result import/publish | **SPLIT** | exact inputs, commands, digest, result graph, stale checks | generic artifact/runtime calls underneath; domain adapter remains FEM-specific |
| `VibeCADNativeAnalyzeSolverState.py` | FEM solver canonical state and exactness | **KEEP FEM-SPECIFIC** | all solver/property semantics | future FEM-only improvements, separate from generic extraction |
| `VibeCADNativeMutation.py` | transaction/recompute/validation/receipt authority | **KEEP HOST AUTHORITY** | transaction atomicity and receipts | generic publication helper may call it; never replaced by scheduler |
| `VibeCADNativePreviewControl.py` | mutation preview apply/reject/stale/intent controls | **KEEP SEPARATE** | preview behavior | analysis result publication may use Native mutation, but long jobs are not previews |
| `/v1/native` + held session | Native control | **PRESERVE** | route behavior/session semantics | additive discovery of job capability only if upstream chooses |
| `/v1/aero` | Aero authority | **PRESERVE** | current low-order/report/repair operations | additive high-fidelity submit/status/result operations using host job IDs |
| `AeroJobStore.py` reference overlay | solver-neutral Aero lifecycle model | **TRANSITIONAL / SUPERSEDED AS AUTHORITY** | lifecycle ideas retained as host-runtime requirements | compatibility reader/facade, then retire after no consumers |
| `AeroDetachedExecution.py` reference overlay | frozen input/stale-attach reference semantics | **MERGED into host design** | domain-independent invariants retained | Aero-specific wrapper becomes thin or unnecessary |
| CMake enumerations | install/build registration | **UPDATE ADDITIVELY** | all existing entries | add new host modules/tests; remove old entries only when facade deletion is explicitly safe |

## Migration invariants

The following are hard implementation invariants, not optional suggestions:

1. **No big-bang replacement.** New host internals enter behind existing surfaces.
2. **No solver behavior change while extracting mechanics.** A solver improvement belongs in another PR.
3. **No concurrency expansion while extracting.** Preserve one active job/document first.
4. **No persistence migration while extracting the subprocess layer.** Different risk classes stay separable.
5. **No FreeCAD objects in durable worker state.** Store stable IDs/primitive snapshots only.
6. **No worker-thread document mutation.** Publish on the document thread through existing mutation authority.
7. **No genericization of FEM solver-state semantics.** Generic runtime takes snapshots; FEM decides what they mean.
8. **No `returncode == 0` qualification shortcut.** Solve completion and engineering qualification remain separate.
9. **No deleting stale successful results.** Quarantine historical evidence with provenance.
10. **No license/purpose logic in the runtime.** Third-party notices remain informational/documented elsewhere.
11. **No public error/API churn merely for architecture cleanliness.** Compatibility facade maps internals to existing contracts.
12. **No old-module deletion until an import audit and compatibility window prove it safe.**

## Rollback model

| Phase | Rollback cost | Required strategy |
|---|---:|---|
| characterization tests | trivial | revert tests if incorrect; no runtime touched |
| contracts/facades | trivial | old implementation remains active |
| process extraction | low | facade can point back to original implementation; no data format change |
| artifact extraction | low | retain compatibility digest path; no persisted schema yet |
| runtime orchestration | medium | preserve old NativeBackground facade/semantics; internal reversible routing seam only; no user-facing setting; code-level rollback stays possible |
| FEM migration | medium | migrate one solver at a time; revert adapter for that solver |
| persistence | higher | version schema; explicit migration/downlevel policy; backup DB before migration |
| Aero adoption | medium | low-order Aero unaffected; high-fidelity adapters can be disabled/reverted independently without removing existing Aero |
| remote provider | low-medium | local provider remains reference fallback where solver supports it |

## Existing behavior that is intentionally *not* “fixed” during extraction

Some current constraints are imperfect but should remain until the runtime is proven:

- one active background job per document;
- bounded in-memory records;
- current progress percentages/messages;
- existing timeout range;
- current Native error codes;
- current FEM input directory shape;
- existing History exactness policy;
- KeepResultsOnReRun stale behavior.

Each can be improved later with its own test-backed change. Mixing them into extraction would make regression attribution unnecessarily hard.

## Deepening additions

| Existing behavior/authority | Initial migration treatment | Final target |
|---|---|---|
| FEM input-state hash | Preserve exact implementation/semantics behind FEM adapter | Domain-owned opaque currentness evidence |
| `analyze.solver_execution` | No rename/schema rewrite | Stable FEM capability using host runtime underneath |
| `native.job` | Preserve lifecycle facade | Compatibility facade; additive host APIs only if explicitly versioned |
| cancellation before commit | Preserve user intent, fix race | Linearizable atomic cancel/commit gate |
| direct-parent process stop | Characterize, then harden | Provider owns full launched process tree |
| in-memory job records | Preserve during parity stage | Versioned durable descriptor + ephemeral runtime handle |
| live FreeCAD objects in prepared request | Allowed only in current ephemeral path | Never persisted; main-thread rebind before publish |
| exact document UID/revision guard | Preserve | Host orchestrates, domain/Native validates before publish |
| legacy FEM execution path | Retain behind internal routing switch | Remove only after parity/burn-in/rollback gates |
| cancellation/commit check window | characterize current race independently | dedicated host correctness fix if reproduced; then re-baseline oracle |
| direct-parent `stop_process()` | characterize child-spawning behavior per platform | dedicated process-tree hardening if needed; then provider extraction |
| duplicate provider/completion callbacks | no behavior expansion during FEM extraction | monotonic/idempotent host lifecycle before durable remote providers |
| Save-As/clone/reopen identity | preserve strict current behavior first | exact dependency-based reattachment; never label/path authority |

