# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd worker for exact solid Mesh booleans."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping


REQUEST_SCHEMA = "vibecad-mesh-boolean-job-v1"
RESULT_SCHEMA = "vibecad-mesh-boolean-result-v1"
OPERATIONS = {"union": "Union", "intersection": "Intersection", "difference": "Difference"}


class BooleanFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


def _path(request: Mapping[str, Any], name: str, root: Path) -> Path:
    value = Path(str(request.get(name) or "")).resolve()
    if value.parent != root:
        raise BooleanFailure(
            "NATIVE_MESH_BOOLEAN_ARTIFACT_INVALID",
            f"{name} must be one file in the private Mesh boolean workspace.",
        )
    return value


def _closed_source(mesh: Any, role: str) -> None:
    if int(mesh.CountFacets) < 1:
        raise BooleanFailure(
            "NATIVE_MESH_BOOLEAN_SOURCE_EMPTY",
            f"The {role} Mesh contains no facets.",
        )
    if not bool(mesh.isSolid()):
        raise BooleanFailure(
            "NATIVE_MESH_BOOLEAN_SOURCE_NOT_SOLID",
            f"The {role} Mesh is not a closed solid.",
        )
    if bool(mesh.hasSelfIntersections()):
        raise BooleanFailure(
            "NATIVE_MESH_BOOLEAN_SOURCE_SELF_INTERSECTS",
            f"The {role} Mesh has self-intersections.",
        )


def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema") != REQUEST_SCHEMA:
        raise BooleanFailure(
            "NATIVE_MESH_BOOLEAN_REQUEST_INVALID",
            "The isolated Mesh boolean request has an unsupported schema.",
        )
    root = Path(str(request.get("workspace") or "")).resolve()
    if not root.is_dir():
        raise BooleanFailure(
            "NATIVE_MESH_BOOLEAN_REQUEST_INVALID",
            "The isolated Mesh boolean workspace is unavailable.",
        )
    first_path = _path(request, "first_path", root)
    second_path = _path(request, "second_path", root)
    output_path = _path(request, "output_path", root)
    operation = str(request.get("operation") or "")
    if operation not in OPERATIONS:
        raise BooleanFailure(
            "NATIVE_MESH_BOOLEAN_REQUEST_INVALID",
            "Mesh boolean operation must be union, intersection, or difference.",
        )

    import FreeCAD as App
    import Mesh
    import MeshPart  # noqa: F401 - registers MeshPart::Boolean

    document = App.newDocument("VibeCADMeshBooleanWorker")
    try:
        first_mesh = Mesh.Mesh(str(first_path))
        second_mesh = Mesh.Mesh(str(second_path))
        _closed_source(first_mesh, "first")
        _closed_source(second_mesh, "second")
        first = document.addObject("Mesh::Feature", "First")
        second = document.addObject("Mesh::Feature", "Second")
        first.Mesh = first_mesh
        second.Mesh = second_mesh
        result = document.addObject("MeshPart::Boolean", "BooleanResult")
        result.Source1 = first
        result.Source2 = second
        result.Operation = OPERATIONS[operation]
        result.LinearDeflection = float(request["linear_deflection_mm"])
        result.AngularDeflection = float(request["angular_deflection_radians"])
        result.Relative = bool(request["relative"])
        result.UpdateFromSource = True
        if document.recompute([result], True, True) is False or not result.isValid():
            detail = str(result.getStatusString() or "").strip()
            raise BooleanFailure(
                "NATIVE_MESH_BOOLEAN_FAILED",
                detail or "The isolated Mesh boolean failed.",
            )
        output = result.Mesh
        if int(output.CountFacets) < 1 or not bool(output.isSolid()):
            raise BooleanFailure(
                "NATIVE_MESH_BOOLEAN_RESULT_INVALID",
                "The Mesh boolean did not produce one nonempty closed result.",
            )
        if bool(output.hasSelfIntersections()):
            raise BooleanFailure(
                "NATIVE_MESH_BOOLEAN_RESULT_SELF_INTERSECTS",
                "The Mesh boolean result has self-intersections.",
            )
        output.write(str(output_path))
        if not output_path.is_file() or output_path.stat().st_size < 1:
            raise BooleanFailure(
                "NATIVE_MESH_BOOLEAN_ARTIFACT_INVALID",
                "The Mesh boolean produced no output artifact.",
            )
        bounds = output.BoundBox
        return {
            "schema": RESULT_SCHEMA,
            "ok": True,
            "operation": operation,
            "points": int(output.CountPoints),
            "facets": int(output.CountFacets),
            "solid": True,
            "self_intersections": 0,
            "bounds_mm": {
                "min": [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
                "max": [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
            },
        }
    finally:
        App.closeDocument(document.Name)


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The Mesh boolean request is not an object.")
    root = Path(str(request.get("workspace") or "")).resolve()
    result_path = Path(str(request.get("result_path") or "")).resolve()
    if result_path.parent != root:
        raise ValueError("The Mesh boolean result path is outside its workspace.")
    try:
        result = execute(request)
    except BooleanFailure as exc:
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
            "failure_code": "NATIVE_MESH_BOOLEAN_FAILED",
            "error": "The isolated Mesh boolean failed.",
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


if __name__ != "VibeCADMeshBooleanChild":
    raise SystemExit(main())
