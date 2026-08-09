# SPDX-License-Identifier: LGPL-2.1-or-later

"""B-spline degree elevation coverage for Native Sketch GUI lifecycle gates."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCAD as App
import Part

from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchExternalState import iter_external_reference_records
from VibeCADNativeSketchMutationState import geometry_records_without_tags
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)


def _state(sketch: Any) -> dict[str, Any]:
    return {
        "geometry": geometry_records_without_tags(
            canonical_sketch_records(iter_sketch_geometry_records(sketch))
        ),
        "constraints": canonical_sketch_records(iter_sketch_constraint_records(sketch)),
        "references": canonical_sketch_records(iter_external_reference_records(sketch)),
        "external_geometry": canonical_sketch_records(
            iter_sketch_external_geometry_records(sketch)
        ),
        "expressions": tuple(
            (str(path), str(expression))
            for path, expression in list(sketch.ExpressionEngine)
        ),
        "degrees_of_freedom": int(sketch.DoF),
    }


def _arguments(sketch: Any, geometry_indices: list[int]) -> dict[str, object]:
    state = _state(sketch)
    return {
        "operation": "increase_bspline_degree",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_indices": geometry_indices,
    }


def _samples(curve: Any) -> tuple[tuple[float, float, float], ...]:
    first = float(curve.FirstParameter)
    last = float(curve.LastParameter)
    return tuple(
        tuple(curve.value(first + (last - first) * fraction))
        for fraction in (0.0, 0.0625, 0.125, 0.25, 0.5, 0.75, 0.875, 1.0)
    )


def _same_samples(
    actual: tuple[tuple[float, float, float], ...],
    expected: tuple[tuple[float, float, float], ...],
) -> bool:
    return all(
        math.dist(point, reference) <= 1.0e-8
        for point, reference in zip(actual, expected, strict=True)
    )


def _interpolated_spline(sketch: Any) -> int:
    curve = Part.BSplineCurve()
    curve.interpolate(
        [
            App.Vector(0, 0),
            App.Vector(3, 4),
            App.Vector(7, -2),
            App.Vector(11, 3),
            App.Vector(15, 0),
        ]
    )
    return int(sketch.addGeometry(curve, True))


def _undo_redo(
    document: Any,
    sketch: Any,
    process_events: Callable[[int], None],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    document.undo()
    process_events(16)
    assert _state(sketch) == before
    document.redo()
    process_events(16)
    assert _state(sketch) == after


def exercise_bspline_degree_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    spline = _interpolated_spline(sketch)
    initial_degree = int(sketch.Geometry[spline].Degree)
    exposed = sketch.exposeInternalGeometry(spline)
    assert exposed["created_count"] > 0
    weight = next(
        index
        for index, constraint in enumerate(sketch.Constraints)
        if constraint.Type == "Weight"
    )
    sketch.renameConstraint(weight, "ElevatedWeight")
    sketch.setExpression(f"Constraints[{weight}]", "1")

    line = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 10), App.Vector(10, 10)),
            False,
        )
    )
    maximum = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 14), App.Vector(10, 14)),
            False,
        )
    )
    sketch.convertToNURBS(maximum)
    sketch.increaseBSplineDegree(maximum, 24)
    assert int(sketch.Geometry[maximum].Degree) == 25
    document.recompute()
    process_events(8)
    document.clearUndos()

    root_tag = str(sketch.GeometryFacadeList[spline].Tag)
    root_id = int(sketch.GeometryFacadeList[spline].Id)
    helper_tags = tuple(
        str(item.Tag)
        for item in sketch.GeometryFacadeList[1 : 1 + exposed["created_count"]]
    )
    constraint_tags = tuple(str(item.Tag) for item in sketch.Constraints)
    expressions = tuple(sketch.ExpressionEngine)
    shape = _samples(sketch.Geometry[spline])
    arguments = _arguments(sketch, [spline])
    before = _state(sketch)
    before_geometry_count = int(sketch.GeometryCount)
    before_constraint_count = int(sketch.ConstraintCount)
    undo_before = int(document.UndoCount)

    diagnostic = sketch.diagnoseIncreaseBSplineDegree([spline])
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_indices"] == [spline]
    assert diagnostic["old_degrees"] == [initial_degree]
    assert diagnostic["new_degrees"] == [initial_degree + 1]
    expected_exposed = int(diagnostic["exposed_internal_geometry_count"])
    assert expected_exposed > 0
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    for call_id, invalid in (
        (
            "rolling-bspline-degree-stale",
            {**arguments, "expected_geometry_count": before_geometry_count + 1},
        ),
        ("rolling-bspline-degree-line", _arguments(sketch, [line])),
        ("rolling-bspline-degree-maximum", _arguments(sketch, [maximum])),
    ):
        failure = native_call(invalid, succeeds=False, call_id=call_id)
        assert failure["error_code"] == "NATIVE_SKETCH_INVALID", failure
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    result = native_call(arguments, call_id="rolling-bspline-degree")
    assert result["geometry_indices"] == [spline]
    assert result["old_degrees"] == [initial_degree]
    assert result["new_degrees"] == [initial_degree + 1]
    assert result["exposed_internal_geometry_count"] == expected_exposed
    assert result["created_geometry_count"] == expected_exposed
    assert result["created_constraint_count"] == (
        int(sketch.ConstraintCount) - before_constraint_count
    )
    assert int(sketch.GeometryCount) == before_geometry_count + expected_exposed
    assert int(sketch.Geometry[spline].Degree) == initial_degree + 1
    assert _same_samples(_samples(sketch.Geometry[spline]), shape)
    assert str(sketch.GeometryFacadeList[spline].Tag) == root_tag
    assert int(sketch.GeometryFacadeList[spline].Id) == root_id
    assert bool(sketch.GeometryFacadeList[spline].Construction) is True
    assert (
        tuple(
            str(item.Tag)
            for item in sketch.GeometryFacadeList[1 : 1 + len(helper_tags)]
        )
        == helper_tags
    )
    assert tuple(
        str(item.Tag) for item in sketch.Constraints[: len(constraint_tags)]
    ) == (constraint_tags)
    assert tuple(sketch.ExpressionEngine) == expressions
    assert document.UndoNames[0] == "Increase Sketch B-Spline Degree"

    after = _state(sketch)
    _undo_redo(document, sketch, process_events, before, after)
    assert edit_boundary(document, sketch, controller) == boundary
    return after


def verify_reopened_bspline_degree(sketch: Any, expected: dict[str, Any]) -> None:
    observed = _state(sketch)
    for key, value in expected.items():
        assert observed[key] == value, (
            "reopened B-spline degree state drift",
            key,
            value,
            observed[key],
        )
