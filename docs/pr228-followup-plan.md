# PR 228 follow-up: restored display and cross-workspace repair

Do not merge or describe these follow-ups as fixed until their acceptance checks
pass. Preserve customer documents and the running review instance. Diagnose and
test with private copies/profiles; do not use console interventions as a fix.

## 1. Restored sheet display and camera — first priority

Observed: saved geometry is present, up to date and visible, but the viewport is
blank. One inspection found valid display meshes with an enormous camera span;
a subsequent reopen had no attached display meshes. A private copied-file run
reproduces the visible final feature waiting for asynchronous meshing with no
published nodes. The owner later saw geometry after substantial zooming.
The camera symptom is not yet proven to be the root cause.

- [x] Measure restore completion, queue wait, mesh preparation, GUI publication,
      first visible frame, camera and scene bounds separately.
- [x] Trace hidden-history mesh requests, cancellation, restore notifications and
      Fit All before meshes exist. Identify which actually causes the delay and
      camera error before choosing a production change.
- [x] Add failing native regressions, then repair the normal restore/display path.
      Retain worker computation and GUI-owned scene publication. Reuse existing
      scheduling/status mechanisms; do not add a second rendering fallback.
- [x] Verify cold launch/open and repeated close/reopen on the saved-file copy,
      plus representative generated sheets. Check folded/flat, visibility, camera
      preservation and Fit All during/after loading. No forced recompute or
      visibility toggles to make the test pass.
- [x] Report measured latency and keep a visible loading status while necessary
      work remains. Geometry, history and saved document inputs must be unchanged.

Completed display evidence: 129 affected native GUI tests passed in 291.813 s.
Identically instrumented copied-file runs fell from 88.875 s to 7.157 s until
display-ready, including the diagnostic startup delay. The visible final sheet
previously waited about 39 s behind hidden history. Repeated surface extraction
and planar-normal calculation dominated its remaining mesh cost; mesh preparation
is now about 2.8 s. Separate unprofiled opens/reopens completed in about 5-6 s.
Only the final visible state was meshed. Saved camera and sheet inventory stayed
unchanged, including a Fit All request during loading. The earlier enormous
camera span was not reproduced, so no speculative camera-reset code was added.
Hidden App::Link sources remain renderable; direct forced requests remain valid.
The reusable `src/Tools/performance/sheetmetal_restore_probe.py` retains the
copied-file diagnostic. See the Windows acceptance audit for exact commands.

## 2. Shared context across every workspace

Observed: provider threads are keyed by conversation and tool schema. Resuming a
thread removes conversation replay even when another workspace received newer
instructions. Immediate tool receipts transfer, but are not a shared task history.

- [ ] Reproduce lost cross-workspace instructions with provider-request tests.
- [ ] Reuse the canonical ordered conversation/operation records. Track delivery
      per actual provider thread; transfer unseen events, not the whole transcript.
      Record delivery only after success. Verify failure, retry, cancellation,
      new thread and resumed thread behavior.
- [ ] Preserve original requirements and unfinished work across automatic
      continuations and steering. Provide a compact handoff of pending work,
      known targets, failures and next action; distinguish observations from intent.
- [ ] Keep large tool outputs/snapshots out of the handoff; retain exact references
      and expose omitted history/details through bounded, paginated reads. Reuse
      existing retrieval APIs where possible; no duplicate history store.
- [ ] Cover Sheet Metal, Sketching, Parameters and another workbench. Confirm
      unchanged delivered history is not retransmitted and missed events are not
      silently discarded. Keep provider-independent session behavior consistent.

## 3. Discoverable edit/DFM repair workflow

- [ ] Add concise generic guidance: inspect the owning feature and source links;
      edit bend parameters in Sheet Metal; open the existing sketch to change
      sketch geometry/constraints; finish, return, verify and rerun DFM before
      requesting a quote for the corrected revision.
- [ ] Correct the stale sketch-finish tool reference. Distinguish switching to
      Sketching from entering an individual sketch's edit mode.
- [ ] Supply authoritative source/profile references with inspection/repair
      results where known. Never guess a DFM-to-feature mapping or prescribe
      design-specific dimensions.
- [ ] Exercise DFM failure -> source discovery -> sketch edit -> finish -> Sheet
      Metal -> fresh DFM -> quote request. Verify a bend-only repair too. Preserve
      existing object identity and original requirements. No real order placement.

## Completion boundary

Batch related implementation changes after red regressions. Run focused native
and provider checks, then the integrated affected suites. Record exact commands,
results, remaining limits and before/after timings. Commit coherent changes,
update the PR, and launch a tested local build with the user's normal profile
when requested. Do not overwrite modules used by the active review instance.

The previously completed 375-GUI/441-unit validation remains historical evidence;
it does not validate these new defects or future changes.
