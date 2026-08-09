# SPDX-License-Identifier: LGPL-2.1-or-later

"""Parallel lifecycle for the rolling Sketch gate."""

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
    parallel_arguments,
)


_INITIAL_GEOMETRY_COUNT = 196
_INITIAL_CONSTRAINT_COUNT = 261
_FIRST_INTERNAL_LINE = 196
_SECOND_INTERNAL_LINE = 197
_INTERNAL_CONSTRAINT = 261
_AXIS_LINE = 198
_AXIS_CONSTRAINT = 262
_EXTERNAL_LINE = 199
_EXTERNAL_CONSTRAINT = 263


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


def _line_points(sketch: Any, index: int) -> tuple[tuple[float, float], ...]:
    return _point(sketch, index, 1), _point(sketch, index, 2)


def _angular_error(sketch: Any, first: int, second: int) -> float:
    first_points = _line_points(sketch, first)
    second_points = _line_points(sketch, second)
    first_delta = (
        first_points[1][0] - first_points[0][0],
        first_points[1][1] - first_points[0][1],
    )
    second_delta = (
        second_points[1][0] - second_points[0][0],
        second_points[1][1] - second_points[0][1],
    )
    denominator = math.hypot(*first_delta) * math.hypot(*second_delta)
    sine = abs(
        first_delta[0] * second_delta[1] - first_delta[1] * second_delta[0]
    ) / denominator
    return math.degrees(math.asin(min(1.0, sine)))


def _assert_constraint(
    constraint: dict[str, Any],
    *,
    index: int,
    first: int,
    second: int,
) -> None:
    assert constraint == {
        "index": index,
        "type": "Parallel",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": first},
            {"slot": 2, "geometry_index": second},
        ],
    }


def _undo_redo(
    *,
    sketch: Any,
    document: Any,
    process_events: Callable[[int], None],
    before_count: int,
    before_geometry: dict[int, tuple[tuple[float, float], ...]],
    constraint_index: int,
    constraint: dict[str, Any],
    first: int,
    second: int,
) -> None:
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == before_count
    for index, points in before_geometry.items():
        assert _line_points(sketch, index) == points
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == before_count + 1
    assert serialize_sketch_constraint(sketch, constraint_index) == constraint
    assert math.isclose(
        _angular_error(sketch, first, second),
        0.0,
        abs_tol=1.0e-7,
    )


