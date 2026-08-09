# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Symmetric forms for the rolling Sketch GUI lifecycle gate."""

from __future__ import annotations

import os
from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
import Part

from VibeCADNativeSketchConstraintTargets import SketchConstraintElement
from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from VibeCADNativeSketchSymmetricMeasure import measure_sketch_symmetric
from VibeCADNativeSketchSymmetricTarget import ResolvedSketchSymmetric


_POINT_LINE_FIRST = 0
_POINT_LINE_SECOND = 1
_INTERNAL_LINE = 2
_POINT_ROOT_FIRST = 3
_POINT_ROOT_SECOND = 4
_CURVE_LINE = 5
_CURVE_POINT = 6
_CURVE_POINT_REFERENCE = 7
_CIRCULAR_ARC = 8
_CIRCULAR_REFERENCE = 9
_ELLIPTICAL_ARC = 10
_ELLIPTICAL_REFERENCE = 11
_HYPERBOLIC_ARC = 12
_HYPERBOLIC_REFERENCE = 13
_PARABOLIC_ARC = 14
_BSPLINE = 15
_BSPLINE_REFERENCE = 16
_EXTERNAL_LINE_CURVE = 17
_EXTERNAL_POINT_CURVE = 18
_CIRCLE = 19
_PERIODIC_BSPLINE = 20
_GEOMETRY_COUNT = 21
_CONSTRAINT_COUNT = 11


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_SYMMETRIC_PHASE {name}\n".encode("ascii"))


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _target(form: str, **values) -> dict[str, object]:
    return {"form": form, **values}


def _arguments(
    sketch: Any,
    target: dict[str, object],
    *,
    geometry_count: int = _GEOMETRY_COUNT,
    constraint_count: int | None = None,
) -> dict[str, object]:
    return {
        "operation": "constrain_symmetric",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": (
            int(sketch.ConstraintCount)
            if constraint_count is None
            else constraint_count
        ),
        "expected_external_geometry_count": 2,
        "target": target,
    }


def _points_about_line(first: tuple[int, str], second: tuple[int, str], line: int):
    return _target(
        "points_about_line",
        first_point=_element(*first),
        second_point=_element(*second),
        symmetry_line=_element(line, "whole"),
    )


def _points_about_point(
    first: tuple[int, str],
    second: tuple[int, str],
    point: tuple[int, str],
):
    return _target(
        "points_about_point",
        first_point=_element(*first),
        second_point=_element(*second),
        symmetry_point=_element(*point),
    )


def _curve_about_line(curve: int, line: int):
    return _target(
        "curve_about_line",
        curve=_element(curve, "whole"),
        symmetry_line=_element(line, "whole"),
    )


def _curve_about_point(curve: int, point: tuple[int, str]):
    return _target(
        "curve_about_point",
        curve=_element(curve, "whole"),
        symmetry_point=_element(*point),
    )


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _add(sketch: Any, geometry: Any, expected: int) -> None:
    assert int(sketch.addGeometry(geometry, False)) == expected


