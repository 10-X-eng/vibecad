# SPDX-License-Identifier: LGPL-2.1-or-later

"""Translate and two-vector array case for the Native Sketch GUI lifecycle gate."""

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
    first: tuple[float, float],
    copy_count: int,
    second: tuple[float, float] = (0.0, 0.0),
    row_count: int = 1,
    constraint_mode: str = "copy",
    **updates,
) -> dict[str, object]:
    state = _state(sketch)
    result: dict[str, object] = {
        "operation": "translate",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_indices": geometry_indices,
        "first_translation_mm": {"x": first[0], "y": first[1]},
        "copy_count": copy_count,
        "second_translation_mm": {"x": second[0], "y": second[1]},
        "row_count": row_count,
        "constraint_mode": constraint_mode,
    }
    result.update(updates)
    return result


def exercise_translate_case(
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
    line = sketch.addGeometry(
        Part.LineSegment(
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(5.0, 0.0, 0.0),
        ),
        False,
    )
    line_constraint = sketch.addConstraint(
        Sketcher.Constraint("DistanceX", line, 1, line, 2, 5.0)
    )
    sketch.setExpression(f"Constraints[{line_constraint}]", "5 mm")
    document.recompute()
    process_events(8)
    initial = _state(sketch)
    undo_before = int(document.UndoCount)

    move_arguments = _arguments(
        sketch,
        [line],
        first=(4.0, 3.0),
        copy_count=0,
    )
    diagnostic = sketch.diagnoseTranslate(
        [line],
        App.Vector(4.0, 3.0, 0.0),
        0,
        App.Vector(0.0, 0.0, 0.0),
        1,
        False,
    )
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_indices"] == [line]
    assert diagnostic["deleted_originals"] is True
    assert len(diagnostic["geometry_tags"]) == 1
    assert len(diagnostic["constraint_tags"]) == 1
    assert diagnostic["mutation_receipt"]["geometry"]["deleted"][0]["index"] == line
    assert _state(sketch) == initial
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        {**move_arguments, "expected_geometry_count": 2},
        succeeds=False,
        call_id="rolling-translate-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _state(sketch) == initial
    assert int(document.UndoCount) == undo_before

    moved = native_call(move_arguments, call_id="rolling-translate-move")
    assert moved["mode"] == "move"
    assert moved["input_geometry_count"] == 1
    assert moved["created_geometry_indices"] == [0]
    assert moved["deleted_geometry_indices"] == [0]
    assert moved["created_constraint_indices"] == [0]
    assert moved["deleted_constraint_indices"] == [0]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Translate Native Sketch Geometry"
    moved_geometry = serialize_sketch_geometry(sketch, 0)
    assert moved_geometry["start_mm"] == [4.0, 3.0, 0.0]
    assert moved_geometry["end_mm"] == [9.0, 3.0, 0.0]
    moved_state = _state(sketch)

    document.undo()
    process_events(16)
    assert _state(sketch) == initial
    document.redo()
    process_events(16)
    assert _state(sketch) == moved_state
    assert edit_boundary(document, sketch, controller) == boundary

    circle = sketch.addGeometry(
        Part.Circle(
            App.Vector(20.0, 0.0, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            3.0,
        ),
        False,
    )
    radius_constraint = sketch.addConstraint(Sketcher.Constraint("Radius", circle, 3.0))
    sketch.setExpression(f"Constraints[{radius_constraint}]", "3 mm")
    document.recompute()
    process_events(8)
    array_before = _state(sketch)
    array_undo_before = int(document.UndoCount)

    array_arguments = _arguments(
        sketch,
        [circle],
        first=(5.0, 0.0),
        copy_count=1,
        second=(1.0, 4.0),
        row_count=2,
        constraint_mode="equalize_dimensions",
    )
    array = native_call(array_arguments, call_id="rolling-translate-array")
    assert array["mode"] == "array"
    assert array["copy_count"] == 1
    assert array["row_count"] == 2
    assert array["constraint_mode"] == "equalize_dimensions"
    assert array["created_geometry_indices"] == [2, 3, 4]
    assert array["created_constraint_indices"] == [2, 3, 4]
    assert array["deleted_geometry_count"] == 0
    assert array["deleted_constraint_count"] == 0
    assert int(document.UndoCount) == array_undo_before + 1
    assert document.UndoNames[0] == "Translate Native Sketch Geometry"
    centers = [
        serialize_sketch_geometry(sketch, index)["center_mm"] for index in range(1, 5)
    ]
    assert centers == [
        [20.0, 0.0, 0.0],
        [25.0, 0.0, 0.0],
        [21.0, 4.0, 0.0],
        [26.0, 4.0, 0.0],
    ]
    assert [constraint.Type for constraint in sketch.Constraints] == [
        "DistanceX",
        "Radius",
        "Equal",
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


def verify_reopened_translate(sketch: Any, expected: dict[str, Any]) -> None:
    observed = _state(sketch)
    for key, value in expected.items():
        assert observed[key] == value, (
            "reopened Translate state drift",
            key,
            value,
            observed[key],
        )
