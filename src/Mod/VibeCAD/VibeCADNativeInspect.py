# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact element, inspection-result, and geometry-validity reads."""

from __future__ import annotations

import math
from statistics import fmean
from typing import Any

from VibeCADNativeTargets import (
    NativeElementRef,
    NativeObjectRef,
    object_reference,
    resolve_element,
    resolve_object,
)


MAX_INSPECTION_DISTANCES = 4096
MAX_INSPECTION_ELEMENTS = 256


class NativeInspectError(RuntimeError):
    def failure(self) -> dict[str, str]:
        return {"error_code": "NATIVE_INSPECT_FAILED", "message": str(self)}


def _vector(value: Any) -> list[float]:
    try:
        return [float(value.x), float(value.y), float(value.z)]
    except Exception as exc:
        raise NativeInspectError("An inspected vector is unavailable.") from exc


def inspect_element(document: Any, target: NativeElementRef) -> dict[str, Any]:
    _obj, element = resolve_element(document, target)
    shape_type = str(getattr(element, "ShapeType", "") or "")
    result: dict[str, Any] = {
        "target": target.summary(),
        "shape_type": shape_type,
    }
    if shape_type == "Vertex":
        result["point_mm"] = _vector(element.Point)
    elif shape_type == "Edge":
        result["length_mm"] = float(element.Length)
        result["curve_type"] = type(getattr(element, "Curve", None)).__name__
        radius = getattr(getattr(element, "Curve", None), "Radius", None)
        if radius is not None:
            result["radius_mm"] = float(radius)
        vertices = list(getattr(element, "Vertexes", []) or [])
        if vertices:
            result["endpoints_mm"] = [_vector(value.Point) for value in vertices[:2]]
    elif shape_type == "Face":
        result["area_mm2"] = float(element.Area)
        result["surface_type"] = type(getattr(element, "Surface", None)).__name__
        result["center_mm"] = _vector(element.CenterOfMass)
        try:
            u_min, u_max, v_min, v_max = element.ParameterRange
            result["normal"] = _vector(
                element.normalAt(
                    0.5 * (float(u_min) + float(u_max)),
                    0.5 * (float(v_min) + float(v_max)),
                )
            )
        except Exception:
            pass
    else:
        if hasattr(element, "Length"):
            result["length_mm"] = float(element.Length)
        if hasattr(element, "Area"):
            result["area_mm2"] = float(element.Area)
        if hasattr(element, "Volume"):
            result["volume_mm3"] = float(element.Volume)
    return result


def geometry_validity(document: Any, target: NativeObjectRef) -> dict[str, Any]:
    obj = resolve_object(document, target)
    shape = getattr(obj, "Shape", None)
    if shape is None:
        raise NativeInspectError("The exact object has no Part shape to validate.")
    try:
        is_null = bool(shape.isNull())
        is_valid = False if is_null else bool(shape.isValid())
    except Exception as exc:
        raise NativeInspectError("The exact object's shape validity is unreadable.") from exc
    state = sorted(str(value) for value in list(getattr(obj, "State", []) or []))
    return {
        "target": target.summary(),
        "valid": is_valid,
        "is_null": is_null,
        "shape_counts": {
            "solids": len(list(getattr(shape, "Solids", []) or [])),
            "shells": len(list(getattr(shape, "Shells", []) or [])),
            "faces": len(list(getattr(shape, "Faces", []) or [])),
            "edges": len(list(getattr(shape, "Edges", []) or [])),
            "vertices": len(list(getattr(shape, "Vertexes", []) or [])),
        },
        "object_state": state[:16],
    }


def _distance_values(feature: Any) -> list[float]:
    raw = getattr(feature, "Distances", None)
    if raw is None:
        return []
    try:
        values = list(raw)
    except Exception as exc:
        raise NativeInspectError("Inspection distances are unreadable.") from exc
    if len(values) > MAX_INSPECTION_DISTANCES:
        raise NativeInspectError("Inspection distances exceed the bounded read size.")
    result = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def visual_inspection_result(
    document: Any,
    target: NativeObjectRef,
) -> dict[str, Any]:
    feature = resolve_object(
        document,
        target,
        expected_types=("Inspection::Feature",),
    )
    distances = _distance_values(feature)
    actual = getattr(feature, "Actual", None)
    nominals = list(getattr(feature, "Nominals", []) or [])
    result: dict[str, Any] = {
        "target": target.summary(),
        "distance_count": len(distances),
        "actual": object_reference(actual) if actual is not None else None,
        "nominals": [object_reference(value) for value in nominals[:32]],
    }
    if distances:
        result["distance_statistics_mm"] = {
            "minimum": min(distances),
            "maximum": max(distances),
            "mean": fmean(distances),
            "maximum_absolute": max(abs(value) for value in distances),
        }
    return result
