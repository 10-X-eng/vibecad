# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit Vertical lifecycle for the rolling Sketch gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    line_arguments,
    point_arguments,
    vertical_arguments,
)


_INITIAL_GEOMETRY_COUNT = 193
_INITIAL_CONSTRAINT_COUNT = 259
_LINE_INDEX = 193
_LINE_CONSTRAINT_INDEX = 259
_FIRST_POINT_INDEX = 194
_SECOND_POINT_INDEX = 195
_POINT_CONSTRAINT_INDEX = 260


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _point(sketch: Any, index: int, position: int) -> tuple[float, float]:
    value = sketch.getPoint(index, position)
    return float(value.x), float(value.y)


def _delta(
    sketch: Any,
    first: tuple[int, int],
    second: tuple[int, int],
) -> tuple[float, float]:
    first_point = _point(sketch, *first)
    second_point = _point(sketch, *second)
    return (
        second_point[0] - first_point[0],
        second_point[1] - first_point[1],
    )


def _assert_constraint(
    constraint: dict[str, Any],
    *,
    index: int,
    references: list[dict[str, int]],
) -> None:
    assert constraint == {
        "index": index,
        "type": "Vertical",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": references,
    }


def exercise_vertical_case(
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
    line = native_call(
        line_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            start=(400.0, 180.0),
            end=(408.0, 182.0),
        )
    )
    assert line["geometry"]["index"] == _LINE_INDEX
    before_line = _delta(sketch, (_LINE_INDEX, 1), (_LINE_INDEX, 2))
    assert before_line == (8.0, 2.0)
    failure_undo_before = int(document.UndoCount)

    axis = native_call(
        vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((-1, "whole"),),
        ),
        succeeds=False,
    )
    assert axis["error_code"] == "NATIVE_SKETCH_INVALID"
    nonwhole = native_call(
        vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_LINE_INDEX, "start"),),
        ),
        succeeds=False,
    )
    assert nonwhole["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    stale = native_call(
        vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            external_geometry_count=1,
            selection=((_LINE_INDEX, "whole"),),
        ),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    unexpected_inference = vertical_arguments(
        sketch,
        geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
        external_geometry_count=1,
        selection=((_LINE_INDEX, "whole"),),
    )
    unexpected_inference["expected_inference"] = "vertical"
    closed = native_call(unexpected_inference, succeeds=False)
    assert closed["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == failure_undo_before
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    vertical_line = native_call(
        vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_LINE_INDEX, "whole"),),
        )
    )
    assert vertical_line["operation"] == "constrain_vertical"
    assert vertical_line["target_form"] == "line"
    assert vertical_line["alignment"] == "vertical"
    assert vertical_line["measured_before"] == {
        "delta_x": 8.0,
        "delta_y": 2.0,
        "unit": "mm",
    }
    assert math.isclose(
        vertical_line["measured_after"]["delta_x"],
        0.0,
        abs_tol=1.0e-7,
    )
    line_constraint = vertical_line["constraint"]
    _assert_constraint(
        line_constraint,
        index=_LINE_CONSTRAINT_INDEX,
        references=[{"slot": 1, "geometry_index": _LINE_INDEX}],
    )
    assert vertical_line["assistant_undo_available"] is True
    assert len(vertical_line["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Vertical"
    assert int(document.UndoCount) == 20
    assert _selection_state(document) == selection

    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT
    assert _delta(sketch, (_LINE_INDEX, 1), (_LINE_INDEX, 2)) == before_line
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1
    assert serialize_sketch_constraint(sketch, _LINE_CONSTRAINT_INDEX) == (
        line_constraint
    )
    assert math.isclose(
        _delta(sketch, (_LINE_INDEX, 1), (_LINE_INDEX, 2))[0],
        0.0,
        abs_tol=1.0e-7,
    )

    for geometry_count, position, expected_index in (
        (_INITIAL_GEOMETRY_COUNT + 1, (410.0, 180.0), _FIRST_POINT_INDEX),
        (_INITIAL_GEOMETRY_COUNT + 2, (418.0, 184.0), _SECOND_POINT_INDEX),
    ):
        response = native_call(
            point_arguments(
                sketch,
                geometry_count=geometry_count,
                x=position[0],
                y=position[1],
            )
        )
        assert response["geometry"]["index"] == expected_index
    before_points = (
        _point(sketch, _FIRST_POINT_INDEX, 1),
        _point(sketch, _SECOND_POINT_INDEX, 1),
    )
    point_failure_undo_before = int(document.UndoCount)
    whole_point = native_call(
        vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 3,
            external_geometry_count=1,
            selection=(
                (_FIRST_POINT_INDEX, "whole"),
                (_SECOND_POINT_INDEX, "start"),
            ),
        ),
        succeeds=False,
    )
    assert whole_point["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == point_failure_undo_before

    vertical_points = native_call(
        vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 3,
            external_geometry_count=1,
            selection=(
                (_FIRST_POINT_INDEX, "start"),
                (_SECOND_POINT_INDEX, "start"),
            ),
        )
    )
    assert vertical_points["target_form"] == "point_pair"
    assert vertical_points["alignment"] == "vertical"
    assert vertical_points["measured_before"] == {
        "delta_x": 8.0,
        "delta_y": 4.0,
        "unit": "mm",
    }
    assert math.isclose(
        vertical_points["measured_after"]["delta_x"],
        0.0,
        abs_tol=1.0e-7,
    )
    point_constraint = vertical_points["constraint"]
    _assert_constraint(
        point_constraint,
        index=_POINT_CONSTRAINT_INDEX,
        references=[
            {"slot": 1, "geometry_index": _FIRST_POINT_INDEX, "position": 1},
            {"slot": 2, "geometry_index": _SECOND_POINT_INDEX, "position": 1},
        ],
    )
    assert document.UndoNames[0] == "Create Native Sketch Vertical"

    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1
    assert (
        _point(sketch, _FIRST_POINT_INDEX, 1),
        _point(sketch, _SECOND_POINT_INDEX, 1),
    ) == before_points
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 2
    assert serialize_sketch_constraint(sketch, _POINT_CONSTRAINT_INDEX) == (
        point_constraint
    )
    assert math.isclose(
        _delta(
            sketch,
            (_FIRST_POINT_INDEX, 1),
            (_SECOND_POINT_INDEX, 1),
        )[0],
        0.0,
        abs_tol=1.0e-7,
    )
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "line": {
            "geometry": serialize_sketch_geometry(sketch, _LINE_INDEX),
            "constraint": line_constraint,
        },
        "points": {
            "first": serialize_sketch_geometry(sketch, _FIRST_POINT_INDEX),
            "second": serialize_sketch_geometry(sketch, _SECOND_POINT_INDEX),
            "constraint": point_constraint,
        },
    }


