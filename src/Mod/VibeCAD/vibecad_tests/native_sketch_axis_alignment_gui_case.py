# SPDX-License-Identifier: LGPL-2.1-or-later

"""Remove Axes Alignment coverage for the Native Sketch GUI lifecycle gates."""

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
        "operation": "remove_axis_alignment",
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


def _constraint_index_for_tag(sketch: Any, tag: str) -> int:
    matches = [
        index
        for index, constraint in enumerate(sketch.Constraints)
        if str(constraint.Tag) == tag
    ]
    assert len(matches) == 1, (tag, matches)
    return matches[0]


def exercise_axis_alignment_case(
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

    horizontal_anchor = _line(sketch, (0, 20), (8, 20))
    horizontal_peer = _line(sketch, (12, 25), (20, 25))
    vertical = _line(sketch, (30, 0), (30, 8))
    symmetry_first = _line(sketch, (40, 4), (43, 6))
    symmetry_second = _line(sketch, (40, -4), (43, -6))
    point_on_axis = _line(sketch, (0, 30), (5, 34))
    distance_first = _line(sketch, (50, 12), (53, 15))
    distance_second = _line(sketch, (57, 12), (60, 16))
    point_horizontal_first = _line(sketch, (70, 5), (73, 7))
    point_horizontal_second = _line(sketch, (75, 5), (78, 9))
    relation_point = _line(sketch, (90, 0), (92, 2))
    relation_curve = _line(sketch, (88, 0), (98, 0))
    unselected_horizontal = _line(sketch, (100, 10), (106, 10))
    noop_circle = int(
        sketch.addGeometry(
            Part.Circle(App.Vector(120, 10), App.Vector(0, 0, 1), 3),
            False,
        )
    )

    sketch.addConstraint(Sketcher.Constraint("Horizontal", horizontal_anchor))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", horizontal_peer))
    sketch.addConstraint(Sketcher.Constraint("Vertical", vertical))
    sketch.addConstraint(
        Sketcher.Constraint(
            "Symmetric",
            symmetry_first,
            1,
            symmetry_second,
            1,
            -1,
        )
    )
    sketch.addConstraint(Sketcher.Constraint("PointOnObject", point_on_axis, 1, -2))
    distance = int(
        sketch.addConstraint(
            Sketcher.Constraint(
                "DistanceX",
                distance_first,
                1,
                distance_second,
                1,
                7.0,
            )
        )
    )
    sketch.renameConstraint(distance, "AxisDistance")
    sketch.setExpression(f"Constraints[{distance}]", "7 mm")
    distance_tag = str(sketch.Constraints[distance].Tag)
    distance_expression = tuple(
        (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
    )
    assert len(distance_expression) == 1
    sketch.addConstraint(
        Sketcher.Constraint(
            "Horizontal",
            point_horizontal_first,
            1,
            point_horizontal_second,
            1,
        )
    )
    sketch.addConstraint(
        Sketcher.Constraint("PointOnObject", relation_point, 1, relation_curve)
    )
    unselected_constraint = int(
        sketch.addConstraint(Sketcher.Constraint("Horizontal", unselected_horizontal))
    )
    unselected_tag = str(sketch.Constraints[unselected_constraint].Tag)
    document.recompute()
    process_events(8)
    assert not list(sketch.ConflictingConstraints)
    assert not list(sketch.RedundantConstraints)

    selected = [
        horizontal_anchor,
        horizontal_peer,
        vertical,
        symmetry_first,
        symmetry_second,
        point_on_axis,
        distance_first,
        distance_second,
        point_horizontal_first,
        point_horizontal_second,
        relation_point,
        relation_curve,
    ]
    before = _state(sketch)
    undo_before = int(document.UndoCount)
    arguments = _arguments(sketch, selected)
    diagnostic = sketch.diagnoseRemoveAxesAlignment(selected)
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_indices"] == selected
    assert diagnostic["removed_horizontal_constraints"] == 2
    assert diagnostic["removed_vertical_constraints"] == 1
    assert diagnostic["created_parallel_constraints"] == 1
    assert diagnostic["removed_axis_symmetry_constraints"] == 1
    assert diagnostic["removed_point_on_axis_constraints"] == 1
    assert diagnostic["converted_distance_constraints"] == 1
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        {**arguments, "expected_constraint_count": int(sketch.ConstraintCount) + 1},
        succeeds=False,
        call_id="rolling-axis-alignment-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    noop = native_call(
        _arguments(sketch, [noop_circle]),
        succeeds=False,
        call_id="rolling-axis-alignment-noop",
    )
    assert noop["error_code"] == "NATIVE_SKETCH_INVALID", noop
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before

    result = native_call(arguments, call_id="rolling-axis-alignment")
    for field, expected in (
        ("input_geometry_count", len(selected)),
        ("removed_horizontal_constraints", 2),
        ("removed_vertical_constraints", 1),
        ("created_parallel_constraints", 1),
        ("removed_axis_symmetry_constraints", 1),
        ("removed_point_on_axis_constraints", 1),
        ("converted_distance_constraints", 1),
        ("created_constraint_count", 1),
        ("removed_constraint_count", 5),
    ):
        assert result[field] == expected, (field, result)
    assert int(sketch.GeometryCount) == 14
    assert int(sketch.ConstraintCount) == 5
    assert [constraint.Type for constraint in sketch.Constraints].count("Parallel") == 1
    converted = _constraint_index_for_tag(sketch, distance_tag)
    assert sketch.Constraints[converted].Type == "Distance"
    assert sketch.Constraints[converted].Name == "AxisDistance"
    assert (
        tuple(
            (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
        )
        == distance_expression
    )
    unselected = _constraint_index_for_tag(sketch, unselected_tag)
    assert sketch.Constraints[unselected].Type == "Horizontal"
    assert [constraint.Type for constraint in sketch.Constraints].count(
        "PointOnObject"
    ) == 1
    assert [constraint.Type for constraint in sketch.Constraints].count(
        "Horizontal"
    ) == 2
    assert int(document.UndoCount) in {undo_before, undo_before + 1}
    assert document.UndoNames[0] == "Remove Sketch Axes Alignment"

    after = _state(sketch)
    _undo_redo(document, sketch, process_events, before, after)
    assert edit_boundary(document, sketch, controller) == boundary
    return after


def verify_reopened_axis_alignment(
    sketch: Any,
    expected: dict[str, Any],
) -> None:
    observed = _state(sketch)
    for key, value in expected.items():
        assert observed[key] == value, (
            "reopened Remove Axes Alignment state drift",
            key,
            value,
            observed[key],
        )
