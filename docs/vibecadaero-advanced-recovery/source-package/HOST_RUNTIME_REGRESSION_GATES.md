# Host Analysis Runtime — Regression and Release Gates

## Gate 0 — fresh source freeze

Before implementation:

- freeze current upstream `main` SHA;
- diff against `df07a5e…` and this correction;
- re-read background, runtime-context, FEM solver execution/state/process, bindings/schema/runtime, Native dispatch, CMake, relevant tests;
- update `LIVE_DRIFT_AFTER_PASS_03.md`;
- do not assume the four-commit drift observed during this correction is still current.

## Gate 1 — characterization before extraction

Record legacy behavior for:

- `analyze.solver_execution` / `run` submission plus its background `native.job` status/cancel lifecycle;
- background FEM solve;
- job status/progress/log shape;
- successful result object graph;
- prior-result replacement;
- History `solve` record;
- state hashes/currentness;
- command/environment/workdir behavior;
- solver-specific platform restrictions;
- timeout;
- cancellation;
- commit exception rollback;
- document switch/close/revision change during solve;
- multiple analysis membership errors;
- mesh association errors.

Where upstream lacks a test, add a characterization test before refactoring; do not claim parity from documentation alone.

## Gate 1A — cancellation/commit race characterization/fix

Before using current background semantics as the extraction oracle, stress the existing cancellation window around document-thread commit. If an accepted cancellation can be followed by commit, land a dedicated correctness fix and update golden behavior. Do not combine the fix with generic runtime extraction.

## Gate 1B — owned process-tree characterization/hardening

Use child-spawning synthetic processes to determine whether current cancel/timeout can leave descendants. If it can, fix process ownership/termination in its own PR with Windows/POSIX tests, then update the oracle.

## Gate 2 — pure runtime state-machine tests

Required:

1. cancel before run is accepted and no commit occurs;
2. cancel while provider runs is accepted;
3. cancel in `waiting_to_commit` linearizes before commit and commit gate fails;
4. commit gate linearizes first and subsequent cancel is rejected;
5. terminal transitions are idempotent;
6. duplicate completion cannot overwrite terminal state;
7. cleanup may be invoked repeatedly without semantic change.

The overlay contains a reference proof for cases 3–5.

## Gate 3 — local process provider tests

Use a synthetic parent that spawns a child:

- cancel kills parent and child;
- timeout kills parent and child;
- stdout streaming cannot deadlock;
- output reader terminates;
- nonzero exit is classified correctly;
- cleanup twice is safe;
- paths containing spaces work;
- Windows and POSIX implementations run on their native CI targets.

## Gate 4 — document lifecycle tests

Under FreeCAD:

- exact same document + same revision publishes;
- switched active document does not publish;
- closed document does not publish;
- reopened same-name/new-UID document does not receive old result;
- changed revision/input hash blocks active attachment;
- deleted/reassigned analysis/solver blocks attachment;
- stale outputs remain inspectable historical evidence when policy allows.

## Gate 5 — FEM parity A/B

Run identical prepared cases through legacy and extracted paths. Compare:

- solver input files/hashes;
- command line/environment;
- solver return behavior;
- imported result types/properties;
- result count and analysis membership;
- History entries;
- input/output state hashes;
- transaction receipts;
- public JSON response shape;
- errors for invalid requests;
- cleanup/workdir outcome.

Any difference must be classified: intended bug fix, benign nondeterminism, or regression.

## Gate 6 — persistence/recovery

Fault-inject restart boundaries:

- queued;
- provider submitted but external ID not yet persisted;
- provider running;
- outputs sealed;
- waiting to commit;
- commit receipt written/terminal success not yet written.

Local non-reattachable execution must fail conservatively. Remote reconnect is permitted only with authoritative provider identity and artifact verification. No recovery path may auto-publish without currentness revalidation.

## Gate 6A — durable publication authority

Before production Aero uses durable jobs:

- prove `Document.Uid` behavior across Save, Save As, Save Copy/clone and close/reopen;
- persisted records contain inert publication/submission descriptors, not live `NativeRuntimeContext`, reusable mutation ticket, callbacks or FreeCAD objects;
- missing source becomes `AWAITING_SOURCE`;
- exact current source without a valid publication context becomes `AWAITING_PUBLICATION`;
- relevant domain drift becomes stale/quarantined;
- unrelated dependency changes can be distinguished by a synthetic domain fixture without bypassing host mutation authority;
- duplicate completion/reconnect/retry resolves one publication identity/receipt;
- fresh Native publication authorization is required before document mutation;
- failed publication rolls back CAD but preserves solver artifacts;
- accepted cancellation cannot later publish.

This gate is additive. It does not weaken current FEM global-revision/ticket publication semantics.

## Gate 7 — rollback exercise

Before deleting legacy code:

- route FEM through new runtime;
- intentionally trigger representative success/cancel/failure/stale cases;
- flip internal compatibility switch back to legacy;
- prove no durable schema or CAD document state prevents fallback;
- verify old client/API paths remain intact.

## Gate 8 — Aero adoption

Only after FEM parity/burn-in:

- make VibeCADAero a second domain adapter;
- preserve VibeCADAero authority/stamps/results/qualification contracts;
- use shared host provider/runtime for OpenFOAM/FluidX3D/Kaggle work;
- do not move Aero physics into the runtime.