def _verify_geometry(sketch: Any, index: int, expected: dict[str, Any]) -> None:
    observed = serialize_sketch_geometry(sketch, index)
    for key in expected:
        if key != "tag":
            assert observed[key] == expected[key]
    assert observed["tag"]
    assert expected["tag"]


def verify_reopened_vertical(sketch: Any, expected: dict[str, Any]) -> None:
    _verify_geometry(sketch, _LINE_INDEX, expected["line"]["geometry"])
    _verify_geometry(sketch, _FIRST_POINT_INDEX, expected["points"]["first"])
    _verify_geometry(sketch, _SECOND_POINT_INDEX, expected["points"]["second"])
    assert serialize_sketch_constraint(sketch, _LINE_CONSTRAINT_INDEX) == (
        expected["line"]["constraint"]
    )
    assert serialize_sketch_constraint(sketch, _POINT_CONSTRAINT_INDEX) == (
        expected["points"]["constraint"]
    )
    assert math.isclose(
        _delta(sketch, (_LINE_INDEX, 1), (_LINE_INDEX, 2))[0],
        0.0,
        abs_tol=1.0e-7,
    )
    assert math.isclose(
        _delta(
            sketch,
            (_FIRST_POINT_INDEX, 1),
            (_SECOND_POINT_INDEX, 1),
        )[0],
        0.0,
        abs_tol=1.0e-7,
    )
