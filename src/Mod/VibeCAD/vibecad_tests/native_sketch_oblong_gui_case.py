# SPDX-License-Identifier: LGPL-2.1-or-later

"""Oblong lifecycle case for the rolling Native Sketch GUI gate."""

from __future__ import annotations

from typing import Any, Callable

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import rounded_rectangle_arguments


def exercise_oblong_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict:
    undo_before = int(document.UndoCount)
    invalid = rounded_rectangle_arguments(
        sketch,
        geometry_count=40,
        first_corner=(-40.0, -30.0),
        opposite_corner=(-20.0, -18.0),
        radius=6.0,
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (40, 36)

    response = native_call(
        rounded_rectangle_arguments(
            sketch,
            geometry_count=40,
            first_corner=(-40.0, -30.0),
            opposite_corner=(-20.0, -18.0),
            radius=2.0,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (50, 55)
    assert response["corner_radius_mm"] == 2.0
    assert response["segment_count"] == 8
    assert response["closed"] is True
    assert response["corners_mm"] == [
        [-40.0, -30.0, 0.0],
        [-20.0, -30.0, 0.0],
        [-20.0, -18.0, 0.0],
        [-40.0, -18.0, 0.0],
    ]
    geometry_refs = response["geometry_refs"]
    assert [item["geometry_index"] for item in geometry_refs] == list(range(40, 48))
    assert [item["kind"] for item in geometry_refs] == [
        *(["line"] * 4),
        *(["circular_arc"] * 4),
    ]
    construction_refs = response["construction_geometry_refs"]
    assert [item["geometry_index"] for item in construction_refs] == [48, 49]
    assert all(item["construction"] is True for item in construction_refs)
    constraint_refs = response["constraint_refs"]
    assert [item["constraint_index"] for item in constraint_refs] == list(range(36, 55))
    assert [item["type"] for item in constraint_refs] == [
        *(["Tangent"] * 8),
        "Horizontal",
        "Vertical",
        "Horizontal",
        "Vertical",
        *(["Equal"] * 3),
        *(["PointOnObject"] * 4),
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Oblong"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (40, 36)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (50, 55)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": [
            serialize_sketch_geometry(sketch, index) for index in range(40, 48)
        ],
        "construction_points": [
            serialize_sketch_geometry(sketch, index) for index in (48, 49)
        ],
        "constraints": [
            serialize_sketch_constraint(sketch, index) for index in range(36, 55)
        ],
    }


def verify_reopened_oblong(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(40, 48)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        keys = ("type_id", "kind", "construction", "start_mm", "end_mm")
        if actual["type_id"] == "Part::GeomArcOfCircle":
            keys = (
                *keys,
                "center_mm",
                "axis",
                "radius_mm",
                "first_parameter",
                "last_parameter",
                "closed",
            )
        for key in keys:
            assert actual[key] == saved[key]
    points = [serialize_sketch_geometry(sketch, index) for index in (48, 49)]
    for actual, saved in zip(points, expected["construction_points"], strict=True):
        for key in ("type_id", "kind", "construction", "position_mm"):
            assert actual[key] == saved[key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(36, 55)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]
