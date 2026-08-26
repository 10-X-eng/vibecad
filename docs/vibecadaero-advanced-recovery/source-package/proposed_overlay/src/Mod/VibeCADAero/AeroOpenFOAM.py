# SPDX-License-Identifier: LGPL-2.1-or-later
"""OpenFOAM/CfdOF integration boundary for the canonical overlay.

CfdOF is treated as an optional FreeCAD-native case authoring integration;
OpenFOAM is the solver.  The generic CFD orchestration can then execute a case
locally or through another compute provider without duplicating physics setup.

This reference deliberately does not invent automatic external-domain face selection.
A preconfigured CfdOF analysis can already be written through the *verified*
current CfdOF API.  Automatic far-field domain generation is specified in the
builder plan and must be validated on actual FreeCAD topology before it becomes
canonical behavior.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

from AeroCFDContracts import (
    AeroCase,
    Artifact,
    CFDResult,
    Coefficients,
    Diagnostics,
    ExecutionReceipt,
    JobState,
    PreparedJob,
)


class OpenFOAMError(RuntimeError):
    pass


def cfdof_available() -> bool:
    try:
        from CfdOF import CfdAnalysis, CfdTools  # noqa: F401
        from CfdOF.Solve.CfdCaseWriterFoam import CfdCaseWriterFoam  # noqa: F401
        return True
    except Exception:
        return False


def create_cfdof_analysis_shell(document: Any, *, name: str = "CfdAnalysis") -> Any:
    """Create the minimum CfdOF analysis object graph using current namespaces.

    This mirrors the API used by CfdOF's own UAV/demo macros.  Geometry, mesh and
    boundary conditions are intentionally not guessed here.
    """

    from CfdOF import CfdAnalysis, CfdTools
    from CfdOF.Solve import CfdFluidMaterial, CfdInitialiseFlowField, CfdPhysicsSelection, CfdSolverFoam

    analysis = CfdAnalysis.makeCfdAnalysis(name)
    CfdTools.setActiveAnalysis(analysis)
    analysis.addObject(CfdPhysicsSelection.makeCfdPhysicsSelection())
    analysis.addObject(CfdFluidMaterial.makeCfdFluidMaterial("FluidProperties"))
    analysis.addObject(CfdInitialiseFlowField.makeCfdInitialFlowField())
    analysis.addObject(CfdSolverFoam.makeCfdSolverFoam())
    return analysis


def attach_cfdof_mesh(analysis: Any, part_object: Any, *, name: str = "VibeCADAeroCFDMesh", utility: str = "cfMesh") -> Any:
    """Attach a CfdOF 3-D mesh object to an analysis.

    ``part_object`` must be the *fluid-region/domain object*, not the aircraft
    solid itself.  This explicit requirement prevents the old discussion from
    conflating STL/LBM surface meshing with finite-volume OpenFOAM volume meshes.
    """

    from CfdOF import CfdTools
    from CfdOF.Mesh import CfdMesh

    CfdMesh.makeCfdMesh(name)
    import FreeCAD  # type: ignore

    mesh = FreeCAD.ActiveDocument.ActiveObject
    mesh.Part = part_object
    mesh.MeshUtility = utility
    mesh.ElementDimension = "3D"
    CfdTools.getActiveAnalysis().addObject(mesh)
    return mesh


def write_cfdof_case(analysis: Any) -> str:
    """Write a fully configured CfdOF analysis using its current case writer."""

    from CfdOF.Solve.CfdCaseWriterFoam import CfdCaseWriterFoam

    writer = CfdCaseWriterFoam(analysis)
    writer.writeCase()
    if not writer.case_folder:
        raise OpenFOAMError("CfdOF case writer did not expose a case folder")
    return str(Path(writer.case_folder).resolve())


class OpenFOAMBackend:
    """Run an already-authored OpenFOAM case and consume VibeCAD result JSON.

    Solver settings:
      case_dir: directory produced by CfdOF or another verified case author
      command: list/string command to execute (default: ./Allrun)
      collector_output: JSON path relative to case (default: vibecad_result.json)

    The collector JSON contract decouples OpenFOAM-version-specific function
    object layouts from VibeCAD's stable result schema.
    """

    name = "openfoam"

    def prepare(self, case: AeroCase, workdir: Path) -> PreparedJob:
        case.validate()
        source_dir = case.solver.settings.get("case_dir")
        if not source_dir:
            raise OpenFOAMError(
                "OpenFOAM solver.settings.case_dir is required. Use write_cfdof_case() "
                "after configuring a CfdOF analysis, or provide another verified case."
            )
        src = Path(str(source_dir)).expanduser().resolve()
        if not src.is_dir():
            raise OpenFOAMError(f"OpenFOAM case directory not found: {src}")
        case_dir = workdir / "openfoam_case"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        shutil.copytree(src, case_dir)

        raw_command = case.solver.settings.get("command", ("./Allrun",))
        if isinstance(raw_command, str):
            command = (raw_command,)
        else:
            command = tuple(str(v) for v in raw_command)
        expected = str(case.solver.settings.get("collector_output", "vibecad_result.json"))
        return PreparedJob(
            case=case,
            workdir=str(case_dir),
            command=command,
            expected_result=expected,
            metadata={"source_case": str(src)},
        )

    def parse(self, case: AeroCase, job: PreparedJob, receipt: ExecutionReceipt) -> CFDResult:
        if receipt.state != JobState.SUCCEEDED:
            return CFDResult(
                case_id=case.case_id,
                solver_backend=self.name,
                solver_version=case.solver.backend_version,
                compute_provider=case.compute.provider,
                state=JobState.FAILED,
                evidence_state="failed",
                method="cfd:openfoam",
                error=receipt.error or "OpenFOAM execution failed",
            )
        path = Path(job.workdir) / job.expected_result
        if not path.is_file():
            raise OpenFOAMError(
                f"OpenFOAM run completed but collector result is missing: {path}. "
                "Configure forceCoeffs and the VibeCAD collector before execution."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != "vibecad.openfoam.collector/1":
            raise OpenFOAMError("OpenFOAM collector schema mismatch")
        c = data.get("coefficients") or {}
        coefficients = Coefficients(
            cd=float(c["cd"]),
            cl=float(c["cl"]),
            cs=float(c.get("cs", 0.0)),
            cm_pitch=float(c["cm_pitch"]) if c.get("cm_pitch") is not None else None,
            cl_roll=float(c["cl_roll"]) if c.get("cl_roll") is not None else None,
            cn_yaw=float(c["cn_yaw"]) if c.get("cn_yaw") is not None else None,
        )
        artifact = Artifact.from_file(path, media_type="application/json", role="solver_result")
        return CFDResult(
            case_id=case.case_id,
            solver_backend=self.name,
            solver_version=str(data.get("openfoam_version") or case.solver.backend_version or "") or None,
            compute_provider=case.compute.provider,
            state=JobState.SUCCEEDED,
            coefficients=coefficients,
            diagnostics=Diagnostics(
                converged=data.get("converged"),
                residuals={str(k): float(v) for k, v in (data.get("residuals") or {}).items()},
                iterations=int(data["iterations"]) if data.get("iterations") is not None else None,
                wall_time_s=receipt.wall_time_s,
                warnings=tuple(str(v) for v in data.get("warnings") or []),
            ),
            artifacts=(artifact,),
            evidence_state="model_unqualified",
            claim_ceiling="not_airworthy",
            method="cfd:openfoam",
            metadata={
                "case_source": job.metadata.get("source_case"),
                "force_coefficients": True,
            },
        )
