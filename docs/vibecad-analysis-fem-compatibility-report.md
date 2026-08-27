# VibeCAD Analysis FEM compatibility baseline

This report freezes the normalized compatibility target for the four FEM
solver paths supported at the Analysis host migration boundary. The executable
oracle is `src/Mod/VibeCAD/vibecad_tests/fixtures/analysis_fem_parity_v1.json`;
`test_analysis_fem_parity_oracle.py` proves the host path against it.

The oracle binds each solver's exact direct-argument command sequence,
environment identity, timeout, normalized progress lifecycle, stage summary,
input hash/count, solver-state dependency, History identity, document UID,
adapter identity, and compatibility provenance. Existing solver-specific tests
remain authoritative for timeout, cancellation, output-limit, backend-failure,
cleanup, commit, verification, and public error mappings.

## Covered solver paths

| Solver | Host provider | Stages | Compatibility disposition |
|---|---|---:|---|
| CalculiX | `local-process` | 1 | Frozen |
| Elmer | `local-process` | 3 | Frozen |
| Z88 | `local-process` | 2 | Frozen |
| Mystran | `local-process` | 1 | Frozen |

## Accepted intentional differences

1. Execution is delegated to `LocalProcessProvider` instead of the legacy
   private process loop.
2. A serializable `PreparedAnalysis` identity is exposed in addition to the
   legacy transient request.
3. Progress includes the host-owned input-frozen event at seven percent.

No physics, input writer, command, environment, importer, result graph,
History, receipt, publication, cleanup, or public error difference is accepted
by this report. A future difference must update both this report and the
executable oracle in a dedicated compatibility change.

## Remaining stabilization boundary

This baseline closes the normalized four-solver A/B fixture gap. It does not by
itself claim cross-platform stabilization complete. Repeated process-tree,
cancel/timeout, close/switch/reopen, bounded-output, orphan, workspace, thread,
and document-mutation stress must still pass on Windows and POSIX before the
roadmap can mark the full Step 7 stabilization interval complete.
