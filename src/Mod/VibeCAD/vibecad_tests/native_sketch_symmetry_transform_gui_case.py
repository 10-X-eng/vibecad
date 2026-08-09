# SPDX-License-Identifier: LGPL-2.1-or-later

"""Symmetry modes and references for the Native Sketch GUI lifecycle gate."""

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
    reference_index: int,
    reference_position: str,
    source_mode: str,
    **updates,
) -> dict[str, object]:
    state = _state(sketch)
    result: dict[str, object] = {
        "operation": "symmetry",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(state["references"]),
        "expected_external_geometry_count": len(state["external_geometry"]),
        "geometry_indices": geometry_indices,
        "reference": {
            "geometry_index": reference_index,
            "position": reference_position,
        },
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


def _assert_one_symmetry_transaction(document: Any, before_count: int) -> None:
    assert int(document.UndoCount) in {before_count, before_count + 1}
    assert document.UndoNames[0] == "Mirror Native Sketch Geometry"


def exercise_symmetry_transform_case(
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

    reference = sketch.addGeometry(
        Part.LineSegment(App.Vector(0, -10), App.Vector(0, 10)),
        True,
    )
    source = sketch.addGeometry(
        Part.LineSegment(App.Vector(3, 2), App.Vector(8, 2)),
        False,
    )
    sketch.addConstraint(Sketcher.Constraint("Vertical", reference))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", source))
    document.recompute()
    process_events(8)
    keep_before = _state(sketch)
    undo_before = int(document.UndoCount)
    keep_arguments = _arguments(
        sketch,
        [source],
        reference_index=reference,
        reference_position="whole",
        source_mode="keep",
    )
    diagnostic = sketch.diagnoseSymmetry([source], reference, 0, 0)
    assert diagnostic["accepted"] is True
    assert diagnostic["input_geometry_indices"] == [source]
    assert diagnostic["reference_geometry_index"] == reference
    assert diagnostic["reference_position"] == "whole"
    assert diagnostic["source_mode"] == "keep"
    assert diagnostic["deleted_originals"] is False
    assert diagnostic["constrained_symmetry"] is False
    assert _state(sketch) == keep_before
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        {**keep_arguments, "expected_geometry_count": 3},
        succeeds=False,
        call_id="rolling-symmetry-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _state(sketch) == keep_before
    assert int(document.UndoCount) == undo_before

    kept = native_call(keep_arguments, call_id="rolling-symmetry-keep")
    assert kept["reference"] == {
        "geometry_index": reference,
        "position": "whole",
    }
    assert kept["source_mode"] == "keep"
    assert kept["created_geometry_indices"] == [2]
    assert kept["deleted_geometry_indices"] == []
    assert kept["created_constraint_indices"] == [2]
    mirrored = serialize_sketch_geometry(sketch, 2)
    assert mirrored["start_mm"] == [-3.0, 2.0, 0.0]
    assert mirrored["end_mm"] == [-8.0, 2.0, 0.0]
    _assert_one_symmetry_transaction(document, undo_before)
    keep_after = _state(sketch)
    _undo_redo(document, sketch, process_events, keep_before, keep_after)
    assert edit_boundary(document, sketch, controller) == boundary

    constrained_source = sketch.addGeometry(
        Part.LineSegment(App.Vector(4, -6), App.Vector(7, -3)),
        False,
    )
    document.recompute()
    process_events(8)
    constrain_before = _state(sketch)
    constrain_undo_before = int(document.UndoCount)
    constrained = native_call(
        _arguments(
            sketch,
            [constrained_source],
            reference_index=-2,
            reference_position="whole",
            source_mode="constrain",
        ),
        call_id="rolling-symmetry-constrain",
    )
    assert constrained["source_mode"] == "constrain"
    assert constrained["created_geometry_indices"] == [4]
    assert constrained["deleted_geometry_count"] == 0
    assert constrained["created_constraint_count"] == 2
    created_constraints = constrained["created_constraint_indices"]
    assert [sketch.Constraints[index].Type for index in created_constraints] == [
        "Symmetric",
        "Symmetric",
    ]
    constrained_copy = serialize_sketch_geometry(sketch, 4)
    assert constrained_copy["start_mm"] == [-4.0, -6.0, 0.0]
    assert constrained_copy["end_mm"] == [-7.0, -3.0, 0.0]
    _assert_one_symmetry_transaction(document, constrain_undo_before)
    constrain_after = _state(sketch)
    _undo_redo(
        document,
        sketch,
        process_events,
        constrain_before,
        constrain_after,
    )
    assert edit_boundary(document, sketch, controller) == boundary

    circle = sketch.addGeometry(
        Part.Circle(App.Vector(12, 5), App.Vector(0, 0, 1), 3),
        False,
    )
    radius = sketch.addConstraint(Sketcher.Constraint("Radius", circle, 3.0))
    sketch.setExpression(f"Constraints[{radius}]", "3 mm")
    document.recompute()
    process_events(8)
    delete_before = _state(sketch)
    delete_undo_before = int(document.UndoCount)
    deleted = native_call(
        _arguments(
            sketch,
            [circle],
            reference_index=-1,
            reference_position="start",
            source_mode="delete",
        ),
        call_id="rolling-symmetry-delete-point",
    )
    assert deleted["reference"] == {"geometry_index": -1, "position": "start"}
    assert deleted["source_mode"] == "delete"
    assert deleted["created_geometry_indices"] == [5]
    assert deleted["deleted_geometry_indices"] == [5]
    assert deleted["created_constraint_count"] == 1
    assert deleted["deleted_constraint_count"] == 1
    mirrored_circle = serialize_sketch_geometry(sketch, 5)
    assert mirrored_circle["center_mm"] == [-12.0, -5.0, 0.0]
    assert mirrored_circle["radius_mm"] == 3.0
    assert list(sketch.ExpressionEngine) == []
    _assert_one_symmetry_transaction(document, delete_undo_before)
    delete_after = _state(sketch)
    _undo_redo(document, sketch, process_events, delete_before, delete_after)
    assert edit_boundary(document, sketch, controller) == boundary

    external_source = document.addObject("Part::Feature", "SymmetryReferenceSource")
    external_source.Shape = Part.makeLine(
        App.Vector(-20, 15, 0),
        App.Vector(20, 15, 0),
    )
    sketch.addExternal(external_source.Name, "Edge1", False, False)
    external_target = sketch.addGeometry(
        Part.LineSegment(App.Vector(2, 20), App.Vector(6, 22)),
        False,
    )
    document.recompute()
    process_events(8)
    external_before = _state(sketch)
    external_undo_before = int(document.UndoCount)
    external = native_call(
        _arguments(
            sketch,
            [external_target],
            reference_index=-3,
            reference_position="whole",
            source_mode="keep",
        ),
        call_id="rolling-symmetry-external-reference",
    )
    assert external["reference"] == {"geometry_index": -3, "position": "whole"}
    assert external["created_geometry_indices"] == [7]
    external_copy = serialize_sketch_geometry(sketch, 7)
    assert external_copy["start_mm"] == [2.0, 10.0, 0.0]
    assert external_copy["end_mm"] == [6.0, 8.0, 0.0]
    _assert_one_symmetry_transaction(document, external_undo_before)
    final_state = _state(sketch)
    _undo_redo(document, sketch, process_events, external_before, final_state)
    assert edit_boundary(document, sketch, controller) == boundary
    return final_state


def verify_reopened_symmetry_transform(
    sketch: Any,
    expected: dict[str, Any],
) -> None:
    observed = _state(sketch)
    for key, value in expected.items():
        assert observed[key] == value, (
            "reopened Symmetry state drift",
            key,
            value,
            observed[key],
        )
