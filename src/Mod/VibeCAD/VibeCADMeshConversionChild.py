# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd worker for Mesh-to-BREP conversion."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping


_SCHEMA = "vibecad-mesh-conversion-job-v1"
_RESULT_SCHEMA = "vibecad-mesh-conversion-result-v1"
_REPRESENTATION = {
    "Face": "faceted_face",
    "Shell": "faceted_shell",
    "Solid": "faceted_solid",
    "CompSolid": "faceted_compsolid",
    "Compound": "faceted_compound",
}


class ConversionFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


def _path(request: Mapping[str, Any], name: str, root: Path) -> Path:
    value = Path(str(request.get(name) or "")).resolve()
    if value.parent != root:
        raise ConversionFailure(
            "NATIVE_MESH_CONVERSION_ARTIFACT_INVALID",
            f"{name} must be one file in the private conversion workspace.",
        )
    return value


def _topology(shape: Any) -> dict[str, int]:
    return {
        "solids": len(shape.Solids),
        "shells": len(shape.Shells),
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "vertices": len(shape.Vertexes),
    }


def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema") != _SCHEMA:
        raise ConversionFailure(
            "NATIVE_MESH_CONVERSION_REQUEST_INVALID",
            "The isolated Mesh conversion request has an unsupported schema.",
        )
    root = Path(str(request.get("workspace") or "")).resolve()
    if not root.is_dir():
        raise ConversionFailure(
            "NATIVE_MESH_CONVERSION_REQUEST_INVALID",
            "The isolated Mesh conversion workspace is unavailable.",
        )
    source_path = _path(request, "source_path", root)
    output_path = _path(request, "output_path", root)
    tolerance = float(request.get("tolerance_mm", 0.0))
    sew = bool(request.get("sew_adjacent_faces", False))
    make_solid = bool(request.get("make_solid", False))

    import FreeCAD as App
    import Mesh
    import MeshPart  # noqa: F401 - registers MeshPart::ShapeFromMesh

    document = App.newDocument("VibeCADMeshConversionWorker")
    try:
        mesh = Mesh.Mesh(str(source_path))
        if int(mesh.CountFacets) < 1:
            raise ConversionFailure(
                "NATIVE_MESH_CONVERSION_SOURCE_EMPTY",
                "The detached Mesh conversion source contains no facets.",
            )
        if make_solid:
            if not bool(mesh.isSolid()):
                raise ConversionFailure(
                    "NATIVE_MESH_SOLID_REQUIRED",
                    "mesh_to_solid requires one closed manifold Mesh.",
                )
            if bool(mesh.hasSelfIntersections()):
                raise ConversionFailure(
                    "NATIVE_MESH_SELF_INTERSECTIONS",
                    "mesh_to_solid requires a Mesh without self-intersections.",
                )
        source = document.addObject("Mesh::Feature", "SourceMesh")
        source.Mesh = mesh
        result = document.addObject("MeshPart::ShapeFromMesh", "ConvertedShape")
        result.Source = source
        result.Tolerance = tolerance
        result.SewShape = sew
        result.MakeSolid = make_solid
        result.UpdateFromSource = True
        if document.recompute([result], True, True) is False or not result.isValid():
            raise ConversionFailure(
                "NATIVE_MESH_CONVERSION_FAILED",
                "The detached Mesh could not be converted to a valid BREP.",
            )
        shape = result.Shape
        if shape.isNull() or not shape.isValid():
            raise ConversionFailure(
                "NATIVE_MESH_CONVERSION_FAILED",
                "The detached Mesh conversion produced an invalid BREP.",
            )
        shape = shape.removeSplitter()
        if shape.isNull() or not shape.isValid():
            raise ConversionFailure(
                "NATIVE_MESH_CONVERSION_FAILED",
                "The detached Mesh conversion could not produce a valid refined BREP.",
            )
        topology = _topology(shape)
        shape_type = str(shape.ShapeType)
        if make_solid and (shape_type != "Solid" or topology["solids"] != 1):
            raise ConversionFailure(
                "NATIVE_MESH_SINGLE_SOLID_REQUIRED",
                "mesh_to_solid requires exactly one solid volume; separate disconnected components first.",
            )
        shape.exportBrep(str(output_path))
        if not output_path.is_file() or output_path.stat().st_size < 1:
            raise ConversionFailure(
                "NATIVE_MESH_CONVERSION_ARTIFACT_INVALID",
                "The detached Mesh conversion produced no BREP artifact.",
            )
        return {
            "schema": _RESULT_SCHEMA,
            "ok": True,
            "shape_type": shape_type,
            "representation": _REPRESENTATION.get(shape_type, "faceted_shape"),
            "topology": topology,
        }
    finally:
        App.closeDocument(document.Name)


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The Mesh conversion request is not an object.")
    result_path = Path(str(request.get("result_path") or "")).resolve()
    root = Path(str(request.get("workspace") or "")).resolve()
    if result_path.parent != root:
        raise ValueError("The Mesh conversion result path is outside its workspace.")
    try:
        result = execute(request)
    except ConversionFailure as exc:
        result = {
            "schema": _RESULT_SCHEMA,
            "ok": False,
            "failure_code": exc.code,
            "error": str(exc),
        }
    except Exception as exc:
        result = {
            "schema": _RESULT_SCHEMA,
            "ok": False,
            "failure_code": "NATIVE_MESH_CONVERSION_FAILED",
            "error": "The isolated Mesh conversion failed.",
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


if __name__ != "VibeCADMeshConversionChild":
    raise SystemExit(main())
