# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared analytic and internal-geometry proof for Sketch ellipses."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    same_sketch_number,
    same_sketch_point,
    same_sketch_vector,
)
from VibeCADNativeSketchInternalGeometry import ExpectedInternalGeometry


def ellipse_axes(
    rotation_degrees: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    rotation = math.radians(rotation_degrees)
    major = (math.cos(rotation), math.sin(rotation))
    return major, (-major[1], major[0])


def ellipse_point(
    center_mm: tuple[float, float],
    major_radius_mm: float,
    minor_radius_mm: float,
    major_axis: tuple[float, float],
    parameter: float,
) -> tuple[float, float]:
    minor_axis = (-major_axis[1], major_axis[0])
    return (
        center_mm[0]
        + major_radius_mm * math.cos(parameter) * major_axis[0]
        + minor_radius_mm * math.sin(parameter) * minor_axis[0],
        center_mm[1]
        + major_radius_mm * math.cos(parameter) * major_axis[1]
        + minor_radius_mm * math.sin(parameter) * minor_axis[1],
    )


def expected_ellipse_internal_geometry(
    center_mm: tuple[float, float],
    major_radius_mm: float,
    minor_radius_mm: float,
    major_axis: tuple[float, float],
) -> tuple[ExpectedInternalGeometry, ...]:
    center_x, center_y = center_mm
    major_x, major_y = major_axis
    minor_x, minor_y = -major_y, major_x
    focus = math.sqrt(
        major_radius_mm * major_radius_mm - minor_radius_mm * minor_radius_mm
    )
    return (
        ExpectedInternalGeometry(
            "EllipseMajorDiameter",
            "line",
            (center_x + major_radius_mm * major_x, center_y + major_radius_mm * major_y),
            (center_x - major_radius_mm * major_x, center_y - major_radius_mm * major_y),
        ),
        ExpectedInternalGeometry(
            "EllipseMinorDiameter",
            "line",
            (center_x + minor_radius_mm * minor_x, center_y + minor_radius_mm * minor_y),
            (center_x - minor_radius_mm * minor_x, center_y - minor_radius_mm * minor_y),
        ),
        ExpectedInternalGeometry(
            "EllipseFocus1",
            "point",
            (center_x + focus * major_x, center_y + focus * major_y),
        ),
        ExpectedInternalGeometry(
            "EllipseFocus2",
            "point",
            (center_x - focus * major_x, center_y - focus * major_y),
        ),
    )


def verify_ellipse_record(
    geometry: Mapping[str, Any],
    *,
    type_id: str,
    kind: str,
    closed: bool,
    center_mm: tuple[float, float],
    major_radius_mm: float,
    minor_radius_mm: float,
    major_axis: tuple[float, float],
    first_parameter: float | None = None,
    last_parameter: float | None = None,
) -> None:
    invalid = (
        geometry.get("type_id") != type_id
        or geometry.get("kind") != kind
        or bool(geometry.get("construction"))
        or bool(geometry.get("blocked"))
        or geometry.get("closed") is not closed
        or not same_sketch_point(geometry.get("center_mm"), center_mm)
        or not same_sketch_vector(geometry.get("axis"), (0.0, 0.0, 1.0))
        or not same_sketch_vector(geometry.get("x_axis"), (*major_axis, 0.0))
        or not same_sketch_number(geometry.get("major_radius_mm"), major_radius_mm)
        or not same_sketch_number(geometry.get("minor_radius_mm"), minor_radius_mm)
    )
    if first_parameter is not None and last_parameter is not None:
        invalid = invalid or (
            not same_sketch_number(
                geometry.get("first_parameter"), first_parameter, tolerance=1.0e-10
            )
            or not same_sketch_number(
                geometry.get("last_parameter"), last_parameter, tolerance=1.0e-10
            )
            or not same_sketch_point(
                geometry.get("start_mm"),
                ellipse_point(
                    center_mm,
                    major_radius_mm,
                    minor_radius_mm,
                    major_axis,
                    first_parameter,
                ),
            )
            or not same_sketch_point(
                geometry.get("end_mm"),
                ellipse_point(
                    center_mm,
                    major_radius_mm,
                    minor_radius_mm,
                    major_axis,
                    last_parameter,
                ),
            )
        )
    if invalid:
        raise NativeSketchError("Sketch ellipse geometry differs from its exact definition.")
