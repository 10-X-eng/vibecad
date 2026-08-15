# SPDX-License-Identifier: LGPL-2.1-or-later

"""Text lifecycle case for the rolling Native Sketch geometry gate."""

from __future__ import annotations

from typing import Any, Callable

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import text_arguments


_BASE_GEOMETRY = 148
_BASE_CONSTRAINTS = 241
_FINAL_GEOMETRY = 171
_FINAL_CONSTRAINTS = 242
_HANDLE_INDEX = 148
_GENERATED_INDICES = tuple(range(149, 171))
_CONSTRAINT_INDEX = 241
_REBUILT_GEOMETRY_METADATA = frozenset(
    {"geometry_id", "internal_type", "layer_id", "tag"}
)


def _durable_geometry(record: dict) -> dict:
    return {
        key: value
        for key, value in record.items()
        if key not in _REBUILT_GEOMETRY_METADATA
    }


def _constraint_state(sketch: Any) -> dict:
    constraint = sketch.Constraints[_CONSTRAINT_INDEX]
    return {
        "record": serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX),
        "elements": tuple(tuple(int(value) for value in item) for item in constraint.Elements),
        "text": str(constraint.Text),
        "font_name": str(constraint.Font),
        "is_height": bool(constraint.IsTextHeight),
    }


def exercise_text_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict:
    undo_before = int(document.UndoCount)
    invalid = text_arguments(
        sketch,
        geometry_count=_BASE_GEOMETRY,
        text="two\nlines",
        font_name="default",
        handle_start=(38.0, -92.0),
        handle_end=(78.0, -92.0),
        sizing_mode="width",
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _BASE_GEOMETRY,
        _BASE_CONSTRAINTS,
    )

    response = native_call(
        text_arguments(
            sketch,
            geometry_count=_BASE_GEOMETRY,
            text="AI",
            font_name="default",
            handle_start=(38.0, -92.0),
            handle_end=(78.0, -92.0),
            sizing_mode="width",
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (
        _FINAL_GEOMETRY,
        _FINAL_CONSTRAINTS,
    )
    assert response["text"] == "AI"
    assert response["font_name"] == "osifont-lgpl3fe"
    assert response["sizing_mode"] == "width"
    handle = response["handle"]
    assert handle["index"] == _HANDLE_INDEX
    assert handle["type_id"] == "Part::GeomLineSegment"
    assert handle["kind"] == "line"
    assert handle["construction"] is True
    assert handle["blocked"] is False
    assert handle["start_mm"] == [38.0, -92.0, 0.0]
    assert handle["end_mm"] == [78.0, -92.0, 0.0]

    text_constraint = response["text_constraint"]
    assert text_constraint == {
        "index": _CONSTRAINT_INDEX,
        "type": "Text",
        "handle_index": _HANDLE_INDEX,
        "element_count": 23,
    }
    generated = response["generated_geometry"]
    assert generated["count"] == 22
    assert generated["first_index"] == _GENERATED_INDICES[0]
    assert generated["last_index"] == _GENERATED_INDICES[-1]
    assert generated["kind_counts"] == {"b_spline": 13, "line": 9}
    assert len(generated["sha256"]) == 64
    assert generated["construction"] is False

    records = [
        serialize_sketch_geometry(sketch, index) for index in _GENERATED_INDICES
    ]
    assert all(record["construction"] is False for record in records)
    constraint = _constraint_state(sketch)
    assert constraint["elements"] == tuple(
        (index, 0) for index in range(_HANDLE_INDEX, _FINAL_GEOMETRY)
    )
    assert constraint["text"] == "AI"
    assert constraint["font_name"] == "osifont-lgpl3fe"
    assert constraint["is_height"] is False
    assert constraint["record"]["type"] == "Text"
    assert constraint["record"]["text"] == "AI"
    assert constraint["record"]["font_name"] == "osifont-lgpl3fe"
    assert constraint["record"]["sizing_mode"] == "width"
    assert constraint["record"]["element_count"] == 23
    assert constraint["record"]["elements_truncated"] is True

    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Create Native Sketch Text"
    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _BASE_GEOMETRY,
        _BASE_CONSTRAINTS,
    )
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _FINAL_GEOMETRY,
        _FINAL_CONSTRAINTS,
    )
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "handle": handle,
        "generated": records,
        "constraint": constraint,
    }


def verify_reopened_text(sketch: Any, expected: dict) -> None:
    assert _durable_geometry(serialize_sketch_geometry(sketch, _HANDLE_INDEX)) == (
        _durable_geometry(expected["handle"])
    )
    generated = [
        serialize_sketch_geometry(sketch, index) for index in _GENERATED_INDICES
    ]
    assert [_durable_geometry(record) for record in generated] == [
        _durable_geometry(record) for record in expected["generated"]
    ]
    assert _constraint_state(sketch) == expected["constraint"]
