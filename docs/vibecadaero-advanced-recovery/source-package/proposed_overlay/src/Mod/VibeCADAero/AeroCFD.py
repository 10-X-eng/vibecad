# SPDX-License-Identifier: LGPL-2.1-or-later
"""High-fidelity solver orchestration for the VibeCADAero overlay.

The core design rule is separation of *solver* and *compute provider*:
FluidX3D or OpenFOAM defines what physics is solved; local execution or Kaggle
defines where a prepared job is run.  This prevents cloud-provider policy from
leaking into aerodynamic semantics and keeps future HPC providers possible.

This module intentionally does not write FreeCAD documents.  The public
``VibeCADAero.py`` authority should call this layer and then persist a summary
through the existing ``AeroResults`` / ``AeroStamp`` path.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
from typing import Protocol

from AeroCFDContracts import (
    AeroCase,
    CFDResult,
    ExecutionReceipt,
    JobState,
    PreparedJob,
)


class BackendError(RuntimeError):
    pass


class SolverBackend(Protocol):
    name: str

    def prepare(self, case: AeroCase, workdir: Path) -> PreparedJob: ...
    def parse(self, case: AeroCase, job: PreparedJob, receipt: ExecutionReceipt) -> CFDResult: ...


class ComputeProvider(Protocol):
    name: str

    def execute(self, job: PreparedJob) -> ExecutionReceipt: ...


class Registry:
    def __init__(self) -> None:
        self.solvers: dict[str, SolverBackend] = {}
        self.providers: dict[str, ComputeProvider] = {}

    def add_solver(self, backend: SolverBackend) -> None:
        key = str(backend.name).strip().lower()
        if not key:
            raise ValueError("solver backend requires a name")
        self.solvers[key] = backend

    def add_provider(self, provider: ComputeProvider) -> None:
        key = str(provider.name).strip().lower()
        if not key:
            raise ValueError("compute provider requires a name")
        self.providers[key] = provider

    def solver(self, name: str) -> SolverBackend:
        try:
            return self.solvers[str(name).lower()]
        except KeyError as exc:
            raise BackendError(f"CFD solver backend is not registered: {name}") from exc

    def provider(self, name: str) -> ComputeProvider:
        try:
            return self.providers[str(name).lower()]
        except KeyError as exc:
            raise BackendError(f"CFD compute provider is not registered: {name}") from exc


def run_case(
    case: AeroCase,
    *,
    registry: Registry,
    workspace_root: str | Path | None = None,
) -> CFDResult:
    """Prepare, execute and parse exactly one immutable CFD case."""

    case.validate()
    solver = registry.solver(case.solver.backend)
    provider = registry.provider(case.compute.provider)

    root = Path(workspace_root) if workspace_root else Path(tempfile.mkdtemp(prefix="vibecad_cfd_"))
    workdir = root / case.case_id
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        job = solver.prepare(case, workdir)
        receipt = provider.execute(job)
        result = solver.parse(case, job, receipt)
        result.validate()
        return result
    except Exception as exc:
        return CFDResult(
            case_id=case.case_id,
            solver_backend=case.solver.backend,
            solver_version=case.solver.backend_version,
            compute_provider=case.compute.provider,
            state=JobState.FAILED,
            evidence_state="failed",
            claim_ceiling="not_airworthy",
            method=f"cfd:{case.solver.backend}",
            error=str(exc),
            metadata={"workspace": str(workdir)},
        )


def report_patch(result: CFDResult) -> dict[str, object]:
    """Map a CFD result into fields that can extend the existing AeroReport.

    Existing CL/CD/CM names are intentionally preserved.  New provenance fields
    are namespaced so the upstream report can grow without breaking existing
    consumers or assistant context.
    """

    patch: dict[str, object] = {
        "CFDBackend": result.solver_backend,
        "CFDBackendVersion": result.solver_version or "",
        "CFDComputeProvider": result.compute_provider,
        "CFDState": result.state.value,
        "CFDMethod": result.method,
        "CFDEvidenceState": result.evidence_state,
        "CFDClaimCeiling": result.claim_ceiling,
        "CFDError": result.error or "",
        "CFDArtifacts": [
            {
                "path": a.path,
                "sha256": a.sha256,
                "media_type": a.media_type,
                "role": a.role,
                "size_bytes": a.size_bytes,
            }
            for a in result.artifacts
        ],
    }
    if result.coefficients is not None:
        patch.update(
            {
                "CL": result.coefficients.cl,
                "CD": result.coefficients.cd,
                "CM": result.coefficients.cm_pitch if result.coefficients.cm_pitch is not None else 0.0,
                "CFDSideCoefficient": result.coefficients.cs,
                "CFDRollCoefficient": result.coefficients.cl_roll,
                "CFDYawCoefficient": result.coefficients.cn_yaw,
                "source": f"CFD:{result.solver_backend}",
            }
        )
    if result.force_moment is not None:
        patch["CFDForceBodyN"] = list(result.force_moment.force_body_n.as_tuple())
        patch["CFDMomentBodyNm"] = list(result.force_moment.moment_body_nm.as_tuple())
    patch["CFDConverged"] = result.diagnostics.converged
    patch["CFDResiduals"] = dict(result.diagnostics.residuals)
    return patch
