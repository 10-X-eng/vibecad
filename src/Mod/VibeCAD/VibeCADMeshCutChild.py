# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated compiled-geometry worker for Mesh cuts and sections."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping


REQUEST_SCHEMA = "vibecad-mesh-cut-job-v1"
RESULT_SCHEMA = "vibecad-mesh-cut-result-v1"


class CutFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


def _path(value: Any, root: Path, field: str) -> Path:
    path = Path(str(value or "")).resolve()
    if path.parent != root:
        raise CutFailure(
            "NATIVE_MESH_CUT_ARTIFACT_INVALID",
            f"{field} must be one file in the private Mesh-cut workspace.",
        )
    return path


def _vector(value: Any, field: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise CutFailure("NATIVE_MESH_CUT_REQUEST_INVALID", f"{field} is invalid.")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise CutFailure("NATIVE_MESH_CUT_REQUEST_INVALID", f"{field} is invalid.")
    return result  # type: ignore[return-value]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _shape_topology(shape: Any) -> dict[str, int]:
    return {
        "vertices": len(shape.Vertexes),
        "edges": len(shape.Edges),
        "wires": len(shape.Wires),
        "faces": len(shape.Faces),
        "shells": len(shape.Shells),
        "solids": len(shape.Solids),
    }


def _restore_placement(mesh: Any, value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"base", "quaternion"}:
        raise CutFailure(
            "NATIVE_MESH_CUT_REQUEST_INVALID",
            "A detached Mesh placement is invalid.",
        )
    base = value["base"]
    quaternion = value["quaternion"]
    if not isinstance(base, list) or len(base) != 3 or not isinstance(
        quaternion, list
    ) or len(quaternion) != 4:
        raise CutFailure(
            "NATIVE_MESH_CUT_REQUEST_INVALID",
            "A detached Mesh placement is invalid.",
        )
    import FreeCAD as App

    quaternion_values = tuple(float(component) for component in quaternion)
    if not all(math.isfinite(component) for component in quaternion_values):
        raise CutFailure(
            "NATIVE_MESH_CUT_REQUEST_INVALID",
            "A detached Mesh placement is invalid.",
        )
    mesh.Placement = App.Placement(
        App.Vector(*_vector(base, "placement.base")),
        App.Rotation(*quaternion_values),
    )


def _mesh_outputs(
    operation: str,
    source: Any,
    parameters: Mapping[str, Any],
) -> list[Any]:
    import FreeCAD as App

    if operation in {"poly_cut", "poly_trim"}:
        raw_polygon = parameters.get("polygon")
        regions = parameters.get("regions")
        if not isinstance(raw_polygon, list) or not isinstance(regions, list):
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "The polygon Mesh-cut request is incomplete.",
            )
        polygon = [App.Vector(*_vector(point, "polygon vertex")) for point in raw_polygon]
        outputs = []
        for region in regions:
            if region not in {"Inside", "Outside"}:
                raise CutFailure(
                    "NATIVE_MESH_CUT_REQUEST_INVALID",
                    "The polygon region is invalid.",
                )
            result = source.copy()
            method = result.cut if operation == "poly_cut" else result.trim
            method(polygon, 0 if region == "Inside" else 1)
            outputs.append(result)
        return outputs
    if operation == "trim_by_plane":
        base = App.Vector(*_vector(parameters.get("plane_base"), "plane_base"))
        normal = App.Vector(*_vector(parameters.get("plane_normal"), "plane_normal"))
        sides = parameters.get("sides")
        if float(normal.Length) <= 1.0e-12 or not isinstance(sides, list):
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "The plane-trim request is invalid.",
            )
        normal.normalize()
        outputs = []
        for side in sides:
            if side not in {"Below", "Above"}:
                raise CutFailure(
                    "NATIVE_MESH_CUT_REQUEST_INVALID",
                    "The plane-trim side is invalid.",
                )
            result = source.copy()
            result.trimByPlane(base, normal if side == "Below" else -normal)
            outputs.append(result)
        return outputs
    raise CutFailure(
        "NATIVE_MESH_CUT_REQUEST_INVALID",
        "The requested Mesh output operation is unavailable.",
    )


