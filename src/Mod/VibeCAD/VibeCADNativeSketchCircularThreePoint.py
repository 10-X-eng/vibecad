# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared bounded circumcircle derivation for three-point Sketch geometry."""

from __future__ import annotations

import math

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MIN_SKETCH_GEOMETRY_LENGTH_MM,
    require_distinct_points,
    sketch_coordinate,
    sketch_positive_length,
)


def circumcircle(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
    *,
    label: str,
    pair_labels: tuple[str, str, str] = (
        "first and second points",
        "first and third points",
        "second and third points",
    ),
) -> tuple[tuple[float, float], float]:
    require_distinct_points(first, second, f"{label} {pair_labels[0]}")
    require_distinct_points(first, third, f"{label} {pair_labels[1]}")
    require_distinct_points(second, third, f"{label} {pair_labels[2]}")
    ax, ay = first
    bx, by = second
    cx, cy = third
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    longest = max(
        math.hypot(bx - ax, by - ay),
        math.hypot(cx - ax, cy - ay),
        math.hypot(cx - bx, cy - by),
    )
    if abs(cross) <= MIN_SKETCH_GEOMETRY_LENGTH_MM * longest:
        raise NativeSketchError(f"Sketch {label} points must not be collinear.")
    denominator = 2.0 * cross
    a_squared = ax * ax + ay * ay
    b_squared = bx * bx + by * by
    c_squared = cx * cx + cy * cy
    center = (
        (
            a_squared * (by - cy)
            + b_squared * (cy - ay)
            + c_squared * (ay - by)
        )
        / denominator,
        (
            a_squared * (cx - bx)
            + b_squared * (ax - cx)
            + c_squared * (bx - ax)
        )
        / denominator,
    )
    bounded_center = (
        sketch_coordinate(center[0], f"{label} center.x"),
        sketch_coordinate(center[1], f"{label} center.y"),
    )
    radius = sketch_positive_length(
        math.hypot(first[0] - bounded_center[0], first[1] - bounded_center[1]),
        f"{label} radius",
    )
    return bounded_center, radius
