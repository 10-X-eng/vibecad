# SPDX-License-Identifier: LGPL-2.1-or-later

"""B-spline degree reduction coverage for Native Sketch GUI lifecycle gates."""

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


def _arguments(sketch: Any, geometry_index: int, limit: float) -> dict[str, object]:
    state = _state(sketch)
    return {
        "operation": "decrease_bspline_degree",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_index": geometry_index,
        "maximum_deviation_mm": limit,
    }


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


def _geometry_index(sketch: Any, tag: str) -> int:
    matches = [
        index
        for index, facade in enumerate(sketch.GeometryFacadeList)
        if str(facade.Tag) == tag
    ]
    assert len(matches) == 1, (tag, matches)
    return matches[0]


def _constraint_index(sketch: Any, tag: str) -> int:
    matches = [
        index
        for index, constraint in enumerate(sketch.Constraints)
        if str(constraint.Tag) == tag
    ]
    assert len(matches) == 1, (tag, matches)
    return matches[0]


def _helper_alignment(sketch: Any, root: int) -> tuple[tuple[Any, ...], ...]:
    result = []
    for index, constraint in enumerate(sketch.Constraints):
        if (
            str(constraint.Type) != "InternalAlignment"
            or int(constraint.Second) != root
        ):
            continue
        helper = int(constraint.First)
        result.append(
            (
                helper,
                str(sketch.GeometryFacadeList[helper].Tag),
                str(sketch.GeometryFacadeList[helper].InternalType),
                int(constraint.InternalAlignmentIndex),
                index,
                str(constraint.Tag),
            )
        )
    return tuple(sorted(result))


def _helper_roles(sketch: Any, root: int) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (geometry, internal_type, alignment_index, constraint)
        for (
            geometry,
            _geometry_tag,
            internal_type,
            alignment_index,
            constraint,
            _constraint_tag,
        ) in _helper_alignment(sketch, root)
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


def exercise_bspline_degree_decrease_case(
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
    root = _interpolated_spline(sketch)
    initial_degree = int(sketch.Geometry[root].Degree)
    assert initial_degree == 3
    exposed = sketch.exposeInternalGeometry(root)
    old_helper_count = int(exposed["created_count"])
    assert old_helper_count > 0

    line = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 10), App.Vector(10, 10)),
            False,
        )
    )
    length = int(sketch.addConstraint(Sketcher.Constraint("Distance", line, 10.0)))
    sketch.renameConstraint(length, "PreservedLength")
    sketch.setExpression(f"Constraints[{length}]", "10 mm")
    linear_nurbs = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 14), App.Vector(10, 14)),
            False,
        )
    )
    sketch.convertToNURBS(linear_nurbs)
    document.recompute()
    process_events(8)
    assert int(sketch.Geometry[linear_nurbs].Degree) == 1
    assert not list(sketch.ConflictingConstraints)
    assert not list(sketch.RedundantConstraints)
    document.clearUndos()

    root_tag = str(sketch.GeometryFacadeList[root].Tag)
    root_id = int(sketch.GeometryFacadeList[root].Id)
    line_tag = str(sketch.GeometryFacadeList[line].Tag)
    length_tag = str(sketch.Constraints[length].Tag)
    old_helper_tags = {
        str(item.Tag)
        for item in sketch.GeometryFacadeList[root + 1 : root + 1 + old_helper_count]
    }
    before = _state(sketch)
    undo_before = int(document.UndoCount)
    arguments = _arguments(sketch, root, 10.0)

    diagnostic = sketch.diagnoseDecreaseBSplineDegree(root)
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_index"] == root
    assert diagnostic["output_geometry_index"] == root
    assert diagnostic["old_degree"] == initial_degree
    assert diagnostic["new_degree"] == initial_degree - 1
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    for call_id, invalid in (
        (
            "rolling-bspline-degree-decrease-stale",
            {**arguments, "expected_geometry_count": int(sketch.GeometryCount) + 1},
        ),
        (
            "rolling-bspline-degree-decrease-limit",
            {**arguments, "maximum_deviation_mm": 0.0},
        ),
        (
            "rolling-bspline-degree-decrease-linear",
            _arguments(sketch, linear_nurbs, 10.0),
        ),
        (
            "rolling-bspline-degree-decrease-line",
            _arguments(sketch, line, 10.0),
        ),
    ):
        failure = native_call(invalid, succeeds=False, call_id=call_id)
        assert failure["error_code"] == "NATIVE_SKETCH_INVALID", failure
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    result = native_call(arguments, call_id="rolling-bspline-degree-decrease")
    assert result["geometry_index"] == root
    assert (result["old_degree"], result["new_degree"]) == (3, 2)
    assert 0.0 < float(result["measured_deviation_mm"]) <= 10.0
    assert (
        result["retained_internal_geometry_count"]
        == diagnostic["retained_internal_geometry_count"]
    )
    assert (
        result["deleted_geometry_count"]
        == diagnostic["deleted_internal_geometry_count"]
    )
    assert (
        result["created_geometry_count"]
        == diagnostic["exposed_internal_geometry_count"]
    )
    assert int(sketch.Geometry[root].Degree) == 2
    assert str(sketch.GeometryFacadeList[root].Tag) == root_tag
    assert int(sketch.GeometryFacadeList[root].Id) == root_id
    assert bool(sketch.GeometryFacadeList[root].Construction) is True

    new_alignment = _helper_alignment(sketch, root)
    assert new_alignment
    new_helper_tags = {item[1] for item in new_alignment}
    assert (
        len(old_helper_tags & new_helper_tags)
        == result["retained_internal_geometry_count"]
    )
    assert len(old_helper_tags - new_helper_tags) == result["deleted_geometry_count"]
    assert len(new_helper_tags - old_helper_tags) == result["created_geometry_count"]
    line_after = _geometry_index(sketch, line_tag)
    length_after = _constraint_index(sketch, length_tag)
    assert sketch.Constraints[length_after].Name == "PreservedLength"
    expression_values = tuple(
        (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
    )
    assert expression_values == before["expressions"]
    assert int(sketch.Constraints[length_after].First) == line_after
    assert document.UndoNames[0] == "Decrease Sketch B-Spline Degree"

    after = _state(sketch)
    _undo_redo(document, sketch, process_events, before, after)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "state": after,
        "root_index": root,
        "root_id": root_id,
        "line_index": line_after,
        "constraint_index": length_after,
        "helper_roles": _helper_roles(sketch, root),
    }


def verify_reopened_bspline_degree_decrease(
    sketch: Any, expected: dict[str, Any]
) -> None:
    assert _state(sketch) == expected["state"]
    root = expected["root_index"]
    assert root == 0
    assert int(sketch.Geometry[root].Degree) == 2
    assert int(sketch.GeometryFacadeList[root].Id) == expected["root_id"]
    line = expected["line_index"]
    constraint = expected["constraint_index"]
    assert sketch.Constraints[constraint].Name == "PreservedLength"
    assert int(sketch.Constraints[constraint].First) == line
    assert _helper_roles(sketch, root) == expected["helper_roles"]
