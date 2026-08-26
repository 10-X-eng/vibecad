# FEM Characterization and Parity Test Plan

## Purpose

The existing detached FEM implementation is the behavioral oracle for the first migration stages. The host runtime is not accepted because its architecture looks cleaner; it is accepted only if it reproduces current FEM behavior where behavior is intended to remain unchanged.

## 1. Test layers

### Layer A — Pure process runner characterization

Test the current process helper with controlled child programs/scripts:

- successful zero exit;
- non-zero exit;
- stdout/stderr tail truncation;
- Unicode/invalid-byte replacement behavior;
- cancel before launch;
- cancel while running;
- timeout;
- direct-process cleanup exactly as current source; separately characterize child/grandchild survival and make full process-tree cleanup a target only if the characterization proves it is needed;
- working directory;
- environment propagation;
- sequential multi-stage commands;
- progress callbacks/order;
- exception/failure code mapping.

Run on Windows and Linux CI where possible. Preserve Windows `CREATE_NEW_PROCESS_GROUP` / startup behavior and optional `psutil` child-tree cleanup equivalently.

### Layer B — Input sealing/digest characterization

Construct deterministic directory fixtures to verify:

- recursive sorted traversal;
- relative path contribution to digest;
- byte streaming;
- empty input rejection;
- symlink rejection;
- 4096-file bound behavior;
- 4-GiB bound logic via mocked metadata rather than huge fixture;
- path/case behavior per supported platform;
- digest stability before/after extraction.

A compatibility test should run both old facade and new generic implementation against the same fixture and compare exact digests.

### Layer C — FEM preparation parity

For each solver family, monkeypatch or fixture the FreeCAD solver/tool APIs sufficiently to snapshot:

- selected implementation;
- generated input filenames/tree;
- command program + argv;
- environment modifications;
- timeout;
- importer state;
- input SHA-256/file count;
- captured History operations;
- KeepResultsOnReRun state.

Golden snapshots are tied to the exact pre-extraction baseline. Review golden changes rather than auto-updating them.

### Layer D — Source-stale publication parity

The current FEM path also preserves the **original Native call ticket** created at submission. Characterize and assert that publication still passes through `NativeMutationRunner` with that ticket's exact document UID and submission-time expected structural revision. This is a compatibility oracle for extraction; do not replace it with the future durable publication coordinator in the same migration.


Explicit cases:

1. solver state unchanged -> publish allowed;
2. solver state property changed -> stale refused;
3. History order/content changed -> stale refused;
4. KeepResultsOnReRun changed -> stale refused;
5. source solver removed/suppressed as applicable -> correct refusal;
6. unrelated state that current implementation ignores -> behavior remains current behavior during extraction.

This layer guards against accidentally replacing exact current FEM rules with a new generic document-revision rule.

### Layer E — Result graph and transaction parity

For each supported importer path, verify:

- root result identity behavior;
- resource object identities;
- `root_is_new` handling;
- retained result graph semantics;
- History placement/finalization;
- recompute targets;
- NativeMutationDraft `created`/`changed` values;
- mutation receipt contents;
- rollback if import/publication fails midway.

No migration is accepted if the solver finishes but document result topology changes unexpectedly.

### Layer F — Native background/public API parity

Exercise through the same surface used by clients:

- `analyze.solver_execution` operation `run` job-creation response, including its `job` and `next` envelope;
- status with/without explicit job ID;
- cancel;
- clear;
- busy refusal when another job is active for document;
- bounded/pruned completed job behavior;
- completion/failure snapshots;
- claim ceiling/handoff fields;
- public failure codes.

The generic runtime can have richer internal state, but compatibility output must remain equivalent during migration.

---

## 2. Solver matrix

| Solver path | Prepare parity | Process parity | Import/result parity | Stale parity | Platform notes |
|---|---|---|---|---|---|
| CalculiX detached `CalculiXTools` | required | required | required | required | primary first migration |
| CalculiX `ccx_tools` fallback | required | required | required | required | separate path; do not assume same importer |
| Elmer | required | multi-stage required | required | required | MPI/single-task cases where testable |
| Z88 | required | test/check + solve stages | required | required | solver-type argv exactness |
| Mystran | required when dependencies available; otherwise capability fixture | required | required/fixture | required | pyNastran/result module availability paths |

## 3. Failure injection matrix

Before Aero adoption, inject at least:

