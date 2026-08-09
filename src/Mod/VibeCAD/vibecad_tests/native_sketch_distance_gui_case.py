# SPDX-License-Identifier: LGPL-2.1-or-later

"""General Distance lifecycle case for the rolling Native Sketch gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    distance_arguments,
    line_arguments,
)


_INITIAL_GEOMETRY_COUNT = 174
_INITIAL_CONSTRAINT_COUNT = 245
_LINE_INDEX = 174
_CONSTRAINT_INDEX = 245


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _line_length(line: dict[str, Any]) -> float:
    return math.dist(line["start_mm"][:2], line["end_mm"][:2])


def exercise_distance_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT,
        _INITIAL_CONSTRAINT_COUNT,
    )
    undo_before = int(document.UndoCount)
    incomplete = native_call(
        distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            external_geometry_count=1,
            selection=((173, "start"),),
            value=10.0,
        ),
        succeeds=False,
    )
    assert incomplete["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before

    line_response = native_call(
        line_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            start=(180.0, 160.0),
            end=(183.0, 164.0),
        )
    )
    assert line_response["geometry"]["index"] == _LINE_INDEX
    assert math.isclose(_line_length(line_response["geometry"]), 5.0, abs_tol=1.0e-9)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT + 1,
        _INITIAL_CONSTRAINT_COUNT,
    )

    dimension_undo_before = int(document.UndoCount)
    stale_reference = native_call(
        distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_LINE_INDEX, "whole"),),
            value=4.0,
            driving=False,
        ),
        succeeds=False,
    )
    assert stale_reference["error_code"] == "NATIVE_SKETCH_INVALID"
    wrong_unit = native_call(
        distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_LINE_INDEX, "whole"),),
            value=13.0,
            unit="deg",
        ),
        succeeds=False,
    )
    assert wrong_unit["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert serialize_sketch_geometry(sketch, 1)["kind"] == "line"
    two_lines = native_call(
        distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((1, "whole"), (_LINE_INDEX, "whole")),
            value=13.0,
        ),
        succeeds=False,
    )
    assert two_lines["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == dimension_undo_before

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    response = native_call(
        distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_LINE_INDEX, "whole"),),
            value=13.0,
        )
    )
    assert response["operation"] == "constrain_distance"
    assert response["target_form"] == "line_length"
    assert response["geometry_count"] == _INITIAL_GEOMETRY_COUNT + 1
    assert response["constraint_count"] == _INITIAL_CONSTRAINT_COUNT + 1
    assert response["measured_before"] == {"value": 5.0, "unit": "mm"}
    assert math.isclose(response["measured_after"]["value"], 13.0, abs_tol=1.0e-8)
    assert response["measured_after"]["unit"] == "mm"
    constraint = response["constraint"]
    assert constraint["index"] == _CONSTRAINT_INDEX
    assert constraint["type"] == "Distance"
    assert constraint["driving"] is True
    assert constraint["active"] is True
    assert constraint["virtual"] is False
    assert constraint["references"] == [
        {"slot": 1, "geometry_index": _LINE_INDEX}
    ]
    assert math.isclose(constraint["value"], 13.0, abs_tol=1.0e-9)
    assert response["assistant_undo_available"] is True
    assert len(response["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Distance"
    assert int(document.UndoCount) == 20
    assert _selection_state(document) == selection
    line = serialize_sketch_geometry(sketch, _LINE_INDEX)
    assert math.isclose(_line_length(line), 13.0, abs_tol=1.0e-8)

    redundant_undo_before = int(document.UndoCount)
    redundant = native_call(
        distance_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_LINE_INDEX, "whole"),),
            value=13.0,
        ),
        succeeds=False,
    )
    assert redundant["error_code"] == "NATIVE_SKETCH_INVALID"
    assert "no constraint was added" in redundant["error"]
    assert int(document.UndoCount) == redundant_undo_before
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1

    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT
    assert math.isclose(
        _line_length(serialize_sketch_geometry(sketch, _LINE_INDEX)),
        5.0,
        abs_tol=1.0e-8,
    )
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1
    assert serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX) == constraint
    line = serialize_sketch_geometry(sketch, _LINE_INDEX)
    assert math.isclose(_line_length(line), 13.0, abs_tol=1.0e-8)
    assert edit_boundary(document, sketch, controller) == boundary
    return {"line": line, "constraint": constraint}


def verify_reopened_distance(sketch: Any, expected: dict) -> None:
    line = serialize_sketch_geometry(sketch, _LINE_INDEX)
    constraint = serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX)
    for key in (
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "start_mm",
        "end_mm",
    ):
        assert line[key] == expected["line"][key]
    assert line["tag"]
    assert expected["line"]["tag"]
    assert math.isclose(_line_length(line), 13.0, abs_tol=1.0e-8)
    assert constraint == expected["constraint"]
    assert constraint["type"] == "Distance"
    assert constraint["driving"] is True
    assert math.isclose(constraint["value"], 13.0, abs_tol=1.0e-9)
