# SPDX-License-Identifier: LGPL-2.1-or-later

"""Off-thread Gmsh preparation and retained exact-result publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshModify import (
    PreparedMeshModification,
    verify_mesh_modification,
)
from VibeCADNativeMeshState import mesh_geometry_sha256
from VibeCADNativeMeshTargets import (
    PreparedMeshTarget,
    is_active_mesh_input,
    mesh_target_still_exact,
    prepare_mesh_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_ALGORITHMS = {
    "automatic": 2,
    "adaptive": 1,
    "delaunay": 5,
    "frontal": 6,
    "bamg": 7,
    "frontal_quad": 8,
    "parallelograms": 9,
    "quasi_structured_quad": 11,
}


@dataclass(frozen=True, slots=True)
class GmshRemeshRequest:
    target: PreparedMeshTarget
    algorithm: str
    algorithm_id: int
    minimum_element_size_mm: float
    maximum_element_size_mm: float
    surface_angle_degrees: float
    timeout_seconds: int
    executable: str
    local_source_mesh: Any
    source_placement: Any


@dataclass(frozen=True, slots=True)
class PreparedGmshRemesh:
    request: GmshRemeshRequest
    output_mesh: Any
    output_geometry_sha256: str


def _finite(value: Any, field: str) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise NativeMeshError(f"{field} must be one finite number.")
    return result


def _configured_gmsh_executable() -> str:
    import FreeCAD as App

    configured = str(
        App.ParamGet("User parameter:BaseApp/Preferences/Mod/Mesh/Meshing").GetString(
            "gmshExe", ""
        )
        or ""
    ).strip()
    requested = configured or "gmsh"
    resolved = shutil.which(requested)
    if resolved is None:
        message = (
            "The human-configured Gmsh executable is unavailable."
            if configured
            else "Gmsh is unavailable; configure it in Mesh preferences before remeshing."
        )
        raise NativeMeshError(message, error_code="NATIVE_MESH_GMSH_UNAVAILABLE")
    return str(Path(resolved).resolve())


def prepare_gmsh_request(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> GmshRemeshRequest:
    raw_targets = values["targets"]
    if not isinstance(raw_targets, list) or len(raw_targets) != 1:
        raise NativeMeshError("gmsh_remesh requires exactly one target in targets.")
    target = prepare_mesh_target(document, document_uid, raw_targets[0])
    algorithm = str(values["algorithm"])
    if algorithm not in _ALGORITHMS:
        raise NativeMeshError("algorithm must be one of the published Gmsh algorithms.")
    minimum = _finite(values["minimum_element_size_mm"], "minimum_element_size_mm")
    maximum = _finite(values["maximum_element_size_mm"], "maximum_element_size_mm")
    angle = _finite(values["surface_angle_degrees"], "surface_angle_degrees")
    timeout = values["timeout_seconds"]
    if minimum < 0.0 or maximum < 0.0 or (maximum > 0.0 and minimum > maximum):
        raise NativeMeshError(
            "Element sizes must be non-negative and minimum_element_size_mm cannot exceed a nonzero maximum."
        )
    if not 20.0 <= angle <= 120.0:
        raise NativeMeshError("surface_angle_degrees must be between 20 and 120.")
    if type(timeout) is not int or not 1 <= timeout <= 86_400:
        raise NativeMeshError("timeout_seconds must be between 1 and 86400.")
    import FreeCAD as App

    return GmshRemeshRequest(
        target,
        algorithm,
        _ALGORITHMS[algorithm],
        minimum,
        maximum,
        angle,
        timeout,
        _configured_gmsh_executable(),
        target.source_mesh,
        App.Placement(target.source.Placement),
    )


def _gmsh_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def _project_text(request: GmshRemeshRequest, input_path: Path) -> str:
    maximum = request.maximum_element_size_mm or 1.0e22
    return (
        "If(GMSH_MAJOR_VERSION < 4)\n"
        '  Error("Gmsh 4 or later is required");\n'
        "  Exit;\n"
        "EndIf\n"
        f'Merge "{_gmsh_path(input_path)}";\n'
        f"Mesh.Algorithm = {request.algorithm_id};\n"
        f"Mesh.CharacteristicLengthMax = {maximum:.17g};\n"
        f"Mesh.CharacteristicLengthMin = {request.minimum_element_size_mm:.17g};\n"
        f"ClassifySurfaces{{{request.surface_angle_degrees:.17g} * Pi/180, 1, 0}};\n"
        "CreateGeometry;\n"
        "Surface Loop(1) = Surface{:};\n"
        "Volume(1) = {1};\n"
    )


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def run_gmsh_remesh(
    request: GmshRemeshRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedGmshRemesh:
    if not isinstance(request, GmshRemeshRequest):
        raise TypeError("request must be a GmshRemeshRequest")
    from VibeCADNativeBackground import NativeBackgroundCancelled
    from VibeCADNativeMeshTargets import snapshot_mesh_targets

    if cancelled():
        raise NativeBackgroundCancelled()
    progress(1, "Capturing exact Mesh snapshot")
    exact_targets, snapshots = snapshot_mesh_targets((request.target,))
    local_source = snapshots[0]
    import FreeCAD as App

    local_source.Placement = App.Placement()
    request = replace(
        request,
        target=exact_targets[0],
        local_source_mesh=local_source,
    )
    progress(5, "Writing detached Gmsh input")
    with tempfile.TemporaryDirectory(prefix="vibecad-native-gmsh-") as directory:
        root = Path(directory)
        input_path = root / "source.stl"
        project_path = root / "remesh.geo"
        output_path = root / "result.stl"
        log_path = root / "gmsh.log"
        try:
            request.local_source_mesh.write(Filename=str(input_path), Format="STL")
            project_path.write_text(_project_text(request, input_path), encoding="utf-8")
        except Exception as exc:
            raise NativeMeshError(
                "The detached Gmsh input could not be prepared.",
                error_code="NATIVE_MESH_GMSH_INPUT_FAILED",
            ) from exc
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(20, "Running Gmsh")
        started = time.monotonic()
        try:
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    [
                        request.executable,
                        "-",
                        "-bin",
                        "-2",
                        str(project_path),
                        "-o",
                        str(output_path),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                )
                last_progress = 20
                while process.poll() is None:
                    if cancelled():
                        _stop_process(process)
                        raise NativeBackgroundCancelled()
                    elapsed = time.monotonic() - started
                    if elapsed > request.timeout_seconds:
                        _stop_process(process)
                        raise NativeMeshError(
                            "Gmsh exceeded timeout_seconds before producing a result.",
                            error_code="NATIVE_MESH_GMSH_TIMEOUT",
                        )
                    percent = min(75, 20 + int(55 * elapsed / request.timeout_seconds))
                    if percent > last_progress:
                        progress(percent, "Running Gmsh")
                        last_progress = percent
                    time.sleep(0.1)
                exit_code = int(process.returncode or 0)
        except (NativeBackgroundCancelled, NativeMeshError):
            raise
        except Exception as exc:
            raise NativeMeshError(
                "Gmsh could not be started.",
                error_code="NATIVE_MESH_GMSH_START_FAILED",
            ) from exc
        if exit_code != 0:
            try:
                detail = log_path.read_text(encoding="utf-8", errors="replace").strip()[-1000:]
            except Exception:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise NativeMeshError(
                f"Gmsh exited with code {exit_code}{suffix}",
                error_code="NATIVE_MESH_GMSH_FAILED",
            )
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(80, "Validating Gmsh result")
        try:
            import Mesh

            output = Mesh.read(str(output_path))
            output.harmonizeNormals()
            output.Placement = request.source_placement
        except Exception as exc:
            raise NativeMeshError(
                "Gmsh did not create a readable Mesh result.",
                error_code="NATIVE_MESH_GMSH_RESULT_INVALID",
            ) from exc
        if int(getattr(output, "CountFacets", 0) or 0) < 1:
            raise NativeMeshError(
                "Gmsh produced an empty Mesh.",
                error_code="NATIVE_MESH_GMSH_RESULT_EMPTY",
            )
        output_sha = mesh_geometry_sha256(output)
        if output_sha == request.target.source_geometry_sha256:
            raise NativeMeshError(
                "Gmsh did not change the exact source Mesh.",
                error_code="NATIVE_MESH_OPERATION_NO_CHANGE",
            )
        progress(88, "Gmsh result verified")
        return PreparedGmshRemesh(request, output, output_sha)


def commit_gmsh_remesh(document: Any, prepared: PreparedGmshRemesh) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedGmshRemesh):
        raise TypeError("prepared must be a PreparedGmshRemesh")
    request = prepared.request
    if not mesh_target_still_exact(
        document, request.target
    ) or not is_active_mesh_input(request.target.source):
        raise NativeMeshError(
            "The exact Mesh changed while Gmsh was running; the stale result was not applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    import Mesh  # noqa: F401 - registers Mesh::GmshRemesh
    import MeshGui

    result = document.addObject(
        "Mesh::GmshRemesh",
        document.getUniqueObjectName("GmshRemesh"),
    )
    if result is None:
        raise NativeMeshError("The retained Gmsh result could not be created.")
    result.Label = request.target.label
    result.Source = request.target.source
    result.Algorithm = request.algorithm_id
    result.MinimumElementSize = request.minimum_element_size_mm
    result.MaximumElementSize = request.maximum_element_size_mm
    result.SurfaceAngle = request.surface_angle_degrees
    result.Executable = request.executable
    result.TimeoutSeconds = request.timeout_seconds
    result.CachedSource = request.target.source.Mesh
    result.CachedResult = prepared.output_mesh
    result.Mesh = prepared.output_mesh
    result.purgeTouched()
    MeshGui.publishReplacingOutputs(
        str(document.Name),
        [request.target.source],
        [result],
        "GmshRemeshResults",
        "Gmsh Remesh",
        "Gmsh remesh",
    )
    operation = PreparedMeshModification(
        "gmsh_remesh",
        (request.target,),
        {
            "algorithm": request.algorithm,
            "algorithm_id": request.algorithm_id,
            "minimum_element_size_mm": request.minimum_element_size_mm,
            "maximum_element_size_mm": request.maximum_element_size_mm,
            "surface_angle_degrees": request.surface_angle_degrees,
            "timeout_seconds": request.timeout_seconds,
            "executable_source": "Mesh preferences",
            "_executable": request.executable,
            "_accepted_result_sha256": prepared.output_geometry_sha256,
        },
    )
    return NativeMutationDraft(
        value={
            "prepared": operation,
            "results": (result,),
            "result_labels": (str(result.Label),),
            "group": None,
        },
        recompute_targets=(result,),
        created=(object_identity(result),),
        replaced=(object_identity(request.target.source),),
    )


def verify_gmsh_remesh(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    result = verify_mesh_modification(document, draft)
    result["background_prepared"] = True
    return result
