# SPDX-License-Identifier: LGPL-2.1-or-later

"""In-process FreeCAD Part fallback for isolated geometry jobs."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_MAX_QUERIES = 16
_MAX_SUBELEMENTS = 32
_MAX_QUERY_RESULTS = 16


def load_brep(path: str | Path) -> Any:
    """Load one BREP artifact through FreeCAD Part."""

    import Part

    shape = Part.Shape()
    shape.importBrep(str(path))
    if bool(getattr(shape, "isNull", lambda: True)()):
        raise RuntimeError(f"Cannot read BREP artifact: {path}")
    return shape


def execute_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run inspect_brep, validate_brep, or minimum_distance in-process."""

    started = time.monotonic()
    try:
        if str(request.get("schema") or "") != "vibecad-geometry-job-v1":
            raise ValueError("Unsupported geometry request schema.")
        operation = str(request.get("operation") or "")
        if operation == "inspect_brep":
            result = inspect_brep(request)
        elif operation == "validate_brep":
            result = validate_brep(request)
        elif operation == "minimum_distance":
            result = minimum_distance(request)
        else:
            raise ValueError("Unsupported geometry worker operation.")
        result["schema"] = "vibecad-geometry-result-v1"
        result["execution_mode"] = "in_process_part"
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000.0)
        return result
    except Exception as exc:
        return {
            "schema": "vibecad-geometry-result-v1",
            "ok": False,
            "failure_stage": "in_process_fallback",
            "exception_type": type(exc).__name__,
            "error": str(exc),
            "execution_mode": "in_process_part",
            "elapsed_ms": int((time.monotonic() - started) * 1000.0),
        }


def inspect_brep(request: Mapping[str, Any]) -> dict[str, Any]:
    shape = _require_brep(request.get("shape"), noun="Shape inspection")
    analysis_level = str(request.get("analysis_level") or "full").strip().lower()
    if analysis_level not in {"topology", "full"}:
        raise ValueError("Shape inspection analysis_level must be topology or full.")
    detail_limit = max(0, min(int(request.get("max_subelements") or 0), _MAX_SUBELEMENTS))
    queries = list(request.get("queries") or [])
    if len(queries) > _MAX_QUERIES:
        raise ValueError("Shape inspection accepts at most 16 geometry queries.")

    faces = list(getattr(shape, "Faces", []) or [])
    edges = list(getattr(shape, "Edges", []) or [])
    query_results = [_empty_query_result(query) for query in queries]
    query_faces = any(str(query.get("element_type") or "") == "face" for query in queries)
    query_edges = any(str(query.get("element_type") or "") == "edge" for query in queries)

    face_details: list[dict[str, Any]] = []
    inspected_faces = len(faces) if query_faces else min(detail_limit, len(faces))
    for index, face in enumerate(faces[:inspected_faces], start=1):
        candidate_indexes = [
            query_index
            for query_index, query in enumerate(queries)
            if str(query.get("element_type") or "") == "face"
            and _face_query_may_match(face, query)
        ]
        if index > detail_limit and not candidate_indexes:
            continue
        facts = _face_facts(index, face)
        if index <= detail_limit:
            face_details.append(facts)
        for query_index in candidate_indexes:
            _record_query_match(query_results[query_index], queries[query_index], facts)

    edge_details: list[dict[str, Any]] = []
    inspected_edges = len(edges) if query_edges else min(detail_limit, len(edges))
    for index, edge in enumerate(edges[:inspected_edges], start=1):
        candidate_indexes = [
            query_index
            for query_index, query in enumerate(queries)
            if str(query.get("element_type") or "") == "edge"
            and _edge_query_may_match(edge, query)
        ]
        if index > detail_limit and not candidate_indexes:
            continue
        facts = _edge_facts(index, edge)
        if index <= detail_limit:
            edge_details.append(facts)
        for query_index in candidate_indexes:
            _record_query_match(query_results[query_index], queries[query_index], facts)

    for query, result in zip(queries, query_results):
        result["matches_truncated"] = int(result["matched_count"]) > len(result["matches"])
        if "expected_count" in query:
            expected = int(query["expected_count"])
            result["expected_count"] = expected
            result["cardinality_ok"] = int(result["matched_count"]) == expected

    bounds = _bounds_facts(shape)
    minimum = bounds["min"]
    maximum = bounds["max"]
    geometry: dict[str, Any] = {
        "analysis_level": analysis_level,
        "shape_type": str(getattr(shape, "ShapeType", "") or "Unknown"),
        "null": bool(getattr(shape, "isNull", lambda: False)()),
        "solids": _count(shape, "Solids"),
        "shells": _count(shape, "Shells"),
        "faces": len(faces),
        "wires": _count(shape, "Wires"),
        "edges": len(edges),
        "vertices": _count(shape, "Vertexes"),
        "bounds_center_mm": [
            (minimum[0] + maximum[0]) / 2.0,
            (minimum[1] + maximum[1]) / 2.0,
            (minimum[2] + maximum[2]) / 2.0,
        ],
        "bounds_mm": bounds,
        "face_details": face_details,
        "edge_details": edge_details,
        "query_results": query_results,
        "subelement_detail_limit": detail_limit,
        "subelement_details_truncated": len(faces) > detail_limit or len(edges) > detail_limit,
    }
    if analysis_level == "full":
        center = _point(getattr(shape, "CenterOfMass", None))
        geometry["valid"] = bool(getattr(shape, "isValid", lambda: False)())
        geometry["length_mm"] = float(getattr(shape, "Length", 0.0) or 0.0)
        geometry["area_mm2"] = float(getattr(shape, "Area", 0.0) or 0.0)
        geometry["volume_mm3"] = float(getattr(shape, "Volume", 0.0) or 0.0)
        geometry["center_of_mass_mm"] = center
    return {
        "ok": True,
        "operation": "inspect_brep",
        "geometry": geometry,
    }


