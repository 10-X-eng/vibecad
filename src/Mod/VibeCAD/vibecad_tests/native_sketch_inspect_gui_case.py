# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI parity coverage for exact Sketch relationship reads."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher

from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchMutationState import geometry_records_without_tags
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)


def _state(sketch: Any) -> dict[str, Any]:
    return {
        "geometry": geometry_records_without_tags(
            canonical_sketch_records(iter_sketch_geometry_records(sketch))
        ),
        "constraints": canonical_sketch_records(iter_sketch_constraint_records(sketch)),
        "external_geometry": canonical_sketch_records(
            iter_sketch_external_geometry_records(sketch)
        ),
        "expressions": tuple(
            (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
        ),
        "degrees_of_freedom": int(sketch.DoF),
    }


def _selection(document: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (str(item.ObjectName), tuple(str(name) for name in item.SubElementNames))
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _arguments(sketch: Any, selection: list[dict[str, object]]) -> dict[str, object]:
    return {
        "operation": "select_constraints",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "selection": selection,
    }


def _constraint_arguments(sketch: Any, indices: tuple[int, ...]) -> dict[str, object]:
    return {
        "operation": "select_elements",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "constraints": [
            {
                "constraint_index": index,
                "expected_type": str(sketch.Constraints[index].Type),
                "expected_name": str(sketch.Constraints[index].Name),
            }
            for index in indices
        ],
    }


def _elements(result: dict[str, Any]) -> tuple[tuple[int, str], ...]:
    return tuple(
        (int(item["geometry_index"]), str(item["position"]))
        for item in result["associated_elements"]
    )


def exercise_inspect_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    first = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0), App.Vector(10, 0)),
            False,
        )
    )
    second = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(10, 0), App.Vector(16, 4)),
            False,
        )
    )
    third = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(20, 0), App.Vector(20, 8)),
            False,
        )
    )
    fourth = int(
        sketch.addGeometry(
            Part.LineSegment(App.Vector(25, 0), App.Vector(25, 8)),
            True,
        )
    )
    horizontal = int(sketch.addConstraint(Sketcher.Constraint("Horizontal", first)))
    coincident = int(
        sketch.addConstraint(Sketcher.Constraint("Coincident", first, 2, second, 1))
    )
    vertical = int(sketch.addConstraint(Sketcher.Constraint("Vertical", third)))
    distance = int(
        sketch.addConstraint(Sketcher.Constraint("Distance", second, 7.211102550928))
    )
    sketch.renameConstraint(distance, "PreservedLength")
    document.recompute()
    process_events(8)
    document.clearUndos()

    before = _state(sketch)
    undo_before = int(document.UndoCount)
    transaction_before = (
        int(document.getBookedTransactionID()),
        bool(document.HasPendingTransaction),
    )
    expected_whole = (horizontal, coincident)

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(document.Name, sketch.Name, f"Edge{second + 1}")
    process_events(8)
    selection_before = _selection(document)
    whole_arguments = _arguments(
        sketch,
        [{"geometry_index": first, "position": "whole"}],
    )
    whole = native_call(whole_arguments, call_id="rolling-inspect-whole")
    assert (
        tuple(item["constraint_index"] for item in whole["associated_constraints"])
        == expected_whole
    )
    assert tuple(
        item["matched_selection_indices"] for item in whole["associated_constraints"]
    ) == ([0], [0])
    assert _selection(document) == selection_before

    point = native_call(
        _arguments(
            sketch,
            [{"geometry_index": first, "position": "end"}],
        ),
        call_id="rolling-inspect-point",
    )
    assert tuple(
        item["constraint_index"] for item in point["associated_constraints"]
    ) == (coincident,)

    multiple = native_call(
        _arguments(
            sketch,
            [
                {"geometry_index": first, "position": "whole"},
                {"geometry_index": third, "position": "whole"},
            ],
        ),
        call_id="rolling-inspect-multiple",
    )
    assert tuple(
        item["constraint_index"] for item in multiple["associated_constraints"]
    ) == (horizontal, coincident, vertical)
    assert tuple(
        tuple(item["matched_selection_indices"])
        for item in multiple["associated_constraints"]
    ) == ((0,), (0,), (1,))

    stale = native_call(
        {
            **whole_arguments,
            "expected_constraint_count": int(sketch.ConstraintCount) + 1,
        },
        succeeds=False,
        call_id="rolling-inspect-stale",
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before
    assert (
        int(document.getBookedTransactionID()),
        bool(document.HasPendingTransaction),
    ) == transaction_before
    assert edit_boundary(document, sketch, controller) == boundary

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(document.Name, sketch.Name, f"Edge{first + 1}")
    process_events(8)
    assert Gui.isCommandActive("Sketcher_SelectConstraints")
    Gui.runCommand("Sketcher_SelectConstraints")
    process_events(12)
    human_selection = _selection(document)
    assert human_selection == (
        (
            sketch.Name,
            tuple(f"Constraint{index + 1}" for index in (horizontal, coincident)),
        ),
    ), human_selection

    reverse = native_call(
        _constraint_arguments(sketch, (horizontal, coincident)),
        call_id="rolling-inspect-elements",
    )
    assert _elements(reverse) == (
        (first, "whole"),
        (first, "end"),
        (second, "start"),
    )
    assert tuple(
        tuple(item["matched_constraint_selection_indices"])
        for item in reverse["associated_elements"]
    ) == ((0,), (1,), (1,))
    assert _selection(document) == human_selection

    stale_constraint = native_call(
        {
            **_constraint_arguments(sketch, (horizontal,)),
            "constraints": [
                {
                    "constraint_index": horizontal,
                    "expected_type": "Vertical",
                    "expected_name": "",
                }
            ],
        },
        succeeds=False,
        call_id="rolling-inspect-elements-stale",
    )
    assert stale_constraint["error_code"] == "NATIVE_SKETCH_INVALID"

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(
        document.Name,
        sketch.Name,
        f"Constraint{horizontal + 1}",
    )
    Gui.Selection.addSelection(
        document.Name,
        sketch.Name,
        f"Constraint{coincident + 1}",
    )
    process_events(8)
    assert Gui.isCommandActive("Sketcher_SelectElementsAssociatedWithConstraints")
    Gui.runCommand("Sketcher_SelectElementsAssociatedWithConstraints")
    process_events(12)
    human_reverse = _selection(document)
    assert human_reverse == (
        (
            sketch.Name,
            (
                f"Edge{first + 1}",
                "Vertex2",
                "Vertex3",
            ),
        ),
    ), human_reverse

    group = int(
        sketch.addConstraint(
            Sketcher.Constraint(
                "Group",
                [fourth, 0, first, 0, second, 0, third, 0],
            )
        )
    )
    document.recompute()
    process_events(12)
    document.clearUndos()
    before = _state(sketch)
    undo_before = int(document.UndoCount)
    transaction_before = (
        int(document.getBookedTransactionID()),
        bool(document.HasPendingTransaction),
    )
    group_selection_before = _selection(document)

    full_group = native_call(
        _constraint_arguments(sketch, (group,)),
        call_id="rolling-inspect-full-group",
    )
    assert _elements(full_group) == (
        (fourth, "whole"),
        (first, "whole"),
        (second, "whole"),
        (third, "whole"),
    )
    assert _selection(document) == group_selection_before

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(document.Name, sketch.Name, f"Constraint{group + 1}")
    process_events(8)
    Gui.runCommand("Sketcher_SelectElementsAssociatedWithConstraints")
    process_events(12)
    legacy_group_selection = _selection(document)
    assert legacy_group_selection == (
        (
            sketch.Name,
            (f"Edge{fourth + 1}",),
        ),
    ), legacy_group_selection
    assert _state(sketch) == before
    assert int(document.UndoCount) == undo_before
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "state": before,
        "whole_constraint_indices": expected_whole,
        "group_constraint_index": group,
        "whole_state_sha256": (
            full_group["geometry_state_sha256"],
            full_group["constraint_state_sha256"],
        ),
    }


def verify_reopened_inspect(sketch: Any, expected: dict[str, Any]) -> None:
    current = _state(sketch)
    assert current == expected["state"], {
        key: (expected["state"][key], current[key])
        for key in current
        if current[key] != expected["state"][key]
    }
    indices = expected["whole_constraint_indices"]
    assert indices == (0, 1)
    assert tuple(str(sketch.Constraints[index].Type) for index in indices) == (
        "Horizontal",
        "Coincident",
    )
    assert str(sketch.Constraints[expected["group_constraint_index"]].Type) == "Group"
