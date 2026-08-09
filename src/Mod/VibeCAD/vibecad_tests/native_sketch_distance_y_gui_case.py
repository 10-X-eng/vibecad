# SPDX-License-Identifier: LGPL-2.1-or-later

"""Vertical Distance lifecycle case for the rolling Native Sketch gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    point_arguments,
    vertical_distance_arguments,
)


_INITIAL_GEOMETRY_COUNT = 173
_INITIAL_CONSTRAINT_COUNT = 244
_POINT_INDEX = 173
_CONSTRAINT_INDEX = 244


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def exercise_vertical_distance_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT,
        _INITIAL_CONSTRAINT_COUNT,
    )
    undo_before = int(document.UndoCount)
    axis = native_call(
        vertical_distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            external_geometry_count=1,
            selection=((-1, "whole"),),
            value=10.0,
        ),
        succeeds=False,
    )
    assert axis["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before

    point_response = native_call(
        point_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            x=15.0,
            y=-14.0,
        )
    )
    assert point_response["geometry"]["index"] == _POINT_INDEX
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT + 1,
        _INITIAL_CONSTRAINT_COUNT,
    )

    dimension_undo_before = int(document.UndoCount)
    stale_reference = native_call(
        vertical_distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_POINT_INDEX, "start"),),
            value=-13.0,
            driving=False,
        ),
        succeeds=False,
    )
    assert stale_reference["error_code"] == "NATIVE_SKETCH_INVALID"
    wrong_unit = native_call(
        vertical_distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_POINT_INDEX, "start"),),
            value=-35.0,
            unit="deg",
        ),
        succeeds=False,
    )
    assert wrong_unit["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == dimension_undo_before

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    response = native_call(
        vertical_distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_POINT_INDEX, "start"),),
            value=-35.0,
        )
    )
    assert response["operation"] == "constrain_distance_y"
    assert response["target_form"] == "point_coordinate"
    assert response["geometry_count"] == _INITIAL_GEOMETRY_COUNT + 1
    assert response["constraint_count"] == _INITIAL_CONSTRAINT_COUNT + 1
    assert response["measured_before"] == {"value": -14.0, "unit": "mm"}
    assert response["measured_after"] == {"value": -35.0, "unit": "mm"}
    constraint = response["constraint"]
    assert constraint["index"] == _CONSTRAINT_INDEX
    assert constraint["type"] == "DistanceY"
    assert constraint["driving"] is True
    assert constraint["active"] is True
    assert constraint["virtual"] is False
    assert constraint["references"] == [
        {"slot": 1, "geometry_index": _POINT_INDEX, "position": 1}
    ]
    assert math.isclose(constraint["value"], -35.0, abs_tol=1.0e-9)
    assert response["assistant_undo_available"] is True
    assert len(response["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Vertical Distance"
    assert int(document.UndoCount) == 20
    assert _selection_state(document) == selection
    point = serialize_sketch_geometry(sketch, _POINT_INDEX)
    assert point["position_mm"] == [15.0, -35.0, 0.0]

    redundant_undo_before = int(document.UndoCount)
    redundant = native_call(
        vertical_distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_POINT_INDEX, "start"),),
            value=-35.0,
        ),
        succeeds=False,
    )
    assert redundant["error_code"] == "NATIVE_SKETCH_INVALID"
    assert "no constraint was added" in redundant["error"]
    assert int(document.UndoCount) == redundant_undo_before
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1

    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT
    assert serialize_sketch_geometry(sketch, _POINT_INDEX)["position_mm"] == [
        15.0,
        -14.0,
        0.0,
    ]
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1
    assert serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX) == constraint
    point = serialize_sketch_geometry(sketch, _POINT_INDEX)
    assert point["position_mm"] == [15.0, -35.0, 0.0]
    assert edit_boundary(document, sketch, controller) == boundary
    return {"point": point, "constraint": constraint}


def verify_reopened_vertical_distance(sketch: Any, expected: dict) -> None:
    point = serialize_sketch_geometry(sketch, _POINT_INDEX)
    constraint = serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX)
    for key in (
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "position_mm",
    ):
        assert point[key] == expected["point"][key]
    assert point["tag"]
    assert expected["point"]["tag"]
    assert constraint == expected["constraint"]
    assert constraint["type"] == "DistanceY"
    assert constraint["driving"] is True
    assert math.isclose(constraint["value"], -35.0, abs_tol=1.0e-9)
