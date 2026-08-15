# SPDX-License-Identifier: LGPL-2.1-or-later

"""Carbon Copy case for the rolling Native Sketch GUI lifecycle gate."""

from __future__ import annotations

import hashlib
from typing import Any, Callable

from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchExternalState import iter_external_reference_records
from VibeCADNativeSketchMutationState import geometry_records_without_tags
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)


def _external_source_digests(sketch: Any) -> tuple[tuple[str, str], ...]:
    result = []
    for raw in list(sketch.ExternalGeometry):
        obj = raw[0]
        shape = getattr(obj, "Shape", None)
        export = getattr(shape, "exportBrepToString", None)
        digest = hashlib.sha256(str(export()).encode()).hexdigest()
        result.append((str(obj.Name), digest))
    return tuple(result)


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
        "external_source_digests": _external_source_digests(sketch),
    }


def _arguments(sketch: Any, source: Any, **updates) -> dict[str, object]:
    target_state = _state(sketch)
    source_state = _state(source)
    result: dict[str, object] = {
        "operation": "carbon_copy",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(target_state["references"]),
        "expected_external_geometry_count": len(target_state["external_geometry"]),
        "source_sketch": {"object_name": source.Name},
        "expected_source_geometry_count": int(source.GeometryCount),
        "expected_source_constraint_count": int(source.ConstraintCount),
        "expected_source_external_reference_count": len(source_state["references"]),
        "expected_source_external_geometry_count": len(
            source_state["external_geometry"]
        ),
        "geometry_mode": "construction",
        "reference_permission": "same_body_aligned",
    }
    result.update(updates)
    return result


def exercise_carbon_copy_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    source = document.getObject("CarbonCopySource")
    assert source is not None
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    empty_target = _state(sketch)
    assert empty_target["geometry"] == ()
    assert empty_target["constraints"] == ()
    assert empty_target["references"] == ()
    source_before = _state(source)
    undo_before = int(document.UndoCount)

    diagnostic = sketch.diagnoseCarbonCopy(source.Name, True, False, False)
    assert diagnostic["accepted"] is True
    assert diagnostic["solver_status"] == 0
    assert diagnostic["source_object_name"] == source.Name
    assert diagnostic["requested_construction"] is True
    assert diagnostic["requested_allow_other_body"] is False
    assert diagnostic["requested_allow_unaligned"] is False
    assert diagnostic["x_inverted"] is False
    assert diagnostic["y_inverted"] is False
    assert diagnostic["copied_geometry_count"] == 1
    assert diagnostic["copied_constraint_count"] == 1
    assert diagnostic["copied_external_reference_count"] == 1
    assert diagnostic["geometry_count"] == 1
    assert diagnostic["constraint_count"] == 1
    assert diagnostic["geometry_metadata"][0]["Construction"] is True
    assert diagnostic["external_reference_count"] == 1
    assert diagnostic["external_geometry_count"] == 1
    assert diagnostic["external_references"] == [
        {
            "object_name": "CarbonCopySupport",
            "subelement": "Edge1",
            "type": 0,
        }
    ]
    assert diagnostic["external_geometry_metadata"] == [
        {
            "reference": "CarbonCopySupport.Edge1",
            "defining": False,
            "frozen": False,
            "detached": False,
            "missing": False,
            "synchronized": False,
        }
    ]
    assert diagnostic["expressions"] == [
        {
            "constraint_index": 0,
            "path": "Constraints[0]",
            "expression": "CarbonCopySource.Constraints[0]",
        }
    ]
    assert _state(sketch) == empty_target
    assert _state(source) == source_before
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        _arguments(sketch, source, expected_source_geometry_count=2),
        succeeds=False,
        call_id="rolling-carbon-copy-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _state(sketch) == empty_target
    assert _state(source) == source_before
    assert int(document.UndoCount) == undo_before

    response = native_call(
        _arguments(sketch, source),
        call_id="rolling-carbon-copy-create",
    )
    assert response["operation"] == "carbon_copy"
    assert response["source_sketch"]["object_name"] == source.Name
    assert response["geometry_mode"] == "construction"
    assert response["reference_permission"] == "same_body_aligned"
    assert response["x_inverted"] is False
    assert response["y_inverted"] is False
    assert response["copied_geometry_count"] == 1
    assert response["copied_constraint_count"] == 1
    assert response["created_geometry_indices"] == [0]
    assert response["created_constraint_indices"] == [0]
    assert response["external_reference_count"] == 1
    assert response["external_geometry_count"] == 1
    assert response["geometry_count"] == 1
    assert response["constraint_count"] == 1
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Carbon Copy"
    assert _state(source) == source_before

    expected = _state(sketch)
    assert len(expected["geometry"]) == 1
    assert len(expected["constraints"]) == 1
    assert len(expected["references"]) == 1
    assert len(expected["external_geometry"]) == 1
    assert expected["expressions"] == (
        ("Constraints[0]", "CarbonCopySource.Constraints[0]"),
    )

    duplicate = native_call(
        _arguments(sketch, source),
        succeeds=False,
        call_id="rolling-carbon-copy-duplicate",
    )
    assert duplicate["error_code"] == "NATIVE_SKETCH_INVALID", duplicate
    assert _state(sketch) == expected
    assert _state(source) == source_before
    assert int(document.UndoCount) == undo_before + 1

    document.undo()
    process_events(16)
    assert _state(sketch) == empty_target
    document.redo()
    process_events(16)
    assert _state(sketch) == expected
    assert _state(source) == source_before
    assert edit_boundary(document, sketch, controller) == boundary
    return {"target": expected, "source": source_before}


def verify_reopened_carbon_copy(sketch: Any, expected: dict[str, Any]) -> None:
    source = sketch.Document.getObject("CarbonCopySource")
    assert source is not None
    observed_target = _state(sketch)
    observed_source = _state(source)
    for key, value in expected["target"].items():
        assert observed_target[key] == value, (
            "reopened Carbon Copy target drift",
            key,
            value,
            observed_target[key],
        )
    for key, value in expected["source"].items():
        assert observed_source[key] == value, (
            "reopened Carbon Copy source drift",
            key,
            value,
            observed_source[key],
        )
