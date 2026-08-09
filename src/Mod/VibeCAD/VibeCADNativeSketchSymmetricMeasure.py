# SPDX-License-Identifier: LGPL-2.1-or-later

"""Reflection postconditions for exact Native Sketch Symmetric forms."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from VibeCADNativeSketchConstraintTargets import SketchConstraintElement
from VibeCADNativeSketchCurveDifferential import (
    LINEAR_TOLERANCE,
    line_delta,
    point_coordinates,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchSymmetricTarget import (
    LABEL,
    ResolvedSketchSymmetric,
)


@dataclass(frozen=True, slots=True)
class SymmetricMeasurement:
    reference_kind: str
    reflection_error: float
    midpoint_error: float

    def record(self) -> dict[str, float | str]:
        return {
            "reference_kind": self.reference_kind,
            "reflection_error": self.reflection_error,
            "midpoint_error": self.midpoint_error,
            "unit": "mm",
        }

    def satisfied(self) -> bool:
        return (
            math.isfinite(self.reflection_error)
            and math.isfinite(self.midpoint_error)
            and self.reflection_error <= LINEAR_TOLERANCE
            and self.midpoint_error <= LINEAR_TOLERANCE
        )


def _point(
    sketch: Any,
    element: SketchConstraintElement,
    role: str,
) -> tuple[float, float]:
    _host, coordinates = point_coordinates(
        sketch,
        element,
        label=LABEL,
        role=role,
    )
    return coordinates


def _line(
    sketch: Any,
    element: SketchConstraintElement,
) -> tuple[tuple[float, float], tuple[float, float]]:
    if element.geometry_index in {-1, -2}:
        origin = (0.0, 0.0)
    else:
        origin = _point(
            sketch,
            SketchConstraintElement(element.geometry_index, "start"),
            "symmetry line start",
        )
    delta = line_delta(sketch, element, label=LABEL)
    length = math.hypot(*delta)
    if not math.isfinite(length) or length <= LINEAR_TOLERANCE:
        raise NativeSketchError(f"{LABEL} symmetry line is degenerate.")
    return origin, delta


def _about_point(
    first: tuple[float, float],
    second: tuple[float, float],
    reference: tuple[float, float],
) -> SymmetricMeasurement:
    reflected = (
        2.0 * reference[0] - first[0],
        2.0 * reference[1] - first[1],
    )
    midpoint = (
        0.5 * (first[0] + second[0]),
        0.5 * (first[1] + second[1]),
    )
    return SymmetricMeasurement(
        "point",
        math.dist(reflected, second),
        math.dist(midpoint, reference),
    )


def _about_line(
    first: tuple[float, float],
    second: tuple[float, float],
    origin: tuple[float, float],
    delta: tuple[float, float],
) -> SymmetricMeasurement:
    length_squared = delta[0] * delta[0] + delta[1] * delta[1]
    first_ratio = (
        (first[0] - origin[0]) * delta[0] + (first[1] - origin[1]) * delta[1]
    ) / length_squared
    projection = (
        origin[0] + first_ratio * delta[0],
        origin[1] + first_ratio * delta[1],
    )
    reflected = (
        2.0 * projection[0] - first[0],
        2.0 * projection[1] - first[1],
    )
    midpoint = (
        0.5 * (first[0] + second[0]),
        0.5 * (first[1] + second[1]),
    )
    midpoint_cross = delta[0] * (midpoint[1] - origin[1]) - delta[1] * (
        midpoint[0] - origin[0]
    )
    return SymmetricMeasurement(
        "line",
        math.dist(reflected, second),
        abs(midpoint_cross) / math.sqrt(length_squared),
    )


def measure_sketch_symmetric(
    sketch: Any,
    resolved: ResolvedSketchSymmetric,
) -> SymmetricMeasurement:
    if not isinstance(resolved, ResolvedSketchSymmetric):
        raise TypeError("resolved must be a ResolvedSketchSymmetric")
    first, second, reference = resolved.references
    first_point = _point(sketch, first, "first symmetric point")
    second_point = _point(sketch, second, "second symmetric point")
    if resolved.reference_kind == "point":
        return _about_point(
            first_point,
            second_point,
            _point(sketch, reference, "symmetry point"),
        )
    origin, delta = _line(sketch, reference)
    return _about_line(first_point, second_point, origin, delta)
