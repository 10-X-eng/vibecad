# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit Radius lifecycle case for the rolling Sketch gate."""

from __future__ import annotations

import math
from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    circle_arguments,
    radius_arguments,
)


_INITIAL_GEOMETRY_COUNT = 176
_INITIAL_CONSTRAINT_COUNT = 247
_CIRCLE_INDEX = 176
_CONSTRAINT_INDEX = 247


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def exercise_radius_case(
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
    circle_response = native_call(
        circle_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            center=(230.0, 160.0),
            radius=4.0,
        )
    )
    assert circle_response["geometry"]["index"] == _CIRCLE_INDEX
    assert circle_response["geometry"]["radius_mm"] == 4.0
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT + 1,
        _INITIAL_CONSTRAINT_COUNT,
    )

    radius_undo_before = int(document.UndoCount)
    center_target = native_call(
        radius_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_CIRCLE_INDEX, "center"),),
            value=7.5,
        ),
        succeeds=False,
    )
    assert center_target["error_code"] == "NATIVE_SKETCH_INVALID"
    stale_reference = native_call(
        radius_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_CIRCLE_INDEX, "whole"),),
            value=5.0,
            driving=False,
        ),
        succeeds=False,
    )
    assert stale_reference["error_code"] == "NATIVE_SKETCH_INVALID"
    wrong_unit = native_call(
        radius_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_CIRCLE_INDEX, "whole"),),
            value=7.5,
            unit="deg",
        ),
        succeeds=False,
    )
    assert wrong_unit["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    line_target = native_call(
        radius_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((174, "whole"),),
            value=7.5,
        ),
        succeeds=False,
    )
    assert line_target["error_code"] == "NATIVE_SKETCH_INVALID"
    multi_target = native_call(
        radius_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_CIRCLE_INDEX, "whole"), (175, "whole")),
            value=7.5,
        ),
        succeeds=False,
    )
    assert multi_target["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    unexpected_inference = radius_arguments(
        sketch,
        geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
        external_geometry_count=1,
        selection=((_CIRCLE_INDEX, "whole"),),
        value=7.5,
    )
    unexpected_inference["expected_constraint"] = "radius"
    extra_field = native_call(unexpected_inference, succeeds=False)
    assert extra_field["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == radius_undo_before

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    response = native_call(
        radius_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_CIRCLE_INDEX, "whole"),),
            value=7.5,
        )
    )
    assert response["operation"] == "constrain_radius"
    assert response["target_form"] == "circle_radius"
    assert response["geometry_count"] == _INITIAL_GEOMETRY_COUNT + 1
    assert response["constraint_count"] == _INITIAL_CONSTRAINT_COUNT + 1
    assert response["measured_before"] == {"value": 4.0, "unit": "mm"}
    assert response["measured_after"] == {"value": 7.5, "unit": "mm"}
    constraint = response["constraint"]
    assert constraint["index"] == _CONSTRAINT_INDEX
    assert constraint["type"] == "Radius"
    assert constraint["driving"] is True
    assert constraint["active"] is True
    assert constraint["virtual"] is False
    assert constraint["references"] == [
        {"slot": 1, "geometry_index": _CIRCLE_INDEX}
    ]
    assert math.isclose(constraint["value"], 7.5, abs_tol=1.0e-9)
    assert response["assistant_undo_available"] is True
    assert len(response["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Radius"
    assert int(document.UndoCount) == 20
    assert _selection_state(document) == selection
    circle = serialize_sketch_geometry(sketch, _CIRCLE_INDEX)
    assert math.isclose(circle["radius_mm"], 7.5, abs_tol=1.0e-9)

    redundant_undo_before = int(document.UndoCount)
    redundant = native_call(
        radius_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            selection=((_CIRCLE_INDEX, "whole"),),
            value=7.5,
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
        serialize_sketch_geometry(sketch, _CIRCLE_INDEX)["radius_mm"],
        4.0,
        abs_tol=1.0e-9,
    )
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 1
    assert serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX) == constraint
    circle = serialize_sketch_geometry(sketch, _CIRCLE_INDEX)
    assert math.isclose(circle["radius_mm"], 7.5, abs_tol=1.0e-9)
    assert edit_boundary(document, sketch, controller) == boundary
    return {"circle": circle, "constraint": constraint}


def verify_reopened_radius(sketch: Any, expected: dict) -> None:
    circle = serialize_sketch_geometry(sketch, _CIRCLE_INDEX)
    constraint = serialize_sketch_constraint(sketch, _CONSTRAINT_INDEX)
    for key in (
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "center_mm",
        "radius_mm",
    ):
        assert circle[key] == expected["circle"][key]
    assert circle["tag"]
    assert expected["circle"]["tag"]
    assert math.isclose(circle["radius_mm"], 7.5, abs_tol=1.0e-9)
    assert constraint == expected["constraint"]
    assert constraint["type"] == "Radius"
    assert constraint["driving"] is True
    assert math.isclose(constraint["value"], 7.5, abs_tol=1.0e-9)
