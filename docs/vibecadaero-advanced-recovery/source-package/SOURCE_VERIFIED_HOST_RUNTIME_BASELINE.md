# Source-Verified Host Runtime Baseline — `df07a5e`

This file records the exact VibeCAD behaviors the migration plan is designed to preserve or deliberately evolve. It is a guard against designing from remembered architecture instead of the frozen source.

## 1. `VibeCADNativeBackground.py`

Verified facts:

- host-owned `NativeBackgroundManager`;
- bounded to `MAX_BACKGROUND_JOBS = 32` in-memory records;
- bounded terminal JSON result: 32 KiB;
- progress message bound: 160 characters;
- one active background job per exact `document_uid`;
- terminal phases are `completed`, `cancelled`, `failed`;
- jobs run on daemon Python threads;
- callback shape is `prepare -> validate_before_commit -> commit`, with `dispatch_to_document_thread` and optional cleanup;
- cancellation is a `threading.Event`;
- `cancel()` rejects terminal and `committing/finalizing` jobs, otherwise sets the event;
- completed terminal records are trimmed only to remain within the 32-record bound.

These limits/policies are compatibility facts for initial extraction, not eternal host-runtime requirements.

## 2. `VibeCADNativeBackgroundSchema.py` / Runtime

Verified public capability:

- capability name: `native.job`;
- operations: **`status` and `cancel` only**;
- exact target is a 32-character lowercase-hex job ID;
- the runtime verifies that the job belongs to the exact current document context.

There is no `native.job start` or `clear` operation in the frozen source.

## 3. FEM job creation surface

Verified public capability:

- capability name: `analyze.solver_execution`;
- operation: `run`;
- classification: mutation/background-required;
- request contains target + timeout;
- successful submission returns a `job` summary and a `next` instruction pointing to `native.job status`.

This is the compatibility surface to preserve. Do not rename it merely because execution mechanics become generic underneath.

## 4. Important threading/preparation fact

`NativeAnalyzeSolverExecutionRuntime.execute()` calls `prepare_solver_execution_request(...)` **before** calling `background_manager.submit(...)`.

Therefore current FEM behavior is effectively:

```text
document/context guard
  -> FEM solver target + input writer preparation
  -> frozen detached request/workdir/input hash
  -> manager.submit
       -> daemon worker runs external solver against frozen request
       -> document-thread validation/publication
```

The background manager's generic `prepare` callback is used here to run the already-prepared detached solver request; it is not where the FEM input writers are currently invoked.

**Migration consequence:** do not silently move current FEM input writing/object traversal off-thread during runtime extraction. Preserve the existing thread/context boundary first. Any later split of domain capture vs detached materialization needs its own FreeCAD-safety tests and explicit behavior change.

## 5. Detached FEM request

Verified request includes:

- prepared FEM target;
- solver implementation identity;
- exact History operations tuple;
- detached working directory;
- exact command sequence;
- environment mapping;
- timeout;
- SHA-256 of the input tree;
- input file count;
- `KeepResultsOnReRun` snapshot;
- importer state.

Input tree constraints:

- symbolic links rejected;
- max 4096 files;
- max 4 GiB total input bytes;
- path names participate in the hash;
- file contents are streamed into SHA-256.

## 6. Solver process runner

Verified behavior:

- argv list, `shell=False`;
- detached working directory as `cwd`;
- merged stdout/stderr to stage log;
- 16 MiB log-output bound;
- timeout measured from the start of the whole command sequence;
- polling at 0.1 s;
- cancel/timeout invokes shared `stop_process()`;
- non-zero stage exit maps to FEM-specific error and includes a bounded log tail;
- multiple solver stages are supported.

The shared `stop_process()` currently terminates/kills the direct `Popen`; descendant process-tree behavior is not guaranteed by this source and must be characterized separately.

## 7. FEM currentness before publication

Before importing results, frozen source verifies at least:

- exact solver state still matches `expected_state_sha256`;
- exact History operations tuple still matches;
- `KeepResultsOnReRun` still matches.

These semantics are FEM-owned and must remain the oracle during extraction. They must not be replaced by a generic document revision check.

## 8. Publication/postcondition

Result import builds a `NativeMutationDraft`; existing Native mutation machinery owns transactional publication/recompute/verification.

Postcondition verifies, among other things:

- live result root;
- solver-result link;
- result source association;
- result History role;
- resource ownership;
- canonical History block ordering;
- Native object validity.

A completed solve is stamped `claim_ceiling = model_unqualified`, `solved = true`, `qualified = false`.

## 9. Runtime-context guard

`NativeRuntimeContext` captures exact `document_uid` from the document and `guard()` requires:

- current active document is the same document object;
- current `document_uid` still equals captured UID;
- incompatible active task/edit state is not present, subject to explicitly-owned exceptions.

The generic runtime must not weaken this guard during FEM migration. Durable cross-restart reattachment is a new mechanism layered later and requires fresh domain/currentness validation.

## 10. Submission-time Native ticket semantics

Source verification also establishes the authorization behavior that must not be accidentally weakened:

- `NativeRuntimeContext` obtains `document_uid` through `VibeCADNativeTargets.document_uid(document)`, which reads FreeCAD `Document.Uid`;
- `NativeCallTicket` contains `document_uid`, `capability_name`, `expected_revision` and an idempotency token;
- `begin_call()` freezes the current host structural revision into that ticket;
- `NativeMutationRunner` later requires the exact document UID, live document, reauthorization and `state.authorize_mutation(ticket)`;
- `authorize_mutation(ticket)` rejects when current structural revision differs from the ticket's original `expected_revision` (unless resolving the exact prior verified idempotent result);
- the FEM background commit callback closes over the original ticket/context and calls `run_immediate_mutation`.

**Migration consequence:** current FEM publication is globally strict against the submission-time Native revision and live context. Preserve that behavior through initial extraction. For future durable Aero/CFD, do not serialize/replay this live ticket/context as standing mutation authority. Add a separate durable publication coordinator using inert submission provenance, exact source rebind/domain currentness and fresh Native publication authorization.

`Document.Uid` is already VibeCAD's host identity seam. Characterize it across Save, Save As, Save Copy/clone, close/reopen and recovery before durable automatic reattachment; do not invent a second document ID during extraction.

## 11. Known source-level hazards requiring isolated treatment

### Cancellation/commit window

The worker callback checks cancellation, validates, checks cancellation again, then changes phase to `committing/finalizing`. `cancel()` can still accept while phase is `waiting_to_commit`. This creates a source-level race window that must be stress-tested and, if reproduced, fixed in a dedicated host-correctness slice before genericization uses the lifecycle as its oracle.

### Direct-parent process termination

The shared stop helper controls only the direct `Popen`. Child process leakage is a risk, not a proven cross-platform outcome. Characterize representative child-spawning solver/process cases and harden ownership separately if required.

## 12. Migration law derived from the source

The safe extraction is therefore not “move FEM into a scheduler.” It is:

1. preserve FEM public capability and exact FEM semantics;
2. preserve current main-thread/domain preparation boundary;
3. isolate/fix confirmed host lifecycle/process-control hazards separately;
4. extract only physics-neutral lifecycle/process/artifact/provider mechanics behind facades;
5. prove FEM parity one path at a time;
6. add persistence/restart after parity;
7. add durable publication authority that persists provenance rather than live Native mutation permission;
8. make Aero the second engineering client.
