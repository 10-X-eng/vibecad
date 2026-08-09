# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI behavior checks for Sketch virtual space."""

from __future__ import annotations

from typing import Any, Callable

import FreeCADGui as Gui
import Sketcher


def _selection_state(document: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(name) for name in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _arguments(sketch: Any, target: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": "set_virtual_space",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "target": target,
    }


def _view(expected: bool, shown: bool) -> dict[str, Any]:
    return {
        "kind": "view",
        "expected_shown_virtual_space": expected,
        "shown_virtual_space": shown,
    }


def _constraints(expected: bool, desired: bool) -> dict[str, Any]:
    return {
        "kind": "constraints",
        "constraints": [
            {
                "constraint_index": index,
                "expected_virtual_space": expected,
                "virtual_space": desired,
            }
            for index in (0, 1)
        ],
    }


def _constraint_states(sketch: Any) -> tuple[bool, bool]:
    return tuple(bool(sketch.getVirtualSpace(index)) for index in (0, 1))


def exercise_virtual_space_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
    read_view: Callable[[], bool],
    install_failing_verifier: Callable[[], Callable[[], None]],
) -> dict[str, Any]:
    assert int(sketch.GeometryCount) == 1
    line = 0
    assert sketch.addConstraint(Sketcher.Constraint("Horizontal", line)) == 0
    assert sketch.addConstraint(Sketcher.Constraint("Distance", line, 10.0)) == 1
    document.recompute()
    process_events(24)
    document.clearUndos()
    assert _constraint_states(sketch) == (False, False)
    assert read_view() is False

    Gui.Selection.clearSelection()
    undo_before_view = int(document.UndoCount)
    Gui.runCommand("Sketcher_SwitchVirtualSpace")
    process_events(16)
    assert read_view() is True
    assert int(document.UndoCount) == undo_before_view
    Gui.runCommand("Sketcher_SwitchVirtualSpace")
    process_events(16)
    assert read_view() is False
    assert int(document.UndoCount) == undo_before_view

    Gui.Selection.addSelection(sketch, "Edge1")
    process_events(8)
    selection = _selection_state(document)
    shown = native_call(_arguments(sketch, _view(False, True)))
    process_events(16)
    assert shown["target_kind"] == "view"
    assert shown["changed"] is True
    assert read_view() is True
    assert int(document.UndoCount) == undo_before_view
    assert _selection_state(document) == selection
    hidden = native_call(_arguments(sketch, _view(True, False)))
    assert hidden["changed"] is True
    assert read_view() is False
    assert int(document.UndoCount) == undo_before_view
    assert _selection_state(document) == selection

    Gui.Selection.clearSelection()
    for index in (1, 2):
        Gui.Selection.addSelection(sketch, f"Constraint{index}")
    process_events(12)
    human_undo_before = int(document.UndoCount)
    Gui.runCommand("Sketcher_SwitchVirtualSpace")
    process_events(20)
    assert _constraint_states(sketch) == (True, True)
    assert int(document.UndoCount) == human_undo_before + 1
    assert _selection_state(document) == ()
    document.undo()
    process_events(20)
    assert _constraint_states(sketch) == (False, False)

    document.clearUndos()
    Gui.Selection.addSelection(sketch, "Edge1")
    process_events(8)
    selection = _selection_state(document)
    changed = native_call(_arguments(sketch, _constraints(False, True)))
    process_events(20)
    assert _constraint_states(sketch) == (True, True)
    assert changed["target_kind"] == "constraints"
    assert len(changed["changed_constraints"]) == 2
    assert int(document.UndoCount) == 1
    assert _selection_state(document) == selection

    stale = native_call(
        _arguments(sketch, _constraints(False, True)),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _constraint_states(sketch) == (True, True)
    assert int(document.UndoCount) == 1

    undo_before_rollback = int(document.UndoCount)
    restore_verifier = install_failing_verifier()
    try:
        rolled_back = native_call(
            _arguments(sketch, _constraints(True, False)),
            succeeds=False,
        )
    finally:
        restore_verifier()
    assert rolled_back["error_code"] == "NATIVE_POSTCONDITION_FAILED", rolled_back
    assert _constraint_states(sketch) == (True, True)
    assert int(document.UndoCount) == undo_before_rollback
    assert _selection_state(document) == selection
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "constraint_states": _constraint_states(sketch),
        "geometry_count": int(sketch.GeometryCount),
        "constraint_count": int(sketch.ConstraintCount),
    }


def verify_reopened_virtual_space(sketch: Any, expected: dict[str, Any]) -> None:
    assert int(sketch.GeometryCount) == expected["geometry_count"]
    assert int(sketch.ConstraintCount) == expected["constraint_count"]
    assert _constraint_states(sketch) == tuple(expected["constraint_states"])
    assert bool(sketch.isValid())
