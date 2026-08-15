# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact geometric selection used by durable VibeScript reference rebinding."""

from __future__ import annotations

import math
from typing import Any

_GEOMETRY_ALIASES = {
    "plane": "plane",
    "planar": "plane",
    "flat": "plane",
    "cylinder": "cylinder",
    "cylindrical": "cylinder",
    "cone": "cone",
    "conical": "cone",
    "sphere": "sphere",
    "spherical": "sphere",
    "torus": "toroid",
    "toroid": "toroid",
    "toroidal": "toroid",
    "bspline": "bspline",
    "nurbs": "bspline",
    "freeform": "bspline",
    "line": "line",
    "linear": "line",
    "straight": "line",
    "circle": "circle",
    "circular": "circle",
    "arc": "circle",
    "ellipse": "ellipse",
    "elliptical": "ellipse",
}

_KNOWN_GEOMETRY_PREFIXES = (
    "plane",
    "cylinder",
    "cone",
    "sphere",
    "toroid",
    "line",
    "circle",
    "ellipse",
)


def _canonical_geometry_type(class_name: str) -> str:
    lowered = str(class_name or "").lower()
    if "bspline" in lowered:
        return "bspline"
    for known in _KNOWN_GEOMETRY_PREFIXES:
        if lowered.startswith(known):
            return known
    return lowered


def _requested_geometry_type(value: str) -> str | None:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return None
    return _GEOMETRY_ALIASES.get(lowered, lowered)


def _vector_dict(vector: Any) -> dict[str, float]:
    return {
        "x": round(float(vector.x), 6),
        "y": round(float(vector.y), 6),
        "z": round(float(vector.z), 6),
    }


def _bounding_box_dict(bound_box: Any) -> dict[str, float]:
    return {
        "x_min": round(float(bound_box.XMin), 6),
        "x_max": round(float(bound_box.XMax), 6),
        "y_min": round(float(bound_box.YMin), 6),
        "y_max": round(float(bound_box.YMax), 6),
        "z_min": round(float(bound_box.ZMin), 6),
        "z_max": round(float(bound_box.ZMax), 6),
    }


def _surface_normal(face: Any) -> tuple[Any | None, dict[str, Any] | None]:
    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
        normal = face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
    except Exception as exc:
        return None, {
            "native_stage": "TopoDS_Face::normalAt",
            "native_error": str(exc),
        }
    if float(normal.Length) <= 1e-9:
        return None, {
            "native_stage": "TopoDS_Face::normalAt",
            "native_error": "The evaluated face normal has zero length.",
        }
    return normal.normalize(), None


def _outward_normal(shape: Any, face: Any) -> tuple[Any | None, dict[str, Any] | None]:
    """Return a geometrically verified outward normal for a planar solid face."""
    normal, diagnostic = _surface_normal(face)
    if normal is None:
        return None, diagnostic
    try:
        if float(getattr(shape, "Volume", 0.0) or 0.0) <= 0.0:
            return None, {
                "native_stage": "outward_normal_classification",
                "native_error": "The source shape has no positive solid volume.",
            }
        diagonal = float(shape.BoundBox.DiagonalLength)
        offset = max(diagonal * 1e-3, 1e-4)
        probe = face.CenterOfMass.add(
            type(normal)(normal.x * offset, normal.y * offset, normal.z * offset)
        )
        if shape.isInside(probe, offset * 0.1, False):
            return normal.multiply(-1.0), None
    except Exception as exc:
        return None, {
            "native_stage": "TopoShape::isInside",
            "native_error": str(exc),
        }
    return normal, None


def _element_radius(geometry: Any) -> float | None:
    radius = getattr(geometry, "Radius", None)
    if radius is None:
        return None
    return float(radius)


