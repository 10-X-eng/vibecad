# SPDX-License-Identifier: LGPL-2.1-or-later

"""Straight Slot lifecycle case for the rolling Native Sketch GUI gate."""

from __future__ import annotations

import math
from typing import Any, Callable

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    arc_slot_arguments,
    slot_arguments,
)


def exercise_slot_case(
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
    invalid = slot_arguments(
        sketch,
        geometry_count=99,
        start_center=(-12.0, -52.0),
        end_center=(12.0, -52.0),
        radius=0.0,
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (99, 174)

    response = native_call(
        slot_arguments(
            sketch,
            geometry_count=99,
            start_center=(-12.0, -52.0),
            end_center=(12.0, -52.0),
            radius=3.0,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (103, 179)
    assert response["start_center_mm"] == [-12.0, -52.0, 0.0]
    assert response["end_center_mm"] == [12.0, -52.0, 0.0]
    assert response["centerline_length_mm"] == 24.0
    assert response["radius_mm"] == 3.0
    assert response["closed"] is True

    arcs = response["arcs"]
    assert [item["index"] for item in arcs] == [99, 100]
    assert [item["center_mm"] for item in arcs] == [
        [-12.0, -52.0, 0.0],
        [12.0, -52.0, 0.0],
    ]
    assert [item["start_mm"] for item in arcs] == [
        [-12.0, -49.0, 0.0],
        [12.0, -55.0, 0.0],
    ]
    assert [item["end_mm"] for item in arcs] == [
        [-12.0, -55.0, 0.0],
        [12.0, -49.0, 0.0],
    ]

    lines = response["lines"]
    assert [item["index"] for item in lines] == [101, 102]
    assert [item["start_mm"] for item in lines] == [
        [-12.0, -49.0, 0.0],
        [-12.0, -55.0, 0.0],
    ]
    assert [item["end_mm"] for item in lines] == [
        [12.0, -49.0, 0.0],
        [12.0, -55.0, 0.0],
    ]

    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(174, 179))
    assert [item["type"] for item in constraints] == [
        "Tangent",
        "Tangent",
        "Tangent",
        "Tangent",
        "Equal",
    ]
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 99, "position": 1},
        {"slot": 2, "geometry_index": 101, "position": 1},
    ]
    assert constraints[3]["references"] == [
        {"slot": 1, "geometry_index": 100, "position": 1},
        {"slot": 2, "geometry_index": 102, "position": 2},
    ]
    assert constraints[4]["references"] == [
        {"slot": 1, "geometry_index": 99},
        {"slot": 2, "geometry_index": 100},
    ]
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Create Native Sketch Slot"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (99, 174)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (103, 179)
    assert edit_boundary(document, sketch, controller) == boundary
    return {"arcs": arcs, "lines": lines, "constraints": constraints}


def verify_reopened_slot(sketch: Any, expected: dict) -> None:
    arcs = [serialize_sketch_geometry(sketch, index) for index in (99, 100)]
    arc_keys = (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "first_parameter",
        "last_parameter",
        "start_mm",
        "end_mm",
        "closed",
    )
    for actual, saved in zip(arcs, expected["arcs"], strict=True):
        for key in arc_keys:
            assert actual[key] == saved[key]

    lines = [serialize_sketch_geometry(sketch, index) for index in (101, 102)]
    for actual, saved in zip(lines, expected["lines"], strict=True):
        for key in ("type_id", "kind", "construction", "start_mm", "end_mm"):
            assert actual[key] == saved[key]

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(174, 179)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def _assert_point_close(actual: list[float], expected: tuple[float, ...]) -> None:
    assert len(actual) == len(expected)
    assert all(
        math.isclose(value, target, rel_tol=0.0, abs_tol=1.0e-9)
        for value, target in zip(actual, expected, strict=True)
    )


def exercise_arc_slot_case(
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
    invalid = arc_slot_arguments(
        sketch,
        geometry_count=103,
        center=(30.0, -55.0),
        centerline_radius=10.0,
        start_degrees=20.0,
        sweep_degrees=-110.0,
        slot_radius=11.0,
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (103, 179)

    response = native_call(
        arc_slot_arguments(
            sketch,
            geometry_count=103,
            center=(30.0, -55.0),
            centerline_radius=10.0,
            start_degrees=20.0,
            sweep_degrees=-110.0,
            slot_radius=2.0,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (107, 184)
    assert response["center_mm"] == [30.0, -55.0, 0.0]
    assert response["centerline_radius_mm"] == 10.0
    assert response["start_angle_degrees"] == 20.0
    assert response["sweep_angle_degrees"] == -110.0
    assert response["slot_radius_mm"] == 2.0
    assert response["clockwise"] is True
    assert response["inner_boundary_present"] is True
    assert response["closed"] is True
    assert response["arc_roles"] == {
        "outer_boundary": 103,
        "initial_end": 104,
        "terminal_end": 105,
        "inner_boundary": 106,
    }

    arcs = response["arcs"]
    assert [item["index"] for item in arcs] == [103, 104, 105, 106]
    assert [item["radius_mm"] for item in arcs] == [12.0, 2.0, 2.0, 8.0]
    assert math.isclose(arcs[0]["first_parameter"], 1.5 * math.pi)
    assert math.isclose(arcs[0]["last_parameter"], math.radians(380.0))
    initial_center = (
        30.0 + 10.0 * math.cos(math.radians(20.0)),
        -55.0 + 10.0 * math.sin(math.radians(20.0)),
        0.0,
    )
    _assert_point_close(arcs[1]["center_mm"], initial_center)
    _assert_point_close(arcs[2]["center_mm"], (30.0, -65.0, 0.0))

    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(179, 184))
    assert [item["type"] for item in constraints] == [
        "Coincident",
        "Tangent",
        "Tangent",
        "Tangent",
        "Tangent",
    ]
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 103, "position": 3},
        {"slot": 2, "geometry_index": 106, "position": 3},
    ]
    assert constraints[1]["references"] == [
        {"slot": 1, "geometry_index": 106, "position": 1},
        {"slot": 2, "geometry_index": 105, "position": 1},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 103, "position": 2},
        {"slot": 2, "geometry_index": 104, "position": 1},
    ]
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Create Native Sketch Arc Slot"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (103, 179)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (107, 184)
    assert edit_boundary(document, sketch, controller) == boundary
    return {"arcs": arcs, "constraints": constraints}


def verify_reopened_arc_slot(sketch: Any, expected: dict) -> None:
    arcs = [serialize_sketch_geometry(sketch, index) for index in range(103, 107)]
    arc_keys = (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "first_parameter",
        "last_parameter",
        "start_mm",
        "end_mm",
        "closed",
    )
    for actual, saved in zip(arcs, expected["arcs"], strict=True):
        for key in arc_keys:
            assert actual[key] == saved[key]
    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(179, 184)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]
