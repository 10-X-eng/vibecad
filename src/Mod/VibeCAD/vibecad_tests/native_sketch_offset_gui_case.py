# SPDX-License-Identifier: LGPL-2.1-or-later

"""Offset modes and joins for the Native Sketch GUI lifecycle gate."""

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
    serialize_sketch_geometry,
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
    sketch: Any,
    geometry_indices: list[int],
    *,
    distance: float,
    join_type: str,
    source_mode: str,
    **updates,
) -> dict[str, object]:
    state = _state(sketch)
    result: dict[str, object] = {
        "operation": "offset",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_indices": geometry_indices,
        "offset_distance": {"value": distance, "unit": "mm"},
        "join_type": join_type,
        "source_mode": source_mode,
    }
    result.update(updates)
    return result


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


def _assert_one_offset_transaction(document: Any, before_count: int) -> None:
    assert int(document.UndoCount) in {before_count, before_count + 1}
    assert document.UndoNames[0] == "Offset Native Sketch Geometry"


def exercise_offset_case(
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

    source_circle = sketch.addGeometry(
        Part.Circle(App.Vector(4, 1), App.Vector(0, 0, 1), 5.0),
        False,
    )
    radius = sketch.addConstraint(Sketcher.Constraint("Radius", source_circle, 5.0))
    sketch.setExpression(f"Constraints[{radius}]", "5 mm")
    document.recompute()
    process_events(8)
    delete_before = _state(sketch)
    undo_before = int(document.UndoCount)
    delete_arguments = _arguments(
        sketch,
        [source_circle],
        distance=-1.0,
        join_type="arc",
        source_mode="delete",
    )
    diagnostic = sketch.diagnoseOffset([source_circle], -1.0, 0, 1)
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_indices"] == [source_circle]
    assert diagnostic["offset_length_mm"] == -1.0
    assert diagnostic["join_type"] == "arc"
    assert diagnostic["source_mode"] == "delete"
    assert diagnostic["deleted_originals"] is True
    assert diagnostic["constrained_offset"] is False
    assert diagnostic["geometry_count"] == 1
    assert diagnostic["constraint_count"] == 0
    assert diagnostic["expressions"] == []
    assert _state(sketch) == delete_before
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        {**delete_arguments, "expected_geometry_count": 2},
        succeeds=False,
        call_id="rolling-offset-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _state(sketch) == delete_before
    assert int(document.UndoCount) == undo_before

    deleted = native_call(delete_arguments, call_id="rolling-offset-delete")
    assert deleted["offset_distance"] == {"value": -1.0, "unit": "mm"}
    assert deleted["join_type"] == "arc"
    assert deleted["source_mode"] == "delete"
    assert deleted["created_geometry_indices"] == [0]
    assert deleted["deleted_geometry_indices"] == [0]
    assert deleted["created_constraint_indices"] == []
    assert deleted["deleted_constraint_indices"] == [0]
    _assert_one_offset_transaction(document, undo_before)
    assert serialize_sketch_geometry(sketch, 0)["radius_mm"] == 4.0
    assert list(sketch.ExpressionEngine) == []
    delete_after = _state(sketch)
    _undo_redo(document, sketch, process_events, delete_before, delete_after)
    assert edit_boundary(document, sketch, controller) == boundary

    points = (
        App.Vector(20, 0),
        App.Vector(30, 0),
        App.Vector(30, 10),
        App.Vector(20, 10),
    )
    square = [
        sketch.addGeometry(
            Part.LineSegment(points[index], points[(index + 1) % 4]),
            False,
        )
        for index in range(4)
    ]
    document.recompute()
    process_events(8)
    keep_before = _state(sketch)
    keep_undo_before = int(document.UndoCount)
    arc_diagnostic = sketch.diagnoseOffset(square, 2.0, 0, 0)
    intersection_diagnostic = sketch.diagnoseOffset(square, 2.0, 2, 0)
    assert arc_diagnostic["join_type"] == "arc"
    assert intersection_diagnostic["join_type"] == "intersection"
    assert arc_diagnostic["geometry_count"] > intersection_diagnostic["geometry_count"]
    assert intersection_diagnostic["geometry_count"] == 9
    assert _state(sketch) == keep_before
    kept = native_call(
        _arguments(
            sketch,
            square,
            distance=2.0,
            join_type="intersection",
            source_mode="keep",
        ),
        call_id="rolling-offset-intersection-keep",
    )
    assert kept["join_type"] == "intersection"
    assert kept["source_mode"] == "keep"
    assert kept["created_geometry_indices"] == [5, 6, 7, 8]
    assert kept["deleted_geometry_count"] == 0
    assert kept["created_constraint_indices"] == [0, 1, 2, 3]
    assert [constraint.Type for constraint in sketch.Constraints] == ["Coincident"] * 4
    assert all(
        serialize_sketch_geometry(sketch, index)["kind"] == "line"
        for index in range(5, 9)
    )
    _assert_one_offset_transaction(document, keep_undo_before)
    keep_after = _state(sketch)
    _undo_redo(document, sketch, process_events, keep_before, keep_after)
    assert edit_boundary(document, sketch, controller) == boundary

    constrained_source = sketch.addGeometry(
        Part.Circle(App.Vector(50, 5), App.Vector(0, 0, 1), 5.0),
        False,
    )
    assert constrained_source == 9
    document.recompute()
    process_events(8)
    constrain_before = _state(sketch)
    constrain_undo_before = int(document.UndoCount)
    constrained = native_call(
        _arguments(
            sketch,
            [constrained_source],
            distance=1.5,
            join_type="arc",
            source_mode="constrain",
        ),
        call_id="rolling-offset-constrain",
    )
    assert constrained["source_mode"] == "constrain"
    assert constrained["created_geometry_indices"] == [10]
    assert constrained["created_constraint_indices"] == [4, 5]
    assert constrained["deleted_geometry_count"] == 0
    assert [constraint.Type for constraint in sketch.Constraints] == [
        "Coincident",
        "Coincident",
        "Coincident",
        "Coincident",
        "Coincident",
        "Distance",
    ]
    assert float(sketch.Constraints[5].Value) == 1.5
    assert serialize_sketch_geometry(sketch, 10)["radius_mm"] == 6.5
    _assert_one_offset_transaction(document, constrain_undo_before)
    final_state = _state(sketch)
    _undo_redo(document, sketch, process_events, constrain_before, final_state)
    assert edit_boundary(document, sketch, controller) == boundary
    return final_state


def verify_reopened_offset(sketch: Any, expected: dict[str, Any]) -> None:
    observed = _state(sketch)
    for key, value in expected.items():
        assert observed[key] == value, (
            "reopened Offset state drift",
            key,
            value,
            observed[key],
        )
