# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared analytic proof for circular geometry created in a Sketch."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    same_sketch_number,
    same_sketch_point,
    same_sketch_vector,
)


def circle_point(
    center_mm: tuple[float, float],
    radius_mm: float,
    parameter: float,
) -> tuple[float, float]:
    return (
        center_mm[0] + radius_mm * math.cos(parameter),
        center_mm[1] + radius_mm * math.sin(parameter),
    )


def verify_circular_arc_record(
    geometry: Mapping[str, Any],
    *,
    center_mm: tuple[float, float],
    radius_mm: float,
    first_parameter: float,
    last_parameter: float,
    start_mm: tuple[float, float],
    end_mm: tuple[float, float],
    label: str,
) -> None:
    if (
        not isinstance(geometry, Mapping)
        or geometry.get("type_id") != "Part::GeomArcOfCircle"
        or geometry.get("kind") != "circular_arc"
        or bool(geometry.get("construction"))
        or bool(geometry.get("blocked"))
        or geometry.get("closed") is not False
        or not same_sketch_point(geometry.get("center_mm"), center_mm)
        or not same_sketch_vector(geometry.get("axis"), (0.0, 0.0, 1.0))
        or not same_sketch_number(geometry.get("radius_mm"), radius_mm)
        or not same_sketch_number(
            geometry.get("first_parameter"),
            first_parameter,
            tolerance=1.0e-10,
        )
        or not same_sketch_number(
            geometry.get("last_parameter"),
            last_parameter,
            tolerance=1.0e-10,
        )
        or not same_sketch_point(geometry.get("start_mm"), start_mm)
        or not same_sketch_point(geometry.get("end_mm"), end_mm)
    ):
        raise NativeSketchError(
            f"Sketch {label} geometry differs from its exact definition."
        )


def verify_circle_record(
    geometry: Mapping[str, Any],
    *,
    center_mm: tuple[float, float],
    radius_mm: float,
    label: str,
) -> None:
    if (
        not isinstance(geometry, Mapping)
        or geometry.get("type_id") != "Part::GeomCircle"
        or geometry.get("kind") != "circle"
        or bool(geometry.get("construction"))
        or bool(geometry.get("blocked"))
        or geometry.get("closed") is not True
        or not same_sketch_point(geometry.get("center_mm"), center_mm)
        or not same_sketch_vector(geometry.get("axis"), (0.0, 0.0, 1.0))
        or not same_sketch_number(geometry.get("radius_mm"), radius_mm)
    ):
        raise NativeSketchError(
            f"Sketch {label} geometry differs from its exact definition."
        )
