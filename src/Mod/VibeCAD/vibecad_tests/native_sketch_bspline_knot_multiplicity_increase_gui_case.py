# SPDX-License-Identifier: LGPL-2.1-or-later

"""Knot multiplicity increase coverage for Native Sketch GUI lifecycle gates."""

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


def _arguments(sketch: Any, geometry_index: int, knot_index: int) -> dict[str, object]:
    state = _state(sketch)
    return {
        "operation": "increase_bspline_knot_multiplicity",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_index": geometry_index,
        "knot_index": knot_index,
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
        for geometry, _tag, internal_type, alignment_index, constraint, _ctag in (
            _helper_alignment(sketch, root)
        )
    )


def _undo_redo(document, sketch, process_events, before, after) -> None:
    document.undo()
    process_events(16)
    assert _state(sketch) == before
    document.redo()
    process_events(16)
    assert _state(sketch) == after


def exercise_bspline_knot_multiplicity_increase_case(
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
    original = sketch.Geometry[root].copy()
    initial_multiplicities = tuple(int(value) for value in original.getMultiplicities())
    assert len(initial_multiplicities) > 2
    assert initial_multiplicities[1] < int(original.Degree)
    exposed = sketch.exposeInternalGeometry(root)
    old_helper_count = int(exposed["created_count"])
    assert old_helper_count > 0

    line = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 10), App.Vector(10, 10)), False
        )
    )
    length = int(sketch.addConstraint(Sketcher.Constraint("Distance", line, 10.0)))
    sketch.renameConstraint(length, "PreservedLength")
    sketch.setExpression(f"Constraints[{length}]", "10 mm")
    document.recompute()
    process_events(8)
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
    arguments = _arguments(sketch, root, 1)

    diagnostic = sketch.diagnoseIncreaseBSplineKnotMultiplicity(root, 1)
    assert diagnostic["accepted"] is True
    assert diagnostic["geometry_index"] == root
    assert diagnostic["knot_index"] == 1
    assert diagnostic["old_multiplicity"] == initial_multiplicities[1]
    assert diagnostic["new_multiplicity"] == initial_multiplicities[1] + 1
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    for call_id, invalid in (
        (
            "rolling-knot-multiplicity-stale",
            {**arguments, "expected_geometry_count": int(sketch.GeometryCount) + 1},
        ),
        ("rolling-knot-multiplicity-endpoint", _arguments(sketch, root, 0)),
        ("rolling-knot-multiplicity-line", _arguments(sketch, line, 0)),
        ("rolling-knot-multiplicity-range", _arguments(sketch, root, 999)),
    ):
        failure = native_call(invalid, succeeds=False, call_id=call_id)
        assert failure["error_code"] == "NATIVE_SKETCH_INVALID", failure
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    result = native_call(arguments, call_id="rolling-knot-multiplicity-increase")
    assert result["geometry_index"] == root
    assert result["knot_index"] == 1
    assert (result["old_multiplicity"], result["new_multiplicity"]) == (
        initial_multiplicities[1],
        initial_multiplicities[1] + 1,
    )
    assert 0.0 <= float(result["measured_deviation_mm"]) <= 1.0e-3
    assert tuple(int(value) for value in sketch.Geometry[root].getMultiplicities()) == (
        initial_multiplicities[:1]
        + (initial_multiplicities[1] + 1,)
        + initial_multiplicities[2:]
    )
    assert str(sketch.GeometryFacadeList[root].Tag) == root_tag
    assert int(sketch.GeometryFacadeList[root].Id) == root_id
    assert bool(sketch.GeometryFacadeList[root].Construction) is True

    new_alignment = _helper_alignment(sketch, root)
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
    assert int(sketch.Constraints[length_after].First) == line_after
    assert (
        tuple(
            (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
        )
        == before["expressions"]
    )
    assert document.UndoNames[0] == "Increase Sketch B-Spline Knot Multiplicity"

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
        "multiplicities": tuple(sketch.Geometry[root].getMultiplicities()),
    }


def verify_reopened_bspline_knot_multiplicity_increase(
    sketch: Any, expected: dict[str, Any]
) -> None:
    assert _state(sketch) == expected["state"]
    root = expected["root_index"]
    assert root == 0
    assert (
        tuple(sketch.Geometry[root].getMultiplicities()) == expected["multiplicities"]
    )
    assert int(sketch.GeometryFacadeList[root].Id) == expected["root_id"]
    constraint = expected["constraint_index"]
    assert sketch.Constraints[constraint].Name == "PreservedLength"
    assert int(sketch.Constraints[constraint].First) == expected["line_index"]
    assert _helper_roles(sketch, root) == expected["helper_roles"]