def exercise_parallel_case(
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
    for geometry_count, start, end, expected_index in (
        (196, (420.0, 180.0), (426.0, 182.0), _FIRST_INTERNAL_LINE),
        (197, (420.0, 190.0), (422.0, 196.0), _SECOND_INTERNAL_LINE),
    ):
        response = native_call(
            line_arguments(
                sketch,
                geometry_count=geometry_count,
                start=start,
                end=end,
            )
        )
        assert response["geometry"]["index"] == expected_index
    internal_before = {
        _FIRST_INTERNAL_LINE: _line_points(sketch, _FIRST_INTERNAL_LINE),
        _SECOND_INTERNAL_LINE: _line_points(sketch, _SECOND_INTERNAL_LINE),
    }
    assert _angular_error(
        sketch,
        _FIRST_INTERNAL_LINE,
        _SECOND_INTERNAL_LINE,
    ) > 50.0
    failure_undo_before = int(document.UndoCount)
    same = native_call(
        parallel_arguments(
            sketch,
            geometry_count=198,
            external_geometry_count=1,
            selection=(_FIRST_INTERNAL_LINE, _FIRST_INTERNAL_LINE),
        ),
        succeeds=False,
    )
    assert same["error_code"] == "NATIVE_SKETCH_INVALID"
    axes = native_call(
        parallel_arguments(
            sketch,
            geometry_count=198,
            external_geometry_count=1,
            selection=(-1, -2),
        ),
        succeeds=False,
    )
    assert axes["error_code"] == "NATIVE_SKETCH_INVALID"
    nonwhole = parallel_arguments(
        sketch,
        geometry_count=198,
        external_geometry_count=1,
        selection=(_FIRST_INTERNAL_LINE, _SECOND_INTERNAL_LINE),
    )
    nonwhole["selection"][1]["position"] = "start"
    closed = native_call(nonwhole, succeeds=False)
    assert closed["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    stale = native_call(
        parallel_arguments(
            sketch,
            geometry_count=197,
            external_geometry_count=1,
            selection=(_FIRST_INTERNAL_LINE, _SECOND_INTERNAL_LINE),
        ),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == failure_undo_before

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    internal = native_call(
        parallel_arguments(
            sketch,
            geometry_count=198,
            external_geometry_count=1,
            selection=(_FIRST_INTERNAL_LINE, _SECOND_INTERNAL_LINE),
        )
    )
    assert internal["operation"] == "constrain_parallel"
    assert internal["measured_before"]["angular_error"] > 50.0
    assert internal["measured_before"]["unit"] == "deg"
    assert math.isclose(
        internal["measured_after"]["angular_error"],
        0.0,
        abs_tol=1.0e-7,
    )
    internal_constraint = internal["constraint"]
    _assert_constraint(
        internal_constraint,
        index=_INTERNAL_CONSTRAINT,
        first=_FIRST_INTERNAL_LINE,
        second=_SECOND_INTERNAL_LINE,
    )
    assert internal["assistant_undo_available"] is True
    assert len(internal["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Parallel"
    assert int(document.UndoCount) == 20
    assert _selection_state(document) == selection

    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    _undo_redo(
        sketch=sketch,
        document=document,
        process_events=process_events,
        before_count=_INITIAL_CONSTRAINT_COUNT,
        before_geometry=internal_before,
        constraint_index=_INTERNAL_CONSTRAINT,
        constraint=internal_constraint,
        first=_FIRST_INTERNAL_LINE,
        second=_SECOND_INTERNAL_LINE,
    )

    axis_line = native_call(
        line_arguments(
            sketch,
            geometry_count=198,
            start=(430.0, 180.0),
            end=(433.0, 186.0),
        )
    )
    assert axis_line["geometry"]["index"] == _AXIS_LINE
    axis_before = {_AXIS_LINE: _line_points(sketch, _AXIS_LINE)}
    axis = native_call(
        parallel_arguments(
            sketch,
            geometry_count=199,
            external_geometry_count=1,
            selection=(_AXIS_LINE, -1),
        )
    )
    axis_constraint = axis["constraint"]
    _assert_constraint(
        axis_constraint,
        index=_AXIS_CONSTRAINT,
        first=_AXIS_LINE,
        second=-1,
    )
    assert math.isclose(
        axis["measured_after"]["angular_error"],
        0.0,
        abs_tol=1.0e-7,
    )
    _undo_redo(
        sketch=sketch,
        document=document,
        process_events=process_events,
        before_count=_INITIAL_CONSTRAINT_COUNT + 1,
        before_geometry=axis_before,
        constraint_index=_AXIS_CONSTRAINT,
        constraint=axis_constraint,
        first=_AXIS_LINE,
        second=-1,
    )

    external_line = native_call(
        line_arguments(
            sketch,
            geometry_count=199,
            start=(440.0, 180.0),
            end=(442.0, 186.0),
        )
    )
    assert external_line["geometry"]["index"] == _EXTERNAL_LINE
    external_before = {_EXTERNAL_LINE: _line_points(sketch, _EXTERNAL_LINE)}
    external = native_call(
        parallel_arguments(
            sketch,
            geometry_count=200,
            external_geometry_count=1,
            selection=(-3, _EXTERNAL_LINE),
        )
    )
    external_constraint = external["constraint"]
    _assert_constraint(
        external_constraint,
        index=_EXTERNAL_CONSTRAINT,
        first=-3,
        second=_EXTERNAL_LINE,
    )
    assert math.isclose(
        external["measured_after"]["angular_error"],
        0.0,
        abs_tol=1.0e-7,
    )
    _undo_redo(
        sketch=sketch,
        document=document,
        process_events=process_events,
        before_count=_INITIAL_CONSTRAINT_COUNT + 2,
        before_geometry=external_before,
        constraint_index=_EXTERNAL_CONSTRAINT,
        constraint=external_constraint,
        first=-3,
        second=_EXTERNAL_LINE,
    )
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "internal": {
            "first": serialize_sketch_geometry(sketch, _FIRST_INTERNAL_LINE),
            "second": serialize_sketch_geometry(sketch, _SECOND_INTERNAL_LINE),
            "constraint": internal_constraint,
        },
        "axis": {
            "line": serialize_sketch_geometry(sketch, _AXIS_LINE),
            "constraint": axis_constraint,
        },
        "external": {
            "line": serialize_sketch_geometry(sketch, _EXTERNAL_LINE),
            "constraint": external_constraint,
        },
    }


def _verify_geometry(sketch: Any, index: int, expected: dict[str, Any]) -> None:
    observed = serialize_sketch_geometry(sketch, index)
    for key in expected:
        if key != "tag":
            assert observed[key] == expected[key]
    assert observed["tag"]
    assert expected["tag"]


def verify_reopened_parallel(sketch: Any, expected: dict[str, Any]) -> None:
    _verify_geometry(sketch, _FIRST_INTERNAL_LINE, expected["internal"]["first"])
    _verify_geometry(sketch, _SECOND_INTERNAL_LINE, expected["internal"]["second"])
    _verify_geometry(sketch, _AXIS_LINE, expected["axis"]["line"])
    _verify_geometry(sketch, _EXTERNAL_LINE, expected["external"]["line"])
    for index, constraint in (
        (_INTERNAL_CONSTRAINT, expected["internal"]["constraint"]),
        (_AXIS_CONSTRAINT, expected["axis"]["constraint"]),
        (_EXTERNAL_CONSTRAINT, expected["external"]["constraint"]),
    ):
        assert serialize_sketch_constraint(sketch, index) == constraint
    assert math.isclose(
        _angular_error(sketch, _FIRST_INTERNAL_LINE, _SECOND_INTERNAL_LINE),
        0.0,
        abs_tol=1.0e-7,
    )
    assert math.isclose(
        _angular_error(sketch, _AXIS_LINE, -1),
        0.0,
        abs_tol=1.0e-7,
    )
    assert math.isclose(
        _angular_error(sketch, -3, _EXTERNAL_LINE),
        0.0,
        abs_tol=1.0e-7,
    )
