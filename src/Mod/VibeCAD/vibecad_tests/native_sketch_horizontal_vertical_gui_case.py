# SPDX-License-Identifier: LGPL-2.1-or-later

"""Automatic Horizontal/Vertical lifecycle for the rolling Sketch gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    horizontal_vertical_arguments,
    line_arguments,
    point_arguments,
)


_INITIAL_GEOMETRY_COUNT = 186
_INITIAL_CONSTRAINT_COUNT = 255
_DIAGONAL_LINE_INDEX = 186
_HORIZONTAL_LINE_INDEX = 187
_HORIZONTAL_CONSTRAINT_INDEX = 255
_FIRST_POINT_INDEX = 188
_SECOND_POINT_INDEX = 189
_VERTICAL_CONSTRAINT_INDEX = 256


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
    constraint_type: str,
    references: list[dict[str, int]],
) -> None:
    assert constraint == {
        "index": index,
        "type": constraint_type,
        "driving": True,
        "active": True,
        "virtual": False,
        "references": references,
    }


def exercise_horizontal_vertical_case(
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
    diagonal = native_call(
        line_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            start=(350.0, 180.0),
            end=(354.0, 184.0),
        )
    )
    assert diagonal["geometry"]["index"] == _DIAGONAL_LINE_INDEX
    failure_undo_before = int(document.UndoCount)
    ambiguous = native_call(
        horizontal_vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_DIAGONAL_LINE_INDEX, "whole"),),
            expected_inference="horizontal",
        ),
        succeeds=False,
    )
    assert ambiguous["error_code"] == "NATIVE_SKETCH_INVALID"
    assert "diagonally ambiguous" in ambiguous["error"]
    axis = native_call(
        horizontal_vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((-1, "whole"),),
            expected_inference="horizontal",
        ),
        succeeds=False,
    )
    assert axis["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == failure_undo_before
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT

    horizontal_line = native_call(
        line_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            start=(360.0, 180.0),
            end=(366.0, 182.0),
        )
    )
    assert horizontal_line["geometry"]["index"] == _HORIZONTAL_LINE_INDEX
    line_failure_undo_before = int(document.UndoCount)
    before_line = _delta(
        sketch,
        (_HORIZONTAL_LINE_INDEX, 1),
        (_HORIZONTAL_LINE_INDEX, 2),
    )
    assert before_line == (6.0, 2.0)
    mismatch = native_call(
        horizontal_vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=((_HORIZONTAL_LINE_INDEX, "whole"),),
            expected_inference="vertical",
        ),
        succeeds=False,
    )
    assert mismatch["error_code"] == "NATIVE_SKETCH_INVALID"
    nonwhole = native_call(
        horizontal_vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=((_HORIZONTAL_LINE_INDEX, "start"),),
            expected_inference="horizontal",
        ),
        succeeds=False,
    )
    assert nonwhole["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    stale = native_call(
        horizontal_vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_HORIZONTAL_LINE_INDEX, "whole"),),
            expected_inference="horizontal",
        ),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == line_failure_undo_before

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    horizontal = native_call(
        horizontal_vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=((_HORIZONTAL_LINE_INDEX, "whole"),),
            expected_inference="horizontal",
        )
    )
    assert horizontal["operation"] == "constrain_horizontal_vertical"
    assert horizontal["target_form"] == "line"
    assert horizontal["inference"] == "horizontal"
    assert horizontal["measured_before"] == {
        "delta_x": 6.0,
        "delta_y": 2.0,
        "unit": "mm",
    }
    assert math.isclose(horizontal["measured_after"]["delta_y"], 0.0, abs_tol=1.0e-7)
    horizontal_constraint = horizontal["constraint"]
    _assert_constraint(
        horizontal_constraint,
        index=_HORIZONTAL_CONSTRAINT_INDEX,
        constraint_type="Horizontal",
        references=[{"slot": 1, "geometry_index": _HORIZONTAL_LINE_INDEX}],
    )
    assert horizontal["assistant_undo_available"] is True
    assert len(horizontal["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Horizontal/Vertical"
    assert int(document.UndoCount) == 20
    assert _selection_state(document) == selection

    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT
    assert _delta(
        sketch,
        (_HORIZONTAL_LINE_INDEX, 1),
        (_HORIZONTAL_LINE_INDEX, 2),
    ) == before_line
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1
    assert serialize_sketch_constraint(
        sketch,
        _HORIZONTAL_CONSTRAINT_INDEX,
    ) == horizontal_constraint
    assert math.isclose(
        _delta(
            sketch,
            (_HORIZONTAL_LINE_INDEX, 1),
            (_HORIZONTAL_LINE_INDEX, 2),
        )[1],
        0.0,
        abs_tol=1.0e-7,
    )

    for geometry_count, position, expected_index in (
        (_INITIAL_GEOMETRY_COUNT + 2, (370.0, 180.0), _FIRST_POINT_INDEX),
        (_INITIAL_GEOMETRY_COUNT + 3, (372.0, 186.0), _SECOND_POINT_INDEX),
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
        horizontal_vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 4,
            external_geometry_count=1,
            selection=(
                (_FIRST_POINT_INDEX, "whole"),
                (_SECOND_POINT_INDEX, "start"),
            ),
            expected_inference="vertical",
        ),
        succeeds=False,
    )
    assert whole_point["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == point_failure_undo_before

    vertical = native_call(
        horizontal_vertical_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 4,
            external_geometry_count=1,
            selection=(
                (_FIRST_POINT_INDEX, "start"),
                (_SECOND_POINT_INDEX, "start"),
            ),
            expected_inference="vertical",
        )
    )
    assert vertical["target_form"] == "point_pair"
    assert vertical["inference"] == "vertical"
    assert vertical["measured_before"] == {
        "delta_x": 2.0,
        "delta_y": 6.0,
        "unit": "mm",
    }
    assert math.isclose(vertical["measured_after"]["delta_x"], 0.0, abs_tol=1.0e-7)
    vertical_constraint = vertical["constraint"]
    _assert_constraint(
        vertical_constraint,
        index=_VERTICAL_CONSTRAINT_INDEX,
        constraint_type="Vertical",
        references=[
            {"slot": 1, "geometry_index": _FIRST_POINT_INDEX, "position": 1},
            {"slot": 2, "geometry_index": _SECOND_POINT_INDEX, "position": 1},
        ],
    )
    assert document.UndoNames[0] == "Create Native Sketch Horizontal/Vertical"

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
    assert serialize_sketch_constraint(
        sketch,
        _VERTICAL_CONSTRAINT_INDEX,
    ) == vertical_constraint
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
        "diagonal_line": serialize_sketch_geometry(sketch, _DIAGONAL_LINE_INDEX),
        "horizontal": {
            "line": serialize_sketch_geometry(sketch, _HORIZONTAL_LINE_INDEX),
            "constraint": horizontal_constraint,
        },
        "vertical": {
            "first": serialize_sketch_geometry(sketch, _FIRST_POINT_INDEX),
            "second": serialize_sketch_geometry(sketch, _SECOND_POINT_INDEX),
            "constraint": vertical_constraint,
        },
    }


def _verify_geometry(sketch: Any, index: int, expected: dict[str, Any]) -> None:
    observed = serialize_sketch_geometry(sketch, index)
    for key in expected:
        if key != "tag":
            assert observed[key] == expected[key]
    assert observed["tag"]
    assert expected["tag"]


def verify_reopened_horizontal_vertical(
    sketch: Any,
    expected: dict[str, Any],
) -> None:
    _verify_geometry(sketch, _DIAGONAL_LINE_INDEX, expected["diagonal_line"])
    _verify_geometry(
        sketch,
        _HORIZONTAL_LINE_INDEX,
        expected["horizontal"]["line"],
    )
    _verify_geometry(sketch, _FIRST_POINT_INDEX, expected["vertical"]["first"])
    _verify_geometry(sketch, _SECOND_POINT_INDEX, expected["vertical"]["second"])
    assert serialize_sketch_constraint(
        sketch,
        _HORIZONTAL_CONSTRAINT_INDEX,
    ) == expected["horizontal"]["constraint"]
    assert serialize_sketch_constraint(
        sketch,
        _VERTICAL_CONSTRAINT_INDEX,
    ) == expected["vertical"]["constraint"]
    assert math.isclose(
        _delta(
            sketch,
            (_HORIZONTAL_LINE_INDEX, 1),
            (_HORIZONTAL_LINE_INDEX, 2),
        )[1],
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
