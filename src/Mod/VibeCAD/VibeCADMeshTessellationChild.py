# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd worker for shape tessellation."""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


REQUEST_SCHEMA = "vibecad-shape-tessellation-job-v1"
RESULT_SCHEMA = "vibecad-shape-tessellation-result-v1"


class TessellationFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


def _path(request: Mapping[str, Any], name: str, root: Path) -> Path:
    value = Path(str(request.get(name) or "")).resolve()
    if value.parent != root:
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
            f"{name} must be one file in the private tessellation workspace.",
        )
    return value


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if type(value) not in {int, float}:
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
            f"{name} must be one finite number.",
        )
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
            f"{name} is outside its supported range.",
        )
    return result


def _tessellate(shape: Any, raw: Any, *, source_path: Path, root: Path) -> Any:
    if not isinstance(raw, Mapping):
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
            "Tessellation settings must be an object.",
        )
    settings = dict(raw)
    method = str(settings.get("method") or "")
    if method == "standard" and set(settings) == {
        "method",
        "linear_deflection_mm",
        "angular_deflection_radians",
        "relative",
        "segments",
    }:
        linear = _number(settings["linear_deflection_mm"], "linear_deflection_mm", 1.0e-12, 1.0e12)
        angular = _number(settings["angular_deflection_radians"], "angular_deflection_radians", 1.0e-12, math.pi)
        if type(settings["relative"]) is not bool or type(settings["segments"]) is not bool:
            raise TessellationFailure(
                "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
                "relative and segments must be boolean.",
            )
        import MeshPart

        return MeshPart.meshFromShape(
            Shape=shape,
            LinearDeflection=linear,
            AngularDeflection=angular,
            Relative=settings["relative"],
            Segments=settings["segments"],
        )
    if method == "mefisto" and set(settings) == {"method", "maximum_edge_length_mm"}:
        import MeshPart

        maximum = _number(
            settings["maximum_edge_length_mm"], "maximum_edge_length_mm", 0.0, 1.0e12
        )
        return MeshPart.meshFromShape(Shape=shape, MaxLength=maximum)
    if method == "netgen" and set(settings) == {
        "method",
        "fineness",
        "growth_rate",
        "segments_per_edge",
        "segments_per_radius",
        "second_order",
        "optimize",
        "quad_dominated",
    }:
        if type(settings["fineness"]) is not int or not 0 <= settings["fineness"] <= 5:
            raise TessellationFailure(
                "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
                "fineness must be an integer from 0 through 5.",
            )
        for name in ("second_order", "optimize", "quad_dominated"):
            if type(settings[name]) is not bool:
                raise TessellationFailure(
                    "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
                    f"{name} must be boolean.",
                )
        growth_rate = _number(settings["growth_rate"], "growth_rate", 0.0, 1.0e6)
        segments_per_edge = _number(
            settings["segments_per_edge"], "segments_per_edge", 0.0, 1.0e6
        )
        segments_per_radius = _number(
            settings["segments_per_radius"], "segments_per_radius", 0.0, 1.0e6
        )
        import MeshPart

        if settings["fineness"] <= 4:
            return MeshPart.meshFromShape(
                Shape=shape,
                Fineness=settings["fineness"],
                SecondOrder=int(settings["second_order"]),
                Optimize=int(settings["optimize"]),
                AllowQuad=int(settings["quad_dominated"]),
            )
        return MeshPart.meshFromShape(
            Shape=shape,
            GrowthRate=growth_rate,
            SegPerEdge=segments_per_edge,
            SegPerRadius=segments_per_radius,
            SecondOrder=int(settings["second_order"]),
            Optimize=int(settings["optimize"]),
            AllowQuad=int(settings["quad_dominated"]),
        )
    if method == "gmsh" and set(settings) == {
        "method",
        "algorithm",
        "minimum_size_mm",
        "maximum_size_mm",
        "geometry_tolerance_mm",
        "element_order",
        "optimize",
        "executable",
        "timeout_seconds",
    }:
        algorithm = settings["algorithm"]
        if type(algorithm) is not int or algorithm not in {1, 2, 5, 6, 7, 8, 9, 11}:
            raise TessellationFailure(
                "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
                "algorithm must identify one supported Gmsh surface algorithm.",
            )
        minimum = _number(settings["minimum_size_mm"], "minimum_size_mm", 0.0, 1.0e12)
        maximum = _number(settings["maximum_size_mm"], "maximum_size_mm", 0.0, 1.0e12)
        tolerance = _number(
            settings["geometry_tolerance_mm"], "geometry_tolerance_mm", 1.0e-15, 1.0e6
        )
        if maximum > 0.0 and minimum > maximum:
            raise TessellationFailure(
                "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
                "minimum_size_mm cannot exceed a nonzero maximum_size_mm.",
            )
        order = settings["element_order"]
        timeout = settings["timeout_seconds"]
        executable = Path(str(settings["executable"] or "")).resolve()
        if (
            type(order) is not int
            or order not in {1, 2}
            or type(settings["optimize"]) is not bool
            or type(timeout) is not int
            or not 1 <= timeout <= 86_400
            or not executable.is_file()
        ):
            raise TessellationFailure(
                "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
                "The Gmsh execution settings are invalid.",
            )
        project_path = root / "shape.geo"
        gmsh_output = root / "gmsh-output.stl"
        log_path = root / "gmsh.log"
        source_text = str(source_path).replace("\\", "\\\\").replace('"', '\\"')
        project_path.write_text(
            f'Merge "{source_text}";\n'
            f"Mesh.CharacteristicLengthMax = {maximum or 1.0e22:.17g};\n"
            f"Mesh.CharacteristicLengthMin = {minimum:.17g};\n"
            f"Mesh.Optimize = {1 if settings['optimize'] else 0};\n"
            "Mesh.OptimizeNetgen = 0;\n"
            "Mesh.HighOrderOptimize = 0;\n"
            f"Mesh.ElementOrder = {order};\n"
            "Mesh.SecondOrderLinear = 1;\n"
            f"Mesh.Algorithm = {algorithm};\n"
            "Mesh.Algorithm3D = 1;\n"
            f"Geometry.Tolerance = {tolerance:.17g};\n"
            "Mesh 2;\n"
            "Coherence Mesh;\n",
            encoding="utf-8",
        )
        try:
            with log_path.open("wb") as log:
                completed = subprocess.run(
                    [
                        str(executable),
                        "-",
                        "-bin",
                        "-2",
                        str(project_path),
                        "-o",
                        str(gmsh_output),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise TessellationFailure(
                "NATIVE_MESH_TESSELLATION_TIMEOUT",
                "Gmsh exceeded timeout_seconds.",
            ) from exc
        if completed.returncode != 0 or not gmsh_output.is_file():
            detail = log_path.read_text(encoding="utf-8", errors="replace").strip()[-1000:]
            raise TessellationFailure(
                "NATIVE_MESH_TESSELLATION_FAILED",
                f"Gmsh failed{': ' + detail if detail else '.'}",
            )
        import Mesh

        mesh = Mesh.Mesh(str(gmsh_output))
        mesh.harmonizeNormals()
        return mesh
    raise TessellationFailure(
        "NATIVE_MESH_TESSELLATION_SETTINGS_INVALID",
        "Tessellation settings do not match one supported method.",
    )


def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema") != REQUEST_SCHEMA:
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_REQUEST_INVALID",
            "The isolated shape tessellation request has an unsupported schema.",
        )
    root = Path(str(request.get("workspace") or "")).resolve()
    if not root.is_dir():
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_REQUEST_INVALID",
            "The isolated shape tessellation workspace is unavailable.",
        )
    source_path = _path(request, "source_path", root)
    output_path = _path(request, "output_path", root)
    segments_path = _path(request, "segments_path", root)

    import Part

    shape = Part.Shape()
    shape.importBrep(str(source_path))
    raw_subelements = request.get("subelements")
    if not isinstance(raw_subelements, list) or any(
        not isinstance(name, str)
        or not name.startswith("Face")
        or not name[4:].isdigit()
        or int(name[4:]) < 1
        for name in raw_subelements
    ):
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_REQUEST_INVALID",
            "The isolated shape tessellation face selection is invalid.",
        )
    if raw_subelements:
        faces = list(shape.Faces)
        indices = [int(name[4:]) - 1 for name in raw_subelements]
        if any(index >= len(faces) for index in indices):
            raise TessellationFailure(
                "NATIVE_MESH_TESSELLATION_SOURCE_INVALID",
                "A selected source face does not exist.",
            )
        selected = [faces[index] for index in indices]
        shape = selected[0] if len(selected) == 1 else Part.makeCompound(selected)
    if shape.isNull() or not shape.isValid() or len(shape.Faces) < 1:
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_SOURCE_INVALID",
            "The detached source does not contain a valid shape with faces.",
        )
    mesh = _tessellate(shape, request.get("settings"), source_path=source_path, root=root)
    if int(mesh.CountPoints) < 1 or int(mesh.CountFacets) < 1:
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_RESULT_INVALID",
            "The shape tessellation produced an empty Mesh.",
        )
    mesh.write(str(output_path))
    if not output_path.is_file() or output_path.stat().st_size < 1:
        raise TessellationFailure(
            "NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
            "The shape tessellation produced no Mesh artifact.",
        )
    segments = [
        [int(value) for value in mesh.getSegment(index)]
        for index in range(int(mesh.countSegments()))
    ]
    segments_path.write_text(
        json.dumps(segments, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    bounds = mesh.BoundBox
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "points": int(mesh.CountPoints),
        "facets": int(mesh.CountFacets),
        "segments": int(mesh.countSegments()),
        "bounds_mm": {
            "min": [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
            "max": [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
        },
    }


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The shape tessellation request is not an object.")
    root = Path(str(request.get("workspace") or "")).resolve()
    result_path = Path(str(request.get("result_path") or "")).resolve()
    if result_path.parent != root:
        raise ValueError("The shape tessellation result path is outside its workspace.")
    try:
        result = execute(request)
    except TessellationFailure as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "failure_code": exc.code,
            "error": str(exc),
        }
    except Exception as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "failure_code": "NATIVE_MESH_TESSELLATION_FAILED",
            "error": "The isolated shape tessellation failed.",
            "exception_type": type(exc).__name__,
            "native_error": str(exc)[:1000],
        }
    result_path.write_text(
        json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return 2
    result = run(arguments[-1])
    return 0 if result.get("ok") is True else 1


if __name__ != "VibeCADMeshTessellationChild":
    raise SystemExit(main())
