# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regular-polygon lifecycle cases for the rolling Native Sketch GUI gate."""

from __future__ import annotations

import math
from typing import Any, Callable

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    heptagon_arguments,
    hexagon_arguments,
    octagon_arguments,
    pentagon_arguments,
    regular_polygon_arguments,
    square_arguments,
    triangle_arguments,
)


def exercise_triangle_case(
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
    invalid = triangle_arguments(
        sketch,
        geometry_count=50,
        center=(25.0, 24.0),
        corner=(25.0, 24.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (50, 55)

    response = native_call(
        triangle_arguments(
            sketch,
            geometry_count=50,
            center=(25.0, 24.0),
            corner=(31.0, 24.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (54, 63)
    assert response["center_mm"] == [25.0, 24.0, 0.0]
    assert response["corner_mm"] == [31.0, 24.0, 0.0]
    assert response["radius_mm"] == 6.0
    assert response["side_count"] == 3
    assert response["closed"] is True
    expected_vertices = (
        (31.0, 24.0, 0.0),
        (22.0, 24.0 + 3.0 * math.sqrt(3.0), 0.0),
        (22.0, 24.0 - 3.0 * math.sqrt(3.0), 0.0),
    )
    for actual, expected in zip(
        response["vertices_mm"],
        expected_vertices,
        strict=True,
    ):
        assert all(
            math.isclose(value, target, rel_tol=0.0, abs_tol=1.0e-9)
            for value, target in zip(actual, expected, strict=True)
        )

    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == [50, 51, 52]
    assert all(item["type_id"] == "Part::GeomLineSegment" for item in geometries)
    assert all(item["construction"] is False for item in geometries)
    circle = response["construction_circle"]
    assert circle["index"] == 53
    assert circle["type_id"] == "Part::GeomCircle"
    assert circle["construction"] is True
    assert circle["center_mm"] == [25.0, 24.0, 0.0]
    assert circle["radius_mm"] == 6.0
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(55, 63))
    assert [item["type"] for item in constraints] == [
        *(["Coincident"] * 3),
        *(["Equal"] * 2),
        *(["PointOnObject"] * 3),
    ]
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 50, "position": 2},
        {"slot": 2, "geometry_index": 51, "position": 1},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 52, "position": 2},
        {"slot": 2, "geometry_index": 53},
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Triangle"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (50, 55)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (54, 63)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "construction_circle": circle,
        "constraints": constraints,
    }


def verify_reopened_triangle(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(50, 53)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    circle = serialize_sketch_geometry(sketch, 53)
    for key in (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "closed",
    ):
        assert circle[key] == expected["construction_circle"][key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(55, 63)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_square_case(
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
    invalid = square_arguments(
        sketch,
        geometry_count=54,
        center=(-25.0, 24.0),
        corner=(-25.0, 24.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (54, 63)

    response = native_call(
        square_arguments(
            sketch,
            geometry_count=54,
            center=(-25.0, 24.0),
            corner=(-19.0, 24.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (59, 74)
    assert response["center_mm"] == [-25.0, 24.0, 0.0]
    assert response["corner_mm"] == [-19.0, 24.0, 0.0]
    assert response["radius_mm"] == 6.0
    assert response["side_count"] == 4
    expected_vertices = (
        (-19.0, 24.0, 0.0),
        (-25.0, 30.0, 0.0),
        (-31.0, 24.0, 0.0),
        (-25.0, 18.0, 0.0),
    )
    for actual, expected in zip(
        response["vertices_mm"],
        expected_vertices,
        strict=True,
    ):
        assert all(
            math.isclose(value, target, rel_tol=0.0, abs_tol=1.0e-9)
            for value, target in zip(actual, expected, strict=True)
        )
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == [54, 55, 56, 57]
    circle = response["construction_circle"]
    assert circle["index"] == 58
    assert circle["construction"] is True
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(63, 74))
    assert [item["type"] for item in constraints] == [
        *(["Coincident"] * 4),
        *(["Equal"] * 3),
        *(["PointOnObject"] * 4),
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Square"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (54, 63)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (59, 74)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "construction_circle": circle,
        "constraints": constraints,
    }


def verify_reopened_square(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(54, 58)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    circle = serialize_sketch_geometry(sketch, 58)
    for key in (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "closed",
    ):
        assert circle[key] == expected["construction_circle"][key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(63, 74)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_pentagon_case(
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
    invalid = pentagon_arguments(
        sketch,
        geometry_count=59,
        center=(0.0, 35.0),
        corner=(0.0, 35.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (59, 74)

    response = native_call(
        pentagon_arguments(
            sketch,
            geometry_count=59,
            center=(0.0, 35.0),
            corner=(6.0, 35.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (65, 88)
    assert response["center_mm"] == [0.0, 35.0, 0.0]
    assert response["corner_mm"] == [6.0, 35.0, 0.0]
    assert response["radius_mm"] == 6.0
    assert response["side_count"] == 5
    expected_second = (
        6.0 * math.cos(math.tau / 5.0),
        35.0 + 6.0 * math.sin(math.tau / 5.0),
        0.0,
    )
    assert all(
        math.isclose(value, target, rel_tol=0.0, abs_tol=1.0e-9)
        for value, target in zip(
            response["vertices_mm"][1],
            expected_second,
            strict=True,
        )
    )
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == list(range(59, 64))
    circle = response["construction_circle"]
    assert circle["index"] == 64
    assert circle["construction"] is True
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(74, 88))
    assert [item["type"] for item in constraints] == [
        *(["Coincident"] * 5),
        *(["Equal"] * 4),
        *(["PointOnObject"] * 5),
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Pentagon"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (59, 74)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (65, 88)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "construction_circle": circle,
        "constraints": constraints,
    }


def verify_reopened_pentagon(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(59, 64)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    circle = serialize_sketch_geometry(sketch, 64)
    for key in (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "closed",
    ):
        assert circle[key] == expected["construction_circle"][key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(74, 88)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_hexagon_case(
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
    invalid = hexagon_arguments(
        sketch,
        geometry_count=65,
        center=(20.0, 38.0),
        corner=(20.0, 38.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (65, 88)

    response = native_call(
        hexagon_arguments(
            sketch,
            geometry_count=65,
            center=(20.0, 38.0),
            corner=(26.0, 38.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (72, 105)
    assert response["center_mm"] == [20.0, 38.0, 0.0]
    assert response["corner_mm"] == [26.0, 38.0, 0.0]
    assert response["radius_mm"] == 6.0
    assert response["side_count"] == 6
    assert all(
        math.isclose(value, target, rel_tol=0.0, abs_tol=1.0e-9)
        for value, target in zip(
            response["vertices_mm"][1],
            (23.0, 38.0 + 3.0 * math.sqrt(3.0), 0.0),
            strict=True,
        )
    )
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == list(range(65, 71))
    circle = response["construction_circle"]
    assert circle["index"] == 71
    assert circle["construction"] is True
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(88, 105))
    assert [item["type"] for item in constraints] == [
        *(["Coincident"] * 6),
        *(["Equal"] * 5),
        *(["PointOnObject"] * 6),
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Hexagon"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (65, 88)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (72, 105)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "construction_circle": circle,
        "constraints": constraints,
    }


def verify_reopened_hexagon(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(65, 71)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    circle = serialize_sketch_geometry(sketch, 71)
    for key in (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "closed",
    ):
        assert circle[key] == expected["construction_circle"][key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(88, 105)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_heptagon_case(
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
    invalid = heptagon_arguments(
        sketch,
        geometry_count=72,
        center=(-20.0, 40.0),
        corner=(-20.0, 40.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (72, 105)

    response = native_call(
        heptagon_arguments(
            sketch,
            geometry_count=72,
            center=(-20.0, 40.0),
            corner=(-14.0, 40.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (80, 125)
    assert response["center_mm"] == [-20.0, 40.0, 0.0]
    assert response["corner_mm"] == [-14.0, 40.0, 0.0]
    assert response["radius_mm"] == 6.0
    assert response["side_count"] == 7
    expected_second = (
        -20.0 + 6.0 * math.cos(math.tau / 7.0),
        40.0 + 6.0 * math.sin(math.tau / 7.0),
        0.0,
    )
    assert all(
        math.isclose(value, target, rel_tol=0.0, abs_tol=1.0e-9)
        for value, target in zip(
            response["vertices_mm"][1],
            expected_second,
            strict=True,
        )
    )
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == list(range(72, 79))
    circle = response["construction_circle"]
    assert circle["index"] == 79
    assert circle["construction"] is True
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(105, 125))
    assert [item["type"] for item in constraints] == [
        *(["Coincident"] * 7),
        *(["Equal"] * 6),
        *(["PointOnObject"] * 7),
    ]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Heptagon"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (72, 105)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (80, 125)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "construction_circle": circle,
        "constraints": constraints,
    }


def verify_reopened_heptagon(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(72, 79)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    circle = serialize_sketch_geometry(sketch, 79)
    for key in (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "closed",
    ):
        assert circle[key] == expected["construction_circle"][key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(105, 125)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_octagon_case(
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
    invalid = octagon_arguments(
        sketch,
        geometry_count=80,
        center=(0.0, -40.0),
        corner=(0.0, -40.0),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (80, 125)

    response = native_call(
        octagon_arguments(
            sketch,
            geometry_count=80,
            center=(0.0, -40.0),
            corner=(6.0, -40.0),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (89, 148)
    assert response["center_mm"] == [0.0, -40.0, 0.0]
    assert response["corner_mm"] == [6.0, -40.0, 0.0]
    assert response["radius_mm"] == 6.0
    assert response["side_count"] == 8
    diagonal = 3.0 * math.sqrt(2.0)
    assert all(
        math.isclose(value, target, rel_tol=0.0, abs_tol=1.0e-9)
        for value, target in zip(
            response["vertices_mm"][1],
            (diagonal, -40.0 + diagonal, 0.0),
            strict=True,
        )
    )
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == list(range(80, 88))
    circle = response["construction_circle"]
    assert circle["index"] == 88
    assert circle["construction"] is True
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(125, 148))
    assert [item["type"] for item in constraints] == [
        *(["Coincident"] * 8),
        *(["Equal"] * 7),
        *(["PointOnObject"] * 8),
    ]
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Create Native Sketch Octagon"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (80, 125)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (89, 148)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "construction_circle": circle,
        "constraints": constraints,
    }


def verify_reopened_octagon(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(80, 88)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    circle = serialize_sketch_geometry(sketch, 88)
    for key in (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "closed",
    ):
        assert circle[key] == expected["construction_circle"][key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(125, 148)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_arbitrary_regular_polygon_case(
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
    invalid = regular_polygon_arguments(
        sketch,
        geometry_count=89,
        center=(25.0, -40.0),
        corner=(31.0, -40.0),
        side_count=2,
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (89, 148)

    response = native_call(
        regular_polygon_arguments(
            sketch,
            geometry_count=89,
            center=(25.0, -40.0),
            corner=(31.0, -40.0),
            side_count=9,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (99, 174)
    assert response["center_mm"] == [25.0, -40.0, 0.0]
    assert response["corner_mm"] == [31.0, -40.0, 0.0]
    assert response["radius_mm"] == 6.0
    assert response["side_count"] == 9
    expected_second = (
        25.0 + 6.0 * math.cos(math.tau / 9.0),
        -40.0 + 6.0 * math.sin(math.tau / 9.0),
        0.0,
    )
    assert all(
        math.isclose(value, target, rel_tol=0.0, abs_tol=1.0e-9)
        for value, target in zip(
            response["vertices_mm"][1],
            expected_second,
            strict=True,
        )
    )
    geometries = response["geometries"]
    assert [item["index"] for item in geometries] == list(range(89, 98))
    circle = response["construction_circle"]
    assert circle["index"] == 98
    assert circle["construction"] is True
    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(148, 174))
    assert [item["type"] for item in constraints] == [
        *(["Coincident"] * 9),
        *(["Equal"] * 8),
        *(["PointOnObject"] * 9),
    ]
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Create Native Sketch Regular Polygon"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (89, 148)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (99, 174)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": geometries,
        "construction_circle": circle,
        "constraints": constraints,
    }


def verify_reopened_arbitrary_regular_polygon(sketch: Any, expected: dict) -> None:
    geometries = [serialize_sketch_geometry(sketch, index) for index in range(89, 98)]
    for actual, saved in zip(geometries, expected["geometries"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]
    circle = serialize_sketch_geometry(sketch, 98)
    for key in (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "closed",
    ):
        assert circle[key] == expected["construction_circle"][key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(148, 174)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]
