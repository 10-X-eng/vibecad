# SPDX-License-Identifier: LGPL-2.1-or-later

"""Geometric postconditions for exact Native Sketch Perpendicular forms."""

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
    LINEAR_TOLERANCE,
    LINE_TYPE as _LINE_TYPE,
    curve_tangent,
    line_delta,
    nearest_perpendicular_error_degrees,
    point_coordinates,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchPerpendicularTarget import (
    LABEL,
    ResolvedSketchPerpendicular,
)


@dataclass(frozen=True, slots=True)
class PerpendicularMeasurement:
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


def _point(
    sketch: Any,
    element: SketchConstraintElement,
    role: str,
) -> tuple[Any, tuple[float, float]]:
    return point_coordinates(sketch, element, label=LABEL, role=role)


def _line_delta(
    sketch: Any,
    element: SketchConstraintElement,
) -> tuple[float, float]:
    return line_delta(sketch, element, label=LABEL)


def _tangent(
    sketch: Any,
    curve: SketchConstraintElement,
    point_element: SketchConstraintElement,
    role: str,
) -> tuple[float, float]:
    return curve_tangent(
        sketch,
        curve,
        point_element,
        label=LABEL,
        role=role,
    )


def _angular_error(
    first: tuple[float, float],
    second: tuple[float, float],
) -> PerpendicularMeasurement:
    return PerpendicularMeasurement(
        "angular_error",
        nearest_perpendicular_error_degrees(first, second),
        "deg",
    )


def _center_line_distance(
    sketch: Any,
    line: SketchConstraintElement,
    curve: SketchConstraintElement,
) -> PerpendicularMeasurement:
    geometry = sketch_constraint_geometry(sketch, curve.geometry_index)
    center = getattr(geometry, "Center", None)
    if center is None:
        raise NativeSketchError(f"{LABEL} circular center is unavailable.")
    start_element = SketchConstraintElement(line.geometry_index, "start")
    _start_host, start = _point(sketch, start_element, "line start")
    delta = _line_delta(sketch, line)
    distance = abs(
        delta[0] * (float(center.y) - start[1])
        - delta[1] * (float(center.x) - start[0])
    ) / math.hypot(*delta)
    return PerpendicularMeasurement("center_line_distance", distance, "mm")


def measure_sketch_perpendicular(
    sketch: Any,
    resolved: ResolvedSketchPerpendicular,
) -> PerpendicularMeasurement:
    references = resolved.references
    if resolved.target_form == "curve_curve":
        types = tuple(
            str(
                getattr(
                    sketch_constraint_geometry(sketch, element.geometry_index),
                    "TypeId",
                    "",
                )
                or ""
            )
            for element in references
        )
        if types == (_LINE_TYPE, _LINE_TYPE):
            return _angular_error(
                _line_delta(sketch, references[0]),
                _line_delta(sketch, references[1]),
            )
        line_index = 0 if types[0] == _LINE_TYPE else 1
        return _center_line_distance(
            sketch,
            references[line_index],
            references[1 - line_index],
        )
    if resolved.target_form == "point_pair_line":
        _first_host, first = _point(sketch, references[0], "first point")
        _second_host, second = _point(sketch, references[1], "second point")
        point_delta = second[0] - first[0], second[1] - first[1]
        if math.hypot(*point_delta) <= LINEAR_TOLERANCE:
            raise NativeSketchError(
                f"{LABEL} point_pair_line cannot use coincident points."
            )
        return _angular_error(point_delta, _line_delta(sketch, references[2]))
    if resolved.target_form == "endpoint_curve":
        point = references[0]
        return _angular_error(
            _tangent(sketch, SketchConstraintElement(point.geometry_index, "whole"), point, "endpoint curve"),
            _tangent(sketch, references[1], point, "target curve"),
        )
    if resolved.target_form == "endpoint_endpoint":
        return _angular_error(
            _tangent(
                sketch,
                SketchConstraintElement(references[0].geometry_index, "whole"),
                references[0],
                "first endpoint",
            ),
            _tangent(
                sketch,
                SketchConstraintElement(references[1].geometry_index, "whole"),
                references[1],
                "second endpoint",
            ),
        )
    point = references[2]
    return _angular_error(
        _tangent(sketch, references[0], point, "first curve"),
        _tangent(sketch, references[1], point, "second curve"),
    )
