# SPDX-License-Identifier: LGPL-2.1-or-later

"""Construction lifecycle case for the rolling Native Sketch geometry gate."""

from __future__ import annotations

from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_external_geometry,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import construction_arguments


_GEOMETRY_COUNT = 171
_CONSTRAINT_COUNT = 242
_LINE_INDEX = 1
_INTERNAL_ALIGNMENT_INDEX = 8
_TEXT_MEMBER_INDEX = 149
_EXTERNAL_INDEX = -3


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def exercise_construction_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _GEOMETRY_COUNT,
        _CONSTRAINT_COUNT,
    )
    assert serialize_sketch_geometry(sketch, _LINE_INDEX)["construction"] is False
    assert (
        serialize_sketch_external_geometry(sketch, _EXTERNAL_INDEX)["defining"]
        is False
    )
    undo_before = int(document.UndoCount)

    stale = native_call(
        construction_arguments(
            sketch,
            geometry_count=_GEOMETRY_COUNT,
            external_geometry_count=1,
            targets=((_LINE_INDEX, True),),
        ),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    grouped = native_call(
        construction_arguments(
            sketch,
            geometry_count=_GEOMETRY_COUNT,
            external_geometry_count=1,
            targets=((_TEXT_MEMBER_INDEX, False),),
        ),
        succeeds=False,
    )
    assert grouped["error_code"] == "NATIVE_SKETCH_INVALID"
    internal = native_call(
        construction_arguments(
            sketch,
            geometry_count=_GEOMETRY_COUNT,
            external_geometry_count=1,
            targets=((_INTERNAL_ALIGNMENT_INDEX, True),),
        ),
        succeeds=False,
    )
    assert internal["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    response = native_call(
        construction_arguments(
            sketch,
            geometry_count=_GEOMETRY_COUNT,
            external_geometry_count=1,
            targets=((_LINE_INDEX, False), (_EXTERNAL_INDEX, False)),
        )
    )
    assert response["operation"] == "toggle_construction"
    assert response["geometry_count"] == _GEOMETRY_COUNT
    assert response["constraint_count"] == _CONSTRAINT_COUNT
    assert response["external_geometry_count"] == 1
    assert response["changed_geometry"] == [
        {
            "geometry_index": _LINE_INDEX,
            "geometry_kind": "line",
            "state_kind": "construction",
            "previous_state": False,
            "current_state": True,
        },
        {
            "geometry_index": _EXTERNAL_INDEX,
            "geometry_kind": "line",
            "state_kind": "defining",
            "previous_state": False,
            "current_state": True,
        },
    ]
    assert _selection_state(document) == selection
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Toggle Native Sketch Construction"
    assert response["assistant_undo_available"] is True
    assert len(response["receipt"]["changed"]) == 1
    line = serialize_sketch_geometry(sketch, _LINE_INDEX)
    external = serialize_sketch_external_geometry(sketch, _EXTERNAL_INDEX)
    assert line["construction"] is True
    assert external["defining"] is True

    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    document.undo()
    process_events(16)
    assert serialize_sketch_geometry(sketch, _LINE_INDEX)["construction"] is False
    assert (
        serialize_sketch_external_geometry(sketch, _EXTERNAL_INDEX)["defining"]
        is False
    )
    document.redo()
    process_events(16)
    line = serialize_sketch_geometry(sketch, _LINE_INDEX)
    external = serialize_sketch_external_geometry(sketch, _EXTERNAL_INDEX)
    assert line["construction"] is True
    assert external["defining"] is True
    assert edit_boundary(document, sketch, controller) == boundary
    return {"line": line, "external": external}


def verify_reopened_construction(sketch: Any, expected: dict) -> None:
    line = serialize_sketch_geometry(sketch, _LINE_INDEX)
    external = serialize_sketch_external_geometry(sketch, _EXTERNAL_INDEX)
    assert line["construction"] is True
    assert external["defining"] is True
    for key in ("index", "type_id", "kind", "construction", "start_mm", "end_mm"):
        assert line[key] == expected["line"][key]
    for key in (
        "geometry_index",
        "type_id",
        "kind",
        "defining",
        "reference",
        "start_mm",
        "end_mm",
    ):
        assert external[key] == expected["external"][key]