def validate_brep(request: Mapping[str, Any]) -> dict[str, Any]:
    shape = _require_brep(request.get("shape"), noun="Shape validation")
    valid = bool(getattr(shape, "isValid", lambda: False)())
    return {
        "ok": True,
        "operation": "validate_brep",
        "valid": valid,
        "brep": {
            "valid": valid,
            "defects": [],
        },
        "bop": {
            "performed": False,
            "valid": None,
            "defects": [],
            "reason": (
                "BOP analysis is available only in the isolated geometry worker; "
                "the in-process fallback reports FreeCAD Part.isValid()."
            ),
        },
    }


def minimum_distance(request: Mapping[str, Any]) -> dict[str, Any]:
    first_spec = request.get("first")
    second_spec = request.get("second")
    first_format = _artifact_format(first_spec)
    second_format = _artifact_format(second_spec)
    if first_format != "brep" or second_format != "brep":
        raise ValueError(
            "In-process geometry fallback supports BREP distance only."
        )
    first = _require_brep(first_spec, noun="Distance")
    second = _require_brep(second_spec, noun="Distance")
    measured = first.distToShape(second)
    distance = float(measured[0])
    pairs: list[dict[str, list[float]]] = []
    raw_pairs = measured[1] if len(measured) > 1 else []
    for pair in list(raw_pairs or []):
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        first_point = _point(pair[0])
        second_point = _point(pair[1])
        if first_point is None or second_point is None:
            continue
        pairs.append({"first": first_point, "second": second_point})
    return {
        "ok": True,
        "fidelity": request.get("fidelity", "exact_brep"),
        "calculation": "in_process_part_dist_to_shape",
        "distance": distance,
        "closest_point_pairs": pairs,
        "first_shape": _shape_facts(first),
        "second_shape": _shape_facts(second),
    }


def _require_brep(spec: Any, *, noun: str) -> Any:
    if not isinstance(spec, Mapping) or _artifact_format(spec) != "brep":
        raise ValueError(f"{noun} requires a BREP artifact.")
    path = str(spec.get("path") or "")
    if not path:
        raise ValueError(f"{noun} requires a BREP artifact.")
    return load_brep(path)