def _prepare_fixtures(document: Any, sketch: Any) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    document.openTransaction("Prepare Native Sketch Symmetric fixtures")
    try:
        _add(sketch, Part.Point(App.Vector(-20, 5)), _POINT_LINE_FIRST)
        _add(sketch, Part.Point(App.Vector(-5, 18)), _POINT_LINE_SECOND)
        _add(
            sketch,
            Part.LineSegment(App.Vector(0, -15), App.Vector(0, 15)),
            _INTERNAL_LINE,
        )
        _add(sketch, Part.Point(App.Vector(10, 10)), _POINT_ROOT_FIRST)
        _add(sketch, Part.Point(App.Vector(12, 15)), _POINT_ROOT_SECOND)
        _add(
            sketch,
            Part.LineSegment(App.Vector(30, 8), App.Vector(40, 15)),
            _CURVE_LINE,
        )
        _add(
            sketch,
            Part.LineSegment(App.Vector(50, 5), App.Vector(60, 10)),
            _CURVE_POINT,
        )
        _add(sketch, Part.Point(App.Vector(55, 0)), _CURVE_POINT_REFERENCE)
        _add(
            sketch,
            Part.ArcOfCircle(
                Part.Circle(App.Vector(80, 0), App.Vector(0, 0, 1), 6),
                0.3,
                2.0,
            ),
            _CIRCULAR_ARC,
        )
        _add(sketch, Part.Point(App.Vector(80, 0)), _CIRCULAR_REFERENCE)
        _add(
            sketch,
            Part.ArcOfEllipse(Part.Ellipse(App.Vector(110, 0), 8, 3), 0.2, 1.8),
            _ELLIPTICAL_ARC,
        )
        _add(sketch, Part.Point(App.Vector(112, 5)), _ELLIPTICAL_REFERENCE)
        _add(
            sketch,
            Part.ArcOfHyperbola(
                Part.Hyperbola(App.Vector(140, 0), 5, 2),
                -0.7,
                0.9,
            ),
            _HYPERBOLIC_ARC,
        )
        _add(sketch, Part.Point(App.Vector(140, 0)), _HYPERBOLIC_REFERENCE)
        _add(
            sketch,
            Part.ArcOfParabola(
                Part.Parabola(
                    App.Vector(173, 0),
                    App.Vector(170, 0),
                    App.Vector(0, 0, 1),
                ),
                -3,
                5,
            ),
            _PARABOLIC_ARC,
        )
        _add(
            sketch,
            Part.BSplineCurve(
                [App.Vector(200, 4), App.Vector(205, 9), App.Vector(210, 3)],
                [3, 3],
                [0.0, 1.0],
                False,
                2,
                [1.0, 1.0, 1.0],
                False,
            ),
            _BSPLINE,
        )
        _add(sketch, Part.Point(App.Vector(205, 0)), _BSPLINE_REFERENCE)
        _add(
            sketch,
            Part.LineSegment(App.Vector(230, -45), App.Vector(240, -35)),
            _EXTERNAL_LINE_CURVE,
        )
        _add(
            sketch,
            Part.LineSegment(App.Vector(250, -55), App.Vector(260, -45)),
            _EXTERNAL_POINT_CURVE,
        )
        _add(
            sketch,
            Part.Circle(App.Vector(280, 0), App.Vector(0, 0, 1), 5),
            _CIRCLE,
        )
        periodic = Part.BSplineCurve()
        periodic.interpolate(
            [
                App.Vector(300, 0),
                App.Vector(307, 6),
                App.Vector(314, 0),
                App.Vector(307, -6),
            ],
            True,
        )
        _add(sketch, periodic, _PERIODIC_BSPLINE)
        source = document.getObject("ExternalSource")
        assert source is not None
        sketch.addExternal(source.Name, "Edge1")
        sketch.addExternal(source.Name, "Edge2")
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def _assert_satisfied(result: dict[str, Any], form: str, index: int) -> None:
    assert result["operation"] == "constrain_symmetric"
    assert result["form"] == form
    assert result["constraint"]["index"] == index
    assert result["constraint"]["type"] == "Symmetric"
    assert result["constraint"]["driving"] is True
    assert result["constraint"]["active"] is True
    assert result["constraint"]["virtual"] is False
    assert result["measured_after"]["reflection_error"] <= 1.0e-7
    assert result["measured_after"]["midpoint_error"] <= 1.0e-7
    assert result["measured_after"]["unit"] == "mm"


def _resolved_from_constraint(constraint: Any) -> ResolvedSketchSymmetric:
    reference_kind = "point" if int(constraint.ThirdPos) else "line"
    return ResolvedSketchSymmetric(
        "reopened",
        (
            SketchConstraintElement(
                int(constraint.First),
                {1: "start", 2: "end", 3: "center"}[int(constraint.FirstPos)],
            ),
            SketchConstraintElement(
                int(constraint.Second),
                {1: "start", 2: "end", 3: "center"}[int(constraint.SecondPos)],
            ),
            SketchConstraintElement(
                int(constraint.Third),
                (
                    {1: "start", 2: "end", 3: "center"}[int(constraint.ThirdPos)]
                    if reference_kind == "point"
                    else "whole"
                ),
            ),
        ),
        reference_kind,
    )


