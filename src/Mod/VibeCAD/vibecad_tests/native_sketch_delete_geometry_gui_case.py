# SPDX-License-Identifier: LGPL-2.1-or-later

"""Delete Geometry case for the rolling Native Sketch GUI lifecycle gate."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import Part
import Sketcher

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)


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


def _arguments(sketch: Any, *, geometry_count: int = 3) -> dict[str, object]:
    return {
        "operation": "delete_geometry",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "geometry_indices": [1],
    }


def _prepare_fixture(document: Any, sketch: Any) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    document.openTransaction("Prepare Native Sketch Delete Geometry fixture")
    try:
        for y_value in (0.0, 4.0, 8.0):
            sketch.addGeometry(
                Part.LineSegment(
                    App.Vector(0.0, y_value),
                    App.Vector(12.0, y_value),
                ),
                False,
            )
        assert sketch.addConstraint(Sketcher.Constraint("Horizontal", 1)) == 0
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def exercise_delete_geometry_case(
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
    before = _records(sketch)
    undo_before = int(document.UndoCount)

    stale = native_call(
        _arguments(sketch, geometry_count=4),
        succeeds=False,
        call_id="rolling-delete-geometry-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    assert _records(sketch) == before
    assert int(document.UndoCount) == undo_before

    response = native_call(
        _arguments(sketch),
        call_id="rolling-delete-geometry",
    )
    assert response["operation"] == "delete_geometry"
    assert response["requested_geometry_indices"] == [1]
    assert response["deleted_geometry_count"] == 1
    assert response["deleted_constraint_count"] == 1
    assert (response["geometry_count"], response["constraint_count"]) == (2, 0)
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Delete Native Sketch Geometry"

    expected = _records(sketch)
    assert [item["start_mm"][1] for item in expected["geometry"]] == [0.0, 8.0]
    document.undo()
    process_events(16)
    assert _records(sketch) == before
    document.redo()
    process_events(16)
    assert _records(sketch) == expected
    assert edit_boundary(document, sketch, controller) == boundary
    return expected


def verify_reopened_delete_geometry(sketch: Any, expected: dict[str, Any]) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (2, 0)
    observed = _records(sketch)
    assert observed["constraints"] == expected["constraints"]
    for saved, reopened in zip(
        expected["geometry"], observed["geometry"], strict=True
    ):
        saved = dict(saved)
        reopened = dict(reopened)
        assert saved.pop("tag", "")
        assert reopened.pop("tag", "")
        assert reopened == saved
    assert [item["start_mm"][1] for item in observed["geometry"]] == [0.0, 8.0]
