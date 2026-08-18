# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict bounded scalar and 2D-point values for Native Sketch geometry."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError


MAX_SKETCH_COORDINATE_MM = 1_000_000.0
MIN_SKETCH_GEOMETRY_LENGTH_MM = 1.0e-9
_POINT_FIELDS = frozenset({"x", "y"})


def sketch_coordinate(value: Any, label: str) -> float:
    if type(value) not in {int, float}:
        raise NativeSketchError(f"Sketch {label} must be a finite number.")
    result = float(value)
    if not math.isfinite(result) or abs(result) > MAX_SKETCH_COORDINATE_MM:
        raise NativeSketchError(
            f"Sketch {label} must be within +/-{int(MAX_SKETCH_COORDINATE_MM)} mm."
        )
    return 0.0 if abs(result) < 1.0e-14 else result


def sketch_point_2d(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != _POINT_FIELDS:
        raise NativeSketchError(f"Sketch {label} has incorrect fields.")
    return (
        sketch_coordinate(value["x"], f"{label}.x"),
        sketch_coordinate(value["y"], f"{label}.y"),
    )


def require_distinct_points(
    start: tuple[float, float],
    end: tuple[float, float],
    label: str,
) -> None:
    if math.hypot(end[0] - start[0], end[1] - start[1]) <= (
        MIN_SKETCH_GEOMETRY_LENGTH_MM
    ):
        raise NativeSketchError(f"Sketch {label} endpoints must be distinct.")


def same_sketch_point(
    actual: Any,
    expected: tuple[float, float],
) -> bool:
    return same_sketch_vector(actual, (*expected, 0.0))


def same_sketch_vector(
    actual: Any,
    expected: tuple[float, float, float],
) -> bool:
    return bool(
        isinstance(actual, list)
        and len(actual) == 3
        and all(
            type(value) in {int, float}
            and math.isclose(
                float(value),
                target,
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
            for value, target in zip(actual, expected, strict=True)
        )
    )


def sketch_positive_length(value: Any, label: str) -> float:
    result = sketch_coordinate(value, label)
    if result <= MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError(
            f"Sketch {label} must be greater than {MIN_SKETCH_GEOMETRY_LENGTH_MM} mm."
        )
    return result


def sketch_start_angle_degrees(value: Any, label: str) -> float:
    result = sketch_coordinate(value, label)
    if result < 0.0 or result > 360.0:
        raise NativeSketchError(f"Sketch {label} must be between 0 and 360 degrees.")
    return 0.0 if result == 360.0 else result


def sketch_sweep_angle_degrees(value: Any, label: str) -> float:
    result = sketch_coordinate(value, label)
    if result <= 1.0e-9 or result >= 360.0:
        raise NativeSketchError(
            f"Sketch {label} must be greater than 0 and below 360 degrees."
        )
    return result


def sketch_bounded_parameter(
    value: Any,
    label: str,
    *,
    maximum_absolute: float,
) -> float:
    if type(value) not in {int, float}:
        raise NativeSketchError(f"Sketch {label} must be a finite number.")
    result = float(value)
    if not math.isfinite(result) or abs(result) > maximum_absolute:
        raise NativeSketchError(
            f"Sketch {label} must be within +/-{maximum_absolute}."
        )
    return 0.0 if abs(result) < 1.0e-14 else result


def same_sketch_number(actual: Any, expected: float, *, tolerance: float = 1.0e-9) -> bool:
    return bool(
        type(actual) in {int, float}
        and math.isfinite(float(actual))
        and math.isclose(float(actual), expected, rel_tol=0.0, abs_tol=tolerance)
    )
