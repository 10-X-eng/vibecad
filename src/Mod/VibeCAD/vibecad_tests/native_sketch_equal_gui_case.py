# SPDX-License-Identifier: LGPL-2.1-or-later

"""All exact Equal families for the rolling Sketch GUI lifecycle gate."""

from __future__ import annotations

import math
import os
from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)


_INITIAL_GEOMETRY_COUNT = 0
_INITIAL_CONSTRAINT_COUNT = 0
_LINE_FIRST = 0
_LINE_SECOND = 1
_LINE_THIRD = 2
_CIRCLE = 3
_CIRCULAR_ARC = 4
_ELLIPSE = 5
_ELLIPTICAL_ARC = 6
_HYPERBOLA_FIRST = 7
_HYPERBOLA_SECOND = 8
_PARABOLA_FIRST = 9
_PARABOLA_SECOND = 10
_WEIGHT_FIRST = 11
_WEIGHT_SECOND = 12
_WEIGHT_THIRD = 13
_BSPLINE = 14
_EXTERNAL_LINE = 15
_ALIGNMENT_INDICES = (0, 1, 2)
_EQUAL_INDICES = tuple(range(3, 11))


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_EQUAL_PHASE {name}\n".encode("ascii"))


def _element(index: int, position: str = "whole") -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _arguments(
    sketch: Any,
    selection: list[dict[str, object]],
    *,
    geometry_count: int = 16,
    constraint_count: int | None = None,
) -> dict[str, object]:
    return {
        "operation": "constrain_equal",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": (
            int(sketch.ConstraintCount)
            if constraint_count is None
            else constraint_count
        ),
        "expected_external_geometry_count": 2,
        "selection": selection,
    }


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _line_length(sketch: Any, index: int) -> float:
    start = sketch.getPoint(index, 1)
    end = sketch.getPoint(index, 2)
    return math.hypot(float(end.x) - float(start.x), float(end.y) - float(start.y))


def _attribute(sketch: Any, index: int, name: str) -> float:
    return float(getattr(sketch.Geometry[index], name))


def _assert_equal_constraint(
    record: dict[str, Any],
    index: int,
    first: int,
    second: int,
) -> None:
    assert record == {
        "index": index,
        "type": "Equal",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": first},
            {"slot": 2, "geometry_index": second},
        ],
    }


def _add_fixture_geometry(
    sketch: Any, geometry: Any, expected: int, construction=False
):
    assert int(sketch.addGeometry(geometry, construction)) == expected


