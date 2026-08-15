# SPDX-License-Identifier: LGPL-2.1-or-later

"""Center-radius Circle lifecycle case for the rolling Native Sketch GUI gate."""

from __future__ import annotations

from typing import Any, Callable

from VibeCADNativeSketchState import serialize_sketch_geometry
from vibecad_tests.native_sketch_geometry_gui_support import (
    circle_arguments,
    three_point_circle_arguments,
)


def exercise_circle_case(
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
    invalid = circle_arguments(
        sketch,
        geometry_count=19,
        center=(25.0, -5.0),
        radius=0.0,
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (19, 11)

    response = native_call(
        circle_arguments(
            sketch,
            geometry_count=19,
            center=(25.0, -5.0),
            radius=7.0,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (20, 11)
    geometry = response["geometry"]
    assert geometry["index"] == 19
    assert geometry["type_id"] == "Part::GeomCircle"
    assert geometry["kind"] == "circle"
    assert geometry["construction"] is False
    assert geometry["center_mm"] == [25.0, -5.0, 0.0]
    assert geometry["axis"] == [0.0, 0.0, 1.0]
    assert geometry["radius_mm"] == 7.0
    assert geometry["closed"] is True
    assert response["assistant_undo_available"] is True
    assert len(response["receipt"]["changed"]) == 1
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Circle"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (19, 11)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (20, 11)
    assert serialize_sketch_geometry(sketch, 19) == geometry
    assert edit_boundary(document, sketch, controller) == boundary
    return geometry


def verify_reopened_circle(sketch: Any, expected: dict) -> None:
    geometry = serialize_sketch_geometry(sketch, 19)
    assert geometry["type_id"] == "Part::GeomCircle"
    assert geometry["construction"] is False
    assert geometry["center_mm"] == [25.0, -5.0, 0.0]
    assert geometry["axis"] == [0.0, 0.0, 1.0]
    assert geometry["radius_mm"] == 7.0
    assert geometry["closed"] is True
    for key in (
        "type_id",
        "kind",
        "construction",
        "center_mm",
        "axis",
        "radius_mm",
        "closed",
    ):
        assert geometry[key] == expected[key]


def exercise_three_point_circle_case(
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
    invalid = three_point_circle_arguments(
        sketch,
        geometry_count=20,
        points=((-10.0, 20.0), (0.0, 20.0), (10.0, 20.0)),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (20, 11)

    response = native_call(
        three_point_circle_arguments(
            sketch,
            geometry_count=20,
            points=((-10.0, 20.0), (10.0, 20.0), (0.0, 30.0)),
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (21, 11)
    geometry = response["geometry"]
    assert geometry["index"] == 20
    assert geometry["type_id"] == "Part::GeomCircle"
    assert geometry["center_mm"] == [0.0, 20.0, 0.0]
    assert geometry["radius_mm"] == 10.0
    assert geometry["closed"] is True
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Three-Point Circle"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (20, 11)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (21, 11)
    assert edit_boundary(document, sketch, controller) == boundary
    return geometry


def verify_reopened_three_point_circle(sketch: Any, expected: dict) -> None:
    geometry = serialize_sketch_geometry(sketch, 20)
    for key in ("type_id", "kind", "construction", "center_mm", "radius_mm", "closed"):
        assert geometry[key] == expected[key]
