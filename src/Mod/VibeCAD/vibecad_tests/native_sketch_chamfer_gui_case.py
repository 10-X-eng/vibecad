# SPDX-License-Identifier: LGPL-2.1-or-later

"""Chamfer case for the rolling Native Sketch GUI lifecycle gate."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import Part
import Sketcher

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)


def _arguments(sketch: Any, *, geometry_count: int = 2) -> dict[str, object]:
    return {
        "operation": "create_chamfer",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "target": {
            "form": "corner",
            "geometry_index": 0,
            "position": "end",
        },
        "preserve_corner": True,
    }


def _records(sketch: Any) -> dict[str, Any]:
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


def _prepare_fixtures(document: Any, sketch: Any) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    document.openTransaction("Prepare Native Sketch Chamfer fixtures")
    try:
        assert (
            int(
                sketch.addGeometry(
                    Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)),
                    False,
                )
            )
            == 0
        )
        assert (
            int(
                sketch.addGeometry(
                    Part.LineSegment(App.Vector(20, 0), App.Vector(20, 15)),
                    False,
                )
            )
            == 1
        )
        assert (
            int(sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1)))
            == 0
        )
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def exercise_chamfer_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    _prepare_fixtures(document, sketch)
    process_events(16)
    assert edit_boundary(document, sketch, controller) == boundary
    undo_before = int(document.UndoCount)

    stale = native_call(
        _arguments(sketch, geometry_count=3),
        succeeds=False,
        call_id="rolling-chamfer-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before

    response = native_call(
        _arguments(sketch),
        call_id="rolling-chamfer-create",
    )
    assert response["operation"] == "create_chamfer"
    assert response["form"] == "corner"
    assert response["input_geometry_indices"] == [0, 1]
    assert response["trimmed"] is True
    assert response["preserve_corner"] is True
    assert response["geometry_count"] == 5
    assert response["constraint_count"] == 6
    assert response["chamfer"]["kind"] == "line"
    assert response["support_arc_geometry_index"] == 2
    assert response["preserved_corner"]["kind"] == "point"
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Create Native Sketch Chamfer"

    expected = _records(sketch)
    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (2, 1)
    document.redo()
    process_events(16)
    assert _records(sketch) == expected
    assert edit_boundary(document, sketch, controller) == boundary
    return expected


def verify_reopened_chamfer(sketch: Any, expected: dict[str, Any]) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (5, 6)
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
    support_arc = serialize_sketch_geometry(sketch, 2)
    corner = serialize_sketch_geometry(sketch, 3)
    chamfer = serialize_sketch_geometry(sketch, 4)
    assert support_arc["kind"] == "circular_arc"
    assert support_arc["construction"] is True
    assert corner["kind"] == "point"
    assert corner["construction"] is True
    assert chamfer["kind"] == "line"
    assert chamfer["construction"] is False