def _shape_outputs(
    operation: str,
    sources: list[Any],
    parameters: Mapping[str, Any],
) -> list[Any]:
    import FreeCAD as App
    import Mesh  # noqa: F401 - registers Mesh objects
    import MeshPart  # noqa: F401 - registers section objects
    import Part  # noqa: F401 - registers Part::Plane

    document = App.newDocument("VibeCADMeshCutWorker")
    try:
        results = []
        if operation == "section_by_plane":
            if len(sources) != 1:
                raise CutFailure(
                    "NATIVE_MESH_CUT_REQUEST_INVALID",
                    "A plane section requires one Mesh source.",
                )
            base = App.Vector(*_vector(parameters.get("plane_base"), "plane_base"))
            normal = App.Vector(*_vector(parameters.get("plane_normal"), "plane_normal"))
            if float(normal.Length) <= 1.0e-12:
                raise CutFailure(
                    "NATIVE_MESH_CUT_REQUEST_INVALID",
                    "The section plane normal is invalid.",
                )
            normal.normalize()
            source = document.addObject("Mesh::Feature", "Source")
            source.Mesh = sources[0]
            plane = document.addObject("Part::Plane", "Plane")
            plane.Placement = App.Placement(
                base,
                App.Rotation(App.Vector(0.0, 0.0, 1.0), normal),
            )
            result = document.addObject("MeshPart::SectionByPlane", "Section")
            result.Source = source
            result.Plane = plane
            result.MinimumLength = float(parameters["minimum_length_mm"])
            result.ConnectEdges = bool(parameters["connect_edges"])
            result.UpdateFromSource = True
            if document.recompute([result], True, True) is False or not result.isValid():
                raise CutFailure(
                    "NATIVE_MESH_CUT_NO_INTERSECTION",
                    str(result.getStatusString() or "The datum plane does not intersect the Mesh."),
                )
            results.append(result.Shape.copy())
        elif operation == "cross_sections":
            normal = App.Vector(*_vector(parameters.get("normal"), "normal"))
            positions = parameters.get("positions_mm")
            if float(normal.Length) <= 1.0e-12 or not isinstance(positions, list):
                raise CutFailure(
                    "NATIVE_MESH_CUT_REQUEST_INVALID",
                    "The cross-section planes are invalid.",
                )
            normal.normalize()
            for index, mesh in enumerate(sources):
                source = document.addObject("Mesh::Feature", f"Source{index}")
                source.Mesh = mesh
                result = document.addObject("MeshPart::CrossSections", f"Sections{index}")
                result.Source = source
                result.PlaneNormal = normal
                result.PlanePositions = [float(value) for value in positions]
                result.Epsilon = float(parameters["epsilon_mm"])
                result.ConnectEdges = bool(parameters["connect_edges"])
                result.UpdateFromSource = True
                if document.recompute([result], True, True) is False or not result.isValid():
                    raise CutFailure(
                        "NATIVE_MESH_CUT_NO_INTERSECTION",
                        str(
                            result.getStatusString()
                            or f"The configured planes do not intersect Mesh {index}."
                        ),
                    )
                results.append(result.Shape.copy())
        else:
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "The requested Mesh section operation is unavailable.",
            )
        if any(shape.isNull() or not shape.isValid() or not shape.Edges for shape in results):
            raise CutFailure(
                "NATIVE_MESH_CUT_NO_INTERSECTION",
                "The Mesh section produced no valid section edges.",
            )
        return results
    finally:
        App.closeDocument(document.Name)


def _viewport_outputs(
    sources: list[Any],
    parameters: Mapping[str, Any],
) -> tuple[list[Any], list[int]]:
    import Mesh

    polygon = parameters.get("polygon")
    matrix = parameters.get("projection_matrix")
    action = str(parameters.get("action") or "")
    source_regions = parameters.get("source_regions")
    if (
        not isinstance(polygon, list)
        or not isinstance(matrix, list)
        or action not in {"cut", "trim"}
        or not isinstance(source_regions, list)
        or len(source_regions) != len(sources)
    ):
        raise CutFailure(
            "NATIVE_MESH_CUT_REQUEST_INVALID",
            "The viewport Mesh-cut request is invalid.",
        )
    outputs = []
    source_indices = []
    for source_index, (source, regions) in enumerate(zip(sources, source_regions, strict=True)):
        if (
            not isinstance(regions, list)
            or not regions
            or any(region not in {"inside", "outside"} for region in regions)
            or len(set(regions)) != len(regions)
        ):
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "The viewport Mesh-cut regions are invalid.",
            )
        outputs.extend(Mesh.projectedPolygonEdit(source, polygon, matrix, action, regions))
        source_indices.extend([source_index] * len(regions))
    return outputs, source_indices


