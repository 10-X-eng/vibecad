# SPDX-License-Identifier: LGPL-2.1-or-later

"""Unified Coincident lifecycle cases for the rolling Sketch gate."""

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
    point_arguments,
)


_INITIAL_GEOMETRY_COUNT = 181
_INITIAL_CONSTRAINT_COUNT = 252
_FIRST_POINT_INDEX = 181
_SECOND_POINT_INDEX = 182
_POINT_PAIR_CONSTRAINT_INDEX = 252
_CURVE_POINT_INDEX = 183
_POINT_ON_OBJECT_CONSTRAINT_INDEX = 253
_FIRST_CIRCLE_INDEX = 184
_SECOND_CIRCLE_INDEX = 185
_CONCENTRIC_CONSTRAINT_INDEX = 254


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _point_pair_target(
    first: int = _FIRST_POINT_INDEX,
    first_position: str = "start",
    second: int = _SECOND_POINT_INDEX,
    second_position: str = "start",
) -> dict[str, object]:
    return {
        "form": "point_point",
        "first_point": _element(first, first_position),
        "second_point": _element(second, second_position),
    }


def _point_on_object_target() -> dict[str, object]:
    return {
        "form": "point_on_object",
        "point": _element(_CURVE_POINT_INDEX, "start"),
        "curve": _element(-1, "whole"),
    }


def _concentric_target(
    first: int = _FIRST_CIRCLE_INDEX,
    second: int = _SECOND_CIRCLE_INDEX,
) -> dict[str, object]:
    return {
        "form": "concentric",
        "first_curve": _element(first, "whole"),
        "second_curve": _element(second, "whole"),
    }


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _point(sketch: Any, index: int, position: int = 1) -> tuple[float, float]:
    value = sketch.getPoint(index, position)
    return float(value.x), float(value.y)


def _separation(sketch: Any, first: int, second: int, position: int) -> float:
    return math.dist(
        _point(sketch, first, position),
        _point(sketch, second, position),
    )


