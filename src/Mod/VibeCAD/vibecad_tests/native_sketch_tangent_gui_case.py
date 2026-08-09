# SPDX-License-Identifier: LGPL-2.1-or-later

"""All exact Tangent forms and replacements for the rolling Sketch GUI gate."""

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
    coincident_arguments,
    ellipse_arguments,
    horizontal_arguments,
    line_arguments,
)


_INITIAL_GEOMETRY_COUNT = 216
_INITIAL_CONSTRAINT_COUNT = 275
_EXISTING_ELLIPSE_INDEX = 21
_FIRST_LINE = 216
_SECOND_LINE = 217
_LINE_CIRCLE_LINE = 218
_LINE_CIRCLE = 219
_FIRST_CIRCLE = 220
_SECOND_CIRCLE = 221
_ENDPOINT_CURVE_FIRST = 222
_ENDPOINT_CURVE_SECOND = 223
_ENDPOINT_ENDPOINT_FIRST = 224
_ENDPOINT_ENDPOINT_SECOND = 225
_VIA_ELLIPSE = 226
_VIA_LINE = 231
_POO_FIRST = 232
_POO_SECOND = 233
_WHOLE_ENDPOINT_FIRST = 234
_WHOLE_ENDPOINT_SECOND = 235
_WHOLE_CURVE_FIRST = 236
_WHOLE_CURVE_SECOND = 237
_PRESERVE_FIRST = 238
_PRESERVE_SECOND = 239
_CONSTRAINT_INDICES = tuple(range(275, 291))


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _arguments(
    sketch: Any,
    *,
    geometry_count: int,
    target: dict[str, object],
) -> dict[str, object]:
    return {
        "operation": "constrain_tangent",
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


def _endpoint_curve(first: int, second: int) -> dict[str, object]:
    return {
        "form": "endpoint_curve",
        "endpoint": _element(first, "end"),
        "curve": _element(second, "whole"),
    }


def _endpoint_endpoint(first: int, second: int) -> dict[str, object]:
    return {
        "form": "endpoint_endpoint",
        "first_endpoint": _element(first, "end"),
        "second_endpoint": _element(second, "start"),
    }


def _replace_endpoint_curve(
    constraint_index: int,
    first: int,
    second: int,
) -> dict[str, object]:
    return {
        "form": "replace_with_endpoint_curve",
        "constraint_index": constraint_index,
        "endpoint": _element(first, "end"),
        "curve": _element(second, "whole"),
    }


def _replace_endpoint_endpoint(
    constraint_index: int,
    first: int,
    second: int,
) -> dict[str, object]:
    return {
        "form": "replace_with_endpoint_endpoint",
        "constraint_index": constraint_index,
        "first_endpoint": _element(first, "end"),
        "second_endpoint": _element(second, "start"),
    }


def _point(sketch: Any, index: int, position: int) -> tuple[float, float]:
    value = sketch.getPoint(index, position)
    return float(value.x), float(value.y)


def _line_points(sketch: Any, index: int) -> tuple[tuple[float, float], ...]:
    return _point(sketch, index, 1), _point(sketch, index, 2)


def _line_delta(sketch: Any, index: int) -> tuple[float, float]:
    start, end = _line_points(sketch, index)
    return end[0] - start[0], end[1] - start[1]


def _angular_error(first: tuple[float, float], second: tuple[float, float]) -> float:
    denominator = math.hypot(*first) * math.hypot(*second)
    sine = abs(first[0] * second[1] - first[1] * second[0]) / denominator
    return math.degrees(math.asin(min(1.0, sine)))


def _line_error(sketch: Any, first: int, second: int) -> float:
    return _angular_error(_line_delta(sketch, first), _line_delta(sketch, second))


def _line_circle_error(sketch: Any, line: int, circle: int) -> float:
    start, _end = _line_points(sketch, line)
    delta = _line_delta(sketch, line)
    geometry = sketch.Geometry[circle]
    distance = abs(
        delta[0] * (float(geometry.Center.y) - start[1])
        - delta[1] * (float(geometry.Center.x) - start[0])
    ) / math.hypot(*delta)
    return abs(distance - float(geometry.Radius))


def _circle_circle_error(sketch: Any, first: int, second: int) -> float:
    first_circle = sketch.Geometry[first]
    second_circle = sketch.Geometry[second]
    distance = math.hypot(
        float(second_circle.Center.x) - float(first_circle.Center.x),
        float(second_circle.Center.y) - float(first_circle.Center.y),
    )
    external = abs(distance - float(first_circle.Radius) - float(second_circle.Radius))
    internal = abs(distance - abs(float(first_circle.Radius) - float(second_circle.Radius)))
    return min(external, internal)


def _curve_tangent(sketch: Any, curve: int, point: int, position: int):
    geometry = sketch.Geometry[curve]
    host_point = sketch.getPoint(point, position)
    tangent = geometry.tangent(geometry.parameter(host_point))[0]
    return float(tangent.x), float(tangent.y)


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _assert_tangent(
    constraint: dict[str, Any],
    *,
    index: int,
    references: list[dict[str, int]],
    oriented: bool = False,
) -> None:
    expected = {
        "index": index,
        "type": "Tangent",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": references,
    }
    if oriented:
        value = float(constraint["value"])
        assert any(
            math.isclose(value, allowed, abs_tol=1.0e-12)
            for allowed in (-math.pi / 2.0, math.pi / 2.0)
        )
        expected["value"] = value
    assert constraint == expected


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


def _replacement(
    *,
    native_call: Callable[..., dict],
    sketch: Any,
    geometry_count: int,
    target: dict[str, object],
    replaced_type: str,
    expected_index: int,
) -> dict[str, Any]:
    result = native_call(
        _arguments(sketch, geometry_count=geometry_count, target=target)
    )
    assert result["replaced_constraint"]["index"] == target["constraint_index"]
    assert result["replaced_constraint"]["type"] == replaced_type
    assert result["constraint"]["index"] == expected_index
    assert result["constraint"]["type"] == "Tangent"
    return result


def exercise_tangent_case(
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
    assert _add_line(native_call, sketch, 216, (580.0, 180.0), (586.0, 182.0)) == _FIRST_LINE
    undo_before_failures = int(document.UndoCount)
    for target in (
        _curve_curve(_FIRST_LINE, _FIRST_LINE),
        _curve_curve(_FIRST_LINE, _EXISTING_ELLIPSE_INDEX),
    ):
        failure = native_call(
            _arguments(sketch, geometry_count=217, target=target),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    closed = _arguments(
        sketch,
        geometry_count=217,
        target=_curve_curve(_FIRST_LINE, -1),
    )
    closed["target"]["first_curve"]["position"] = "start"
    assert native_call(closed, succeeds=False)["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    stale = _arguments(
        sketch,
        geometry_count=216,
        target=_curve_curve(_FIRST_LINE, -1),
    )
    assert native_call(stale, succeeds=False)["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before_failures

    assert _add_line(native_call, sketch, 217, (580.0, 190.0), (582.0, 196.0)) == _SECOND_LINE
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
            geometry_count=218,
            target=_curve_curve(_FIRST_LINE, _SECOND_LINE),
        )
    )
    line_constraint = line_line["constraint"]
    _assert_tangent(
        line_constraint,
        index=275,
        references=[
            {"slot": 1, "geometry_index": _FIRST_LINE},
            {"slot": 2, "geometry_index": _SECOND_LINE},
        ],
    )
    assert math.isclose(_line_error(sketch, _FIRST_LINE, _SECOND_LINE), 0.0, abs_tol=1.0e-7)
    assert _selection_state(document) == selection
    assert document.UndoNames[0] == "Create Native Sketch Tangent"
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == 275
    for index, points in line_before.items():
        assert _line_points(sketch, index) == points
    document.redo()
    process_events(16)
    assert serialize_sketch_constraint(sketch, 275) == line_constraint

    assert _add_line(native_call, sketch, 218, (595.0, 180.0), (601.0, 183.0)) == _LINE_CIRCLE_LINE
    assert native_call(
        circle_arguments(
            sketch,
            geometry_count=219,
            center=(600.0, 190.0),
            radius=4.0,
        )
    )["geometry"]["index"] == _LINE_CIRCLE
    line_circle = native_call(
        _arguments(
            sketch,
            geometry_count=220,
            target=_curve_curve(_LINE_CIRCLE_LINE, _LINE_CIRCLE),
        )
    )
    _assert_tangent(
        line_circle["constraint"],
        index=276,
        references=[
            {"slot": 1, "geometry_index": _LINE_CIRCLE_LINE},
            {"slot": 2, "geometry_index": _LINE_CIRCLE},
        ],
    )
    assert _line_circle_error(sketch, _LINE_CIRCLE_LINE, _LINE_CIRCLE) <= 1.0e-7

    for geometry_count, center, radius, expected in (
        (220, (615.0, 184.0), 3.0, _FIRST_CIRCLE),
        (221, (624.0, 187.0), 4.0, _SECOND_CIRCLE),
    ):
        assert native_call(
            circle_arguments(
                sketch,
                geometry_count=geometry_count,
                center=center,
                radius=radius,
            )
        )["geometry"]["index"] == expected
    circle_circle = native_call(
        _arguments(
            sketch,
            geometry_count=222,
            target=_curve_curve(_FIRST_CIRCLE, _SECOND_CIRCLE),
        )
    )
    _assert_tangent(
        circle_circle["constraint"],
        index=277,
        references=[
            {"slot": 1, "geometry_index": _FIRST_CIRCLE},
            {"slot": 2, "geometry_index": _SECOND_CIRCLE},
        ],
    )
    assert _circle_circle_error(sketch, _FIRST_CIRCLE, _SECOND_CIRCLE) <= 1.0e-7

    for geometry_count, start, end, expected in (
        (222, (635.0, 180.0), (641.0, 182.0), _ENDPOINT_CURVE_FIRST),
        (223, (646.0, 187.0), (651.0, 192.0), _ENDPOINT_CURVE_SECOND),
        (224, (660.0, 180.0), (666.0, 182.0), _ENDPOINT_ENDPOINT_FIRST),
        (225, (671.0, 188.0), (676.0, 193.0), _ENDPOINT_ENDPOINT_SECOND),
    ):
        assert _add_line(native_call, sketch, geometry_count, start, end) == expected
    endpoint_curve = native_call(
        _arguments(
            sketch,
            geometry_count=226,
            target=_endpoint_curve(_ENDPOINT_CURVE_FIRST, _ENDPOINT_CURVE_SECOND),
        )
    )
    _assert_tangent(
        endpoint_curve["constraint"],
        index=278,
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
    endpoint_endpoint = native_call(
        _arguments(
            sketch,
            geometry_count=226,
            target=_endpoint_endpoint(
                _ENDPOINT_ENDPOINT_FIRST,
                _ENDPOINT_ENDPOINT_SECOND,
            ),
        )
    )
    _assert_tangent(
        endpoint_endpoint["constraint"],
        index=279,
        references=[
            {"slot": 1, "geometry_index": _ENDPOINT_ENDPOINT_FIRST, "position": 2},
            {"slot": 2, "geometry_index": _ENDPOINT_ENDPOINT_SECOND, "position": 1},
        ],
        oriented=True,
    )
    assert math.dist(
        _point(sketch, _ENDPOINT_ENDPOINT_FIRST, 2),
        _point(sketch, _ENDPOINT_ENDPOINT_SECOND, 1),
    ) <= 1.0e-7

    ellipse = native_call(
        ellipse_arguments(
            sketch,
            geometry_count=226,
            center=(690.0, 190.0),
            major_radius=7.0,
            minor_radius=3.0,
            rotation_degrees=20.0,
        )
    )
    assert ellipse["geometry"]["index"] == _VIA_ELLIPSE
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (231, 284)
    start = sketch.Geometry[_VIA_ELLIPSE].value(0.0)
    assert _add_line(
        native_call,
        sketch,
        231,
        (float(start.x), float(start.y)),
        (float(start.x) + 4.0, float(start.y) + 5.0),
    ) == _VIA_LINE
    via = native_call(
        _arguments(
            sketch,
            geometry_count=232,
            target={
                "form": "curves_via_point",
                "first_curve": _element(_VIA_ELLIPSE, "whole"),
                "second_curve": _element(_VIA_LINE, "whole"),
                "point": _element(_VIA_LINE, "start"),
            },
        )
    )
    assert [item["index"] for item in via["support_constraints"]] == [284]
    _assert_tangent(
        via["constraint"],
        index=285,
        references=[
            {"slot": 1, "geometry_index": _VIA_ELLIPSE},
            {"slot": 2, "geometry_index": _VIA_LINE},
            {"slot": 3, "geometry_index": _VIA_LINE, "position": 1},
        ],
        oriented=True,
    )
    via_point = _point(sketch, _VIA_LINE, 1)
    assert sketch.isPointOnCurve(_VIA_ELLIPSE, *via_point)
    assert math.isclose(
        _angular_error(
            _curve_tangent(sketch, _VIA_ELLIPSE, _VIA_LINE, 1),
            _line_delta(sketch, _VIA_LINE),
        ),
        0.0,
        abs_tol=1.0e-7,
    )

    for geometry_count, start, end, expected in (
        (232, (710.0, 180.0), (716.0, 182.0), _POO_FIRST),
        (233, (719.0, 186.0), (725.0, 191.0), _POO_SECOND),
    ):
        assert _add_line(native_call, sketch, geometry_count, start, end) == expected
    poo = native_call(
        coincident_arguments(
            sketch,
            geometry_count=234,
            external_geometry_count=1,
            target={
                "form": "point_on_object",
                "point": _element(_POO_FIRST, "end"),
                "curve": _element(_POO_SECOND, "whole"),
            },
        )
    )
    assert poo["constraint"]["index"] == 286
    direct = native_call(
        _arguments(
            sketch,
            geometry_count=234,
            target=_endpoint_curve(_POO_FIRST, _POO_SECOND),
        ),
        succeeds=False,
    )
    assert direct["error_code"] == "NATIVE_SKETCH_INVALID"
    replaced_poo = _replacement(
        native_call=native_call,
        sketch=sketch,
        geometry_count=234,
        target=_replace_endpoint_curve(286, _POO_FIRST, _POO_SECOND),
        replaced_type="PointOnObject",
        expected_index=286,
    )
    assert sketch.isPointOnCurve(_POO_SECOND, *_point(sketch, _POO_FIRST, 2))

    for geometry_count, start, end, expected in (
        (234, (735.0, 180.0), (741.0, 182.0), _WHOLE_ENDPOINT_FIRST),
        (235, (747.0, 187.0), (752.0, 192.0), _WHOLE_ENDPOINT_SECOND),
    ):
        assert _add_line(native_call, sketch, geometry_count, start, end) == expected
    native_call(
        _arguments(
            sketch,
            geometry_count=236,
            target=_curve_curve(_WHOLE_ENDPOINT_FIRST, _WHOLE_ENDPOINT_SECOND),
        )
    )
    assert native_call(
        _arguments(
            sketch,
            geometry_count=236,
            target=_endpoint_endpoint(_WHOLE_ENDPOINT_FIRST, _WHOLE_ENDPOINT_SECOND),
        ),
        succeeds=False,
    )["error_code"] == "NATIVE_SKETCH_INVALID"
    replaced_whole_endpoint = _replacement(
        native_call=native_call,
        sketch=sketch,
        geometry_count=236,
        target=_replace_endpoint_endpoint(
            287,
            _WHOLE_ENDPOINT_FIRST,
            _WHOLE_ENDPOINT_SECOND,
        ),
        replaced_type="Tangent",
        expected_index=287,
    )

    for geometry_count, start, end, expected in (
        (236, (760.0, 180.0), (766.0, 182.0), _WHOLE_CURVE_FIRST),
        (237, (772.0, 187.0), (777.0, 192.0), _WHOLE_CURVE_SECOND),
    ):
        assert _add_line(native_call, sketch, geometry_count, start, end) == expected
    native_call(
        _arguments(
            sketch,
            geometry_count=238,
            target=_curve_curve(_WHOLE_CURVE_FIRST, _WHOLE_CURVE_SECOND),
        )
    )
    replaced_whole_curve = _replacement(
        native_call=native_call,
        sketch=sketch,
        geometry_count=238,
        target=_replace_endpoint_curve(288, _WHOLE_CURVE_FIRST, _WHOLE_CURVE_SECOND),
        replaced_type="Tangent",
        expected_index=288,
    )

    for geometry_count, start, end, expected in (
        (238, (785.0, 180.0), (791.0, 182.0), _PRESERVE_FIRST),
        (239, (797.0, 187.0), (802.0, 192.0), _PRESERVE_SECOND),
    ):
        assert _add_line(native_call, sketch, geometry_count, start, end) == expected
    coincident = native_call(
        coincident_arguments(
            sketch,
            geometry_count=240,
            external_geometry_count=1,
            target={
                "form": "point_point",
                "first_point": _element(_PRESERVE_FIRST, "end"),
                "second_point": _element(_PRESERVE_SECOND, "start"),
            },
        )
    )["constraint"]
    assert coincident["index"] == 289
    horizontal = native_call(
        horizontal_arguments(
            sketch,
            geometry_count=240,
            external_geometry_count=1,
            selection=((_PRESERVE_FIRST, "whole"),),
        )
    )["constraint"]
    assert horizontal["index"] == 290
    before_replacement = tuple(
        serialize_sketch_constraint(sketch, index) for index in (289, 290)
    )
    geometry_before_replacement = {
        index: _line_points(sketch, index)
        for index in (_PRESERVE_FIRST, _PRESERVE_SECOND)
    }
    preserved = _replacement(
        native_call=native_call,
        sketch=sketch,
        geometry_count=240,
        target=_replace_endpoint_endpoint(289, _PRESERVE_FIRST, _PRESERVE_SECOND),
        replaced_type="Coincident",
        expected_index=290,
    )
    assert serialize_sketch_constraint(sketch, 289)["type"] == "Horizontal"
    replacement_constraint = preserved["constraint"]
    document.undo()
    process_events(16)
    assert tuple(
        serialize_sketch_constraint(sketch, index) for index in (289, 290)
    ) == before_replacement
    for index, points in geometry_before_replacement.items():
        assert _line_points(sketch, index) == points
    document.redo()
    process_events(16)
    assert serialize_sketch_constraint(sketch, 289)["type"] == "Horizontal"
    assert serialize_sketch_constraint(sketch, 290) == replacement_constraint
    assert document.UndoNames[0] == "Create Native Sketch Tangent"
    assert _selection_state(document) == selection
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (240, 291)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": {
            str(index): serialize_sketch_geometry(sketch, index)
            for index in range(_FIRST_LINE, _PRESERVE_SECOND + 1)
        },
        "constraints": {
            str(index): serialize_sketch_constraint(sketch, index)
            for index in _CONSTRAINT_INDICES
        },
        "replacement_types": [
            replaced_poo["replaced_constraint"]["type"],
            replaced_whole_endpoint["replaced_constraint"]["type"],
            replaced_whole_curve["replaced_constraint"]["type"],
            preserved["replaced_constraint"]["type"],
        ],
    }


def verify_reopened_tangent(sketch: Any, expected: dict[str, Any]) -> None:
    assert expected["replacement_types"] == [
        "PointOnObject",
        "Tangent",
        "Tangent",
        "Coincident",
    ]
    for raw_index, geometry in expected["geometries"].items():
        observed = serialize_sketch_geometry(sketch, int(raw_index))
        for key, value in geometry.items():
            if key != "tag":
                assert observed[key] == value
        assert observed["tag"]
    for raw_index, constraint in expected["constraints"].items():
        assert serialize_sketch_constraint(sketch, int(raw_index)) == constraint
    assert _line_error(sketch, _FIRST_LINE, _SECOND_LINE) <= 1.0e-7
    assert _line_circle_error(sketch, _LINE_CIRCLE_LINE, _LINE_CIRCLE) <= 1.0e-7
    assert _circle_circle_error(sketch, _FIRST_CIRCLE, _SECOND_CIRCLE) <= 1.0e-7
    assert sketch.isPointOnCurve(
        _ENDPOINT_CURVE_SECOND,
        *_point(sketch, _ENDPOINT_CURVE_FIRST, 2),
    )
    assert math.dist(
        _point(sketch, _ENDPOINT_ENDPOINT_FIRST, 2),
        _point(sketch, _ENDPOINT_ENDPOINT_SECOND, 1),
    ) <= 1.0e-7
    via_point = _point(sketch, _VIA_LINE, 1)
    assert sketch.isPointOnCurve(_VIA_ELLIPSE, *via_point)
    assert _angular_error(
        _curve_tangent(sketch, _VIA_ELLIPSE, _VIA_LINE, 1),
        _line_delta(sketch, _VIA_LINE),
    ) <= 1.0e-7
    assert serialize_sketch_constraint(sketch, 289)["type"] == "Horizontal"
    assert serialize_sketch_constraint(sketch, 290)["type"] == "Tangent"
