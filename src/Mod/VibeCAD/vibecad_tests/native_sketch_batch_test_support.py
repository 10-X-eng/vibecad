# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical one-call constrained profile for Native Sketch batch gates."""

from __future__ import annotations

from typing import Any


def constrained_rectangle_arguments(
    sketch: Any,
    *,
    geometry_count: int = 0,
    constraint_count: int = 0,
    width_mm: float = 40.0,
    height_mm: float = 20.0,
) -> dict:
    def point(geometry_ref: str, position: str) -> dict:
        return {
            "geometry_ref": geometry_ref,
            "position": position,
        }

    geometry = [
        {
            "ref": "bottom",
            "kind": "line",
            "construction": False,
            "start_mm": {"x": 0.0, "y": 0.0},
            "end_mm": {"x": width_mm, "y": 0.0},
        },
        {
            "ref": "right",
            "kind": "line",
            "construction": False,
            "start_mm": {"x": width_mm, "y": 0.0},
            "end_mm": {"x": width_mm, "y": height_mm},
        },
        {
            "ref": "top",
            "kind": "line",
            "construction": False,
            "start_mm": {"x": width_mm, "y": height_mm},
            "end_mm": {"x": 0.0, "y": height_mm},
        },
        {
            "ref": "left",
            "kind": "line",
            "construction": False,
            "start_mm": {"x": 0.0, "y": height_mm},
            "end_mm": {"x": 0.0, "y": 0.0},
        },
    ]
    constraints = [
        {
            "ref": "join_bottom_right",
            "kind": "coincident",
            "first": point("bottom", "end"),
            "second": point("right", "start"),
        },
        {
            "ref": "join_right_top",
            "kind": "coincident",
            "first": point("right", "end"),
            "second": point("top", "start"),
        },
        {
            "ref": "join_top_left",
            "kind": "coincident",
            "first": point("top", "end"),
            "second": point("left", "start"),
        },
        {
            "ref": "join_left_bottom",
            "kind": "coincident",
            "first": point("left", "end"),
            "second": point("bottom", "start"),
        },
        {"ref": "bottom_horizontal", "kind": "horizontal", "geometry_ref": "bottom"},
        {"ref": "right_vertical", "kind": "vertical", "geometry_ref": "right"},
        {"ref": "top_horizontal", "kind": "horizontal", "geometry_ref": "top"},
        {"ref": "left_vertical", "kind": "vertical", "geometry_ref": "left"},
        {
            "ref": "anchor_origin",
            "kind": "coincident",
            "first": {"origin": True},
            "second": point("bottom", "start"),
        },
        {
            "ref": "width",
            "kind": "distance_x",
            "first": point("bottom", "start"),
            "second": point("bottom", "end"),
            "value_mm": width_mm,
        },
        {
            "ref": "height",
            "kind": "distance_y",
            "first": point("right", "start"),
            "second": point("right", "end"),
            "value_mm": height_mm,
        },
    ]
    return {
        "operation": "create",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": constraint_count,
        "geometry": geometry,
        "constraints": constraints,
    }
