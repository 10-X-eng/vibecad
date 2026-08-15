# SPDX-License-Identifier: LGPL-2.1-or-later

"""Extend case for the rolling Native Sketch GUI lifecycle gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCAD as App
import Part

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)


def _arguments(sketch: Any, *, geometry_count: int = 1) -> dict[str, object]:
    return {
        "operation": "extend",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "target": {
            "geometry_index": 0,
            "endpoint": "start",
            "target_point_mm": {"x": -5.0, "y": 3.0},
        },
    }


def _records(sketch: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    return {
        "geometry": tuple(
            serialize_sketch_geometry(sketch, index)
            for index in range(int(sketch.GeometryCount))
        ),
        "constraints": tuple(
            serialize_sketch_constraint(sketch, index)
            for index in range(int(sketch.ConstraintCount))
        ),
    }


def _prepare_fixture(document: Any, sketch: Any) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    document.openTransaction("Prepare Native Sketch Extend fixture")
    try:
        assert (
            int(
                sketch.addGeometry(
                    Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)),
                    True,
                )
            )
            == 0
        )
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def exercise_extend_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    _prepare_fixture(document, sketch)
    process_events(16)
    assert edit_boundary(document, sketch, controller) == boundary
    undo_before = int(document.UndoCount)

    diagnosis_before = _records(sketch)
    diagnosis = sketch.diagnoseExtend(0, App.Vector(-5, 3), 1)
    assert diagnosis["accepted"] is True
    assert diagnosis["input_endpoint"] == "start"
    assert math.isclose(
        float(diagnosis["extension_increment"]),
        5.0,
        rel_tol=0.0,
        abs_tol=1.0e-10,
    )
    assert diagnosis["geometry_count"] == 1
    assert diagnosis["constraint_count"] == 0
    geometry_receipt = diagnosis["mutation_receipt"]["geometry"]
    assert geometry_receipt["old_to_new"] == {"0": 0}
    assert geometry_receipt["deleted"] == []
    assert geometry_receipt["created"] == []
    assert _records(sketch) == diagnosis_before
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        _arguments(sketch, geometry_count=2),
        succeeds=False,
        call_id="rolling-extend-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before

    response = native_call(
        _arguments(sketch),
        call_id="rolling-extend-create",
    )
    assert response["operation"] == "extend"
    assert response["outcome"] == "extended"
    assert response["geometry_index"] == 0
    assert response["endpoint"] == "start"
    assert response["target_point_mm"] == {"x": -5.0, "y": 3.0}
    assert response["new_endpoint_mm"] == {"x": -5.0, "y": 0.0}
    assert response["changed_geometry_indices"] == [0]
    assert response["geometry_count"] == 1
    assert response["constraint_count"] == 0
    assert serialize_sketch_geometry(sketch, 0)["construction"] is True
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Extend Native Sketch Geometry"

    expected = _records(sketch)
    document.undo()
    process_events(16)
    undone = _records(sketch)
    assert undone == diagnosis_before, {
        "before": diagnosis_before,
        "after_undo": undone,
    }
    document.redo()
    process_events(16)
    redone = _records(sketch)
    assert redone == expected, {"expected": expected, "after_redo": redone}
    assert edit_boundary(document, sketch, controller) == boundary
    return expected


def verify_reopened_extend(sketch: Any, expected: dict[str, Any]) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (1, 0)
    observed = _records(sketch)
    assert observed["constraints"] == expected["constraints"]
    saved = dict(expected["geometry"][0])
    reopened = dict(observed["geometry"][0])
    assert saved.pop("tag", "")
    assert reopened.pop("tag", "")
    assert reopened == saved

    line = serialize_sketch_geometry(sketch, 0)
    assert line["kind"] == "line"
    assert line["start_mm"] == [-5.0, 0.0, 0.0]
    assert line["end_mm"] == [20.0, 0.0, 0.0]
    assert line["construction"] is True