def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema") != REQUEST_SCHEMA:
        raise CutFailure(
            "NATIVE_MESH_CUT_REQUEST_INVALID",
            "The isolated Mesh-cut request has an unsupported schema.",
        )
    root = Path(str(request.get("workspace") or "")).resolve()
    operation = str(request.get("operation") or "")
    parameters = request.get("parameters")
    source_values = request.get("sources")
    output_values = request.get("outputs")
    if (
        not root.is_dir()
        or not isinstance(parameters, Mapping)
        or not isinstance(source_values, list)
        or not source_values
        or not isinstance(output_values, list)
        or not output_values
    ):
        raise CutFailure(
            "NATIVE_MESH_CUT_REQUEST_INVALID",
            "The isolated Mesh-cut request is incomplete.",
        )

    import Mesh

    sources = []
    for value in source_values:
        if not isinstance(value, Mapping) or set(value) != {"path", "placement"}:
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "Every detached Mesh source must identify its path and placement.",
            )
        mesh = Mesh.Mesh(str(_path(value["path"], root, "source_path")))
        _restore_placement(mesh, value["placement"])
        sources.append(mesh)
    if any(int(mesh.CountFacets) < 1 for mesh in sources):
        raise CutFailure(
            "NATIVE_MESH_CUT_SOURCE_EMPTY",
            "A detached Mesh-cut source contains no facets.",
        )
    output_specs = []
    for value in output_values:
        if not isinstance(value, Mapping) or set(value) != {"kind", "path"}:
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "Every Mesh-cut output must identify its kind and private path.",
            )
        kind = str(value["kind"])
        if kind not in {"bms", "brep"}:
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "A Mesh-cut output kind is invalid.",
            )
        output_specs.append((kind, _path(value["path"], root, "output_path")))

    output_source_indices: list[int] = []
    if operation in {"viewport_cut", "viewport_trim"}:
        if any(kind != "bms" for kind, _path_value in output_specs):
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "The viewport Mesh edit output contract is invalid.",
            )
        values, output_source_indices = _viewport_outputs(sources, parameters)
    elif operation in {"poly_cut", "poly_trim", "trim_by_plane"}:
        if len(sources) != 1 or any(kind != "bms" for kind, _path_value in output_specs):
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "The isolated Mesh edit output contract is invalid.",
            )
        values = _mesh_outputs(operation, sources[0], parameters)
        output_source_indices = [0] * len(values)
    else:
        if any(kind != "brep" for kind, _path_value in output_specs):
            raise CutFailure(
                "NATIVE_MESH_CUT_REQUEST_INVALID",
                "The isolated Mesh section output contract is invalid.",
            )
        values = _shape_outputs(operation, sources, parameters)
    if len(values) != len(output_specs):
        raise CutFailure(
            "NATIVE_MESH_CUT_ARTIFACT_INVALID",
            "The isolated Mesh cut returned an unexpected output count.",
        )

    if output_source_indices:
        source_geometry = [str(Mesh.geometrySha256(source)) for source in sources]
        output_geometry = [str(Mesh.geometrySha256(value)) for value in values]
        if any(
            output_geometry[index] == source_geometry[source_index]
            for index, source_index in enumerate(output_source_indices)
        ):
            raise CutFailure(
                "NATIVE_MESH_OPERATION_NO_CHANGE",
                "The selected polygon or plane does not change every requested Mesh result.",
            )
        for source_index in set(output_source_indices):
            values_for_source = [
                output_geometry[index]
                for index, owner in enumerate(output_source_indices)
                if owner == source_index
            ]
            if len(values_for_source) != len(set(values_for_source)):
                raise CutFailure(
                    "NATIVE_MESH_CUT_ARTIFACT_INVALID",
                    "The requested Mesh split did not produce distinct results.",
                )

    outputs = []
    for value, (kind, path) in zip(values, output_specs, strict=True):
        if kind == "bms":
            if int(value.CountFacets) < 1:
                raise CutFailure(
                    "NATIVE_MESH_CUT_EMPTY_RESULT",
                    "The requested Mesh cut leaves no usable facets.",
                )
            value.write(str(path))
            extra = {
                "points": int(value.CountPoints),
                "facets": int(value.CountFacets),
                "geometry_sha256": str(Mesh.geometrySha256(value)),
                "placement": {
                    "base": [
                        float(value.Placement.Base.x),
                        float(value.Placement.Base.y),
                        float(value.Placement.Base.z),
                    ],
                    "quaternion": [
                        float(component) for component in value.Placement.Rotation.Q
                    ],
                },
            }
        else:
            value.exportBrep(str(path))
            extra = {"topology": _shape_topology(value)}
        if not path.is_file() or path.stat().st_size < 1:
            raise CutFailure(
                "NATIVE_MESH_CUT_ARTIFACT_INVALID",
                "The isolated Mesh cut produced no artifact.",
            )
        outputs.append(
            {
                "kind": kind,
                "bytes": path.stat().st_size,
                "artifact_sha256": _sha256(path),
                **extra,
            }
        )
    return {"schema": RESULT_SCHEMA, "ok": True, "outputs": outputs}


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The Mesh-cut request is not an object.")
    root = Path(str(request.get("workspace") or "")).resolve()
    result_path = _path(request.get("result_path"), root, "result_path")
    try:
        result = execute(request)
    except CutFailure as exc:
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
            "failure_code": "NATIVE_MESH_CUT_FAILED",
            "error": "The isolated Mesh cut failed.",
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


if __name__ != "VibeCADMeshCutChild":
    raise SystemExit(main())