def _find_subelements(
    service: Any,
    object_name: str = "",
    element_type: str = "",
    geometry_type: str | None = None,
    normal: dict[str, Any] | None = None,
    normal_tolerance_degrees: float = 5.0,
    direction: dict[str, Any] | None = None,
    direction_tolerance_degrees: float = 5.0,
    radius: float | None = None,
    radius_tolerance: float = 0.01,
    min_area: float | None = None,
    max_area: float | None = None,
    min_length: float | None = None,
    max_length: float | None = None,
    near_point: dict[str, Any] | None = None,
    max_distance: float = 1.0,
) -> dict[str, Any]:
    import FreeCAD as App

    kind = str(element_type or "face").strip().lower()
    if kind not in {"face", "edge"}:
        return {
            "ok": False,
            "found": False,
            "error": "element_type must be 'face' or 'edge'.",
            "requested_element_type": element_type,
        }
    doc = service._active_document()
    obj = doc.getObject(str(object_name)) if doc is not None else None
    if obj is None:
        candidates = [
            service._document_object_summary(candidate)
            for candidate in list(getattr(doc, "Objects", []) or [])
            if getattr(candidate, "Shape", None) is not None
            and not bool(getattr(candidate.Shape, "isNull", lambda: True)())
        ]
        return {
            "ok": False,
            "found": False,
            "error": f"Object not found by exact internal name: {object_name}",
            "candidates": candidates,
        }
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return {
            "ok": False,
            "found": False,
            "error": f"Object has no shape geometry: {object_name}",
        }

    requested_type = _requested_geometry_type(geometry_type or "")
    range_error = _validate_ranges(
        kind,
        min_area=min_area,
        max_area=max_area,
        min_length=min_length,
        max_length=max_length,
        normal_tolerance_degrees=normal_tolerance_degrees,
        direction_tolerance_degrees=direction_tolerance_degrees,
        radius_tolerance=radius_tolerance,
        max_distance=max_distance,
    )
    if range_error is not None:
        return range_error
    wanted_normal = None
    if normal is not None:
        wanted_normal = App.Vector(
            float(normal["x"]),
            float(normal["y"]),
            float(normal["z"]),
        )
        if float(wanted_normal.Length) <= 1e-9:
            return {"ok": False, "found": False, "error": "normal must be a non-zero direction."}
        wanted_normal.normalize()
    wanted_direction = None
    if direction is not None:
        if kind != "edge":
            return {
                "ok": False,
                "found": False,
                "error": "direction can only filter edges.",
            }
        wanted_direction = App.Vector(
            float(direction["x"]),
            float(direction["y"]),
            float(direction["z"]),
        )
        if float(wanted_direction.Length) <= 1e-9:
            return {
                "ok": False,
                "found": False,
                "error": "direction must be a non-zero vector.",
            }
        wanted_direction.normalize()
    target_point = None
    if near_point is not None:
        target_point = App.Vector(
            float(near_point["x"]),
            float(near_point["y"]),
            float(near_point["z"]),
        )
    cos_tolerance = math.cos(math.radians(float(normal_tolerance_degrees)))
    direction_cos_tolerance = math.cos(
        math.radians(float(direction_tolerance_degrees))
    )

    if kind == "face":
        elements = list(getattr(shape, "Faces", []) or [])
        name_prefix = "Face"
    else:
        elements = list(getattr(shape, "Edges", []) or [])
        name_prefix = "Edge"

    matches: list[dict[str, Any]] = []
    distance_errors: list[dict[str, Any]] = []
    geometry_errors: list[dict[str, Any]] = []
    target_vertex = None
    if target_point is not None:
        import Part

        target_vertex = Part.Vertex(target_point)
    for index, element in enumerate(elements):
        if kind == "face":
            geometry = getattr(element, "Surface", None)
            measure = float(getattr(element, "Area", 0.0) or 0.0)
        else:
            geometry = getattr(element, "Curve", None)
            measure = float(getattr(element, "Length", 0.0) or 0.0)
        canonical = _canonical_geometry_type(type(geometry).__name__ if geometry else "")
        if requested_type is not None and canonical != requested_type:
            continue
        if kind == "face":
            if min_area is not None and measure < float(min_area):
                continue
            if max_area is not None and measure > float(max_area):
                continue
        else:
            if min_length is not None and measure < float(min_length):
                continue
            if max_length is not None and measure > float(max_length):
                continue
        element_radius = _element_radius(geometry)
        if radius is not None:
            if element_radius is None:
                continue
            if abs(element_radius - float(radius)) > float(radius_tolerance):
                continue
        outward = None
        edge_direction = None
        if kind == "face" and canonical == "plane":
            outward, normal_error = _outward_normal(shape, element)
            if normal_error:
                geometry_errors.append(
                    {
                        "name": f"{name_prefix}{index + 1}",
                        "field": "outward_normal",
                        "required_by_filter": wanted_normal is not None,
                        **normal_error,
                    }
                )
        if kind == "edge" and canonical == "line":
            try:
                edge_direction = element.tangentAt(element.FirstParameter)
                if float(edge_direction.Length) > 1e-9:
                    edge_direction.normalize()
                else:
                    edge_direction = None
            except Exception as exc:
                edge_direction = None
                geometry_errors.append(
                    {
                        "name": f"{name_prefix}{index + 1}",
                        "field": "direction",
                        "required_by_filter": wanted_direction is not None,
                        "native_stage": "TopoDS_Edge::tangentAt",
                        "native_error": str(exc),
                    }
                )
        if wanted_normal is not None:
            if outward is None:
                continue
            if float(outward.dot(wanted_normal)) < cos_tolerance:
                continue
        if wanted_direction is not None:
            if edge_direction is None:
                continue
            if abs(float(edge_direction.dot(wanted_direction))) < direction_cos_tolerance:
                continue
        center = element.CenterOfMass
        nearest_distance = None
        closest_points = None
        if target_point is not None:
            try:
                nearest_distance, point_pairs, _support = element.distToShape(
                    target_vertex
                )
                nearest_distance = float(nearest_distance)
                closest_points = [
                    {
                        "subelement_point": _vector_dict(pair[0]),
                        "query_point": _vector_dict(pair[1]),
                    }
                    for pair in list(point_pairs or [])[:4]
                ]
            except Exception as exc:
                distance_errors.append(
                    {
                        "name": f"{name_prefix}{index + 1}",
                        "error": str(exc),
                    }
                )
                continue
            if nearest_distance > float(max_distance):
                continue
        entry: dict[str, Any] = {
            "name": f"{name_prefix}{index + 1}",
            "geometry_type": canonical,
            "center_of_mass": _vector_dict(center),
            "bounding_box": _bounding_box_dict(element.BoundBox),
        }
        if kind == "face":
            entry["area"] = round(measure, 6)
        else:
            entry["length"] = round(measure, 6)
        if outward is not None:
            entry["outward_normal"] = _vector_dict(outward)
        if edge_direction is not None:
            entry["direction"] = _vector_dict(edge_direction)
        if element_radius is not None:
            entry["radius"] = round(element_radius, 6)
        if nearest_distance is not None:
            entry["closest_distance"] = nearest_distance
            entry["closest_points"] = closest_points or []
        matches.append(entry)

    if distance_errors:
        return {
            "ok": False,
            "found": True,
            "failure_code": "SUBELEMENT_DISTANCE_FAILED",
            "failure_stage": "native_call",
            "error": "Native closest-distance evaluation failed for one or more subelements.",
            "object": service._document_object_summary(obj),
            "distance_errors": distance_errors,
            "partial_matches": matches,
        }

    required_geometry_errors = [
        item for item in geometry_errors if item.get("required_by_filter")
    ]
    if required_geometry_errors:
        return {
            "ok": False,
            "found": True,
            "failure_code": "SUBELEMENT_GEOMETRY_INSPECTION_FAILED",
            "failure_stage": "native_call",
            "error": (
                "Native geometry inspection failed for one or more subelements "
                "required by the requested filter."
            ),
            "object": service._document_object_summary(obj),
            "geometry_errors": required_geometry_errors,
            "partial_matches": matches,
        }

    return {
        "ok": True,
        "found": True,
        "object": service._document_object_summary(obj),
        "element_type": kind,
        "total_elements": len(elements),
        "match_count": len(matches),
        "matches": matches,
        "inspection_complete": not geometry_errors,
        "geometry_errors": geometry_errors,
        "filters": {
            "geometry_type": requested_type,
            "normal": _vector_dict(wanted_normal) if wanted_normal is not None else None,
            "direction": (
                _vector_dict(wanted_direction) if wanted_direction is not None else None
            ),
            "radius": float(radius) if radius is not None else None,
            "near_point": _vector_dict(target_point) if target_point is not None else None,
            "near_point_metric": "native_closest_distance",
        },
    }


