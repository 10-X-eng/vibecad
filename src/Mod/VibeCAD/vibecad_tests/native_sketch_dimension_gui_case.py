# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contextual Dimension lifecycle case for the rolling Native Sketch gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    dimension_arguments,
    line_arguments,
)


_INITIAL_GEOMETRY_COUNT = 171
_INITIAL_CONSTRAINT_COUNT = 242
_DIAGONAL_LINE_INDEX = 1
_DIMENSION_LINE_INDEX = 171
_DIMENSION_CONSTRAINT_INDEX = 242


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def exercise_dimension_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT,
        _INITIAL_CONSTRAINT_COUNT,
    )
    undo_before = int(document.UndoCount)
    ambiguous = native_call(
        dimension_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            external_geometry_count=1,
            selection=((_DIAGONAL_LINE_INDEX, "whole"),),
            expected_inference="distance",
            value=16.0,
            unit="mm",
        ),
        succeeds=False,
    )
    assert ambiguous["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before

    line_response = native_call(
        line_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            start=(40.0, -40.0),
            end=(50.0, -40.0),
        )
    )
    assert line_response["geometry"]["index"] == _DIMENSION_LINE_INDEX
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT + 1,
        _INITIAL_CONSTRAINT_COUNT,
    )

    dimension_undo_before = int(document.UndoCount)
    stale = native_call(
        dimension_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_DIMENSION_LINE_INDEX, "whole"),),
            expected_inference="distance_y",
            value=20.0,
            unit="mm",
        ),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    wrong_unit = native_call(
        dimension_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_DIMENSION_LINE_INDEX, "whole"),),
            expected_inference="distance_x",
            value=20.0,
            unit="deg",
        ),
        succeeds=False,
    )
    assert wrong_unit["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == dimension_undo_before

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    response = native_call(
        dimension_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_DIMENSION_LINE_INDEX, "whole"),),
            expected_inference="distance_x",
            value=20.0,
            unit="mm",
        )
    )
    assert response["operation"] == "infer_dimension"
    assert response["inference"] == "distance_x"
    assert response["geometry_count"] == _INITIAL_GEOMETRY_COUNT + 1
    assert response["constraint_count"] == _INITIAL_CONSTRAINT_COUNT + 1
    assert response["measured_before"] == {"value": 10.0, "unit": "mm"}
    constraint = response["constraint"]
    assert constraint["index"] == _DIMENSION_CONSTRAINT_INDEX
    assert constraint["type"] == "DistanceX"
    assert constraint["driving"] is True
    assert constraint["active"] is True
    assert constraint["virtual"] is False
    assert constraint["references"] == [
        {
            "slot": 1,
            "geometry_index": _DIMENSION_LINE_INDEX,
            "position": 1,
        },
        {
            "slot": 2,
            "geometry_index": _DIMENSION_LINE_INDEX,
            "position": 2,
        },
    ]
    assert math.isclose(constraint["value"], 20.0, abs_tol=1.0e-9)
    assert response["assistant_undo_available"] is True
    assert len(response["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Dimension"
    assert int(document.UndoCount) == 20
    assert _selection_state(document) == selection
    line = serialize_sketch_geometry(sketch, _DIMENSION_LINE_INDEX)
    assert math.isclose(
        abs(line["end_mm"][0] - line["start_mm"][0]),
        20.0,
        abs_tol=1.0e-7,
    )

    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT
    assert math.isclose(
        abs(
            serialize_sketch_geometry(sketch, _DIMENSION_LINE_INDEX)["end_mm"][0]
            - serialize_sketch_geometry(sketch, _DIMENSION_LINE_INDEX)["start_mm"][0]
        ),
        10.0,
        abs_tol=1.0e-7,
    )
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1
    reopened_constraint = serialize_sketch_constraint(
        sketch,
        _DIMENSION_CONSTRAINT_INDEX,
    )
    assert reopened_constraint == constraint
    line = serialize_sketch_geometry(sketch, _DIMENSION_LINE_INDEX)
    assert math.isclose(
        abs(line["end_mm"][0] - line["start_mm"][0]),
        20.0,
        abs_tol=1.0e-7,
    )
    assert edit_boundary(document, sketch, controller) == boundary
    return {"line": line, "constraint": constraint}


def verify_reopened_dimension(sketch: Any, expected: dict) -> None:
    line = serialize_sketch_geometry(sketch, _DIMENSION_LINE_INDEX)
    constraint = serialize_sketch_constraint(sketch, _DIMENSION_CONSTRAINT_INDEX)
    for key in (
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "start_mm",
        "end_mm",
        "first_parameter",
        "last_parameter",
        "periodic",
        "closed",
    ):
        assert line[key] == expected["line"][key]
    assert line["tag"]
    assert expected["line"]["tag"]
    assert constraint == expected["constraint"], (
        constraint,
        expected["constraint"],
    )
    assert constraint["type"] == "DistanceX"
    assert constraint["driving"] is True
    assert math.isclose(constraint["value"], 20.0, abs_tol=1.0e-9)
