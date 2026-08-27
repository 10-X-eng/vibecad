# VibeCAD Analysis FEM compatibility and known-difference report

This report freezes the normalized compatibility target for the four FEM
solver paths supported at the Analysis host migration boundary. The executable
oracle is `src/Mod/VibeCAD/vibecad_tests/fixtures/analysis_fem_parity_v2.json`;
`test_analysis_fem_parity_oracle.py` executes both the legacy and host paths
against it.

The oracle binds each solver's exact direct-argument command sequence,
environment identity, timeout, normalized progress lifecycle, stage summary,
input hash/count, solver-state dependency, History identity, document UID,
adapter identity, and compatibility provenance. It also executes success,
start failure, timeout, output-limit failure, backend failure, cancellation,
failure cleanup, and exact commit/verification/discard delegation for every
solver. Solver-specific tests remain additional backend-focused evidence.

The v2 comparison found and closed one previously unrecorded incompatibility:
the host adapter preserved the backend-failure code and message but omitted the
legacy bounded diagnostic `repair` payload. Both execution paths now use one
FEM-owned process-failure translator, so code, message, bounded diagnostics,
and repair shape cannot drift independently.

## Covered solver paths

| Solver | Host provider | Stages | Compatibility disposition |
|---|---|---:|---|
| CalculiX | `local-process` | 1 | Frozen |
| Elmer | `local-process` | 3 | Frozen |
| Z88 | `local-process` | 2 | Frozen |
| Mystran | `local-process` | 1 | Frozen |

## Executable evidence matrix

| Compatibility dimension | Evidence | Current disposition |
|---|---|---|
| Input digest/count, commands, cwd, environment, timeout | Legacy and host success paths execute independently for all four solvers and compare to the v2 fixture | Frozen |
| Progress and stage summaries | Legacy and host traces compare directly and to the fixture | Frozen; the reviewed seven-percent host event is retained in both adapter-facing traces |
| Public process failures | Four failure classes are compared for every solver, including bounded diagnostic repair data | Frozen |
| Cancellation and failure cleanup | Both paths raise the preserved cancellation type and remove their private workspaces | Frozen |
| Repeated lifecycle and resource ownership | The cross-platform FEM workflow repeats real descendant-tree cancellation and timeout three times each, repeats four 4-MB combined-output runs under the 16-KiB-per-stream capture bounds, drives 18 success/failure/cancellation runtime cycles per terminal path, and creates/removes 18 private FEM workspaces. A deterministic ownership-race test prevents an older terminal cleanup from releasing a newer job's document lock. | Covered for the in-memory runtime, shared local-process primitive, and synthetic workspaces; installed-host document lifecycle and physical solver leak evidence remain open |
| Result publication seam | The host path passes the exact legacy prepared object to commit and returns the exact legacy verification result for every solver | Frozen at the compatibility seam |
| Result graph, History, hashes, and receipts | An installed Windows VibeCAD 26.3 `FreeCADCmd` A/B gate now covers all four solvers with deterministic synthetic fields. It compares object graph, solver membership, canonical History order, timeline ownership, input/state hash presence, public JSON, and save/reopen links. Both paths expose no durable publication receipt. | Publication parity frozen for the covered installed Windows host; durable receipt integration and installed POSIX evidence remain open |
| Document close/switch/reopen behavior | The installed gate proves exact save/close/reopen persistence for each legacy/host solver pair. The routed runtime rejects publication after the exact active document is switched or replaced on both routes. Repeated runtime/process/workspace cleanup is covered separately; same-name installed replacement, close-while-running, and exact-source rebind remain unclaimed. | Gate G4 and Step 7 remain open |
| Rollback to the legacy execution route | The internal `analysis_runtime_fem` default and temporary `legacy_fem_execution` route are executable and context-local. Submission captures one route; nested use restores the prior route; another thread retains the default. The routed tests cover success, cancellation, failure, cleanup, and stale-document refusal. The v2 oracle supplies independent four-solver host/legacy execution evidence, the installed Windows gate supplies equivalent CAD/save-reopen state, and compatibility-facade tests preserve the old API path. | Gate G7 is covered; this is an internal recovery mechanism, not a public preference or product option |

## Accepted intentional differences

1. Execution is delegated to `LocalProcessProvider` instead of the legacy
   private process loop.
2. A serializable `PreparedAnalysis` identity is exposed in addition to the
   legacy transient request.
3. Progress includes the host-owned input-frozen event at seven percent.

No physics, input writer, command, environment, importer, result graph,
History, receipt, publication, cleanup, public error, or repair-payload
difference is accepted by this report. A future difference must update both
this report and the executable oracle in a dedicated compatibility change.

## Remaining stabilization boundary

This baseline closes the normalized four-solver process-lifecycle A/B fixture
gap and freezes the complete known-difference list at this seam. It does not by
itself claim full FEM Gate G5 or cross-platform stabilization complete.
The installed Windows publication gate uses deterministic synthetic result fields,
not physical solver runs. Gate G7's rollback exercise is now covered by the
temporary routed runtime, the v2 A/B oracle, the installed publication parity
gate, and compatibility-facade checks. Repeated cancel, timeout, bounded-output,
runtime-terminal, document-owner, thread, descendant-process, and synthetic
workspace cleanup checks now run in the Windows/POSIX FEM workflow. Installed
POSIX evidence, real backend/importer result publication, durable receipt
integration, repeated installed close/switch/reopen/same-name replacement,
exact-source rebind, and installed document-mutation/physical-solver leak checks
must still pass before Gates G4 and G5 and Step 7 can close.