def _validate_ranges(
    kind: str,
    *,
    min_area: float | None,
    max_area: float | None,
    min_length: float | None,
    max_length: float | None,
    normal_tolerance_degrees: float,
    direction_tolerance_degrees: float,
    radius_tolerance: float,
    max_distance: float,
) -> dict[str, Any] | None:
    if not 0.0 <= float(normal_tolerance_degrees) <= 180.0:
        return {"ok": False, "error": "normal_tolerance_degrees must be between 0 and 180."}
    if not 0.0 <= float(direction_tolerance_degrees) <= 180.0:
        return {"ok": False, "error": "direction_tolerance_degrees must be between 0 and 180."}
    if float(radius_tolerance) < 0.0 or float(max_distance) < 0.0:
        return {"ok": False, "error": "radius_tolerance and max_distance must be non-negative."}
    if kind == "face" and (min_length is not None or max_length is not None):
        return {
            "ok": False,
            "error": (
                "min_length/max_length filter edge length and apply only to "
                "element_type='edge'. For faces use min_area/max_area, or pass "
                "null to leave the length filter unset."
            ),
        }
    if kind == "edge" and (min_area is not None or max_area is not None):
        return {
            "ok": False,
            "error": (
                "min_area/max_area filter face area and apply only to "
                "element_type='face'. For edges use min_length/max_length, or "
                "pass null to leave the area filter unset."
            ),
        }
    if min_area is not None and max_area is not None and float(min_area) > float(max_area):
        return {"ok": False, "error": "min_area cannot exceed max_area."}
    if min_length is not None and max_length is not None and float(min_length) > float(max_length):
        return {"ok": False, "error": "min_length cannot exceed max_length."}
    return None