def _artifact_format(spec: Any) -> str:
    if not isinstance(spec, Mapping):
        return ""
    return str(spec.get("format") or "").strip().lower()


def _count(shape: Any, attribute: str) -> int:
    return len(list(getattr(shape, attribute, []) or []))


def _point(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return [float(value[0]), float(value[1]), float(value[2])]
    try:
        return [float(value.x), float(value.y), float(value.z)]
    except (AttributeError, TypeError, ValueError):
        return None


def _bounds_facts(shape: Any) -> dict[str, list[float]]:
    bounds = shape.BoundBox
    return {
        "min": [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
        "max": [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
        "size": [float(bounds.XLength), float(bounds.YLength), float(bounds.ZLength)],
    }


def _geometry_type(value: Any) -> str:
    if value is None:
        return "Undefined"
    name = type(value).__name__
    return name.removeprefix("Part.") or "Undefined"


def _safe_geometry(owner: Any, attribute: str) -> Any:
    try:
        return getattr(owner, attribute)
    except (AttributeError, RuntimeError, TypeError):
        return None


def _origin(geometry: Any) -> list[float] | None:
    for name in ("Location", "Center", "Position"):
        point = _point(getattr(geometry, name, None))
        if point is not None:
            return point
    return None


def _axis_direction(geometry: Any) -> list[float] | None:
    for name in ("Axis", "Direction"):
        value = getattr(geometry, name, None)
        if value is None:
            continue
        direction = _point(getattr(value, "Direction", None))
        if direction is not None:
            return direction
        direction = _point(value)
        if direction is not None:
            return direction
    return None


def _x_direction(geometry: Any) -> list[float] | None:
    for name in ("XAxis", "XDirection"):
        point = _point(getattr(geometry, name, None))
        if point is not None:
            return point
    placement = getattr(geometry, "toPlacement", None)
    if not callable(placement):
        return None
    try:
        matrix = placement().toMatrix()
        return [float(matrix.A11), float(matrix.A21), float(matrix.A31)]
    except Exception:
        return None


def _face_facts(index: int, face: Any) -> dict[str, Any]:
    surface = _safe_geometry(face, "Surface")
    geometry_type = _geometry_type(surface)
    center = _point(getattr(face, "CenterOfMass", None))
    normal = None
    try:
        u_min, u_max, v_min, v_max = (float(value) for value in face.ParameterRange)
        normal = _point(face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0))
    except Exception:
        normal = None
    facts: dict[str, Any] = {
        "index": index,
        "geometry_type": geometry_type,
        "surface_type": geometry_type,
        "orientation": str(getattr(face, "Orientation", "") or "Unknown"),
        "area_mm2": float(getattr(face, "Area", 0.0) or 0.0),
        "center_mm": center,
        "bounds_mm": _bounds_facts(face),
        "edge_count": _count(face, "Edges"),
        "wire_count": _count(face, "Wires"),
        "normal": normal,
        "normal_at_center": normal,
    }
    origin = _origin(surface)
    axis = _axis_direction(surface)
    x_direction = _x_direction(surface)
    if origin is not None:
        facts["origin_mm"] = origin
    if axis is not None:
        facts["axis_direction"] = axis
    if x_direction is not None:
        facts["x_direction"] = x_direction
    radius = getattr(surface, "Radius", None)
    if radius is not None:
        facts["radius_mm"] = float(radius)
    reference_radius = getattr(surface, "RefRadius", None)
    if reference_radius is not None:
        facts["reference_radius_mm"] = float(reference_radius)
    semi_angle = getattr(surface, "SemiAngle", None)
    if semi_angle is not None:
        facts["semi_angle_degrees"] = float(semi_angle) * (180.0 / math.pi)
    major_radius = getattr(surface, "MajorRadius", None)
    if major_radius is not None:
        facts["major_radius_mm"] = float(major_radius)
    minor_radius = getattr(surface, "MinorRadius", None)
    if minor_radius is not None:
        facts["minor_radius_mm"] = float(minor_radius)
    return facts


def _edge_facts(index: int, edge: Any) -> dict[str, Any]:
    curve = _safe_geometry(edge, "Curve")
    geometry_type = _geometry_type(curve)
    endpoints: list[list[float]] = []
    for vertex in list(getattr(edge, "Vertexes", []) or [])[:2]:
        point = _point(getattr(vertex, "Point", None))
        if point is not None and point not in endpoints:
            endpoints.append(point)
    direction = None
    try:
        first, last = (float(value) for value in edge.ParameterRange)
        if math.isfinite(first) and math.isfinite(last):
            direction = _point(edge.tangentAt((first + last) / 2.0))
    except Exception:
        direction = None
    facts: dict[str, Any] = {
        "index": index,
        "geometry_type": geometry_type,
        "curve_type": geometry_type,
        "orientation": str(getattr(edge, "Orientation", "") or "Unknown"),
        "length_mm": float(getattr(edge, "Length", 0.0) or 0.0),
        "center_mm": _point(getattr(edge, "CenterOfMass", None)),
        "bounds_mm": _bounds_facts(edge),
        "endpoints_mm": endpoints,
        "closed": bool(getattr(edge, "isClosed", lambda: False)()),
        "direction": direction,
    }
    origin = _origin(curve)
    axis = _axis_direction(curve)
    x_direction = _x_direction(curve)
    if origin is not None:
        facts["origin_mm"] = origin
    if axis is not None:
        facts["axis_direction"] = axis
    if x_direction is not None:
        facts["x_direction"] = x_direction
    radius = getattr(curve, "Radius", None)
    if radius is not None:
        facts["radius_mm"] = float(radius)
    major_radius = getattr(curve, "MajorRadius", None)
    if major_radius is not None:
        facts["major_radius_mm"] = float(major_radius)
    minor_radius = getattr(curve, "MinorRadius", None)
    if minor_radius is not None:
        facts["minor_radius_mm"] = float(minor_radius)
    return facts


def _empty_query_result(query: Mapping[str, Any]) -> dict[str, Any]:
    element_type = str(query.get("element_type") or "")
    if element_type not in {"face", "edge"}:
        raise ValueError("A geometry query element_type must be face or edge.")
    return {
        "name": str(query.get("name") or ""),
        "element_type": element_type,
        "matched_count": 0,
        "matches": [],
    }


def _direction_matches(actual: Any, requested: Any, tolerance_degrees: float) -> bool:
    left = _point(actual)
    right = _point(requested)
    if left is None or right is None:
        return False
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm <= 1.0e-12 or right_norm <= 1.0e-12:
        return False
    cosine = max(
        -1.0,
        min(1.0, sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)),
    )
    return math.degrees(math.acos(cosine)) <= tolerance_degrees


def _numeric_range_matches(
    facts: Mapping[str, Any],
    query: Mapping[str, Any],
    fact_name: str,
    minimum_name: str,
    maximum_name: str,
) -> bool:
    if minimum_name not in query and maximum_name not in query:
        return True
    value = facts.get(fact_name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    number = float(value)
    if minimum_name in query and number < float(query[minimum_name]):
        return False
    if maximum_name in query and number > float(query[maximum_name]):
        return False
    return True


def _geometry_query_matches(facts: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    requested_type = str(query.get("geometry_type") or "").strip()
    if requested_type and str(facts.get("geometry_type") or "").lower() != requested_type.lower():
        return False
    angle_tolerance = float(query.get("angle_tolerance_degrees", 1.0))
    for field in ("normal", "direction", "axis_direction"):
        if field in query and not _direction_matches(facts.get(field), query[field], angle_tolerance):
            return False
    if "radius_mm" in query:
        radius = facts.get("radius_mm")
        if not isinstance(radius, (int, float)) or isinstance(radius, bool):
            return False
        tolerance = float(query.get("radius_tolerance_mm", 1.0e-6))
        if abs(float(radius) - float(query["radius_mm"])) > tolerance:
            return False
    if not _numeric_range_matches(facts, query, "area_mm2", "min_area_mm2", "max_area_mm2"):
        return False
    if not _numeric_range_matches(facts, query, "length_mm", "min_length_mm", "max_length_mm"):
        return False
    if "near_point_mm" in query:
        center = facts.get("center_mm")
        point = query.get("near_point_mm")
        if not isinstance(center, list) or len(center) != 3:
            return False
        if not isinstance(point, list) or len(point) != 3:
            return False
        maximum = float(query.get("max_distance_mm", 1.0e-6))
        if math.dist(center, point) > maximum:
            return False
    return True


def _center_distance_bounds_may_match(shape: Any, query: Mapping[str, Any]) -> bool:
    if "near_point_mm" not in query:
        return True
    point = query.get("near_point_mm")
    if not isinstance(point, list) or len(point) != 3:
        return False
    bounds = _bounds_facts(shape)
    distance_squared = 0.0
    for index, coordinate in enumerate(point):
        nearest = min(max(float(coordinate), bounds["min"][index]), bounds["max"][index])
        delta = float(coordinate) - nearest
        distance_squared += delta * delta
    maximum = float(query.get("max_distance_mm", 1.0e-6))
    return distance_squared <= maximum * maximum


def _radius_matches(actual: float, query: Mapping[str, Any]) -> bool:
    if "radius_mm" not in query:
        return True
    tolerance = float(query.get("radius_tolerance_mm", 1.0e-6))
    return abs(float(actual) - float(query["radius_mm"])) <= tolerance


def _face_query_may_match(face: Any, query: Mapping[str, Any]) -> bool:
    surface = _safe_geometry(face, "Surface")
    geometry_type = _geometry_type(surface)
    requested_type = str(query.get("geometry_type") or "").strip()
    if requested_type and geometry_type.lower() != requested_type.lower():
        return False
    if "radius_mm" in query:
        radius = getattr(surface, "Radius", None)
        if radius is None or geometry_type not in {"Cylinder", "Sphere"}:
            return False
        if not _radius_matches(float(radius), query):
            return False
    return _center_distance_bounds_may_match(face, query)


def _edge_query_may_match(edge: Any, query: Mapping[str, Any]) -> bool:
    curve = _safe_geometry(edge, "Curve")
    geometry_type = _geometry_type(curve)
    requested_type = str(query.get("geometry_type") or "").strip()
    if requested_type and geometry_type.lower() != requested_type.lower():
        return False
    if "radius_mm" in query:
        radius = getattr(curve, "Radius", None)
        if geometry_type != "Circle" or radius is None or not _radius_matches(float(radius), query):
            return False
    return _center_distance_bounds_may_match(edge, query)


def _record_query_match(
    result: dict[str, Any],
    query: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> None:
    if not _geometry_query_matches(facts, query):
        return
    result["matched_count"] = int(result.get("matched_count") or 0) + 1
    limit = max(1, min(int(query.get("max_results") or _MAX_QUERY_RESULTS), _MAX_QUERY_RESULTS))
    if len(result["matches"]) < limit:
        result["matches"].append(dict(facts))


def _shape_facts(shape: Any) -> dict[str, Any]:
    bounds = _bounds_facts(shape)
    return {
        "valid": bool(getattr(shape, "isValid", lambda: False)()),
        "solids": _count(shape, "Solids"),
        "faces": _count(shape, "Faces"),
        "edges": _count(shape, "Edges"),
        "vertices": _count(shape, "Vertexes"),
        "volume_mm3": float(getattr(shape, "Volume", 0.0) or 0.0),
        "area_mm2": float(getattr(shape, "Area", 0.0) or 0.0),
        "bbox": {"min": bounds["min"], "max": bounds["max"]},
    }
