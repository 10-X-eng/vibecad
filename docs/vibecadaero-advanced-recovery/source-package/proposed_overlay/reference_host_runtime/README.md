# Reference host runtime

This directory is **design evidence only**. It is not claimed to be installed or integrated in upstream VibeCAD.

`VibeCADAnalysisJobState.py` demonstrates the corrected lifecycle invariant: cancellation and publication ownership are linearized under one lock so an accepted cancellation can never be followed by CAD publication.

`VibeCADAnalysisPublication.py` demonstrates the second invariant: durable job provenance is inert. Missing source waits, stale dependencies remain stale, current results without fresh host publication authorization wait, and an existing receipt makes replay idempotent. It deliberately contains no FreeCAD mutation code.

Production integration must be reconciled against a fresh upstream SHA and must use VibeCAD's actual main-thread dispatch, Native mutation boundary, FEM adapter, persistence, and provider implementations.
