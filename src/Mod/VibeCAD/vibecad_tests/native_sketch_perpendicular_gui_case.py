# SPDX-License-Identifier: LGPL-2.1-or-later

"""All exact Perpendicular forms for the rolling Native Sketch GUI gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    circle_arguments,
    ellipse_arguments,
    line_arguments,
)


_INITIAL_GEOMETRY_COUNT = 200
_INITIAL_CONSTRAINT_COUNT = 264
_EXISTING_ELLIPSE_INDEX = 21
_FIRST_LINE = 200
_SECOND_LINE = 201
_LINE_CIRCLE_LINE = 202
_CIRCLE = 203
_ENDPOINT_CURVE_FIRST = 204
_ENDPOINT_CURVE_SECOND = 205
_ENDPOINT_ENDPOINT_FIRST = 206
_ENDPOINT_ENDPOINT_SECOND = 207
_POINT_PAIR_SEGMENT = 208
_POINT_PAIR_LINE = 209
_VIA_ELLIPSE = 210
_VIA_LINE = 215
_CONSTRAINT_INDICES = tuple(range(264, 275))


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _arguments(
    sketch: Any,
    *,
    geometry_count: int,
    target: dict[str, object],
) -> dict[str, object]:
    return {
        "operation": "constrain_perpendicular",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 1,
        "target": target,
    }


def _curve_curve(first: int, second: int) -> dict[str, object]:
    return {
        "form": "curve_curve",
        "first_curve": _element(first, "whole"),
        "second_curve": _element(second, "whole"),
    }


def _point(sketch: Any, index: int, position: int) -> tuple[float, float]:
    value = sketch.getPoint(index, position)
    return float(value.x), float(value.y)


def _line_points(sketch: Any, index: int) -> tuple[tuple[float, float], ...]:
    return _point(sketch, index, 1), _point(sketch, index, 2)


def _line_delta(sketch: Any, index: int) -> tuple[float, float]:
    start, end = _line_points(sketch, index)
    return end[0] - start[0], end[1] - start[1]


def _angular_error(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    denominator = math.hypot(*first) * math.hypot(*second)
    cosine = abs(first[0] * second[0] + first[1] * second[1]) / denominator
    return math.degrees(math.asin(min(1.0, cosine)))


def _line_error(sketch: Any, first: int, second: int) -> float:
    return _angular_error(_line_delta(sketch, first), _line_delta(sketch, second))


def _center_line_distance(sketch: Any, line: int, circle: int) -> float:
    start, _end = _line_points(sketch, line)
    delta = _line_delta(sketch, line)
    center = sketch.Geometry[circle].Center
    return abs(
        delta[0] * (float(center.y) - start[1])
        - delta[1] * (float(center.x) - start[0])
    ) / math.hypot(*delta)


def _tangent(sketch: Any, curve_index: int, point_index: int, point_pos: int):
    geometry = sketch.Geometry[curve_index]
    point = sketch.getPoint(point_index, point_pos)
    tangent = geometry.tangent(geometry.parameter(point))[0]
    return float(tangent.x), float(tangent.y)


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _assert_constraint(
    constraint: dict[str, Any],
    *,
    index: int,
    references: list[dict[str, int]],
    oriented: bool = False,
) -> None:
    expected = {
        "index": index,
        "type": "Perpendicular",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": references,
    }
    if oriented:
        value = float(constraint["value"])
        assert any(
            math.isclose(value, allowed, abs_tol=1.0e-12)
            for allowed in (math.pi / 2.0, 3.0 * math.pi / 2.0)
        )
        expected["value"] = value
    assert constraint == expected


def _undo_redo(
    *,
    sketch: Any,
    document: Any,
    process_events: Callable[[int], None],
    constraint_count_before: int,
    geometry_before: dict[int, tuple[tuple[float, float], ...]],
    constraints: tuple[dict[str, Any], ...],
) -> None:
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == constraint_count_before
    for index, points in geometry_before.items():
        assert _line_points(sketch, index) == points
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == constraint_count_before + len(constraints)
    for constraint in constraints:
        assert serialize_sketch_constraint(sketch, constraint["index"]) == constraint


def _add_line(
    native_call: Callable[..., dict],
    sketch: Any,
    geometry_count: int,
    start: tuple[float, float],
    end: tuple[float, float],
) -> int:
    result = native_call(
        line_arguments(
            sketch,
            geometry_count=geometry_count,
            start=start,
            end=end,
        )
    )
    return int(result["geometry"]["index"])


def exercise_perpendicular_case(
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
    assert _add_line(
        native_call,
        sketch,
        200,
        (460.0, 180.0),
        (466.0, 182.0),
    ) == _FIRST_LINE
    undo_before_failures = int(document.UndoCount)
    same = native_call(
        _arguments(
            sketch,
            geometry_count=201,
            target=_curve_curve(_FIRST_LINE, _FIRST_LINE),
        ),
        succeeds=False,
    )
    assert same["error_code"] == "NATIVE_SKETCH_INVALID"
    hidden = native_call(
        _arguments(
            sketch,
            geometry_count=201,
            target=_curve_curve(_FIRST_LINE, _EXISTING_ELLIPSE_INDEX),
        ),
        succeeds=False,
    )
    assert hidden["error_code"] == "NATIVE_SKETCH_INVALID"
    closed_arguments = _arguments(
        sketch,
        geometry_count=201,
        target=_curve_curve(_FIRST_LINE, -1),
    )
    closed_arguments["target"]["first_curve"]["position"] = "start"
    closed = native_call(closed_arguments, succeeds=False)
    assert closed["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    stale = native_call(
        _arguments(
            sketch,
            geometry_count=200,
            target=_curve_curve(_FIRST_LINE, -1),
        ),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before_failures

    assert _add_line(
        native_call,
        sketch,
        201,
        (460.0, 190.0),
        (462.0, 196.0),
    ) == _SECOND_LINE
    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    line_before = {
        _FIRST_LINE: _line_points(sketch, _FIRST_LINE),
        _SECOND_LINE: _line_points(sketch, _SECOND_LINE),
    }
    line_line = native_call(
        _arguments(
            sketch,
            geometry_count=202,
            target=_curve_curve(_FIRST_LINE, _SECOND_LINE),
        )
    )
    line_constraint = line_line["constraint"]
    _assert_constraint(
        line_constraint,
        index=264,
        references=[
            {"slot": 1, "geometry_index": _FIRST_LINE},
            {"slot": 2, "geometry_index": _SECOND_LINE},
        ],
    )
    assert line_line["support_constraints"] == []
    assert math.isclose(_line_error(sketch, _FIRST_LINE, _SECOND_LINE), 0.0, abs_tol=1.0e-7)
    assert _selection_state(document) == selection
    assert document.UndoNames[0] == "Create Native Sketch Perpendicular"
    _undo_redo(
        sketch=sketch,
        document=document,
        process_events=process_events,
        constraint_count_before=264,
        geometry_before=line_before,
        constraints=(line_constraint,),
    )

    assert _add_line(native_call, sketch, 202, (475.0, 180.0), (481.0, 183.0)) == _LINE_CIRCLE_LINE
    circle = native_call(
        circle_arguments(
            sketch,
            geometry_count=203,
            center=(480.0, 190.0),
            radius=4.0,
        )
    )
    assert circle["geometry"]["index"] == _CIRCLE
    line_circle = native_call(
        _arguments(
            sketch,
            geometry_count=204,
            target=_curve_curve(_LINE_CIRCLE_LINE, _CIRCLE),
        )
    )
    line_circle_constraint = line_circle["constraint"]
    _assert_constraint(
        line_circle_constraint,
        index=265,
        references=[
            {"slot": 1, "geometry_index": _LINE_CIRCLE_LINE},
            {"slot": 2, "geometry_index": _CIRCLE},
        ],
    )
    assert math.isclose(
        _center_line_distance(sketch, _LINE_CIRCLE_LINE, _CIRCLE),
        0.0,
        abs_tol=1.0e-7,
    )

    for geometry_count, start, end, expected in (
        (204, (490.0, 180.0), (496.0, 182.0), _ENDPOINT_CURVE_FIRST),
        (205, (500.0, 187.0), (505.0, 192.0), _ENDPOINT_CURVE_SECOND),
    ):
        assert _add_line(native_call, sketch, geometry_count, start, end) == expected
    endpoint_curve = native_call(
        _arguments(
            sketch,
            geometry_count=206,
            target={
                "form": "endpoint_curve",
                "endpoint": _element(_ENDPOINT_CURVE_FIRST, "end"),
                "curve": _element(_ENDPOINT_CURVE_SECOND, "whole"),
            },
        )
    )
    endpoint_curve_constraint = endpoint_curve["constraint"]
    _assert_constraint(
        endpoint_curve_constraint,
        index=266,
        references=[
            {"slot": 1, "geometry_index": _ENDPOINT_CURVE_FIRST, "position": 2},
            {"slot": 2, "geometry_index": _ENDPOINT_CURVE_SECOND},
        ],
        oriented=True,
    )
    assert sketch.isPointOnCurve(
        _ENDPOINT_CURVE_SECOND,
        *_point(sketch, _ENDPOINT_CURVE_FIRST, 2),
    )

    for geometry_count, start, end, expected in (
        (206, (510.0, 180.0), (516.0, 182.0), _ENDPOINT_ENDPOINT_FIRST),
        (207, (521.0, 188.0), (526.0, 193.0), _ENDPOINT_ENDPOINT_SECOND),
    ):
        assert _add_line(native_call, sketch, geometry_count, start, end) == expected
    endpoint_endpoint = native_call(
        _arguments(
            sketch,
            geometry_count=208,
            target={
                "form": "endpoint_endpoint",
                "first_endpoint": _element(_ENDPOINT_ENDPOINT_FIRST, "end"),
                "second_endpoint": _element(_ENDPOINT_ENDPOINT_SECOND, "start"),
            },
        )
    )
    endpoint_endpoint_constraint = endpoint_endpoint["constraint"]
    _assert_constraint(
        endpoint_endpoint_constraint,
        index=267,
        references=[
            {"slot": 1, "geometry_index": _ENDPOINT_ENDPOINT_FIRST, "position": 2},
            {"slot": 2, "geometry_index": _ENDPOINT_ENDPOINT_SECOND, "position": 1},
        ],
        oriented=True,
    )
    first_endpoint = _point(sketch, _ENDPOINT_ENDPOINT_FIRST, 2)
    second_endpoint = _point(sketch, _ENDPOINT_ENDPOINT_SECOND, 1)
    assert math.dist(first_endpoint, second_endpoint) <= 1.0e-7

    for geometry_count, start, end, expected in (
        (208, (530.0, 180.0), (535.0, 182.0), _POINT_PAIR_SEGMENT),
        (209, (530.0, 200.0), (536.0, 203.0), _POINT_PAIR_LINE),
    ):
        assert _add_line(native_call, sketch, geometry_count, start, end) == expected
    point_pair = native_call(
        _arguments(
            sketch,
            geometry_count=210,
            target={
                "form": "point_pair_line",
                "first_point": _element(_POINT_PAIR_SEGMENT, "start"),
                "second_point": _element(_POINT_PAIR_SEGMENT, "end"),
                "line": _element(_POINT_PAIR_LINE, "whole"),
            },
        )
    )
    point_pair_constraint = point_pair["constraint"]
    _assert_constraint(
        point_pair_constraint,
        index=268,
        references=[
            {"slot": 1, "geometry_index": _POINT_PAIR_SEGMENT},
            {"slot": 2, "geometry_index": _POINT_PAIR_LINE},
        ],
    )
    pair_delta = tuple(
        second - first
        for first, second in zip(
            _point(sketch, _POINT_PAIR_SEGMENT, 1),
            _point(sketch, _POINT_PAIR_SEGMENT, 2),
            strict=True,
        )
    )
    assert math.isclose(
        _angular_error(pair_delta, _line_delta(sketch, _POINT_PAIR_LINE)),
        0.0,
        abs_tol=1.0e-7,
    )

    ellipse_result = native_call(
        ellipse_arguments(
            sketch,
            geometry_count=210,
            center=(550.0, 190.0),
            major_radius=7.0,
            minor_radius=3.0,
            rotation_degrees=20.0,
        )
    )
    assert ellipse_result["geometry"]["index"] == _VIA_ELLIPSE
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (215, 273)
    ellipse = sketch.Geometry[_VIA_ELLIPSE]
    via_start = ellipse.value(0.0)
    assert _add_line(
        native_call,
        sketch,
        215,
        (float(via_start.x), float(via_start.y)),
        (float(via_start.x) + 4.0, float(via_start.y) + 5.0),
    ) == _VIA_LINE
    via = native_call(
        _arguments(
            sketch,
            geometry_count=216,
            target={
                "form": "curves_via_point",
                "first_curve": _element(_VIA_ELLIPSE, "whole"),
                "second_curve": _element(_VIA_LINE, "whole"),
                "point": _element(_VIA_LINE, "start"),
            },
        )
    )
    assert len(via["support_constraints"]) == 1
    support_constraint = via["support_constraints"][0]
    assert support_constraint == {
        "index": 273,
        "type": "PointOnObject",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": _VIA_LINE, "position": 1},
            {"slot": 2, "geometry_index": _VIA_ELLIPSE},
        ],
    }
    via_constraint = via["constraint"]
    _assert_constraint(
        via_constraint,
        index=274,
        references=[
            {"slot": 1, "geometry_index": _VIA_ELLIPSE},
            {"slot": 2, "geometry_index": _VIA_LINE},
            {"slot": 3, "geometry_index": _VIA_LINE, "position": 1},
        ],
        oriented=True,
    )
    via_point = _point(sketch, _VIA_LINE, 1)
    assert sketch.isPointOnCurve(_VIA_ELLIPSE, *via_point)
    assert sketch.isPointOnCurve(_VIA_LINE, *via_point)
    assert math.isclose(
        _angular_error(
            _tangent(sketch, _VIA_ELLIPSE, _VIA_LINE, 1),
            _line_delta(sketch, _VIA_LINE),
        ),
        0.0,
        abs_tol=1.0e-7,
    )
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (216, 275)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": {
            str(index): serialize_sketch_geometry(sketch, index)
            for index in range(_FIRST_LINE, _VIA_LINE + 1)
        },
        "constraints": {
            str(index): serialize_sketch_constraint(sketch, index)
            for index in _CONSTRAINT_INDICES
        },
    }


def verify_reopened_perpendicular(sketch: Any, expected: dict[str, Any]) -> None:
    for raw_index, geometry in expected["geometries"].items():
        observed = serialize_sketch_geometry(sketch, int(raw_index))
        for key, value in geometry.items():
            if key != "tag":
                assert observed[key] == value
        assert observed["tag"]
    for raw_index, constraint in expected["constraints"].items():
        assert serialize_sketch_constraint(sketch, int(raw_index)) == constraint
    assert math.isclose(_line_error(sketch, _FIRST_LINE, _SECOND_LINE), 0.0, abs_tol=1.0e-7)
    assert math.isclose(_center_line_distance(sketch, _LINE_CIRCLE_LINE, _CIRCLE), 0.0, abs_tol=1.0e-7)
    assert math.dist(
        _point(sketch, _ENDPOINT_ENDPOINT_FIRST, 2),
        _point(sketch, _ENDPOINT_ENDPOINT_SECOND, 1),
    ) <= 1.0e-7
    via_point = _point(sketch, _VIA_LINE, 1)
    assert sketch.isPointOnCurve(_VIA_ELLIPSE, *via_point)
    assert math.isclose(
        _angular_error(
            _tangent(sketch, _VIA_ELLIPSE, _VIA_LINE, 1),
            _line_delta(sketch, _VIA_LINE),
        ),
        0.0,
        abs_tol=1.0e-7,
    )
