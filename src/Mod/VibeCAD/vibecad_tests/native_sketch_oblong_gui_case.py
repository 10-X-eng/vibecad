# SPDX-License-Identifier: LGPL-2.1-or-later

"""Oblong lifecycle case for the rolling Native Sketch GUI gate."""

from __future__ import annotations

from typing import Any, Callable

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import oblong_arguments


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
    invalid = oblong_arguments(
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
        oblong_arguments(
            sketch,
            geometry_count=40,
            first_corner=(-40.0, -30.0),
            opposite_corner=(-20.0, -18.0),
            radius=2.0,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (50, 55)
    assert response["radius_mm"] == 2.0
    assert response["closed"] is True
    assert response["corners_mm"] == [
        [-40.0, -30.0, 0.0],
        [-20.0, -30.0, 0.0],
        [-20.0, -18.0, 0.0],
        [-40.0, -18.0, 0.0],
    ]
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == list(range(40, 48))
    assert [item["type_id"] for item in geometries] == [
        *(["Part::GeomLineSegment"] * 4),
        *(["Part::GeomArcOfCircle"] * 4),
    ]
    assert [item["center_mm"] for item in geometries[4:]] == [
        [-38.0, -28.0, 0.0],
        [-22.0, -28.0, 0.0],
        [-22.0, -20.0, 0.0],
        [-38.0, -20.0, 0.0],
    ]
    construction_points = response["construction_points"]
    assert [item["index"] for item in construction_points] == [48, 49]
    assert [item["position_mm"] for item in construction_points] == [
        [-40.0, -30.0, 0.0],
        [-20.0, -18.0, 0.0],
    ]
    assert all(item["construction"] is True for item in construction_points)
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(36, 55))
    assert [item["type"] for item in constraints] == [
        *(["Tangent"] * 8),
        "Horizontal",
        "Vertical",
        "Horizontal",
        "Vertical",
        *(["Equal"] * 3),
        *(["PointOnObject"] * 4),
    ]
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 40, "position": 1},
        {"slot": 2, "geometry_index": 44, "position": 2},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 49, "position": 1},
        {"slot": 2, "geometry_index": 42},
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
        "geometries": geometries,
        "construction_points": construction_points,
        "constraints": constraints,
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
