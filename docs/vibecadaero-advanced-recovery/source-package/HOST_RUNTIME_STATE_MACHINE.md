# Host Analysis Runtime — State Machine and Atomicity Contract

## 1. Compatibility rule: preserve the public lifecycle that exists

At the frozen baseline, `NativeBackgroundManager` exposes one `phase` through `native.job status`. The observed host phases are created/used as:

```text
queued
preparing
waiting_to_commit
finalizing OR committing
completed
cancelled
failed
```

The initial extraction must not invent a breaking public replacement such as a new `native.job start`, `clear`, or renamed FEM job operation. Job creation remains `analyze.solver_execution` / `run`; job control remains `native.job` / `status` and `cancel`.

A richer durable runtime may maintain provider/execution/publication state internally, but its compatibility facade must map back to current public behavior until the schema is deliberately versioned.

## 2. Source-level cancellation hazard to resolve separately

Current `NativeBackground.cancel()` refuses terminal, `committing`, and `finalizing` phases and otherwise sets a cancellation event. The worker checks that event before/after `validate_before_commit()`, then transitions phase to `committing`/`finalizing`. There is therefore a narrow check-to-phase-transition window that must be stress-characterized.

Do not hide a semantic fix inside the extraction. First create a deterministic test proving current behavior; then implement/review an atomic lifecycle gate as its own correctness slice; then re-baseline parity before continuing genericization.

## 3. Target internal execution axis

After compatibility extraction, durable internal execution may use a more expressive axis such as:

```text
PREPARED
QUEUED
UPLOADING / SUBMITTED       # remote providers where applicable
RUNNING
CANCELLING
COLLECTING
PARSING
SOLVED
FAILED
CANCELLED
TIMED_OUT
ORPHANED
```

These are host execution facts. `SOLVED` means the declared computation completed and outputs passed required collection/integrity parsing. It does not mean published or qualified.

## 4. Target publication/currentness axis

Publication is deliberately separate:

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

Important laws:

- `SOLVED` does not imply `PUBLISHED`.
- `PUBLISHED` does not imply `QUALIFIED`.
- `STALE` does not imply solver failure.
- `AWAITING_SOURCE` is a valid durable state for successful compute.
- restart never recreates publication authority from persisted job state.

## 5. Atomic cancellation-versus-publication gate

The eventual manager SHALL provide one operation equivalent to:

```python
try_begin_publication(job_id) -> ACQUIRED | CANCELLED | NOT_READY
```

Under one lifecycle lock/transaction it must:

1. load exact job/attempt;
2. reject terminal/ineligible work;
3. observe cancellation state;
4. if cancellation won, terminalize execution cancellation and return `CANCELLED`;
5. otherwise assign one publication owner and transition into a non-cancellable publication critical section;
6. release the lifecycle lock.

For concurrent `cancel(job)` and `try_begin_publication(job)` exactly one wins. Forbidden outcome: cancellation returns accepted and the job later mutates CAD.

## 6. Lifecycle gate is not CAD authority

Acquiring publication ownership does **not** authorize arbitrary document mutation. The publication coordinator must then:

1. validate exact job/output provenance;
2. rebind exact source document;
3. obtain domain `CurrentnessReport`;
4. detect prior publication receipt/replay;
5. obtain fresh Native publication authorization when using the durable model;
6. run domain publication through `NativeMutationRunner`/host transaction on the document thread;
7. verify postconditions;
8. record one publication receipt;
9. mark `PUBLISHED` only after Native commit succeeds.

Initial FEM migration continues using its current original-ticket/global-revision callback semantics; this durable publication gate is introduced separately.

## 7. Terminal and replay laws

State transitions are monotonic. Duplicate provider callbacks, reconnect, repeated `collect`, UI retry or recovery cannot reopen terminal execution or create another publication. Cleanup must be effect-idempotent.

## 8. Concurrency policy

Preserve current one-active-background-operation-per-document during extraction. It is a compatibility policy, not a permanent architectural limit. Later concurrency requires explicit dependency/resource/publication ordering rules and is a separate product behavior change.

## 9. Reference proof

`proposed_overlay/reference_host_runtime/VibeCADAnalysisJobState.py` models the atomic cancellation/publication gate only. It is FreeCAD-independent reference semantics, not an upstream integration claim.