def exercise_symmetric_case(
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
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _GEOMETRY_COUNT,
        0,
    )
    _phase("fixtures")

    undo_before_failures = int(document.UndoCount)
    invalid_calls = (
        _arguments(sketch, _curve_about_line(_CIRCLE, -1)),
        _arguments(sketch, _curve_about_line(_PERIODIC_BSPLINE, -1)),
        _arguments(sketch, _curve_about_line(_INTERNAL_LINE, _INTERNAL_LINE)),
        _arguments(
            sketch,
            _points_about_line(
                (_POINT_LINE_FIRST, "start"),
                (_POINT_LINE_SECOND, "start"),
                _CIRCLE,
            ),
        ),
        _arguments(
            sketch,
            _points_about_line((-3, "start"), (-4, "start"), -1),
        ),
        _arguments(
            sketch,
            _curve_about_line(_CURVE_LINE, -1),
            geometry_count=_GEOMETRY_COUNT - 1,
        ),
    )
    for arguments in invalid_calls:
        failure = native_call(arguments, succeeds=False)
        assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    closed = _arguments(sketch, _curve_about_line(_CURVE_LINE, -1))
    closed["target"]["curve"]["position"] = "start"
    assert native_call(closed, succeeds=False)["error_code"] == (
        "NATIVE_ARGUMENTS_INVALID"
    )
    assert int(document.UndoCount) == undo_before_failures
    _phase("refusals")

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Vertex1")
    process_events(8)
    selection = _selection_state(document)
    first_target = _points_about_line(
        (_POINT_LINE_FIRST, "start"),
        (_POINT_LINE_SECOND, "start"),
        _INTERNAL_LINE,
    )
    first = native_call(_arguments(sketch, first_target))
    _assert_satisfied(first, "points_about_line", 0)
    assert _selection_state(document) == selection
    assert document.UndoNames[0] == "Create Native Sketch Symmetric"
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == 0
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == 1
    assert serialize_sketch_constraint(sketch, 0) == first["constraint"]
    duplicate = _points_about_line(
        (_POINT_LINE_SECOND, "start"),
        (_POINT_LINE_FIRST, "start"),
        _INTERNAL_LINE,
    )
    undo_before_duplicate = int(document.UndoCount)
    assert native_call(_arguments(sketch, duplicate), succeeds=False)["error_code"] == (
        "NATIVE_SKETCH_INVALID"
    )
    assert int(document.UndoCount) == undo_before_duplicate
    _phase("points_line")

    cases = (
        (
            "points_about_point",
            _points_about_point(
                (_POINT_ROOT_FIRST, "start"),
                (_POINT_ROOT_SECOND, "start"),
                (-1, "start"),
            ),
        ),
        ("curve_about_line", _curve_about_line(_CURVE_LINE, -1)),
        (
            "curve_about_point",
            _curve_about_point(_CURVE_POINT, (_CURVE_POINT_REFERENCE, "start")),
        ),
        (
            "curve_about_point",
            _curve_about_point(_CIRCULAR_ARC, (_CIRCULAR_REFERENCE, "start")),
        ),
        (
            "curve_about_point",
            _curve_about_point(
                _ELLIPTICAL_ARC,
                (_ELLIPTICAL_REFERENCE, "start"),
            ),
        ),
        (
            "curve_about_point",
            _curve_about_point(
                _HYPERBOLIC_ARC,
                (_HYPERBOLIC_REFERENCE, "start"),
            ),
        ),
        ("curve_about_line", _curve_about_line(_PARABOLIC_ARC, -1)),
        (
            "curve_about_point",
            _curve_about_point(_BSPLINE, (_BSPLINE_REFERENCE, "start")),
        ),
        ("curve_about_line", _curve_about_line(_EXTERNAL_LINE_CURVE, -3)),
        (
            "curve_about_point",
            _curve_about_point(_EXTERNAL_POINT_CURVE, (-4, "start")),
        ),
    )
    results = [first]
    for expected_index, (form, target) in enumerate(cases, start=1):
        response = native_call(_arguments(sketch, target))
        _assert_satisfied(response, form, expected_index)
        results.append(response)
        _phase(f"constraint_{expected_index}")

    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _GEOMETRY_COUNT,
        _CONSTRAINT_COUNT,
    )
    assert not tuple(sketch.ConflictingConstraints)
    assert not tuple(sketch.RedundantConstraints)
    assert not tuple(sketch.MalformedConstraints)
    assert _selection_state(document) == selection
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometries": {
            str(index): serialize_sketch_geometry(sketch, index)
            for index in range(_GEOMETRY_COUNT)
        },
        "constraints": {
            str(index): serialize_sketch_constraint(sketch, index)
            for index in range(_CONSTRAINT_COUNT)
        },
        "forms": [result["form"] for result in results],
    }


def verify_reopened_symmetric(sketch: Any, expected: dict[str, Any]) -> None:
    assert int(sketch.GeometryCount) == _GEOMETRY_COUNT
    assert int(sketch.ConstraintCount) == _CONSTRAINT_COUNT
    assert expected["forms"] == [
        "points_about_line",
        "points_about_point",
        "curve_about_line",
        "curve_about_point",
        "curve_about_point",
        "curve_about_point",
        "curve_about_point",
        "curve_about_line",
        "curve_about_point",
        "curve_about_line",
        "curve_about_point",
    ]
    for raw_index, record in expected["geometries"].items():
        observed = serialize_sketch_geometry(sketch, int(raw_index))
        for key, value in record.items():
            if key != "tag":
                assert observed[key] == value, (raw_index, key, value, observed[key])
        assert observed["tag"]
    for raw_index, record in expected["constraints"].items():
        index = int(raw_index)
        assert serialize_sketch_constraint(sketch, index) == record
        assert measure_sketch_symmetric(
            sketch,
            _resolved_from_constraint(sketch.Constraints[index]),
        ).satisfied()
    assert not tuple(sketch.ConflictingConstraints)
    assert not tuple(sketch.RedundantConstraints)
    assert not tuple(sketch.MalformedConstraints)
