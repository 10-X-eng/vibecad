# SPDX-License-Identifier: LGPL-2.1-or-later

"""Center-based Ellipse lifecycle case for the rolling Native Sketch GUI gate."""

from __future__ import annotations

import math
from typing import Any, Callable

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    ellipse_arguments,
    three_point_ellipse_arguments,
)


def exercise_ellipse_case(
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
    invalid = ellipse_arguments(
        sketch,
        geometry_count=21,
        center=(20.0, 18.0),
        major_radius=9.0,
        minor_radius=9.0,
        rotation_degrees=25.0,
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (21, 11)

    response = native_call(
        ellipse_arguments(
            sketch,
            geometry_count=21,
            center=(20.0, 18.0),
            major_radius=9.0,
            minor_radius=4.0,
            rotation_degrees=25.0,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (26, 15)
    geometry = response["geometry"]
    assert geometry["index"] == 21
    assert geometry["type_id"] == "Part::GeomEllipse"
    assert geometry["kind"] == "ellipse"
    assert geometry["construction"] is False
    assert geometry["closed"] is True
    assert geometry["center_mm"] == [20.0, 18.0, 0.0]
    assert geometry["major_radius_mm"] == 9.0
    assert geometry["minor_radius_mm"] == 4.0
    assert math.isclose(geometry["x_axis"][0], math.cos(math.radians(25.0)))
    assert math.isclose(geometry["x_axis"][1], math.sin(math.radians(25.0)))
    internal = response["internal_geometries"]
    assert [item["index"] for item in internal] == [22, 23, 24, 25]
    assert [item["internal_type"] for item in internal] == [
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ]
    assert all(item["construction"] is True for item in internal)
    constraints = response["internal_constraints"]
    assert [item["index"] for item in constraints] == [11, 12, 13, 14]
    assert [item["references"] for item in constraints] == [
        [{"slot": 1, "geometry_index": 22}, {"slot": 2, "geometry_index": 21}],
        [{"slot": 1, "geometry_index": 23}, {"slot": 2, "geometry_index": 21}],
        [
            {"slot": 1, "geometry_index": 24, "position": 1},
            {"slot": 2, "geometry_index": 21},
        ],
        [
            {"slot": 1, "geometry_index": 25, "position": 1},
            {"slot": 2, "geometry_index": 21},
        ],
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Ellipse"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (21, 11)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (26, 15)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometry": geometry,
        "constraint_references": [item["references"] for item in constraints],
    }


def verify_reopened_ellipse(sketch: Any, expected: dict) -> None:
    geometry = serialize_sketch_geometry(sketch, 21)
    for key in (
        "type_id",
        "kind",
        "construction",
        "closed",
        "center_mm",
        "x_axis",
        "major_radius_mm",
        "minor_radius_mm",
    ):
        assert geometry[key] == expected["geometry"][key]
    assert [
        serialize_sketch_geometry(sketch, index)["internal_type"]
        for index in (22, 23, 24, 25)
    ] == [
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ]
    assert [
        serialize_sketch_constraint(sketch, index)["references"]
        for index in (11, 12, 13, 14)
    ] == expected["constraint_references"]


def exercise_three_point_ellipse_case(
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
    invalid = three_point_ellipse_arguments(
        sketch,
        geometry_count=26,
        first_axis_endpoint=(-12.0, -15.0),
        second_axis_endpoint=(4.0, -15.0),
        rim_point=(-4.0, -15.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (26, 15)

    response = native_call(
        three_point_ellipse_arguments(
            sketch,
            geometry_count=26,
            first_axis_endpoint=(-12.0, -15.0),
            second_axis_endpoint=(4.0, -15.0),
            rim_point=(-4.0, -11.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (31, 19)
    geometry = response["geometry"]
    assert geometry["index"] == 26
    assert geometry["type_id"] == "Part::GeomEllipse"
    assert geometry["center_mm"] == [-4.0, -15.0, 0.0]
    assert geometry["major_radius_mm"] == 8.0
    assert geometry["minor_radius_mm"] == 4.0
    assert geometry["x_axis"] == [1.0, 0.0, 0.0]
    assert geometry["closed"] is True
    internal = response["internal_geometries"]
    assert [item["index"] for item in internal] == [27, 28, 29, 30]
    assert [item["internal_type"] for item in internal] == [
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ]
    constraints = response["internal_constraints"]
    assert [item["index"] for item in constraints] == [15, 16, 17, 18]
    assert [item["references"] for item in constraints] == [
        [{"slot": 1, "geometry_index": 27}, {"slot": 2, "geometry_index": 26}],
        [{"slot": 1, "geometry_index": 28}, {"slot": 2, "geometry_index": 26}],
        [
            {"slot": 1, "geometry_index": 29, "position": 1},
            {"slot": 2, "geometry_index": 26},
        ],
        [
            {"slot": 1, "geometry_index": 30, "position": 1},
            {"slot": 2, "geometry_index": 26},
        ],
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Three-Point Ellipse"
    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (26, 15)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (31, 19)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometry": geometry,
        "constraint_references": [item["references"] for item in constraints],
    }


def verify_reopened_three_point_ellipse(sketch: Any, expected: dict) -> None:
    geometry = serialize_sketch_geometry(sketch, 26)
    for key in (
        "type_id",
        "kind",
        "construction",
        "closed",
        "center_mm",
        "x_axis",
        "major_radius_mm",
        "minor_radius_mm",
    ):
        assert geometry[key] == expected["geometry"][key]
    assert [
        serialize_sketch_geometry(sketch, index)["internal_type"]
        for index in (27, 28, 29, 30)
    ] == [
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ]
    assert [
        serialize_sketch_constraint(sketch, index)["references"]
        for index in (15, 16, 17, 18)
    ] == expected["constraint_references"]