def resolve_selection(
    service: Any,
    base: Any,
    selection: Any,
    *,
    allow_all_edges: bool,
    face_only: bool,
    edge_only: bool = False,
) -> dict[str, Any]:
    if not isinstance(selection, dict):
        return _invalid("selection must be an object.")
    mode = str(selection.get("type") or "")
    if mode == "all_edges":
        if not allow_all_edges:
            return _invalid("all_edges is not valid for this operation.")
        all_edges = _find_subelements(
            service,
            object_name=base.Name,
            element_type="edge",
        )
        if not all_edges.get("ok"):
            return _invalid(all_edges.get("error") or "Could not inspect base edges.")
        if int(all_edges.get("match_count", 0)) == 0:
            return _invalid(f"Base feature {base.Name} has no edges.")
        return {
            "ok": True,
            "mode": mode,
            "subelements": [],
            "resolved_geometry": all_edges["matches"],
            "use_all_edges": True,
            "request": dict(selection),
        }
    if mode == "exact":
        names = selection.get("subelements")
        if not isinstance(names, list) or not names:
            return _invalid("selection.subelements must contain at least one name.")
        names = [str(value or "").strip() for value in names]
        if len(set(names)) != len(names):
            return _invalid("selection.subelements cannot contain duplicates.")
        if face_only and any(not name.startswith("Face") for name in names):
            return _invalid("This operation requires face subelements.")
        if edge_only and any(not name.startswith("Edge") for name in names):
            return _invalid("This operation requires edge subelements.")
        summaries = _exact_summaries(service, base, names)
        if not summaries.get("ok"):
            return summaries
        return {
            "ok": True,
            "mode": mode,
            "subelements": names,
            "resolved_geometry": summaries["matches"],
            "use_all_edges": False,
            "request": dict(selection),
        }
    if mode == "query":
        kind = str(selection.get("element_type") or "")
        if (
            kind not in {"edge", "face"}
            or face_only and kind != "face"
            or edge_only and kind != "edge"
        ):
            return _invalid("selection.element_type is not valid for this operation.")
        expected = selection.get("expected_count")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            return _invalid("selection.expected_count must be an integer of at least 1.")
        filters = {
            key: value
            for key, value in selection.items()
            if key not in {"type", "element_type", "expected_count"}
        }
        result = _find_subelements(
            service,
            object_name=base.Name,
            element_type=kind,
            **filters,
        )
        if not result.get("ok"):
            return _invalid(result.get("error") or "Geometric selection query failed.")
        actual = int(result.get("match_count", 0))
        if actual != expected:
            return _invalid(
                "Geometric selection did not return the required number of subelements; no feature was created.",
                expected_count=expected,
                actual_count=actual,
                matches=result.get("matches") or [],
                filters=result.get("filters") or {},
            )
        return {
            "ok": True,
            "mode": mode,
            "subelements": [item["name"] for item in result["matches"]],
            "resolved_geometry": result["matches"],
            "use_all_edges": False,
            "request": dict(selection),
        }
    return _invalid("selection.type must be exact, query, or all_edges where supported.")


def _exact_summaries(service: Any, base: Any, names: list[str]) -> dict[str, Any]:
    by_name = {}
    for kind in {"face" if name.startswith("Face") else "edge" for name in names}:
        result = _find_subelements(
            service,
            object_name=base.Name,
            element_type=kind,
        )
        if not result.get("ok"):
            return _invalid(result.get("error") or f"Could not inspect {kind} geometry.")
        by_name.update({item["name"]: item for item in result["matches"]})
    missing = [name for name in names if name not in by_name]
    if missing:
        return _invalid(
            f"Subelements do not exist on {base.Name}: {', '.join(missing)}",
            available_subelements=sorted(by_name),
        )
    return {"ok": True, "matches": [by_name[name] for name in names]}


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