def _assert_exact_geometric_constraint(
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


def _assert_undo_redo(
    *,
    sketch: Any,
    document: Any,
    process_events: Callable[[int], None],
    before_constraint_count: int,
    before_measurement: Callable[[], Any],
    expected_before: Any,
    after_measurement: Callable[[], Any],
    expected_after: Any,
    constraint_index: int,
    constraint: dict[str, Any],
) -> None:
    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == before_constraint_count
    assert before_measurement() == expected_before
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == before_constraint_count + 1
    assert after_measurement() == expected_after
    assert serialize_sketch_constraint(sketch, constraint_index) == constraint


def exercise_coincident_case(
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
    for geometry_count, coordinates, expected_index in (
        (_INITIAL_GEOMETRY_COUNT, (310.0, 160.0), _FIRST_POINT_INDEX),
        (_INITIAL_GEOMETRY_COUNT + 1, (314.0, 163.0), _SECOND_POINT_INDEX),
    ):
        response = native_call(
            point_arguments(
                sketch,
                geometry_count=geometry_count,
                x=coordinates[0],
                y=coordinates[1],
            )
        )
        assert response["geometry"]["index"] == expected_index
    assert _separation(sketch, _FIRST_POINT_INDEX, _SECOND_POINT_INDEX, 1) == 5.0

    failure_undo_before = int(document.UndoCount)
    whole_point = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            target=_point_pair_target(first_position="whole"),
        ),
        succeeds=False,
    )
    assert whole_point["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    same_line = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            target=_point_pair_target(0, "start", 0, "end"),
        ),
        succeeds=False,
    )
    assert same_line["error_code"] == "NATIVE_SKETCH_INVALID"
    unsupported_concentric = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            target=_concentric_target(0, 1),
        ),
        succeeds=False,
    )
    assert unsupported_concentric["error_code"] == "NATIVE_SKETCH_INVALID"
    mixed_target = _point_pair_target()
    mixed_target["curve"] = _element(0, "whole")
    mixed = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            target=mixed_target,
        ),
        succeeds=False,
    )
    assert mixed["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    stale = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            target=_point_pair_target(),
        ),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == failure_undo_before
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT
    assert _separation(sketch, _FIRST_POINT_INDEX, _SECOND_POINT_INDEX, 1) == 5.0

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    point_pair = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            target=_point_pair_target(),
        )
    )
    assert point_pair["operation"] == "constrain_coincident"
    assert point_pair["target_form"] == "point_point"
    assert point_pair["measured_before"] == {
        "satisfied": False,
        "separation": 5.0,
        "unit": "mm",
    }
    assert point_pair["measured_after"]["satisfied"] is True
    assert math.isclose(point_pair["measured_after"]["separation"], 0.0)
    point_pair_constraint = point_pair["constraint"]
    _assert_exact_geometric_constraint(
        point_pair_constraint,
        index=_POINT_PAIR_CONSTRAINT_INDEX,
        constraint_type="Coincident",
        references=[
            {"slot": 1, "geometry_index": _FIRST_POINT_INDEX, "position": 1},
            {"slot": 2, "geometry_index": _SECOND_POINT_INDEX, "position": 1},
        ],
    )
    assert point_pair["assistant_undo_available"] is True
    assert len(point_pair["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Coincident"
    assert int(document.UndoCount) == 20
    assert _selection_state(document) == selection
    assert math.isclose(
        _separation(sketch, _FIRST_POINT_INDEX, _SECOND_POINT_INDEX, 1),
        0.0,
        abs_tol=1.0e-7,
    )

    duplicate = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            external_geometry_count=1,
            target=_point_pair_target(),
        ),
        succeeds=False,
    )
    assert duplicate["error_code"] == "NATIVE_SKETCH_INVALID"
    assert "already coincident" in duplicate["error"]
    _assert_undo_redo(
        sketch=sketch,
        document=document,
        process_events=process_events,
        before_constraint_count=_INITIAL_CONSTRAINT_COUNT,
        before_measurement=lambda: (
            _point(sketch, _FIRST_POINT_INDEX),
            _point(sketch, _SECOND_POINT_INDEX),
        ),
        expected_before=((310.0, 160.0), (314.0, 163.0)),
        after_measurement=lambda: math.isclose(
            _separation(sketch, _FIRST_POINT_INDEX, _SECOND_POINT_INDEX, 1),
            0.0,
            abs_tol=1.0e-7,
        ),
        expected_after=True,
        constraint_index=_POINT_PAIR_CONSTRAINT_INDEX,
        constraint=point_pair_constraint,
    )

    curve_point_response = native_call(
        point_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 2,
            x=320.0,
            y=170.0,
        )
    )
    assert curve_point_response["geometry"]["index"] == _CURVE_POINT_INDEX
    point_on_object = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 3,
            external_geometry_count=1,
            target=_point_on_object_target(),
        )
    )
    assert point_on_object["target_form"] == "point_on_object"
    assert point_on_object["measured_before"] == {"point_on_curve": False}
    assert point_on_object["measured_after"] == {"point_on_curve": True}
    point_on_object_constraint = point_on_object["constraint"]
    _assert_exact_geometric_constraint(
        point_on_object_constraint,
        index=_POINT_ON_OBJECT_CONSTRAINT_INDEX,
        constraint_type="PointOnObject",
        references=[
            {"slot": 1, "geometry_index": _CURVE_POINT_INDEX, "position": 1},
            {"slot": 2, "geometry_index": -1},
        ],
    )
    assert math.isclose(_point(sketch, _CURVE_POINT_INDEX)[1], 0.0, abs_tol=1.0e-7)
    _assert_undo_redo(
        sketch=sketch,
        document=document,
        process_events=process_events,
        before_constraint_count=_INITIAL_CONSTRAINT_COUNT + 1,
        before_measurement=lambda: _point(sketch, _CURVE_POINT_INDEX),
        expected_before=(320.0, 170.0),
        after_measurement=lambda: bool(
            sketch.isPointOnCurve(-1, *_point(sketch, _CURVE_POINT_INDEX))
        ),
        expected_after=True,
        constraint_index=_POINT_ON_OBJECT_CONSTRAINT_INDEX,
        constraint=point_on_object_constraint,
    )

    for geometry_count, center, radius, expected_index in (
        (_INITIAL_GEOMETRY_COUNT + 3, (330.0, 180.0), 4.0, _FIRST_CIRCLE_INDEX),
        (_INITIAL_GEOMETRY_COUNT + 4, (338.0, 186.0), 3.0, _SECOND_CIRCLE_INDEX),
    ):
        response = native_call(
            circle_arguments(
                sketch,
                geometry_count=geometry_count,
                center=center,
                radius=radius,
            )
        )
        assert response["geometry"]["index"] == expected_index
    assert _separation(sketch, _FIRST_CIRCLE_INDEX, _SECOND_CIRCLE_INDEX, 3) == 10.0

    concentric = native_call(
        coincident_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 5,
            external_geometry_count=1,
            target=_concentric_target(),
        )
    )
    assert concentric["target_form"] == "concentric"
    assert concentric["measured_before"] == {
        "satisfied": False,
        "separation": 10.0,
        "unit": "mm",
    }
    assert concentric["measured_after"]["satisfied"] is True
    concentric_constraint = concentric["constraint"]
    _assert_exact_geometric_constraint(
        concentric_constraint,
        index=_CONCENTRIC_CONSTRAINT_INDEX,
        constraint_type="Coincident",
        references=[
            {"slot": 1, "geometry_index": _FIRST_CIRCLE_INDEX, "position": 3},
            {"slot": 2, "geometry_index": _SECOND_CIRCLE_INDEX, "position": 3},
        ],
    )
    assert math.isclose(
        _separation(sketch, _FIRST_CIRCLE_INDEX, _SECOND_CIRCLE_INDEX, 3),
        0.0,
        abs_tol=1.0e-7,
    )
    _assert_undo_redo(
        sketch=sketch,
        document=document,
        process_events=process_events,
        before_constraint_count=_INITIAL_CONSTRAINT_COUNT + 2,
        before_measurement=lambda: (
            _point(sketch, _FIRST_CIRCLE_INDEX, 3),
            _point(sketch, _SECOND_CIRCLE_INDEX, 3),
        ),
        expected_before=((330.0, 180.0), (338.0, 186.0)),
        after_measurement=lambda: math.isclose(
            _separation(sketch, _FIRST_CIRCLE_INDEX, _SECOND_CIRCLE_INDEX, 3),
            0.0,
            abs_tol=1.0e-7,
        ),
        expected_after=True,
        constraint_index=_CONCENTRIC_CONSTRAINT_INDEX,
        constraint=concentric_constraint,
    )
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "point_pair": {
            "first": serialize_sketch_geometry(sketch, _FIRST_POINT_INDEX),
            "second": serialize_sketch_geometry(sketch, _SECOND_POINT_INDEX),
            "constraint": point_pair_constraint,
        },
        "point_on_object": {
            "point": serialize_sketch_geometry(sketch, _CURVE_POINT_INDEX),
            "constraint": point_on_object_constraint,
        },
        "concentric": {
            "first": serialize_sketch_geometry(sketch, _FIRST_CIRCLE_INDEX),
            "second": serialize_sketch_geometry(sketch, _SECOND_CIRCLE_INDEX),
            "constraint": concentric_constraint,
        },
    }