- prepare exception;
- unsafe symlink/input;
- executable missing;
- process spawn failure;
- non-zero exit;
- timeout;
- cancel pre-launch;
- cancel during child process;
- output missing;
- output corrupt;
- parser failure;
- document/source change while running;
- publication validation failure;
- document close before publication;
- application shutdown/restart once persistence is introduced;
- remote provider partial download/hash mismatch once remote providers exist.

Every failure must end in a known job/publication state and must not leave a partial current result graph.

## 4. Thread-safety tests

Instrument/mocks should assert:

- preparation/live-object access occurs only on document thread;
- local provider/process worker receives no live FreeCAD object;
- result publication occurs on document thread;
- cancellation callbacks do not mutate FreeCAD;
- callbacks after document close cannot publish into a replacement document with same label.

## 5. Dependency-direction tests

Static/import tests should ensure:

- `VibeCADAnalysis*` modules do not import VibeCADAero;
- generic host modules do not import solver-specific FEM classes;
- LocalProcessProvider does not import FEM/Aero domain modules;
- Aero adapters import host contracts, not Native FEM execution modules;
- FEM adapter owns `SOLVER_SPECS`/solver state.

## 6. Performance/non-regression checks

Track at least:

- input hashing time and peak memory on representative large trees;
- UI/document-thread blocking during execute phase;
- log memory growth;
- process cleanup latency on cancel/timeout;
- job DB growth once persistence exists;
- artifact manifest creation overhead.

Extraction should not introduce whole-file reads for multi-GB solver inputs.

## 7. Merge gate per migration PR

Every migration PR must publish a parity table:

```text
Existing tests              PASS
Characterization tests      PASS
Public native.job schema    UNCHANGED / justified additive change
Input digest parity         PASS
Command/env parity          PASS
Cancel/timeout parity       PASS
Result graph parity         PASS
Receipt parity              PASS
Thread-boundary tests       PASS
New durable migration       NONE (unless this is the dedicated persistence PR)
Rollback route              DOCUMENTED
```

A red row blocks merge unless the PR explicitly intends that behavior change and it has been separated from the extraction work.

## Deepening — required new characterization cases

Do not assume these tests already exist upstream. Add them where absent before refactoring:

- cancellation accepted in `waiting_to_commit` prevents commit;
- cancellation after commit gate is rejected;
- cancellation/timeout terminates a synthetic child process tree;
- close original document before commit: no CAD mutation;
- switch active document before commit: preserve existing strict behavior;
- reopen same-name/new-UID document: no attachment;
- revise document/domain input state while solve runs: stale publication blocked;
- duplicate terminal callback cannot overwrite success/cancel/failure;
- cleanup is idempotent;
- background response retains existing `job`/`next` envelope;
- public error/action/transaction identities remain compatible;
- local restart/recovery is conservative and does not auto-import output;
- reconnectable remote job still revalidates exact CAD/domain currentness before publish.

The reference overlay proves the lifecycle race invariant only. Real FreeCAD/process/provider parity must be executed in the live source tree after authorization.


## 8. Cutover and recovery tests

Add explicit tests for:

- shadow observation produces no second subprocess;
- shadow observation produces no NativeMutationDraft/commit;
- compatibility facade delegates to exactly one authoritative runner;
- duplicate completion callback does not publish twice;
- repeated status/collect is idempotent;
- cancel racing process exit has deterministic legacy-equivalent outcome during extraction;
- document close before commit cannot publish into replacement document with same label;
- Save-As path change alone does not force a stale decision if the current domain dependencies are unchanged;
- cloned/copied document does not inherit automatic publication authority;
- installed-tree imports include every new `VibeCADAnalysis*` module;
- old execution/background import paths remain importable facades;
- persistence fixtures recover/reject interrupted state once persistence PR begins;
- leftover local files/PIDs after restart never fabricate a successful job.

## 9. Golden-trace parity

Normalize unstable values (temp paths, timestamps, random IDs) and compare:

- prepared request facts;
- input digest/count;
- commands/environment subset;
- lifecycle/progress ordering;
- execution outcome/error code;
- currentness decision;
- result graph/History/receipt summary;
- cleanup outcome.

A golden trace is evidence of behavioral preservation, not a replacement for domain-specific result correctness tests.

## Durable-publication separation test

Add an explicit regression demonstrating that **initial FEM extraction** still refuses publication when the submission-time ticket revision is stale, even if the solver itself completed and FEM solver artifacts are otherwise valid. Separately test the future publication coordinator using a synthetic domain adapter; do not use that synthetic path to redefine FEM currentness during extraction.
