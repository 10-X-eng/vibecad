# SPDX-License-Identifier: LGPL-2.1-or-later

"""Intersection case for the rolling Native Sketch GUI lifecycle gate."""

from __future__ import annotations

import hashlib
from typing import Any, Callable

from VibeCADNativeSketchExternalState import iter_external_reference_records
from VibeCADNativeSketchState import iter_sketch_external_geometry_records


def _source_digest(source: Any) -> str:
    return hashlib.sha256(source.Shape.exportBrepToString().encode()).hexdigest()


def _arguments(sketch: Any, source: Any, **updates) -> dict[str, object]:
    result: dict[str, object] = {
        "operation": "intersect_external_geometry",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": len(
            tuple(iter_external_reference_records(sketch))
        ),
        "expected_external_geometry_count": max(0, len(sketch.ExternalGeo) - 2),
        "source": {"object_name": source.Name, "subelement": "Edge1"},
        "role": "reference",
    }
    result.update(updates)
    return result


def _state(sketch: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    return {
        "references": tuple(iter_external_reference_records(sketch)),
        "geometry": tuple(iter_sketch_external_geometry_records(sketch)),
    }


def exercise_intersection_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    source = document.getObject("IntersectionSource")
    assert source is not None
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    assert _state(sketch) == {"references": (), "geometry": ()}
    source_before = _source_digest(source)
    undo_before = int(document.UndoCount)

    diagnostic = sketch.diagnoseExternal(source.Name, "Edge1", False, True)
    assert diagnostic["source_object_name"] == source.Name
    assert diagnostic["source_subelement"] == "Edge1"
    assert diagnostic["requested_defining"] is False
    assert diagnostic["requested_intersection"] is True
    assert diagnostic["added_reference"] is True
    assert diagnostic["reference_index"] == 0
    assert diagnostic["type"] == 1
    assert diagnostic["defining"] is False
    assert diagnostic["external_geometry_count"] == 1
    assert len(diagnostic["external_geometry"]) == 1
    assert diagnostic["external_geometry"][0].TypeId == "Part::GeomPoint"
    assert diagnostic["external_geometry_metadata"] == [
        {
            "reference": diagnostic["reference"],
            "defining": False,
            "frozen": False,
            "detached": False,
            "missing": False,
            "synchronized": False,
        }
    ]
    assert _state(sketch) == {"references": (), "geometry": ()}
    assert _source_digest(source) == source_before
    assert int(document.UndoCount) == undo_before

    stale = native_call(
        _arguments(sketch, source, expected_external_geometry_count=1),
        succeeds=False,
        call_id="rolling-intersection-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _state(sketch) == {"references": (), "geometry": ()}
    assert int(document.UndoCount) == undo_before

    response = native_call(
        _arguments(sketch, source),
        call_id="rolling-intersection-create",
    )
    assert response["operation"] == "intersect_external_geometry"
    assert response["source"]["object_name"] == source.Name
    assert response["source"]["subelement"] == "Edge1"
    assert response["role"] == "reference"
    assert response["outcome"] == "added_intersection"
    assert response["reference_index"] == 0
    assert response["reference_kind"] == "intersection"
    assert response["affected_geometry_count"] == 1
    assert response["affected_geometry_indices"] == [-3]
    assert response["external_reference_count"] == 1
    assert response["external_geometry_count"] == 1
    assert response["geometry_count"] == 0
    assert response["constraint_count"] == 0
    assert int(document.UndoCount) == undo_before + 1
    assert document.UndoNames[0] == "Intersect Native Sketch External Geometry"
    assert _source_digest(source) == source_before

    expected = _state(sketch)
    assert len(expected["references"]) == 1
    assert expected["references"][0]["object"]["object_name"] == source.Name
    assert expected["references"][0]["subelement"] == "Edge1"
    assert expected["references"][0]["kind"] == "intersection"
    assert len(expected["geometry"]) == 1
    assert expected["geometry"][0]["geometry_index"] == -3
    assert expected["geometry"][0]["kind"] == "point"
    assert expected["geometry"][0]["defining"] is False
    assert expected["geometry"][0]["reference"] == diagnostic["reference"]

    duplicate = native_call(
        _arguments(sketch, source),
        succeeds=False,
        call_id="rolling-intersection-duplicate",
    )
    assert duplicate["error_code"] == "NATIVE_SKETCH_INVALID"
    assert _state(sketch) == expected
    assert int(document.UndoCount) == undo_before + 1

    document.undo()
    process_events(16)
    assert _state(sketch) == {"references": (), "geometry": ()}
    document.redo()
    process_events(16)
    assert _state(sketch) == expected
    assert _source_digest(source) == source_before
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "reference": expected["references"][0],
        "geometry": expected["geometry"],
        "source_digest": source_before,
    }


def verify_reopened_intersection(sketch: Any, expected: dict[str, Any]) -> None:
    source = sketch.Document.getObject("IntersectionSource")
    assert source is not None
    observed = _state(sketch)
    assert len(observed["references"]) == 1
    assert observed["references"][0]["object"]["object_name"] == source.Name
    assert observed["references"][0]["subelement"] == "Edge1"
    assert observed["references"][0]["kind"] == "intersection"
    assert observed["geometry"] == expected["geometry"]
    assert _source_digest(source) == expected["source_digest"]
