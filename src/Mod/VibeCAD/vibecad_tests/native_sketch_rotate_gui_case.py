# SPDX-License-Identifier: LGPL-2.1-or-later

"""Rotate and polar-array case for the Native Sketch GUI lifecycle gate."""

from __future__ import annotations

import math
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
    center: tuple[float, float],
    total_angle_degrees: float,
    copy_count: int,
    constraint_mode: str = "copy",
    **updates,
) -> dict[str, object]:
    state = _state(sketch)
    result: dict[str, object] = {
        "operation": "rotate",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_indices": geometry_indices,
        "center_mm": {"x": center[0], "y": center[1]},
        "total_angle": {"value": total_angle_degrees, "unit": "deg"},
        "copy_count": copy_count,
        "constraint_mode": constraint_mode,
    }
    result.update(updates)
    return result


def exercise_rotate_case(
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
    circle = sketch.addGeometry(
        Part.Circle(
            App.Vector(4.0, 1.0, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            3.0,
        ),
        False,
    )
    radius_constraint = sketch.addConstraint(Sketcher.Constraint("Radius", circle, 3.0))
    sketch.setExpression(f"Constraints[{radius_constraint}]", "3 mm")
    document.recompute()
    process_events(8)
    initial = _state(sketch)
    undo_before = int(document.UndoCount)

    move_arguments = _arguments(
        sketch,
        [circle],
        center=(1.0, 1.0),
        total_angle_degrees=90.0,
        copy_count=0,
    )
    diagnostic = sketch.diagnoseRotate(
        [circle],
        App.Vector(1.0, 1.0, 0.0),
        math.pi / 2.0,
        0,
        False,
    )
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_indices"] == [circle]
    assert diagnostic["deleted_originals"] is True
    assert diagnostic["center_mm"] == {"x": 1.0, "y": 1.0}
    assert diagnostic["total_angle_radians"] == math.pi / 2.0
    assert len(diagnostic["geometry_tags"]) == 1
    assert len(diagnostic["constraint_tags"]) == 1
    assert diagnostic["mutation_receipt"]["geometry"]["deleted"][0]["index"] == circle
    assert _state(sketch) == initial
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        {**move_arguments, "expected_geometry_count": 2},
        succeeds=False,
        call_id="rolling-rotate-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _state(sketch) == initial
    assert int(document.UndoCount) == undo_before

    moved = native_call(move_arguments, call_id="rolling-rotate-move")
    assert moved["mode"] == "move"
    assert moved["input_geometry_count"] == 1
    assert moved["center_mm"] == {"x": 1.0, "y": 1.0}
    assert moved["total_angle"] == {"value": 90.0, "unit": "deg"}
    assert moved["created_geometry_indices"] == [0]
    assert moved["deleted_geometry_indices"] == [0]
    assert moved["created_constraint_indices"] == [0]
    assert moved["deleted_constraint_indices"] == [0]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Rotate Native Sketch Geometry"
    moved_geometry = serialize_sketch_geometry(sketch, 0)
    assert moved_geometry["center_mm"] == [1.0, 4.0, 0.0]
    moved_state = _state(sketch)

    document.undo()
    process_events(16)
    assert _state(sketch) == initial
    document.redo()
    process_events(16)
    assert _state(sketch) == moved_state
    assert edit_boundary(document, sketch, controller) == boundary

    line = sketch.addGeometry(
        Part.LineSegment(
            App.Vector(20.0, 0.0, 0.0),
            App.Vector(24.0, 0.0, 0.0),
        ),
        False,
    )
    length_constraint = sketch.addConstraint(Sketcher.Constraint("Distance", line, 4.0))
    sketch.setExpression(f"Constraints[{length_constraint}]", "4 mm")
    document.recompute()
    process_events(8)
    array_before = _state(sketch)
    array_undo_before = int(document.UndoCount)

    array_arguments = _arguments(
        sketch,
        [line],
        center=(0.0, 0.0),
        total_angle_degrees=180.0,
        copy_count=2,
        constraint_mode="equalize_dimensions",
    )
    array = native_call(array_arguments, call_id="rolling-rotate-array")
    assert array["mode"] == "polar_array"
    assert array["copy_count"] == 2
    assert array["constraint_mode"] == "equalize_dimensions"
    assert array["created_geometry_indices"] == [2, 3]
    assert array["created_constraint_indices"] == [2, 3]
    assert array["deleted_geometry_count"] == 0
    assert array["deleted_constraint_count"] == 0
    assert int(document.UndoCount) == array_undo_before + 1
    assert document.UndoNames[0] == "Rotate Native Sketch Geometry"
    starts = [
        serialize_sketch_geometry(sketch, index)["start_mm"] for index in range(1, 4)
    ]
    assert starts == [
        [20.0, 0.0, 0.0],
        [0.0, 20.0, 0.0],
        [-20.0, 0.0, 0.0],
    ]
    assert [constraint.Type for constraint in sketch.Constraints] == [
        "Radius",
        "Distance",
        "Equal",
        "Equal",
    ]
    final_state = _state(sketch)

    document.undo()
    process_events(16)
    assert _state(sketch) == array_before
    document.redo()
    process_events(16)
    assert _state(sketch) == final_state
    assert edit_boundary(document, sketch, controller) == boundary
    return final_state


def verify_reopened_rotate(sketch: Any, expected: dict[str, Any]) -> None:
    observed = _state(sketch)
    for key, value in expected.items():
        assert observed[key] == value, (
            "reopened Rotate state drift",
            key,
            value,
            observed[key],
        )
