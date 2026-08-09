# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle coverage for Native Sketch Join Curves."""

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
            (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
        ),
        "degrees_of_freedom": int(sketch.DoF),
    }


def _arguments(
    sketch: Any,
    first: int,
    first_endpoint: str,
    second: int,
    second_endpoint: str,
) -> dict[str, object]:
    state = _state(sketch)
    return {
        "operation": "join_curves",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "first": {"geometry_index": first, "endpoint": first_endpoint},
        "second": {"geometry_index": second, "endpoint": second_endpoint},
    }


def _index_by_tag(values: Any, tag: str) -> int:
    matches = [index for index, value in enumerate(values) if str(value.Tag) == tag]
    assert len(matches) == 1, (tag, matches)
    return matches[0]


def _helper_roles(sketch: Any, root: int) -> tuple[tuple[str, int], ...]:
    roles = []
    for constraint in sketch.Constraints:
        if (
            str(constraint.Type) != "InternalAlignment"
            or int(constraint.Second) != root
        ):
            continue
        helper = int(constraint.First)
        roles.append(
            (
                str(sketch.GeometryFacadeList[helper].InternalType),
                int(constraint.InternalAlignmentIndex),
            )
        )
    return tuple(sorted(roles))


def exercise_join_case(
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
    first = int(
        sketch.addGeometry(Part.LineSegment(App.Vector(0, 0), App.Vector(10, 0)), False)
    )
    second = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(10, 0), App.Vector(20, 5)), False
        )
    )
    sketch.addConstraint(Sketcher.Constraint("Coincident", first, 2, second, 1))
    unrelated = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 12), App.Vector(12, 12)), False
        )
    )
    length = int(sketch.addConstraint(Sketcher.Constraint("Distance", unrelated, 12.0)))
    sketch.renameConstraint(length, "PreservedLength")
    sketch.setExpression(f"Constraints[{length}]", "12 mm")
    document.recompute()
    process_events(8)
    assert not list(sketch.ConflictingConstraints)
    assert not list(sketch.RedundantConstraints)
    document.clearUndos()

    first_tag = str(sketch.GeometryFacadeList[first].Tag)
    second_tag = str(sketch.GeometryFacadeList[second].Tag)
    unrelated_tag = str(sketch.GeometryFacadeList[unrelated].Tag)
    length_tag = str(sketch.Constraints[length].Tag)
    before = _state(sketch)
    undo_before = int(document.UndoCount)
    arguments = _arguments(sketch, first, "end", second, "start")
    diagnostic = sketch.diagnoseJoinCurves(first, 2, second, 1)
    assert diagnostic["accepted"] is True
    assert diagnostic["continuity"] == 0
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    for call_id, invalid, error_code in (
        (
            "rolling-join-stale",
            {**arguments, "expected_constraint_count": int(sketch.ConstraintCount) + 1},
            "NATIVE_SKETCH_INVALID",
        ),
        (
            "rolling-join-same-curve",
            {**arguments, "second": {"geometry_index": first, "endpoint": "start"}},
            "NATIVE_SKETCH_INVALID",
        ),
        (
            "rolling-join-bad-endpoint",
            {**arguments, "first": {"geometry_index": first, "endpoint": "whole"}},
            "NATIVE_ARGUMENTS_INVALID",
        ),
    ):
        failure = native_call(invalid, succeeds=False, call_id=call_id)
        assert failure["error_code"] == error_code, failure
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    result = native_call(arguments, call_id="rolling-join-curves")
    assert result["continuity"] == "C0"
    root = int(result["joined_geometry_index"])
    curve = sketch.Geometry[root]
    assert curve.TypeId == "Part::GeomBSplineCurve"
    assert tuple(curve.StartPoint) == (0.0, 0.0, 0.0)
    assert tuple(curve.EndPoint) == (20.0, 5.0, 0.0)
    roles = _helper_roles(sketch, root)
    assert len(roles) == int(curve.NbPoles) + int(curve.NbKnots)
    assert len(roles) == result["created_helper_count"]
    assert not any(
        str(facade.Tag) in {first_tag, second_tag}
        for facade in sketch.GeometryFacadeList
    )
    unrelated_after = _index_by_tag(sketch.GeometryFacadeList, unrelated_tag)
    length_after = _index_by_tag(sketch.Constraints, length_tag)
    assert int(sketch.Constraints[length_after].First) == unrelated_after
    assert sketch.Constraints[length_after].Name == "PreservedLength"
    expressions_after = tuple(
        (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
    )
    expected_expressions = tuple(
        (
            (
                f"{'.' if path.startswith('.') else ''}Constraints[{length_after}]"
                if path.lstrip(".") == f"Constraints[{length}]"
                else path
            ),
            expression,
        )
        for path, expression in before["expressions"]
    )
    assert expressions_after == expected_expressions, (
        expressions_after,
        expected_expressions,
    )
    assert document.UndoNames[0] == "Join Native Sketch Curves"

    after = _state(sketch)
    document.undo()
    process_events(16)
    assert _state(sketch) == before
    document.redo()
    process_events(16)
    assert _state(sketch) == after
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "state": after,
        "root": root,
        "root_id": int(sketch.GeometryFacadeList[root].Id),
        "roles": roles,
        "unrelated": unrelated_after,
        "length": length_after,
    }


def verify_reopened_join(sketch: Any, expected: dict[str, Any]) -> None:
    assert _state(sketch) == expected["state"]
    root = expected["root"]
    curve = sketch.Geometry[root]
    assert curve.TypeId == "Part::GeomBSplineCurve"
    assert int(sketch.GeometryFacadeList[root].Id) == expected["root_id"]
    assert _helper_roles(sketch, root) == expected["roles"]
    unrelated = expected["unrelated"]
    length = expected["length"]
    assert int(sketch.Constraints[length].First) == unrelated
    assert sketch.Constraints[length].Name == "PreservedLength"
