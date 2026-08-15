# SPDX-License-Identifier: LGPL-2.1-or-later

"""Geometry-to-B-Spline coverage for the Native Sketch GUI lifecycle gates."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import Part
import Sketcher

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


def _arguments(
    sketch: Any, geometry_indices: list[int], **updates
) -> dict[str, object]:
    state = _state(sketch)
    result: dict[str, object] = {
        "operation": "convert_to_nurbs",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_indices": geometry_indices,
    }
    result.update(updates)
    return result


def _line(sketch: Any, start: tuple[float, float], end: tuple[float, float]) -> int:
    return int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(*start), App.Vector(*end)),
            False,
        )
    )


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


def exercise_nurbs_conversion_case(
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
    first = _line(sketch, (0, 0), (10, 0))
    second = _line(sketch, (10, 0), (15, 4))
    point = int(sketch.addGeometry(Part.Point(App.Vector(3, 8)), False))
    coincident = int(
        sketch.addConstraint(Sketcher.Constraint("Coincident", first, 2, second, 1))
    )
    coincident_tag = str(sketch.Constraints[coincident].Tag)
    distance = int(sketch.addConstraint(Sketcher.Constraint("Distance", first, 10.0)))
    sketch.renameConstraint(distance, "ConvertedLength")
    sketch.setExpression(f"Constraints[{distance}]", "10 mm")

    source = document.addObject("Part::Feature", "NURBSConversionSource")
    source.Label = "External edge copied by Geometry to B-Spline"
    source.Shape = Part.makeLine(App.Vector(0, 12, 0), App.Vector(10, 12, 0))
    document.recompute()
    sketch.addExternal(source.Name, "Edge1")
    document.recompute()
    process_events(8)
    assert not list(sketch.ConflictingConstraints)
    assert not list(sketch.RedundantConstraints)

    selected = [-3, first]
    before = _state(sketch)
    undo_before = int(document.UndoCount)
    arguments = _arguments(sketch, selected)
    diagnostic = sketch.diagnoseConvertToNURBS(selected)
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_indices"] == selected
    assert diagnostic["converted_geometry_indices"] == [3, first]
    assert diagnostic["exposed_internal_geometry_count"] == 4
    assert diagnostic["geometry_count"] == 8
    assert diagnostic["constraint_count"] == 7
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        {**arguments, "expected_geometry_count": int(sketch.GeometryCount) + 1},
        succeeds=False,
        call_id="rolling-nurbs-conversion-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    wrong_kind = native_call(
        _arguments(sketch, [point]),
        succeeds=False,
        call_id="rolling-nurbs-conversion-point",
    )
    assert wrong_kind["error_code"] == "NATIVE_SKETCH_INVALID", wrong_kind
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    result = native_call(arguments, call_id="rolling-nurbs-conversion")
    expected_result = {
        "input_geometry_count": 2,
        "converted_geometry_indices": [3, first],
        "internal_conversion_count": 1,
        "external_copy_count": 1,
        "exposed_internal_geometry_count": 4,
        "created_geometry_count": 6,
        "removed_geometry_count": 1,
        "created_constraint_count": 6,
        "removed_constraint_count": 1,
    }
    for field, expected in expected_result.items():
        assert result[field] == expected, (field, result)
    assert int(sketch.GeometryCount) == 8
    assert int(sketch.ConstraintCount) == 7
    assert sketch.Geometry[first].TypeId == "Part::GeomBSplineCurve"
    assert sketch.Geometry[3].TypeId == "Part::GeomBSplineCurve"
    assert all(sketch.GeometryFacadeList[index].Construction for index in range(4, 8))
    assert tuple(sketch.ExpressionEngine) == ()
    surviving = [
        constraint
        for constraint in sketch.Constraints
        if str(constraint.Tag) == coincident_tag
    ]
    assert len(surviving) == 1 and surviving[0].Type == "Coincident"
    assert int(document.UndoCount) in {undo_before, undo_before + 1}
    assert document.UndoNames[0] == "Convert Sketch Geometry to B-Splines"

    after = _state(sketch)
    _undo_redo(document, sketch, process_events, before, after)
    assert edit_boundary(document, sketch, controller) == boundary
    return after


def verify_reopened_nurbs_conversion(sketch: Any, expected: dict[str, Any]) -> None:
    observed = _state(sketch)
    for key, value in expected.items():
        assert observed[key] == value, (
            "reopened Geometry-to-B-Spline state drift",
            key,
            value,
            observed[key],
        )
