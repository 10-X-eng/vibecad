# SPDX-License-Identifier: LGPL-2.1-or-later

"""General Angle lifecycle case for the rolling Sketch gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    angle_arguments,
    line_arguments,
)


_INITIAL_GEOMETRY_COUNT = 178
_INITIAL_CONSTRAINT_COUNT = 249
_FIRST_LINE_INDEX = 178
_SECOND_LINE_INDEX = 179
_CONSTRAINT_INDEX = 249


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _directed_angle_degrees(
    sketch: Any,
    first_index: int,
    first_position: int,
    second_index: int,
    second_position: int,
) -> float:
    def direction(index: int, position: int) -> tuple[float, float]:
        start = sketch.getPoint(index, 1)
        end = sketch.getPoint(index, 2)
        dx = float(end.x) - float(start.x)
        dy = float(end.y) - float(start.y)
        if position == 2:
            dx = -dx
            dy = -dy
        length = math.hypot(dx, dy)
        assert length > 1.0e-9
        return dx / length, dy / length

    first = direction(first_index, first_position)
    second = direction(second_index, second_position)
    value = math.degrees(
        math.atan2(
            first[0] * second[1] - first[1] * second[0],
            first[0] * second[0] + first[1] * second[1],
        )
    )
    return abs(value)


def exercise_angle_case(
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
    first_response = native_call(
        line_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            start=(270.0, 160.0),
            end=(280.0, 160.0),
        )
    )
    assert first_response["geometry"]["index"] == _FIRST_LINE_INDEX
    second_response = native_call(
        line_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            start=(270.0, 160.0),
            end=(275.0, 160.0 + 5.0 * math.sqrt(3.0)),
        )
    )
    assert second_response["geometry"]["index"] == _SECOND_LINE_INDEX
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT + 2,
        _INITIAL_CONSTRAINT_COUNT,
    )
    assert math.isclose(
        _directed_angle_degrees(
            sketch,
            _FIRST_LINE_INDEX,
            1,
            _SECOND_LINE_INDEX,
            1,
        ),
        60.0,
        abs_tol=1.0e-8,
    )

    angle_undo_before = int(document.UndoCount)
    whole_ray = native_call(
        angle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=(
                (_FIRST_LINE_INDEX, "whole"),
                (_SECOND_LINE_INDEX, "start"),
            ),
            expected_form="line_line",
            value=45.0,
        ),
        succeeds=False,
    )
    assert whole_ray["error_code"] == "NATIVE_SKETCH_INVALID"
    stale_reference = native_call(
        angle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=(
                (_FIRST_LINE_INDEX, "start"),
                (_SECOND_LINE_INDEX, "start"),
            ),
            expected_form="line_line",
            value=50.0,
            driving=False,
        ),
        succeeds=False,
    )
    assert stale_reference["error_code"] == "NATIVE_SKETCH_INVALID"
    wrong_unit = native_call(
        angle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=(
                (_FIRST_LINE_INDEX, "start"),
                (_SECOND_LINE_INDEX, "start"),
            ),
            expected_form="line_line",
            value=45.0,
            unit="rad",
        ),
        succeeds=False,
    )
    assert wrong_unit["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    wrong_form = native_call(
        angle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=(
                (_FIRST_LINE_INDEX, "start"),
                (_SECOND_LINE_INDEX, "start"),
            ),
            expected_form="line_orientation",
            value=45.0,
        ),
        succeeds=False,
    )
    assert wrong_form["error_code"] == "NATIVE_SKETCH_INVALID"
    parallel_axis = native_call(
        angle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=((_FIRST_LINE_INDEX, "start"), (-1, "whole")),
            expected_form="line_line",
            value=45.0,
        ),
        succeeds=False,
    )
    assert parallel_axis["error_code"] == "NATIVE_SKETCH_INVALID"
    duplicate = native_call(
        angle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=(
                (_FIRST_LINE_INDEX, "start"),
                (_FIRST_LINE_INDEX, "start"),
            ),
            expected_form="line_line",
            value=45.0,
        ),
        succeeds=False,
    )
    assert duplicate["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == angle_undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT + 2,
        _INITIAL_CONSTRAINT_COUNT,
    )

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    response = native_call(
        angle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=(
                (_FIRST_LINE_INDEX, "start"),
                (_SECOND_LINE_INDEX, "start"),
            ),
            expected_form="line_line",
            value=45.0,
        )
    )
    assert response["operation"] == "constrain_angle"
    assert response["target_form"] == "line_line"
    assert response["geometry_count"] == _INITIAL_GEOMETRY_COUNT + 2
    assert response["constraint_count"] == _INITIAL_CONSTRAINT_COUNT + 1
    assert math.isclose(response["measured_before"]["value"], 60.0, abs_tol=1.0e-8)
    assert math.isclose(response["measured_after"]["value"], 45.0, abs_tol=1.0e-8)
    assert response["measured_before"]["unit"] == "deg"
    assert response["measured_after"]["unit"] == "deg"
    constraint = response["constraint"]
    assert constraint["index"] == _CONSTRAINT_INDEX
    assert constraint["type"] == "Angle"
    assert constraint["driving"] is True
    assert constraint["active"] is True
    assert constraint["virtual"] is False
    assert constraint["references"] == [
        {"slot": 1, "geometry_index": _FIRST_LINE_INDEX, "position": 1},
        {"slot": 2, "geometry_index": _SECOND_LINE_INDEX, "position": 1},
    ]
    assert math.isclose(constraint["value"], math.pi / 4.0, abs_tol=1.0e-9)
    assert response["assistant_undo_available"] is True
    assert len(response["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Angle"
    angle_undo_after = int(document.UndoCount)
    assert angle_undo_before == 20
    assert angle_undo_after == 20
    assert document.UndoNames[1] == "Create Native Sketch Line"
    assert _selection_state(document) == selection
    assert math.isclose(
        _directed_angle_degrees(
            sketch,
            _FIRST_LINE_INDEX,
            1,
            _SECOND_LINE_INDEX,
            1,
        ),
        45.0,
        abs_tol=1.0e-8,
    )

    redundant_undo_before = int(document.UndoCount)
    redundant = native_call(
        angle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            selection=(
                (_FIRST_LINE_INDEX, "start"),
                (_SECOND_LINE_INDEX, "start"),
            ),
            expected_form="line_line",
            value=45.0,
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
    assert math.isclose(
        _directed_angle_degrees(
            sketch,
            _FIRST_LINE_INDEX,
            1,
            _SECOND_LINE_INDEX,
            1,
        ),
        60.0,
        abs_tol=1.0e-8,
    )
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1
    assert serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX) == constraint
    assert math.isclose(
        _directed_angle_degrees(
            sketch,
            _FIRST_LINE_INDEX,
            1,
            _SECOND_LINE_INDEX,
            1,
        ),
        45.0,
        abs_tol=1.0e-8,
    )
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "first_line": serialize_sketch_geometry(sketch, _FIRST_LINE_INDEX),
        "second_line": serialize_sketch_geometry(sketch, _SECOND_LINE_INDEX),
        "constraint": constraint,
    }


def verify_reopened_angle(sketch: Any, expected: dict) -> None:
    first = serialize_sketch_geometry(sketch, _FIRST_LINE_INDEX)
    second = serialize_sketch_geometry(sketch, _SECOND_LINE_INDEX)
    constraint = serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX)
    for observed, saved in (
        (first, expected["first_line"]),
        (second, expected["second_line"]),
    ):
        for key in (
            "index",
            "type_id",
            "kind",
            "construction",
            "blocked",
            "geometry_id",
            "start_mm",
            "end_mm",
        ):
            assert observed[key] == saved[key]
        assert observed["tag"]
        assert saved["tag"]
    assert constraint == expected["constraint"]
    assert constraint["type"] == "Angle"
    assert constraint["driving"] is True
    assert math.isclose(constraint["value"], math.pi / 4.0, abs_tol=1.0e-9)
    assert math.isclose(
        _directed_angle_degrees(
            sketch,
            _FIRST_LINE_INDEX,
            1,
            _SECOND_LINE_INDEX,
            1,
        ),
        45.0,
        abs_tol=1.0e-8,
    )
