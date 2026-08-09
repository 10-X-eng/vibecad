# SPDX-License-Identifier: LGPL-2.1-or-later

"""Split case for the rolling Native Sketch GUI lifecycle gate."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import Part

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)


def _arguments(sketch: Any, *, geometry_count: int = 1) -> dict[str, object]:
    return {
        "operation": "split",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "target": {
            "geometry_index": 0,
            "reference_point_mm": {"x": 8.0, "y": 0.0},
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
    document.openTransaction("Prepare Native Sketch Split fixture")
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


def exercise_split_case(
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
    diagnosis = sketch.diagnoseSplit(0, App.Vector(8, 0))
    assert diagnosis["accepted"] is True
    assert diagnosis["geometry_count"] == 2
    assert diagnosis["constraint_count"] == 1
    assert [
        item["index"] for item in diagnosis["mutation_receipt"]["geometry"]["created"]
    ] == [0, 1]
    assert _records(sketch) == diagnosis_before
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        _arguments(sketch, geometry_count=2),
        succeeds=False,
        call_id="rolling-split-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before

    response = native_call(
        _arguments(sketch),
        call_id="rolling-split-create",
    )
    assert response["operation"] == "split"
    assert response["outcome"] == "split"
    assert response["input_geometry_index"] == 0
    assert response["reference_point_mm"] == {"x": 8.0, "y": 0.0}
    assert response["deleted_geometry_indices"] == [0]
    assert response["replacement_geometry_indices"] == [0, 1]
    assert [item["kind"] for item in response["replacement_geometry"]] == [
        "line",
        "line",
    ]
    assert all(item["construction"] for item in response["replacement_geometry"])
    assert response["geometry_count"] == 2
    assert response["constraint_count"] == 1
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Split Native Sketch Geometry"

    expected = _records(sketch)
    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (1, 0)
    document.redo()
    process_events(16)
    assert _records(sketch) == expected
    assert edit_boundary(document, sketch, controller) == boundary
    return expected


def verify_reopened_split(sketch: Any, expected: dict[str, Any]) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (2, 1)
    observed = _records(sketch)
    assert observed["constraints"] == expected["constraints"]
    observed_tags: set[str] = set()
    for saved, reopened in zip(expected["geometry"], observed["geometry"], strict=True):
        saved = dict(saved)
        reopened = dict(reopened)
        assert saved.pop("tag", "")
        tag = str(reopened.pop("tag", "") or "")
        assert tag and tag not in observed_tags
        observed_tags.add(tag)
        assert reopened == saved
    first = serialize_sketch_geometry(sketch, 0)
    second = serialize_sketch_geometry(sketch, 1)
    assert first["kind"] == second["kind"] == "line"
    assert first["start_mm"] == [0.0, 0.0, 0.0]
    assert first["end_mm"] == [8.0, 0.0, 0.0]
    assert second["start_mm"] == [8.0, 0.0, 0.0]
    assert second["end_mm"] == [20.0, 0.0, 0.0]
    assert first["construction"] is second["construction"] is True
