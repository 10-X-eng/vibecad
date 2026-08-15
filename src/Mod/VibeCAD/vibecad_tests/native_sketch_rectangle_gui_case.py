# SPDX-License-Identifier: LGPL-2.1-or-later

"""Corner Rectangle lifecycle case for the rolling Native Sketch GUI gate."""

from __future__ import annotations

from typing import Any, Callable

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    center_rectangle_arguments,
    rectangle_arguments,
)


def exercise_rectangle_case(
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
    invalid = rectangle_arguments(
        sketch,
        geometry_count=31,
        first_corner=(-30.0, 22.0),
        opposite_corner=(-30.0, 10.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (31, 19)

    response = native_call(
        rectangle_arguments(
            sketch,
            geometry_count=31,
            first_corner=(-30.0, 22.0),
            opposite_corner=(-18.0, 10.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (35, 27)
    assert response["segment_count"] == 4
    assert response["closed"] is True
    assert response["corners_mm"] == [
        [-30.0, 22.0, 0.0],
        [-30.0, 10.0, 0.0],
        [-18.0, 10.0, 0.0],
        [-18.0, 22.0, 0.0],
    ]
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == [31, 32, 33, 34]
    assert [item["start_mm"] for item in geometries] == response["corners_mm"]
    assert [item["end_mm"] for item in geometries] == [
        *response["corners_mm"][1:],
        response["corners_mm"][0],
    ]
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(19, 27))
    assert [item["type"] for item in constraints] == [
        "Coincident",
        "Coincident",
        "Coincident",
        "Coincident",
        "Vertical",
        "Horizontal",
        "Vertical",
        "Horizontal",
    ]
    assert [item["references"] for item in constraints[:4]] == [
        [
            {"slot": 1, "geometry_index": 31, "position": 2},
            {"slot": 2, "geometry_index": 32, "position": 1},
        ],
        [
            {"slot": 1, "geometry_index": 32, "position": 2},
            {"slot": 2, "geometry_index": 33, "position": 1},
        ],
        [
            {"slot": 1, "geometry_index": 33, "position": 2},
            {"slot": 2, "geometry_index": 34, "position": 1},
        ],
        [
            {"slot": 1, "geometry_index": 34, "position": 2},
            {"slot": 2, "geometry_index": 31, "position": 1},
        ],
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Rectangle"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (31, 19)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (35, 27)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "constraints": constraints,
    }


def verify_reopened_rectangle(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(31, 35)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(19, 27)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_center_rectangle_case(
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
    invalid = center_rectangle_arguments(
        sketch,
        geometry_count=35,
        center=(10.0, 30.0),
        corner=(10.0, 34.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (35, 27)

    response = native_call(
        center_rectangle_arguments(
            sketch,
            geometry_count=35,
            center=(10.0, 30.0),
            corner=(16.0, 34.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (40, 36)
    assert response["corners_mm"] == [
        [4.0, 26.0, 0.0],
        [16.0, 26.0, 0.0],
        [16.0, 34.0, 0.0],
        [4.0, 34.0, 0.0],
    ]
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == [35, 36, 37, 38]
    center_geometry = response["center_geometry"]
    assert center_geometry["index"] == 39
    assert center_geometry["type_id"] == "Part::GeomPoint"
    assert center_geometry["construction"] is True
    assert center_geometry["position_mm"] == [10.0, 30.0, 0.0]
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(27, 36))
    assert [item["type"] for item in constraints] == [
        "Coincident",
        "Coincident",
        "Coincident",
        "Coincident",
        "Horizontal",
        "Vertical",
        "Horizontal",
        "Vertical",
        "Symmetric",
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 37, "position": 1},
        {"slot": 2, "geometry_index": 35, "position": 1},
        {"slot": 3, "geometry_index": 39, "position": 1},
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Center Rectangle"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (35, 27)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (40, 36)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "center_geometry": center_geometry,
        "constraints": constraints,
    }


def verify_reopened_center_rectangle(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(35, 39)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    center = serialize_sketch_geometry(sketch, 39)
    for key in ("type_id", "kind", "construction", "position_mm"):
        assert center[key] == expected["center_geometry"][key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(27, 36)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]