def _verify_geometry(sketch: Any, index: int, expected: dict[str, Any]) -> None:
    observed = serialize_sketch_geometry(sketch, index)
    for key in expected:
        if key != "tag":
            assert observed[key] == expected[key]
    assert observed["tag"]
    assert expected["tag"]


def verify_reopened_coincident(sketch: Any, expected: dict[str, Any]) -> None:
    _verify_geometry(sketch, _FIRST_POINT_INDEX, expected["point_pair"]["first"])
    _verify_geometry(sketch, _SECOND_POINT_INDEX, expected["point_pair"]["second"])
    _verify_geometry(sketch, _CURVE_POINT_INDEX, expected["point_on_object"]["point"])
    _verify_geometry(sketch, _FIRST_CIRCLE_INDEX, expected["concentric"]["first"])
    _verify_geometry(sketch, _SECOND_CIRCLE_INDEX, expected["concentric"]["second"])
    for index, constraint in (
        (_POINT_PAIR_CONSTRAINT_INDEX, expected["point_pair"]["constraint"]),
        (
            _POINT_ON_OBJECT_CONSTRAINT_INDEX,
            expected["point_on_object"]["constraint"],
        ),
        (_CONCENTRIC_CONSTRAINT_INDEX, expected["concentric"]["constraint"]),
    ):
        assert serialize_sketch_constraint(sketch, index) == constraint
    assert math.isclose(
        _separation(sketch, _FIRST_POINT_INDEX, _SECOND_POINT_INDEX, 1),
        0.0,
        abs_tol=1.0e-7,
    )
    assert sketch.isPointOnCurve(-1, *_point(sketch, _CURVE_POINT_INDEX))
    assert math.isclose(
        _separation(sketch, _FIRST_CIRCLE_INDEX, _SECOND_CIRCLE_INDEX, 3),
        0.0,
        abs_tol=1.0e-7,
    )
