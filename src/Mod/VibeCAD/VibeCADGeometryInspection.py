# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only, bounded inspection of exact document B-rep geometry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Any

from VibeCADDocumentReferences import (
    DocumentReferenceError,
    normalize_document_reference,
    resolve_reference_target,
)


MAX_GEOMETRY_SUBELEMENTS = 32
DEFAULT_GEOMETRY_SUBELEMENTS = 32
MAX_GEOMETRY_QUERIES = 16
MAX_GEOMETRY_QUERY_RESULTS = 16
GEOMETRY_INSPECTION_DEADLINE_SECONDS = 120.0


class GeometryInspectionError(RuntimeError):
    """An exact geometry reference cannot be captured or inspected."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(str(message))


_QUERY_FIELDS = frozenset(
    {
        "name",
        "element_type",
        "geometry_type",
        "normal",
        "direction",
        "axis_direction",
        "radius_mm",
        "radius_tolerance_mm",
        "min_area_mm2",
        "max_area_mm2",
        "min_length_mm",
        "max_length_mm",
        "near_point_mm",
        "max_distance_mm",
        "angle_tolerance_degrees",
        "expected_count",
        "max_results",
    }
)


def _query_number(
    query_name: str,
    field: str,
    value: Any,
    *,
    minimum: float = 0.0,
) -> float:
    if isinstance(value, bool):
        raise GeometryInspectionError(
            "GEOMETRY_QUERY_INVALID",
            f"Geometry query {query_name!r} field {field!r} must be a number.",
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryInspectionError(
            "GEOMETRY_QUERY_INVALID",
            f"Geometry query {query_name!r} field {field!r} must be a number.",
        ) from exc
    if not math.isfinite(result) or result < minimum:
        raise GeometryInspectionError(
            "GEOMETRY_QUERY_INVALID",
            f"Geometry query {query_name!r} field {field!r} must be finite and at least {minimum}.",
        )
    return result


def _query_vector(
    query_name: str,
    field: str,
    value: Any,
    *,
    nonzero: bool,
) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryInspectionError(
            "GEOMETRY_QUERY_INVALID",
            f"Geometry query {query_name!r} field {field!r} must be [x, y, z].",
        )
    result = [
        _query_number(query_name, f"{field}[{index}]", item, minimum=-math.inf)
        for index, item in enumerate(value)
    ]
    if nonzero and math.sqrt(sum(item * item for item in result)) <= 1.0e-12:
        raise GeometryInspectionError(
            "GEOMETRY_QUERY_INVALID",
            f"Geometry query {query_name!r} field {field!r} must be non-zero.",
        )
    return result


def _normalize_geometry_queries(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_GEOMETRY_QUERIES:
        raise GeometryInspectionError(
            "GEOMETRY_QUERY_INVALID",
            f"queries must be an array of at most {MAX_GEOMETRY_QUERIES} explicit queries.",
        )
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or not set(raw) <= _QUERY_FIELDS:
            raise GeometryInspectionError(
                "GEOMETRY_QUERY_INVALID",
                f"Geometry query {index + 1} contains unsupported fields.",
            )
        name = str(raw.get("name") or "").strip()
        if not name or len(name) > 64 or name in names:
            raise GeometryInspectionError(
                "GEOMETRY_QUERY_INVALID",
                "Every geometry query requires a unique name of at most 64 characters.",
            )
        names.add(name)
        element_type = str(raw.get("element_type") or "").strip().lower()
        if element_type not in {"face", "edge"}:
            raise GeometryInspectionError(
                "GEOMETRY_QUERY_INVALID",
                f"Geometry query {name!r} element_type must be face or edge.",
            )
        query: dict[str, Any] = {"name": name, "element_type": element_type}
        geometry_type = str(raw.get("geometry_type") or "").strip()
        if geometry_type:
            query["geometry_type"] = geometry_type
        for field in ("normal", "direction", "axis_direction"):
            if field in raw:
                query[field] = _query_vector(
                    name,
                    field,
                    raw[field],
                    nonzero=True,
                )
        if "near_point_mm" in raw:
            query["near_point_mm"] = _query_vector(
                name,
                "near_point_mm",
                raw["near_point_mm"],
                nonzero=False,
            )
        for field in (
            "radius_mm",
            "radius_tolerance_mm",
            "min_area_mm2",
            "max_area_mm2",
            "min_length_mm",
            "max_length_mm",
            "max_distance_mm",
            "angle_tolerance_degrees",
        ):
            if field in raw:
                query[field] = _query_number(name, field, raw[field])
        if query.get("angle_tolerance_degrees", 1.0) > 180.0:
            raise GeometryInspectionError(
                "GEOMETRY_QUERY_INVALID",
                f"Geometry query {name!r} angle_tolerance_degrees must not exceed 180.",
            )
        for minimum_field, maximum_field in (
            ("min_area_mm2", "max_area_mm2"),
            ("min_length_mm", "max_length_mm"),
        ):
            if (
                minimum_field in query
                and maximum_field in query
                and query[minimum_field] > query[maximum_field]
            ):
                raise GeometryInspectionError(
                    "GEOMETRY_QUERY_INVALID",
                    f"Geometry query {name!r} has {minimum_field} greater than {maximum_field}.",
                )
        for field, maximum in (
            ("expected_count", 256),
            ("max_results", MAX_GEOMETRY_QUERY_RESULTS),
        ):
            if field not in raw:
                continue
            value = raw[field]
            if isinstance(value, bool) or type(value) is not int or not 1 <= value <= maximum:
                raise GeometryInspectionError(
                    "GEOMETRY_QUERY_INVALID",
                    f"Geometry query {name!r} field {field!r} must be an integer from 1 to {maximum}.",
                )
            query[field] = value
        query.setdefault("max_results", MAX_GEOMETRY_QUERY_RESULTS)
        normalized.append(query)
    return normalized


def _stable_selector(match: Mapping[str, Any], element_type: str) -> dict[str, Any]:
    selector: dict[str, Any] = {
        "type": "query",
        "element_type": element_type,
        "expected_count": 1,
        "geometry_type": str(match.get("geometry_type") or ""),
    }
    center = match.get("center_mm")
    if isinstance(center, list) and len(center) == 3:
        selector["near_point"] = [float(value) for value in center]
        selector["max_distance"] = 1.0e-6
    if match.get("radius_mm") is not None:
        selector["radius"] = float(match["radius_mm"])
        selector["radius_tolerance"] = 1.0e-6
    return selector


def _copy_ready_placements(
    match: Mapping[str, Any],
    element_type: str,
) -> dict[str, dict[str, list[float]]]:
    """Return exact placement dictionaries accepted by existing VibeScript calls."""

    origin = match.get("origin_mm")
    x_direction = match.get("x_direction")
    if not (
        isinstance(origin, list)
        and len(origin) == 3
        and isinstance(x_direction, list)
        and len(x_direction) == 3
    ):
        return {}
    clean_origin = [float(value) for value in origin]
    clean_x = [float(value) for value in x_direction]
    result: dict[str, dict[str, list[float]]] = {}
    geometry_type = str(match.get("geometry_type") or "").strip().lower()
    normal = match.get("normal")
    if (
        element_type == "face"
        and geometry_type == "plane"
        and isinstance(normal, list)
        and len(normal) == 3
    ):
        result["sketch_placement"] = {
            "origin": clean_origin,
            "normal": [float(value) for value in normal],
            "x_direction": clean_x,
        }
    axis_direction = match.get("axis_direction")
    if isinstance(axis_direction, list) and len(axis_direction) == 3:
        result["axis_placement"] = {
            "origin": clean_origin,
            "axis_direction": [float(value) for value in axis_direction],
            "x_direction": clean_x,
        }
    return result


def discard_geometry_read(captured: Mapping[str, Any]) -> None:
    """Remove one detached inspection artifact without analyzing it."""

    directory = str(captured.get("artifact_directory") or "").strip()
    if directory:
        shutil.rmtree(Path(directory), ignore_errors=True)


def _placement_payload(placement: Any) -> dict[str, Any] | None:
    try:
        base = placement.Base
        rotation = placement.Rotation
        matrix = placement.toMatrix()
        return {
            "translation_mm": [float(base.x), float(base.y), float(base.z)],
            "rotation_euler_deg": [float(value) for value in rotation.toEuler()],
            "matrix_4x4_row_major": [
                float(getattr(matrix, name))
                for name in (
                    "A11",
                    "A12",
                    "A13",
                    "A14",
                    "A21",
                    "A22",
                    "A23",
                    "A24",
                    "A31",
                    "A32",
                    "A33",
                    "A34",
                    "A41",
                    "A42",
                    "A43",
                    "A44",
                )
            ],
        }
    except Exception:
        return None


def capture_geometry_read(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve and detach one exact shape on FreeCAD's document thread."""

    queries = _normalize_geometry_queries(args.get("queries"))
    analysis_level = str(args.get("analysis_level") or "full").strip().lower()
    if analysis_level not in {"topology", "full"}:
        raise GeometryInspectionError(
            "GEOMETRY_ANALYSIS_LEVEL_INVALID",
            "analysis_level must be 'topology' or 'full'.",
        )
    owner_document = service._active_document()
    if owner_document is None:
        raise GeometryInspectionError(
            "NO_ACTIVE_DOCUMENT",
            "Open a document before reading geometry.",
        )
    try:
        reference = normalize_document_reference(args.get("reference"))
        target = resolve_reference_target(
            owner_document,
            reference,
            "reference",
            open_missing=False,
        )
    except DocumentReferenceError as exc:
        raise GeometryInspectionError("GEOMETRY_REFERENCE_INVALID", str(exc)) from exc

    shape = getattr(target, "Shape", None)
    if shape is None:
        raise GeometryInspectionError(
            "GEOMETRY_TYPE_UNSUPPORTED",
            f"Object {target.Name!r} has no B-rep Shape to inspect.",
        )
    object_payload = {
        "name": str(getattr(target, "Name", "") or ""),
        "label": str(getattr(target, "Label", "") or ""),
        "type": str(getattr(target, "TypeId", "") or ""),
        "visible": bool(
            getattr(
                getattr(target, "ViewObject", None),
                "Visibility",
                False,
            )
        ),
    }
    placement_payload = _placement_payload(getattr(target, "Placement", None))
    artifact_directory = Path(tempfile.mkdtemp(prefix="vibecad-read-geometry-"))
    shape_path = artifact_directory / "shape.brep"
    try:
        if shape.isNull():
            raise GeometryInspectionError(
                "GEOMETRY_EMPTY",
                f"Object {target.Name!r} has an empty B-rep Shape.",
            )
        shape_hash = int(shape.hashCode())
        shape.exportBrep(str(shape_path))
        if not shape_path.is_file() or shape_path.stat().st_size <= 0:
            raise OSError("BREP export produced no geometry artifact.")
    except GeometryInspectionError:
        shutil.rmtree(artifact_directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(artifact_directory, ignore_errors=True)
        raise GeometryInspectionError(
            "GEOMETRY_CAPTURE_FAILED",
            f"Could not snapshot object {target.Name!r}: {exc}",
        ) from exc

    return {
        "reference": reference,
        "object": object_payload,
        "placement": placement_payload,
        "shape_hash": shape_hash,
        "artifact_directory": str(artifact_directory),
        "shape_path": str(shape_path),
        "include_subelements": bool(args.get("include_subelements", False)),
        "max_subelements": int(
            args.get("max_subelements", DEFAULT_GEOMETRY_SUBELEMENTS)
        ),
        "queries": queries,
        "analysis_level": analysis_level,
    }


def complete_geometry_read(
    captured: Mapping[str, Any],
    *,
    cancellation_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Analyze a detached BREP outside the FreeCAD process."""

    detail_limit = 0
    if bool(captured.get("include_subelements", False)):
        detail_limit = max(
            1,
            min(
                int(captured.get("max_subelements", DEFAULT_GEOMETRY_SUBELEMENTS)),
                MAX_GEOMETRY_SUBELEMENTS,
            ),
        )
    artifact_directory = Path(str(captured["artifact_directory"]))
    request_path = artifact_directory / "request.json"
    result_path = artifact_directory / "result.json"
    try:
        queries = [
            dict(query)
            for query in list(captured.get("queries") or [])
            if isinstance(query, Mapping)
        ]
        request_path.write_text(
            json.dumps(
                {
                    "schema": "vibecad-geometry-job-v1",
                    "operation": "inspect_brep",
                    "shape": {
                        "format": "brep",
                        "path": str(captured["shape_path"]),
                    },
                    "max_subelements": detail_limit,
                    "queries": queries,
                    "analysis_level": str(
                        captured.get("analysis_level") or "full"
                    ),
                    "result_path": str(result_path),
                    "deadline_ms": round(
                        GEOMETRY_INSPECTION_DEADLINE_SECONDS * 1000.0
                    ),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        from VibeCADGeometry import execute_job

        execution = execute_job(
            request_path,
            result_path,
            cancellation_check=cancellation_check,
            deadline_seconds=GEOMETRY_INSPECTION_DEADLINE_SECONDS,
        )
        shape_hash = int(captured["shape_hash"])
        if execution.get("ok") is not True:
            return {
                **execution,
                "tool": "vibescript.read_geometry",
                "reference": dict(captured["reference"]),
                "object": dict(captured["object"]),
            }
        facts = execution.get("geometry")
        if not isinstance(facts, Mapping):
            raise GeometryInspectionError(
                "GEOMETRY_WORKER_RESULT_INVALID",
                "The isolated geometry worker returned no geometry facts.",
            )
        payload = {
            "ok": True,
            "tool": "vibescript.read_geometry",
            "reference": dict(captured["reference"]),
            "object": dict(captured["object"]),
            "placement": captured.get("placement"),
            "shape_revision": {
                "shape_hash": shape_hash,
                "rule": (
                    "Face and edge indices apply only while this exact object still has "
                    "this shape_hash. Read geometry again after any topology change."
                ),
            },
            "geometry": dict(facts),
            "execution": {
                "mode": str(
                    execution.get("execution_mode") or "isolated_geometry_worker"
                ),
                "elapsed_seconds": execution.get("elapsed_seconds"),
                "worker_elapsed_ms": execution.get("elapsed_ms"),
            },
        }
        query_results = payload["geometry"].get("query_results")
        if isinstance(query_results, list):
            by_name = {str(query["name"]): query for query in queries}
            for raw_result in query_results:
                if not isinstance(raw_result, dict):
                    continue
                query = by_name.get(str(raw_result.get("name") or ""))
                if query is None:
                    continue
                matches = raw_result.get("matches")
                if isinstance(matches, list):
                    for match in matches:
                        if isinstance(match, dict):
                            match["source_selector"] = _stable_selector(
                                match,
                                str(query["element_type"]),
                            )
                            match.update(
                                _copy_ready_placements(
                                    match,
                                    str(query["element_type"]),
                                )
                            )
                raw_result["query"] = query
        if detail_limit:
            payload["subelement_selectors"] = {
                "index_base": 1,
                "shape_hash": shape_hash,
                "face_field": "geometry.face_details[].index",
                "edge_field": "geometry.edge_details[].index",
            }
        return payload
    finally:
        shutil.rmtree(artifact_directory, ignore_errors=True)
