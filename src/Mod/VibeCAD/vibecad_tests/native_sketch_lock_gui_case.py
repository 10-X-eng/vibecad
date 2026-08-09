# SPDX-License-Identifier: LGPL-2.1-or-later

"""Lock Position lifecycle case for the rolling Sketch gate."""

from __future__ import annotations

from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    lock_arguments,
    point_arguments,
)


_INITIAL_GEOMETRY_COUNT = 180
_INITIAL_CONSTRAINT_COUNT = 250
_POINT_INDEX = 180
_FIRST_CONSTRAINT_INDEX = 250


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _absolute_target(
    *,
    x: float = 290.0,
    y: float = 160.0,
    index: int = _POINT_INDEX,
    position: str = "start",
) -> dict[str, object]:
    return {
        "form": "absolute",
        "point": _element(index, position),
        "expected_position_mm": {"x": x, "y": y},
    }


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _point_position(sketch: Any) -> tuple[float, float]:
    point = sketch.getPoint(_POINT_INDEX, 1)
    return float(point.x), float(point.y)


def exercise_lock_case(
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
    point_response = native_call(
        point_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT,
            x=290.0,
            y=160.0,
        )
    )
    assert point_response["geometry"]["index"] == _POINT_INDEX
    assert point_response["geometry"]["type_id"] == "Part::GeomPoint"
    assert _point_position(sketch) == (290.0, 160.0)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT + 1,
        _INITIAL_CONSTRAINT_COUNT,
    )

    lock_undo_before = int(document.UndoCount)
    stale_position = native_call(
        lock_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            target=_absolute_target(x=289.0),
        ),
        succeeds=False,
    )
    assert stale_position["error_code"] == "NATIVE_SKETCH_INVALID"
    whole_target = native_call(
        lock_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            target=_absolute_target(position="whole"),
        ),
        succeeds=False,
    )
    assert whole_target["error_code"] == "NATIVE_SKETCH_INVALID"
    origin_target = native_call(
        lock_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            target=_absolute_target(x=0.0, y=0.0, index=-1),
        ),
        succeeds=False,
    )
    assert origin_target["error_code"] == "NATIVE_SKETCH_INVALID"
    mixed_form = _absolute_target()
    mixed_form["reference"] = _element(-1, "start")
    mixed_response = native_call(
        lock_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            target=mixed_form,
        ),
        succeeds=False,
    )
    assert mixed_response["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    bad_coordinate = _absolute_target()
    bad_coordinate["expected_position_mm"] = {"x": 290.0, "y": 1_000_001.0}
    coordinate_response = native_call(
        lock_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            target=bad_coordinate,
        ),
        succeeds=False,
    )
    assert coordinate_response["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert int(document.UndoCount) == lock_undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (
        _INITIAL_GEOMETRY_COUNT + 1,
        _INITIAL_CONSTRAINT_COUNT,
    )

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge2",)),)

    response = native_call(
        lock_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            target=_absolute_target(),
        )
    )
    assert response["operation"] == "constrain_lock"
    assert response["target_form"] == "absolute"
    assert response["geometry_count"] == _INITIAL_GEOMETRY_COUNT + 1
    assert response["constraint_count"] == _INITIAL_CONSTRAINT_COUNT + 2
    assert response["measured_before"] == {
        "x": 290.0,
        "y": 160.0,
        "unit": "mm",
    }
    assert response["measured_after"] == response["measured_before"]
    constraints = response["constraints"]
    assert [constraint["index"] for constraint in constraints] == [250, 251]
    assert [constraint["type"] for constraint in constraints] == [
        "DistanceX",
        "DistanceY",
    ]
    assert [constraint["value"] for constraint in constraints] == [290.0, 160.0]
    assert all(constraint["driving"] is True for constraint in constraints)
    assert all(constraint["active"] is True for constraint in constraints)
    assert all(constraint["virtual"] is False for constraint in constraints)
    assert all(
        constraint["references"]
        == [{"slot": 1, "geometry_index": _POINT_INDEX, "position": 1}]
        for constraint in constraints
    )
    assert response["assistant_undo_available"] is True
    assert len(response["receipt"]["changed"]) == 1
    assert document.UndoNames[0] == "Create Native Sketch Lock"
    assert int(document.UndoCount) == lock_undo_before == 20
    assert _selection_state(document) == selection
    assert _point_position(sketch) == (290.0, 160.0)

    redundant_undo_before = int(document.UndoCount)
    redundant = native_call(
        lock_arguments(
            sketch,
            geometry_count=_INITIAL_GEOMETRY_COUNT + 1,
            external_geometry_count=1,
            target=_absolute_target(),
        ),
        succeeds=False,
    )
    assert redundant["error_code"] == "NATIVE_SKETCH_INVALID"
    assert "no constraint was added" in redundant["error"]
    assert int(document.UndoCount) == redundant_undo_before
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 2

    Gui.Selection.clearSelection(document.Name)
    process_events(8)
    document.undo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT
    assert _point_position(sketch) == (290.0, 160.0)
    document.redo()
    process_events(16)
    assert int(sketch.ConstraintCount) == _INITIAL_CONSTRAINT_COUNT + 2
    assert [
        serialize_sketch_constraint(sketch, _FIRST_CONSTRAINT_INDEX + offset)
        for offset in range(2)
    ] == constraints
    assert _point_position(sketch) == (290.0, 160.0)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "point": serialize_sketch_geometry(sketch, _POINT_INDEX),
        "constraints": constraints,
    }


def verify_reopened_lock(sketch: Any, expected: dict) -> None:
    point = serialize_sketch_geometry(sketch, _POINT_INDEX)
    for key in (
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "position_mm",
    ):
        assert point[key] == expected["point"][key]
    assert point["tag"]
    assert expected["point"]["tag"]
    constraints = [
        serialize_sketch_constraint(sketch, _FIRST_CONSTRAINT_INDEX + offset)
        for offset in range(2)
    ]
    assert constraints == expected["constraints"]
    assert [constraint["type"] for constraint in constraints] == [
        "DistanceX",
        "DistanceY",
    ]
    assert all(constraint["driving"] is True for constraint in constraints)
    assert _point_position(sketch) == (290.0, 160.0)
