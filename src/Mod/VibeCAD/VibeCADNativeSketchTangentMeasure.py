# SPDX-License-Identifier: LGPL-2.1-or-later

"""Geometric postconditions for exact Native Sketch Tangent forms."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from VibeCADNativeSketchConstraintTargets import (
    SketchConstraintElement,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchCurveDifferential import (
    ANGULAR_TOLERANCE_RADIANS,
    CIRCULAR_TYPES,
    LINEAR_TOLERANCE,
    LINE_TYPE,
    curve_tangent,
    line_delta,
    nearest_parallel_error_degrees,
    point_coordinates,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTangentTarget import LABEL, ResolvedSketchTangent


@dataclass(frozen=True, slots=True)
class TangentMeasurement:
    name: str
    value: float
    unit: str

    def record(self) -> dict[str, float | str]:
        return {self.name: self.value, "unit": self.unit}

    def satisfied(self) -> bool:
        tolerance = (
            math.degrees(ANGULAR_TOLERANCE_RADIANS)
            if self.unit == "deg"
            else LINEAR_TOLERANCE
        )
        return self.value <= tolerance


def _type(sketch: Any, element: SketchConstraintElement) -> str:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    return str(getattr(geometry, "TypeId", "") or "")


def _angular_error(
    first: tuple[float, float],
    second: tuple[float, float],
) -> TangentMeasurement:
    return TangentMeasurement(
        "angular_error",
        nearest_parallel_error_degrees(first, second),
        "deg",
    )


def _circle_data(
    sketch: Any,
    element: SketchConstraintElement,
) -> tuple[tuple[float, float], float]:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    center = getattr(geometry, "Center", getattr(geometry, "Location", None))
    try:
        result = (float(center.x), float(center.y)), float(geometry.Radius)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} circular geometry is unavailable.") from exc
    if (
        not all(math.isfinite(value) for value in (*result[0], result[1]))
        or result[1] <= LINEAR_TOLERANCE
    ):
        raise NativeSketchError(f"{LABEL} circular geometry is degenerate.")
    return result


def _line_circle_error(
    sketch: Any,
    line: SketchConstraintElement,
    circle: SketchConstraintElement,
) -> TangentMeasurement:
    delta = line_delta(sketch, line, label=LABEL)
    start = SketchConstraintElement(line.geometry_index, "start")
    _host, start_value = point_coordinates(
        sketch, start, label=LABEL, role="line start"
    )
    center, radius = _circle_data(sketch, circle)
    distance = abs(
        delta[0] * (center[1] - start_value[1])
        - delta[1] * (center[0] - start_value[0])
    ) / math.hypot(*delta)
    return TangentMeasurement("tangency_error", abs(distance - radius), "mm")


def _circle_circle_error(
    sketch: Any,
    first: SketchConstraintElement,
    second: SketchConstraintElement,
) -> TangentMeasurement:
    first_center, first_radius = _circle_data(sketch, first)
    second_center, second_radius = _circle_data(sketch, second)
    center_distance = math.hypot(
        second_center[0] - first_center[0],
        second_center[1] - first_center[1],
    )
    external_error = abs(center_distance - first_radius - second_radius)
    internal_error = abs(center_distance - abs(first_radius - second_radius))
    return TangentMeasurement(
        "tangency_error", min(external_error, internal_error), "mm"
    )


def measure_sketch_tangent(
    sketch: Any,
    resolved: ResolvedSketchTangent,
) -> TangentMeasurement:
    references = resolved.references
    form = resolved.semantic_form
    if form == "curve_curve":
        types = tuple(_type(sketch, item) for item in references)
        if types == (LINE_TYPE, LINE_TYPE):
            return _angular_error(
                line_delta(sketch, references[0], label=LABEL),
                line_delta(sketch, references[1], label=LABEL),
            )
        if LINE_TYPE in types:
            line_index = 0 if types[0] == LINE_TYPE else 1
            return _line_circle_error(
                sketch,
                references[line_index],
                references[1 - line_index],
            )
        if all(item in CIRCULAR_TYPES for item in types):
            return _circle_circle_error(sketch, references[0], references[1])
        raise NativeSketchError(
            f"{LABEL} cannot measure that whole-curve combination."
        )
    if form == "endpoint_curve":
        point = references[0]
        return _angular_error(
            curve_tangent(
                sketch,
                SketchConstraintElement(point.geometry_index, "whole"),
                point,
                label=LABEL,
                role="endpoint curve",
            ),
            curve_tangent(
                sketch,
                references[1],
                point,
                label=LABEL,
                role="target curve",
            ),
        )
    if form == "endpoint_endpoint":
        return _angular_error(
            curve_tangent(
                sketch,
                SketchConstraintElement(references[0].geometry_index, "whole"),
                references[0],
                label=LABEL,
                role="first endpoint",
            ),
            curve_tangent(
                sketch,
                SketchConstraintElement(references[1].geometry_index, "whole"),
                references[1],
                label=LABEL,
                role="second endpoint",
            ),
        )
    point = references[2]
    return _angular_error(
        curve_tangent(
            sketch,
            references[0],
            point,
            label=LABEL,
            role="first curve",
        ),
        curve_tangent(
            sketch,
            references[1],
            point,
            label=LABEL,
            role="second curve",
        ),
    )


def tangent_contact_satisfied(
    sketch: Any,
    resolved: ResolvedSketchTangent,
) -> bool:
    references = resolved.references
    if resolved.semantic_form == "endpoint_curve":
        _host, point = point_coordinates(
            sketch, references[0], label=LABEL, role="endpoint"
        )
        query = getattr(sketch, "isPointOnCurve", None)
        if not callable(query):
            raise NativeSketchError(f"{LABEL} point-on-curve query is unavailable.")
        try:
            return bool(
                query(references[1].geometry_index, point[0], point[1])
            )
        except Exception as exc:
            raise NativeSketchError(f"{LABEL} point-on-curve query failed.") from exc
    if resolved.semantic_form == "endpoint_endpoint":
        _first_host, first = point_coordinates(
            sketch, references[0], label=LABEL, role="first endpoint"
        )
        _second_host, second = point_coordinates(
            sketch, references[1], label=LABEL, role="second endpoint"
        )
        return math.hypot(second[0] - first[0], second[1] - first[1]) <= LINEAR_TOLERANCE
    return True
