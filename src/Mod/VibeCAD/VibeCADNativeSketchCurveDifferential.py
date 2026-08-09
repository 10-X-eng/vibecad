# SPDX-License-Identifier: LGPL-2.1-or-later

"""Finite exact-point and tangent reads shared by Sketch angle constraints."""

from __future__ import annotations

import math
from typing import Any

from VibeCADNativeSketchConstraintTargets import (
    SketchConstraintElement,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchErrors import NativeSketchError


LINEAR_TOLERANCE = 1.0e-7
ANGULAR_TOLERANCE_RADIANS = 1.0e-7
LINE_TYPE = "Part::GeomLineSegment"
CIRCULAR_TYPES = frozenset({"Part::GeomCircle", "Part::GeomArcOfCircle"})


def point_coordinates(
    sketch: Any,
    element: SketchConstraintElement,
    *,
    label: str,
    role: str,
) -> tuple[Any, tuple[float, float]]:
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{label} point lookup is unavailable.")
    try:
        value = getter(element.geometry_index, element.position_code)
        result = float(value.x), float(value.y)
    except Exception as exc:
        raise NativeSketchError(f"{label} {role} is unavailable.") from exc
    if not all(math.isfinite(item) for item in result):
        raise NativeSketchError(f"{label} {role} is not finite.")
    return value, result


def line_delta(
    sketch: Any,
    element: SketchConstraintElement,
    *,
    label: str,
) -> tuple[float, float]:
    if element.position != "whole":
        raise NativeSketchError(f"{label} line measurement requires a whole line.")
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    if str(getattr(geometry, "TypeId", "") or "") != LINE_TYPE:
        raise NativeSketchError(f"{label} line measurement target is not straight.")
    start = SketchConstraintElement(element.geometry_index, "start")
    end = SketchConstraintElement(element.geometry_index, "end")
    _start_host, start_value = point_coordinates(
        sketch, start, label=label, role="line start"
    )
    _end_host, end_value = point_coordinates(
        sketch, end, label=label, role="line end"
    )
    delta = end_value[0] - start_value[0], end_value[1] - start_value[1]
    if math.hypot(*delta) <= LINEAR_TOLERANCE:
        raise NativeSketchError(f"{label} cannot use a zero-length line.")
    return delta


def _axes(geometry: Any, *, label: str) -> tuple[tuple[float, float], ...]:
    axis = getattr(geometry, "XAxis", None)
    if axis is None:
        return (1.0, 0.0), (0.0, 1.0)
    major = float(axis.x), float(axis.y)
    length = math.hypot(*major)
    if not math.isfinite(length) or length <= LINEAR_TOLERANCE:
        raise NativeSketchError(f"{label} curve axis is unavailable.")
    major = major[0] / length, major[1] / length
    return major, (-major[1], major[0])


def _fallback_tangent(
    geometry: Any,
    point: tuple[float, float],
    element: SketchConstraintElement,
    *,
    label: str,
) -> tuple[float, float]:
    geometry_type = str(getattr(geometry, "TypeId", "") or "")
    if geometry_type == LINE_TYPE:
        start = geometry.StartPoint
        end = geometry.EndPoint
        return float(end.x) - float(start.x), float(end.y) - float(start.y)
    center = getattr(geometry, "Center", None)
    if geometry_type in CIRCULAR_TYPES and center is not None:
        radial = point[0] - float(center.x), point[1] - float(center.y)
        return -radial[1], radial[0]
    major_axis, minor_axis = _axes(geometry, label=label)
    if center is not None:
        local_x = (
            (point[0] - float(center.x)) * major_axis[0]
            + (point[1] - float(center.y)) * major_axis[1]
        )
        local_y = (
            (point[0] - float(center.x)) * minor_axis[0]
            + (point[1] - float(center.y)) * minor_axis[1]
        )
    else:
        local_x = local_y = 0.0
    if geometry_type in {"Part::GeomEllipse", "Part::GeomArcOfEllipse"}:
        major = float(geometry.MajorRadius)
        minor = float(geometry.MinorRadius)
        parameter = math.atan2(local_y / minor, local_x / major)
        tangent_major = -major * math.sin(parameter)
        tangent_minor = minor * math.cos(parameter)
    elif geometry_type == "Part::GeomArcOfHyperbola":
        major = float(geometry.MajorRadius)
        minor = float(geometry.MinorRadius)
        parameter = math.asinh(local_y / minor)
        tangent_major = major * math.sinh(parameter)
        tangent_minor = minor * math.cosh(parameter)
    elif geometry_type == "Part::GeomArcOfParabola":
        focal = float(geometry.Focal)
        tangent_major = local_y / (2.0 * focal)
        tangent_minor = 1.0
    elif geometry_type == "Part::GeomBSplineCurve":
        if element.position == "start":
            parameter = float(geometry.FirstParameter)
        elif element.position == "end":
            parameter = float(geometry.LastParameter)
        else:
            raise NativeSketchError(
                f"{label} B-spline tangent parameter is unavailable."
            )
        span = max(
            1.0e-7,
            abs(float(geometry.LastParameter) - float(geometry.FirstParameter))
            * 1.0e-6,
        )
        first_parameter = max(float(geometry.FirstParameter), parameter - span)
        second_parameter = min(float(geometry.LastParameter), parameter + span)
        first = geometry.value(first_parameter)
        second = geometry.value(second_parameter)
        return float(second.x) - float(first.x), float(second.y) - float(first.y)
    else:
        raise NativeSketchError(
            f"{label} cannot measure the selected curve tangent."
        )
    return (
        tangent_major * major_axis[0] + tangent_minor * minor_axis[0],
        tangent_major * major_axis[1] + tangent_minor * minor_axis[1],
    )


def curve_tangent(
    sketch: Any,
    curve: SketchConstraintElement,
    point_element: SketchConstraintElement,
    *,
    label: str,
    role: str,
) -> tuple[float, float]:
    host_point, point = point_coordinates(
        sketch, point_element, label=label, role=role
    )
    geometry = sketch_constraint_geometry(sketch, curve.geometry_index)
    parameter = getattr(geometry, "parameter", None)
    tangent = getattr(geometry, "tangent", None)
    if callable(parameter) and callable(tangent):
        try:
            raw = tangent(parameter(host_point))
            vector = raw[0] if isinstance(raw, (list, tuple)) else raw
            result = float(vector.x), float(vector.y)
        except Exception as exc:
            raise NativeSketchError(f"{label} {role} tangent is unavailable.") from exc
    else:
        result = _fallback_tangent(
            geometry, point, point_element, label=label
        )
    if (
        not all(math.isfinite(item) for item in result)
        or math.hypot(*result) <= LINEAR_TOLERANCE
    ):
        raise NativeSketchError(f"{label} {role} tangent is degenerate.")
    return result


def nearest_parallel_error_degrees(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    denominator = math.hypot(*first) * math.hypot(*second)
    sine = abs(first[0] * second[1] - first[1] * second[0]) / denominator
    return math.degrees(math.asin(min(1.0, max(0.0, sine))))


def nearest_perpendicular_error_degrees(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    denominator = math.hypot(*first) * math.hypot(*second)
    cosine = abs(first[0] * second[0] + first[1] * second[1]) / denominator
    return math.degrees(math.asin(min(1.0, max(0.0, cosine))))
