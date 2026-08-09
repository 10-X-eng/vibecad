# SPDX-License-Identifier: LGPL-2.1-or-later

"""Point, Line, and Polyline cases for the rolling Native Sketch GUI gate."""

from __future__ import annotations

from typing import Any, Callable

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADEditState import active_edit_object
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    line_arguments,
    point_arguments,
    polyline_arguments,
)


def exercise_basic_geometry_case(
    *,
    sketch: Any,
    other_sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> None:
    before_invalid = (
        int(sketch.GeometryCount),
        int(sketch.ConstraintCount),
        int(document.UndoCount),
        int(document.RedoCount),
    )
    missing_count = point_arguments(sketch, geometry_count=0, x=1.0, y=2.0)
    del missing_count["expected_constraint_count"]
    invalid_schema = native_call(missing_count, succeeds=False)
    assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"

    wrong_target = point_arguments(other_sketch, geometry_count=0, x=1.0, y=2.0)
    target_failure = native_call(wrong_target, succeeds=False)
    assert target_failure["error_code"] == "NATIVE_SKETCH_INVALID"

    stale_count = point_arguments(sketch, geometry_count=1, x=1.0, y=2.0)
    state_failure = native_call(stale_count, succeeds=False)
    assert state_failure["error_code"] == "NATIVE_SKETCH_INVALID"
    degenerate_line = line_arguments(
        sketch,
        geometry_count=0,
        start=(3.0, 4.0),
        end=(3.0, 4.0),
    )
    line_failure = native_call(degenerate_line, succeeds=False)
    assert line_failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert before_invalid == (
        int(sketch.GeometryCount),
        int(sketch.ConstraintCount),
        int(document.UndoCount),
        int(document.RedoCount),
    )

    arguments = point_arguments(sketch, geometry_count=0, x=12.5, y=-4.0)
    undo_before = int(document.UndoCount)
    response = native_call(arguments, call_id="successful-point")
    assert set(response) == {
        "ok",
        "sketch",
        "geometry",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
        "receipt",
        "assistant_undo_available",
    }
    assert response["geometry_count"] == 1
    assert response["constraint_count"] == 0
    assert response["geometry"]["index"] == 0
    assert response["geometry"]["type_id"] == "Part::GeomPoint"
    assert response["geometry"]["construction"] is False
    assert response["geometry"]["position_mm"] == [12.5, -4.0, 0.0]
    assert response["assistant_undo_available"] is True
    assert response["receipt"]["created"] == []
    assert len(response["receipt"]["changed"]) == 1
    assert response["receipt"]["changed"][0]["object_name"] == sketch.Name
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Point"
    assert int(sketch.GeometryCount) == 1
    assert serialize_sketch_geometry(sketch, 0) == response["geometry"]

    duplicate = native_call(arguments, call_id="successful-point")
    assert duplicate == response
    assert int(sketch.GeometryCount) == 1
    assert int(document.UndoCount) == undo_before + 1

    before_rollback = (
        int(sketch.GeometryCount),
        int(sketch.ConstraintCount),
        int(document.UndoCount),
        tuple(document.UndoNames),
    )
    original_handlers = runtime_module._OPERATIONS["create_point"]

    def reject_after_creation(_document, _draft):
        raise NativeSketchError("Forced Point postcondition failure.")

    runtime_module._OPERATIONS["create_point"] = (
        *original_handlers[:3],
        reject_after_creation,
        original_handlers[4],
    )
    try:
        rollback = native_call(
            point_arguments(sketch, geometry_count=1, x=-3.0, y=7.0),
            succeeds=False,
        )
    finally:
        runtime_module._OPERATIONS["create_point"] = original_handlers
    assert rollback["error_code"] == "NATIVE_SKETCH_INVALID"
    assert before_rollback == (
        int(sketch.GeometryCount),
        int(sketch.ConstraintCount),
        int(document.UndoCount),
        tuple(document.UndoNames),
    )

    document.undo()
    process_events(16)
    assert active_edit_object() is sketch
    assert int(sketch.GeometryCount) == 0
    document.redo()
    process_events(16)
    assert active_edit_object() is sketch
    assert int(sketch.GeometryCount) == 1
    assert serialize_sketch_geometry(sketch, 0)["position_mm"] == [12.5, -4.0, 0.0]
    assert edit_boundary(document, sketch, controller) == boundary

    line_arguments_payload = line_arguments(
        sketch,
        geometry_count=1,
        start=(-6.0, 2.0),
        end=(9.0, 7.5),
    )
    line_undo_before = int(document.UndoCount)
    line_response = native_call(line_arguments_payload)
    assert line_response["geometry_count"] == 2
    assert line_response["constraint_count"] == 0
    assert line_response["geometry"]["index"] == 1
    assert line_response["geometry"]["type_id"] == "Part::GeomLineSegment"
    assert line_response["geometry"]["construction"] is False
    assert line_response["geometry"]["start_mm"] == [-6.0, 2.0, 0.0]
    assert line_response["geometry"]["end_mm"] == [9.0, 7.5, 0.0]
    assert line_response["assistant_undo_available"] is True
    assert line_response["receipt"]["created"] == []
    assert len(line_response["receipt"]["changed"]) == 1
    assert line_response["receipt"]["changed"][0]["object_name"] == sketch.Name
    assert int(document.UndoCount) == line_undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Line"
    assert serialize_sketch_geometry(sketch, 1) == line_response["geometry"]

    document.undo()
    process_events(16)
    assert active_edit_object() is sketch
    assert int(sketch.GeometryCount) == 1
    document.redo()
    process_events(16)
    assert active_edit_object() is sketch
    assert int(sketch.GeometryCount) == 2
    assert serialize_sketch_geometry(sketch, 1)["start_mm"] == [-6.0, 2.0, 0.0]
    assert serialize_sketch_geometry(sketch, 1)["end_mm"] == [9.0, 7.5, 0.0]
    assert edit_boundary(document, sketch, controller) == boundary

    polyline_undo_before = int(document.UndoCount)
    degenerate_polyline = polyline_arguments(
        sketch,
        geometry_count=2,
        vertices=((0.0, 0.0), (0.0, 0.0)),
        closed=False,
    )
    polyline_failure = native_call(degenerate_polyline, succeeds=False)
    assert polyline_failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == polyline_undo_before
    assert int(sketch.GeometryCount) == 2
    assert int(sketch.ConstraintCount) == 0

    polyline_arguments_payload = polyline_arguments(
        sketch,
        geometry_count=2,
        vertices=((-8.0, -5.0), (-2.0, -1.0), (4.0, -3.0), (11.0, 1.0)),
        closed=False,
    )
    polyline_response = native_call(polyline_arguments_payload)
    assert polyline_response["segment_count"] == 3
    assert polyline_response["closed"] is False
    assert polyline_response["geometry_count"] == 5
    assert polyline_response["constraint_count"] == 2
    assert [item["index"] for item in polyline_response["geometries"]] == [2, 3, 4]
    assert [item["start_mm"] for item in polyline_response["geometries"]] == [
        [-8.0, -5.0, 0.0],
        [-2.0, -1.0, 0.0],
        [4.0, -3.0, 0.0],
    ]
    assert [item["end_mm"] for item in polyline_response["geometries"]] == [
        [-2.0, -1.0, 0.0],
        [4.0, -3.0, 0.0],
        [11.0, 1.0, 0.0],
    ]
    assert [item["references"] for item in polyline_response["constraints"]] == [
        [
            {"slot": 1, "geometry_index": 2, "position": 2},
            {"slot": 2, "geometry_index": 3, "position": 1},
        ],
        [
            {"slot": 1, "geometry_index": 3, "position": 2},
            {"slot": 2, "geometry_index": 4, "position": 1},
        ],
    ]
    assert polyline_response["assistant_undo_available"] is True
    assert len(polyline_response["receipt"]["changed"]) == 1
    assert int(document.UndoCount) == polyline_undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Polyline"

    document.undo()
    process_events(16)
    assert active_edit_object() is sketch
    assert int(sketch.GeometryCount) == 2
    assert int(sketch.ConstraintCount) == 0
    document.redo()
    process_events(16)
    assert active_edit_object() is sketch
    assert int(sketch.GeometryCount) == 5
    assert int(sketch.ConstraintCount) == 2
    assert edit_boundary(document, sketch, controller) == boundary


def verify_reopened_basic_geometry(
    sketch: Any,
    *,
    line_construction: bool = False,
) -> None:
    point = serialize_sketch_geometry(sketch, 0)
    assert point["type_id"] == "Part::GeomPoint"
    assert point["construction"] is False
    assert point["position_mm"] == [12.5, -4.0, 0.0]

    line = serialize_sketch_geometry(sketch, 1)
    assert line["type_id"] == "Part::GeomLineSegment"
    assert line["construction"] is line_construction
    assert line["start_mm"] == [-6.0, 2.0, 0.0]
    assert line["end_mm"] == [9.0, 7.5, 0.0]

    assert [
        serialize_sketch_geometry(sketch, index)["start_mm"] for index in (2, 3, 4)
    ] == [
        [-8.0, -5.0, 0.0],
        [-2.0, -1.0, 0.0],
        [4.0, -3.0, 0.0],
    ]
    assert [
        serialize_sketch_constraint(sketch, index)["references"]
        for index in (0, 1)
    ] == [
        [
            {"slot": 1, "geometry_index": 2, "position": 2},
            {"slot": 2, "geometry_index": 3, "position": 1},
        ],
        [
            {"slot": 1, "geometry_index": 3, "position": 2},
            {"slot": 2, "geometry_index": 4, "position": 1},
        ],
    ]