def _prepare_fixtures(document: Any, sketch: Any) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT,
        _INITIAL_CONSTRAINT_COUNT,
    )
    document.openTransaction("Prepare Native Sketch Equal fixtures")
    try:
        _add_fixture_geometry(
            sketch,
            Part.LineSegment(App.Vector(500, 240), App.Vector(503, 240)),
            _LINE_FIRST,
        )
        _add_fixture_geometry(
            sketch,
            Part.LineSegment(App.Vector(500, 250), App.Vector(507, 250)),
            _LINE_SECOND,
        )
        _add_fixture_geometry(
            sketch,
            Part.LineSegment(App.Vector(500, 260), App.Vector(511, 260)),
            _LINE_THIRD,
        )
        _add_fixture_geometry(
            sketch,
            Part.Circle(App.Vector(525, 245), App.Vector(0, 0, 1), 2),
            _CIRCLE,
        )
        _add_fixture_geometry(
            sketch,
            Part.ArcOfCircle(
                Part.Circle(App.Vector(540, 245), App.Vector(0, 0, 1), 5),
                0.2,
                2.1,
            ),
            _CIRCULAR_ARC,
        )
        first_ellipse = Part.Ellipse(App.Vector(560, 245), 8, 3)
        second_ellipse = Part.Ellipse(App.Vector(585, 245), 5, 2)
        _add_fixture_geometry(sketch, first_ellipse, _ELLIPSE)
        _add_fixture_geometry(
            sketch,
            Part.ArcOfEllipse(second_ellipse, 0.2, 2.1),
            _ELLIPTICAL_ARC,
        )
        first_hyperbola = Part.Hyperbola(App.Vector(610, 245), 7, 3)
        second_hyperbola = Part.Hyperbola(App.Vector(635, 245), 4, 2)
        _add_fixture_geometry(
            sketch,
            Part.ArcOfHyperbola(first_hyperbola, -1.0, 1.0),
            _HYPERBOLA_FIRST,
        )
        _add_fixture_geometry(
            sketch,
            Part.ArcOfHyperbola(second_hyperbola, -0.8, 0.9),
            _HYPERBOLA_SECOND,
        )
        first_parabola = Part.Parabola(
            App.Vector(663, 245),
            App.Vector(660, 245),
            App.Vector(0, 0, 1),
        )
        second_parabola = Part.Parabola(
            App.Vector(690, 245),
            App.Vector(685, 245),
            App.Vector(0, 0, 1),
        )
        _add_fixture_geometry(
            sketch,
            Part.ArcOfParabola(first_parabola, -3, 3),
            _PARABOLA_FIRST,
        )
        _add_fixture_geometry(
            sketch,
            Part.ArcOfParabola(second_parabola, -4, 4),
            _PARABOLA_SECOND,
        )
        for center, radius, expected in (
            ((720, 240), 1, _WEIGHT_FIRST),
            ((735, 250), 2, _WEIGHT_SECOND),
            ((750, 240), 1, _WEIGHT_THIRD),
        ):
            _add_fixture_geometry(
                sketch,
                Part.Circle(App.Vector(*center), App.Vector(0, 0, 1), radius),
                expected,
                True,
            )
        spline = Part.BSplineCurve(
            [App.Vector(720, 240), App.Vector(735, 250), App.Vector(750, 240)],
            [3, 3],
            [0.0, 1.0],
            False,
            2,
            [1.0, 2.0, 1.0],
            False,
        )
        _add_fixture_geometry(sketch, spline, _BSPLINE)
        alignments = [
            Sketcher.Constraint(
                "InternalAlignment:Sketcher::BSplineControlPoint",
                geometry_index,
                3,
                _BSPLINE,
                pole_index,
            )
            for pole_index, geometry_index in enumerate(
                (_WEIGHT_FIRST, _WEIGHT_SECOND, _WEIGHT_THIRD)
            )
        ]
        assert tuple(int(value) for value in sketch.addConstraint(alignments)) == (
            _ALIGNMENT_INDICES
        )
        _add_fixture_geometry(
            sketch,
            Part.LineSegment(App.Vector(500, 280), App.Vector(506, 280)),
            _EXTERNAL_LINE,
        )
        external_source = document.getObject("ExternalSource")
        assert external_source is not None
        sketch.addExternal(external_source.Name, "Edge1")
        sketch.addExternal(external_source.Name, "Edge2")
        _phase("fixture_geometry")
        document.recompute()
        _phase("fixture_recompute")
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def exercise_equal_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    _prepare_fixtures(document, sketch)
    _phase("fixtures")
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (16, 3)
    assert [
        str(sketch.GeometryFacadeList[index].InternalType)
        for index in (_WEIGHT_FIRST, _WEIGHT_SECOND, _WEIGHT_THIRD)
    ] == ["BSplineControlPoint"] * 3
    assert list(sketch.Geometry[_BSPLINE].getWeights()) == [1.0, 2.0, 1.0]

    undo_before_failures = int(document.UndoCount)
    invalid_calls = [
        _arguments(sketch, [_element(_LINE_FIRST), _element(_CIRCLE)]),
        _arguments(sketch, [_element(-1), _element(_LINE_FIRST)]),
        _arguments(sketch, [_element(-3), _element(-4)]),
        _arguments(sketch, [_element(_BSPLINE), _element(_LINE_FIRST)]),
        _arguments(sketch, [_element(_LINE_FIRST), _element(_LINE_FIRST)]),
        _arguments(
            sketch,
            [_element(_LINE_FIRST), _element(_LINE_SECOND)],
            geometry_count=15,
        ),
    ]
    for arguments in invalid_calls:
        assert native_call(arguments, succeeds=False)["error_code"] == (
            "NATIVE_SKETCH_INVALID"
        )
    closed = _arguments(sketch, [_element(_LINE_FIRST), _element(_LINE_SECOND)])
    closed["selection"][0]["position"] = "start"
    assert native_call(closed, succeeds=False)["error_code"] == (
        "NATIVE_ARGUMENTS_INVALID"
    )
    assert int(document.UndoCount) == undo_before_failures
    _phase("refusals")

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge3")
    process_events(8)
    selection = _selection_state(document)
    line_lengths_before = [
        _line_length(sketch, index)
        for index in (_LINE_FIRST, _LINE_SECOND, _LINE_THIRD)
    ]
    line_chain = native_call(
        _arguments(
            sketch,
            [_element(_LINE_FIRST), _element(_LINE_SECOND), _element(_LINE_THIRD)],
        )
    )
    assert line_chain["family"] == "line_length"
    assert line_chain["measured_before"]["maximum_error"] == 4.0
    assert line_chain["measured_after"]["maximum_error"] <= 1.0e-7
    _assert_equal_constraint(
        line_chain["constraints"][0],
        3,
        _LINE_FIRST,
        _LINE_SECOND,
    )
    _assert_equal_constraint(
        line_chain["constraints"][1],
        4,
        _LINE_SECOND,
        _LINE_THIRD,
    )
    assert _selection_state(document) == selection
    assert document.UndoNames[0] == "Create Native Sketch Equal"
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == 3
    assert [
        _line_length(sketch, index)
        for index in (_LINE_FIRST, _LINE_SECOND, _LINE_THIRD)
    ] == line_lengths_before
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == 5
    for index, record in zip((3, 4), line_chain["constraints"], strict=True):
        assert serialize_sketch_constraint(sketch, index) == record
    _phase("line_chain")

    undo_before_redundant = int(document.UndoCount)
    for pair in (
        (_LINE_FIRST, _LINE_SECOND),
        (_LINE_FIRST, _LINE_THIRD),
    ):
        failure = native_call(
            _arguments(sketch, [_element(pair[0]), _element(pair[1])]),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before_redundant
    _phase("redundancy")

    cases = (
        (
            "circular_radius",
            (_CIRCLE, _CIRCULAR_ARC),
            5,
        ),
        (
            "elliptic_radii",
            (_ELLIPSE, _ELLIPTICAL_ARC),
            6,
        ),
        (
            "hyperbolic_radii",
            (_HYPERBOLA_FIRST, _HYPERBOLA_SECOND),
            7,
        ),
        (
            "parabolic_focal_length",
            (_PARABOLA_FIRST, _PARABOLA_SECOND),
            8,
        ),
        (
            "b_spline_weight",
            (_WEIGHT_FIRST, _WEIGHT_SECOND),
            9,
        ),
        (
            "line_length",
            (_EXTERNAL_LINE, -3),
            10,
        ),
    )
    results = {}
    for family, (first, second), constraint_index in cases:
        result = native_call(_arguments(sketch, [_element(first), _element(second)]))
        assert result["family"] == family
        assert result["measured_before"]["maximum_error"] > 0.0
        assert result["measured_after"]["maximum_error"] <= 1.0e-7
        assert len(result["constraints"]) == 1
        _assert_equal_constraint(
            result["constraints"][0],
            constraint_index,
            first,
            second,
        )
        results[family] = result
        _phase(family)
    assert list(sketch.Geometry[_BSPLINE].getWeights()) == [1.0, 1.0, 1.0]
    assert _selection_state(document) == selection
    assert document.UndoNames[0] == "Create Native Sketch Equal"
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (16, 11)
    assert edit_boundary(document, sketch, controller) == boundary

    return {
        "geometries": {
            str(index): serialize_sketch_geometry(sketch, index)
            for index in range(_LINE_FIRST, _EXTERNAL_LINE + 1)
        },
        "constraints": {
            str(index): serialize_sketch_constraint(sketch, index)
            for index in (*_ALIGNMENT_INDICES, *_EQUAL_INDICES)
        },
        "families": [line_chain["family"], *(case[0] for case in cases)],
    }


def verify_reopened_equal(sketch: Any, expected: dict[str, Any]) -> None:
    assert expected["families"] == [
        "line_length",
        "circular_radius",
        "elliptic_radii",
        "hyperbolic_radii",
        "parabolic_focal_length",
        "b_spline_weight",
        "line_length",
    ]
    for raw_index, record in expected["geometries"].items():
        observed = serialize_sketch_geometry(sketch, int(raw_index))
        for key, value in record.items():
            if key != "tag":
                assert observed[key] == value, (raw_index, key, value, observed[key])
        assert observed["tag"]
    for raw_index, record in expected["constraints"].items():
        assert serialize_sketch_constraint(sketch, int(raw_index)) == record
    line_lengths = [
        _line_length(sketch, index)
        for index in (_LINE_FIRST, _LINE_SECOND, _LINE_THIRD)
    ]
    assert max(line_lengths) - min(line_lengths) <= 1.0e-7
    assert (
        abs(
            _attribute(sketch, _CIRCLE, "Radius")
            - _attribute(sketch, _CIRCULAR_ARC, "Radius")
        )
        <= 1.0e-7
    )
    for attribute in ("MajorRadius", "MinorRadius"):
        assert (
            abs(
                _attribute(sketch, _ELLIPSE, attribute)
                - _attribute(sketch, _ELLIPTICAL_ARC, attribute)
            )
            <= 1.0e-7
        )
        assert (
            abs(
                _attribute(sketch, _HYPERBOLA_FIRST, attribute)
                - _attribute(sketch, _HYPERBOLA_SECOND, attribute)
            )
            <= 1.0e-7
        )
    assert (
        abs(
            _attribute(sketch, _PARABOLA_FIRST, "Focal")
            - _attribute(sketch, _PARABOLA_SECOND, "Focal")
        )
        <= 1.0e-7
    )
    assert list(sketch.Geometry[_BSPLINE].getWeights()) == [1.0, 1.0, 1.0]
    assert (
        abs(_line_length(sketch, _EXTERNAL_LINE) - _line_length(sketch, -3)) <= 1.0e-7
    )
