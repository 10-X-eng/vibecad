# SPDX-License-Identifier: LGPL-2.1-or-later

"""Scale replacement and copy case for the Native Sketch GUI lifecycle gate."""

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
    center: tuple[float, float],
    scale_factor: float,
    keep_originals: bool,
    **updates,
) -> dict[str, object]:
    state = _state(sketch)
    result: dict[str, object] = {
        "operation": "scale",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_indices": geometry_indices,
        "center_mm": {"x": center[0], "y": center[1]},
        "scale_factor": scale_factor,
        "keep_originals": keep_originals,
    }
    result.update(updates)
    return result


def exercise_scale_case(
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
    initial_facade_id = int(sketch.GeometryFacadeList[circle].Id)
    undo_before = int(document.UndoCount)

    replace_arguments = _arguments(
        sketch,
        [circle],
        center=(1.0, 1.0),
        scale_factor=2.0,
        keep_originals=False,
    )
    diagnostic = sketch.diagnoseScale(
        [circle],
        App.Vector(1.0, 1.0, 0.0),
        2.0,
        False,
        False,
    )
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_indices"] == [circle]
    assert diagnostic["center_mm"] == {"x": 1.0, "y": 1.0}
    assert diagnostic["scale_factor"] == 2.0
    assert diagnostic["keep_originals"] is False
    assert diagnostic["allow_origin_constraints"] is False
    assert diagnostic["deleted_originals"] is True
    assert diagnostic["expressions"] == []
    assert len(diagnostic["geometry_tags"]) == 1
    assert len(diagnostic["constraint_tags"]) == 1
    assert diagnostic["mutation_receipt"]["geometry"]["deleted"][0]["index"] == circle
    assert _state(sketch) == initial
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        {**replace_arguments, "expected_geometry_count": 2},
        succeeds=False,
        call_id="rolling-scale-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _state(sketch) == initial
    assert int(document.UndoCount) == undo_before

    replaced = native_call(replace_arguments, call_id="rolling-scale-replace")
    assert replaced["mode"] == "replace"
    assert replaced["input_geometry_count"] == 1
    assert replaced["center_mm"] == {"x": 1.0, "y": 1.0}
    assert replaced["scale_factor"] == 2.0
    assert replaced["keep_originals"] is False
    assert replaced["created_geometry_indices"] == [0]
    assert replaced["deleted_geometry_indices"] == [0]
    assert replaced["created_constraint_indices"] == [0]
    assert replaced["deleted_constraint_indices"] == [0]
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Scale Native Sketch Geometry"
    replaced_geometry = serialize_sketch_geometry(sketch, 0)
    assert replaced_geometry["center_mm"] == [7.0, 1.0, 0.0]
    assert replaced_geometry["radius_mm"] == 6.0
    assert int(sketch.GeometryFacadeList[0].Id) == initial_facade_id
    assert list(sketch.ExpressionEngine) == []
    replaced_state = _state(sketch)

    document.undo()
    process_events(16)
    assert _state(sketch) == initial
    document.redo()
    process_events(16)
    assert _state(sketch) == replaced_state
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
    copy_before = _state(sketch)
    copy_undo_before = int(document.UndoCount)

    copy_arguments = _arguments(
        sketch,
        [line],
        center=(0.0, 0.0),
        scale_factor=0.5,
        keep_originals=True,
    )
    copied = native_call(copy_arguments, call_id="rolling-scale-copy")
    assert copied["mode"] == "copy"
    assert copied["scale_factor"] == 0.5
    assert copied["keep_originals"] is True
    assert copied["created_geometry_indices"] == [2]
    assert copied["created_constraint_indices"] == [2]
    assert copied["deleted_geometry_count"] == 0
    assert copied["deleted_constraint_count"] == 0
    assert int(document.UndoCount) == copy_undo_before + 1
    assert document.UndoNames[0] == "Scale Native Sketch Geometry"
    original_line = serialize_sketch_geometry(sketch, 1)
    scaled_line = serialize_sketch_geometry(sketch, 2)
    assert original_line["start_mm"] == [20.0, 0.0, 0.0]
    assert original_line["end_mm"] == [24.0, 0.0, 0.0]
    assert scaled_line["start_mm"] == [10.0, 0.0, 0.0]
    assert scaled_line["end_mm"] == [12.0, 0.0, 0.0]
    assert [float(constraint.Value) for constraint in sketch.Constraints] == [
        6.0,
        4.0,
        2.0,
    ]
    assert list(sketch.ExpressionEngine) == [("Constraints[1]", "4 mm")]
    final_state = _state(sketch)

    document.undo()
    process_events(16)
    assert _state(sketch) == copy_before
    document.redo()
    process_events(16)
    assert _state(sketch) == final_state
    assert edit_boundary(document, sketch, controller) == boundary
    return final_state


def verify_reopened_scale(sketch: Any, expected: dict[str, Any]) -> None:
    observed = _state(sketch)
    for key, value in expected.items():
        assert observed[key] == value, (
            "reopened Scale state drift",
            key,
            value,
            observed[key],
        )
