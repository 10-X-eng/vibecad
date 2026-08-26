# SPDX-License-Identifier: LGPL-2.1-or-later
"""FluidX3D solver adapter with a vendored-default deployment model.

Distribution documentation
--------------------------
The canonical VibeCAD distribution vendors a pinned FluidX3D source tree and a
VibeCAD force-extraction bridge alongside VibeCADAero. The FluidX3D license and
origin notice remain intact and govern that third-party code.

VibeCAD/VibeCADAero remains governed by VibeCAD's own project license. FluidX3D
remains third-party software governed by its own license; FluidX3D-specific
commercial, military, AI-source-training, attribution, publication/source and
related requirements apply to FluidX3D, not to Aero globally. VibeCAD does not
infer, police, or enforce purpose. Aero presents one first-entry acknowledgement
that states this component-specific boundary and persists it locally.

An explicitly configured external FluidX3D bridge always overrides the vendored
bridge. This is a normal configuration capability, not a purpose detector or
policy profile.

The adapter deliberately avoids the unverified ``fluidx3d.Config`` Python API
used in earlier discussion drafts. This reference uses a stable process contract around
APIs verified in ProjectPhysX/FluidX3D source: LBM::run, LBM::object_force,
LBM::object_torque, LBM::voxelize_stl and Units conversion.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from AeroCFDContracts import (
    AeroCase,
    Artifact,
    Coefficients,
    CFDResult,
    Diagnostics,
    ExecutionReceipt,
    ForceMoment,
    JobState,
    PreparedJob,
    Vector3,
    coefficients_from_force,
    write_json,
)


class FluidX3DError(RuntimeError):
    pass


class FluidX3DBackend:
    name = "fluidx3d"

    def __init__(
        self,
        executable: str | None = None,
        *,
        vendor_root: str | Path | None = None,
    ) -> None:
        self.executable = executable or os.environ.get("VIBECAD_FLUIDX3D_BRIDGE", "")
        self.vendor_root = Path(vendor_root).expanduser().resolve() if vendor_root else (
            Path(__file__).resolve().parent / "vendor" / "FluidX3D"
        )

    def _vendored_bridge_candidates(self) -> tuple[Path, ...]:
        names = (
            "VibeCADFluidX3D.exe",
            "VibeCADFluidX3D",
            "FluidX3D.exe",
            "FluidX3D",
        )
        return tuple(self.vendor_root / "bin" / name for name in names)


    def _resolve_executable(self, case: AeroCase) -> Path:
        configured = str(case.solver.settings.get("executable") or self.executable or "").strip()
        if configured:
            path = Path(configured).expanduser().resolve()
            if not path.is_file():
                raise FluidX3DError(f"FluidX3D bridge executable does not exist: {path}")
            return path

        for candidate in self._vendored_bridge_candidates():
            if candidate.is_file():
                return candidate.resolve()
        raise FluidX3DError(
            "VibeCAD expects its vendored FluidX3D bridge under "
            f"{self.vendor_root / 'bin'}, or an explicit external bridge via "
            "solver.settings.executable / VIBECAD_FLUIDX3D_BRIDGE."
        )

    def prepare(self, case: AeroCase, workdir: Path) -> PreparedJob:
        case.validate()
        executable = self._resolve_executable(case)
        geometry = Path(case.geometry.artifact.path).expanduser().resolve()
        if not geometry.is_file():
            raise FileNotFoundError(str(geometry))
        if case.geometry.artifact.media_type != "model/stl":
            raise FluidX3DError("the current FluidX3D reference adapter requires STL geometry")

        # The bridge consumes SI quantities and owns all lattice-unit conversion.
        result_path = workdir / "result.json"
        job_payload: dict[str, Any] = {
            "schema_version": "vibecad.fluidx3d.bridge/1",
            "case_id": case.case_id,
            "geometry": {
                "stl_path": str(geometry),
                "sha256": case.geometry.artifact.sha256,
                "source_units": case.geometry.source_units,
                "geometry_revision": case.geometry.geometry_revision,
            },
            "flow": {
                "velocity_body_mps": list(case.flow.freestream_body_mps.as_tuple()),
                "density_kg_m3": case.flow.density_kg_m3,
                "dynamic_viscosity_pa_s": case.flow.dynamic_viscosity_pa_s,
            },
            "reference": {
                "area_m2": case.references.area_m2,
                "length_m": case.references.length_m,
                "span_m": case.references.span_m,
                "moment_reference_body_m": list(case.references.moment_reference_body_m.as_tuple()),
            },
            "solver": dict(case.solver.settings),
            "result_path": str(result_path),
        }
        job_json = workdir / "fluidx3d_job.json"
        write_json(job_json, job_payload)

        device_args: tuple[str, ...] = ()
        device_id = case.compute.settings.get("device_id")
        if device_id is not None:
            # FluidX3D's own main argument handling accepts device identifiers.
            device_args = (str(int(device_id)),)
        settings = dict(case.solver.settings)
        geometry_physical_size_m = settings.get("geometry_physical_size_m")
        if geometry_physical_size_m is None or float(geometry_physical_size_m) <= 0.0:
            raise FluidX3DError(
                "FluidX3D requires solver.settings.geometry_physical_size_m: the physical "
                "size corresponding to the STL maximum dimension. This prevents silent scale errors."
            )
        environment = {
            "VIBECAD_FX3D_JOB": str(job_json),
            "VIBECAD_FX3D_STL": str(geometry),
            "VIBECAD_FX3D_RESULT": str(result_path),
            "VIBECAD_FX3D_CASE_ID": case.case_id,
            "VIBECAD_FX3D_UX": str(case.flow.freestream_body_mps.x),
            "VIBECAD_FX3D_UY": str(case.flow.freestream_body_mps.y),
            "VIBECAD_FX3D_UZ": str(case.flow.freestream_body_mps.z),
            "VIBECAD_FX3D_RHO": str(case.flow.density_kg_m3),
            "VIBECAD_FX3D_MU": str(case.flow.dynamic_viscosity_pa_s),
            "VIBECAD_FX3D_GEOMETRY_SIZE_M": str(float(geometry_physical_size_m)),
            "VIBECAD_FX3D_GEOMETRY_SIZE_LU": str(float(settings.get("geometry_size_lu", 128.0))),
            "VIBECAD_FX3D_NX": str(int(settings.get("nx", 512))),
            "VIBECAD_FX3D_NY": str(int(settings.get("ny", 256))),
            "VIBECAD_FX3D_NZ": str(int(settings.get("nz", 256))),
            "VIBECAD_FX3D_LBM_SPEED": str(float(settings.get("lbm_speed", 0.08))),
            "VIBECAD_FX3D_TRANSIENT_STEPS": str(int(settings.get("transient_steps", 2000))),
            "VIBECAD_FX3D_SAMPLE_EVERY": str(int(settings.get("sample_every", 100))),
            "VIBECAD_FX3D_SAMPLE_COUNT": str(int(settings.get("sample_count", 50))),
        }
        return PreparedJob(
            case=case,
            workdir=str(workdir),
            command=(str(executable), *device_args),
            environment=environment,
            expected_result=str(result_path.name),
            metadata={"bridge_job": str(job_json)},
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
                method="cfd:fluidx3d",
                error=receipt.error or "FluidX3D execution failed",
                metadata={"returncode": receipt.returncode},
            )
        result_path = Path(job.workdir) / job.expected_result
        if not result_path.is_file():
            raise FluidX3DError(f"FluidX3D bridge produced no result: {result_path}")
        data = json.loads(result_path.read_text(encoding="utf-8"))
        if data.get("schema_version") != "vibecad.fluidx3d.bridge/1":
            raise FluidX3DError("FluidX3D bridge result schema mismatch")
        if str(data.get("case_id")) != case.case_id:
            raise FluidX3DError("FluidX3D bridge returned a different case_id")
        force = ForceMoment(
            force_body_n=Vector3.from_any(data.get("force_body_n") or (0.0, 0.0, 0.0)),
            moment_body_nm=Vector3.from_any(data.get("moment_body_nm") or (0.0, 0.0, 0.0)),
            sample_count=int(data["sample_count"]) if data.get("sample_count") is not None else None,
            averaging_start_s=float(data["averaging_start_s"]) if data.get("averaging_start_s") is not None else None,
            averaging_end_s=float(data["averaging_end_s"]) if data.get("averaging_end_s") is not None else None,
        )
        coeffs = coefficients_from_force(case, force)
        # The pass-01 bridge reports torque about the voxelized object's center of mass.
        # Until an explicit CAD-body origin transform is supplied, do not mislabel that
        # torque as a coefficient about AeroConfig.xyz_ref. Force coefficients remain valid.
        if data.get("moment_reference") != "requested_body_reference":
            coeffs = Coefficients(cd=coeffs.cd, cl=coeffs.cl, cs=coeffs.cs)
        artifact = Artifact.from_file(result_path, media_type="application/json", role="solver_result")
        warnings = tuple(str(v) for v in data.get("warnings") or [])
        diagnostics = Diagnostics(
            converged=data.get("converged"),
            iterations=int(data["iterations"]) if data.get("iterations") is not None else None,
            simulated_time_s=float(data["simulated_time_s"]) if data.get("simulated_time_s") is not None else None,
            wall_time_s=receipt.wall_time_s,
            warnings=warnings,
            notes=(
                "FluidX3D result is CFD evidence but remains not-airworthy until independently validated.",
            ),
        )
        return CFDResult(
            case_id=case.case_id,
            solver_backend=self.name,
            solver_version=str(data.get("fluidx3d_commit") or case.solver.backend_version or "") or None,
            compute_provider=case.compute.provider,
            state=JobState.SUCCEEDED,
            force_moment=force,
            coefficients=coeffs,
            diagnostics=diagnostics,
            artifacts=(artifact,),
            evidence_state="model_unqualified",
            claim_ceiling="not_airworthy",
            method="cfd:fluidx3d:lbm",
            metadata={
                "geometry_sha256": case.geometry.artifact.sha256,
                "bridge_version": data.get("bridge_version"),
                "lattice": data.get("lattice"),
                "fluidx3d_runtime_path": str(Path(job.command[0]).resolve()),
                "fluidx3d_vendor_root": str(self.vendor_root),
            },
        )
