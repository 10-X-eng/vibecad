# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI parity coverage for durable Sketch internal geometry."""

from __future__ import annotations

import json
from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher

from VibeCADNativeSketchState import serialize_sketch_state


_COMPLETE_COUNTS = (4, 4, 3, 2, 5)


def _add_fixtures(sketch: Any) -> tuple[int, ...]:
    roots = (
        sketch.addGeometry(Part.Ellipse(App.Vector(0, 0), 8, 3), False),
        sketch.addGeometry(
            Part.ArcOfEllipse(Part.Ellipse(App.Vector(20, 0), 7, 3), -0.5, 2.1),
            False,
        ),
        sketch.addGeometry(
            Part.ArcOfHyperbola(
                Part.Hyperbola(App.Vector(40, 0), 6, 2),
                -0.7,
                0.8,
            ),
            False,
        ),
        sketch.addGeometry(
            Part.ArcOfParabola(
                Part.Parabola(
                    App.Vector(60, 0),
                    App.Vector(57, 0),
                    App.Vector(0, 0, 1),
                ),
                -4,
                5,
            ),
            False,
        ),
        sketch.addGeometry(
            Part.BSplineCurve(
                [App.Vector(75, -5), App.Vector(82, 8), App.Vector(92, -2)],
                [3, 3],
                [0.0, 1.0],
                False,
                2,
                [1.0, 1.8, 1.2],
                False,
            ),
            False,
        ),
    )
    assert roots == (0, 1, 2, 3, 4)
    return roots


def _helper_indices(sketch: Any, root_index: int) -> tuple[int, ...]:
    values = []
    for constraint in tuple(sketch.Constraints):
        if (
            str(getattr(constraint, "Type", "")) == "InternalAlignment"
            and int(constraint.Second) == root_index
        ):
            values.append(int(constraint.First))
    return tuple(sorted(values))


def _helper_counts(sketch: Any, roots: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(len(_helper_indices(sketch, root)) for root in roots)


def _arguments(
    sketch: Any,
    roots: tuple[int, ...],
    expected_helpers: tuple[int, ...],
) -> dict[str, Any]:
    return {
        "operation": "restore_internal_alignment_geometry",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "targets": [
            {
                "geometry_index": root,
                "expected_internal_geometry_count": count,
            }
            for root, count in zip(roots, expected_helpers, strict=True)
        ],
    }


def _semantic_state(sketch: Any) -> str:
    state = serialize_sketch_state(sketch)
    state.pop("solver", None)
    state.pop("profile", None)
    for record in state["geometry"]:
        record.pop("geometry_id", None)
        record.pop("tag", None)
    return json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _select_roots(sketch: Any, roots: tuple[int, ...], process_events) -> None:
    Gui.Selection.clearSelection()
    for root in roots:
        Gui.Selection.addSelection(sketch, f"Edge{root + 1}")
    process_events(16)


def _selection_state(document: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(name) for name in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _assert_result(
    response: dict[str, Any],
    *,
    actions: tuple[str, ...],
    states: tuple[str, ...],
) -> None:
    changed = response["changed_targets"]
    assert tuple(item["action"] for item in changed) == actions
    assert tuple(item["state"] for item in changed) == states
    assert response["sketch"]["object_name"]
    assert response["solver"]["conflicting_constraints"] == []
    assert response["solver"]["redundant_constraints"] == []


def exercise_internal_alignment_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
    install_failing_verifier: Callable[[], Callable[[], None]],
) -> dict[str, Any]:
    roots = _add_fixtures(sketch)
    document.recompute()
    process_events(24)
    document.clearUndos()
    _select_roots(sketch, (roots[0],), process_events)
    selection = _selection_state(document)

    exposed = native_call(_arguments(sketch, roots, (0, 0, 0, 0, 0)))
    process_events(24)
    _assert_result(
        exposed,
        actions=("expose_missing",) * 5,
        states=("exposed",) * 5,
    )
    assert _helper_counts(sketch, roots) == _COMPLETE_COUNTS
    assert (exposed["created_geometry_count"], exposed["created_constraint_count"]) == (
        18,
        18,
    )
    assert int(document.UndoCount) == 1
    assert _selection_state(document) == selection
    assert edit_boundary(document, sketch, controller) == boundary
    native_exposed_state = _semantic_state(sketch)

    before_stale = _semantic_state(sketch)
    stale = native_call(
        _arguments(sketch, roots, (0, 0, 0, 0, 0)),
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
    assert _semantic_state(sketch) == before_stale
    assert int(document.UndoCount) == 1

    hidden = native_call(_arguments(sketch, roots, _COMPLETE_COUNTS))
    process_events(24)
    _assert_result(
        hidden,
        actions=("hide_unused",) * 5,
        states=("hidden",) * 5,
    )
    assert _helper_counts(sketch, roots) == (0, 0, 0, 0, 0)
    assert (hidden["removed_geometry_count"], hidden["removed_constraint_count"]) == (
        18,
        18,
    )
    assert int(document.UndoCount) == 2
    assert _selection_state(document) == selection

    _select_roots(sketch, roots, process_events)
    undo_before_human = int(document.UndoCount)
    assert Gui.isCommandActive("Sketcher_RestoreInternalAlignmentGeometry")
    Gui.runCommand("Sketcher_RestoreInternalAlignmentGeometry")
    process_events(24)
    assert int(document.UndoCount) == undo_before_human + len(roots)
    assert _helper_counts(sketch, roots) == _COMPLETE_COUNTS
    assert _semantic_state(sketch) == native_exposed_state
    _select_roots(sketch, roots, process_events)
    Gui.runCommand("Sketcher_RestoreInternalAlignmentGeometry")
    process_events(24)
    assert _helper_counts(sketch, roots) == (0, 0, 0, 0, 0)

    document.clearUndos()
    Gui.Selection.clearSelection()
    process_events(8)
    ellipse_exposed = native_call(_arguments(sketch, (roots[0],), (0,)))
    assert ellipse_exposed["changed_targets"][0]["state"] == "exposed"
    major = next(
        index
        for index in _helper_indices(sketch, roots[0])
        if str(sketch.GeometryFacadeList[index].InternalType)
        == "EllipseMajorDiameter"
    )
    document.openTransaction("Constrain retained internal helper")
    sketch.addConstraint(Sketcher.Constraint("Distance", major, 8.0))
    document.recompute()
    document.commitTransaction()
    process_events(16)
    document.clearUndos()
    _select_roots(sketch, (roots[0],), process_events)
    partial = native_call(_arguments(sketch, (roots[0],), (4,)))
    process_events(16)
    _assert_result(partial, actions=("hide_unused",), states=("partial",))
    assert _helper_counts(sketch, roots)[0] == 1
    assert partial["removed_geometry_count"] == 3
    assert int(document.UndoCount) == 1
    selected_partial = _selection_state(document)

    restored = native_call(_arguments(sketch, (roots[0],), (1,)))
    process_events(16)
    _assert_result(restored, actions=("expose_missing",), states=("exposed",))
    assert _helper_counts(sketch, roots)[0] == 4
    assert restored["created_geometry_count"] == 3
    assert int(document.UndoCount) == 2
    assert _selection_state(document) == selected_partial

    before_rollback = _semantic_state(sketch)
    undo_before_rollback = int(document.UndoCount)
    restore_verifier = install_failing_verifier()
    try:
        rolled_back = native_call(
            _arguments(sketch, (roots[1],), (0,)),
            succeeds=False,
        )
    finally:
        restore_verifier()
    assert rolled_back["error_code"] == "NATIVE_POSTCONDITION_FAILED", rolled_back
    assert _semantic_state(sketch) == before_rollback
    assert int(document.UndoCount) == undo_before_rollback
    assert _helper_counts(sketch, roots)[1] == 0

    remaining = native_call(
        _arguments(sketch, roots[1:], (0, 0, 0, 0)),
    )
    process_events(24)
    _assert_result(
        remaining,
        actions=("expose_missing",) * 4,
        states=("exposed",) * 4,
    )
    assert _helper_counts(sketch, roots) == _COMPLETE_COUNTS
    assert _selection_state(document) == selected_partial
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "roots": roots,
        "helper_counts": _COMPLETE_COUNTS,
        "constraint_count": int(sketch.ConstraintCount),
    }


def verify_reopened_internal_alignment(sketch: Any, expected: dict[str, Any]) -> None:
    roots = tuple(expected["roots"])
    assert _helper_counts(sketch, roots) == tuple(expected["helper_counts"])
    assert int(sketch.ConstraintCount) == expected["constraint_count"]
    assert bool(sketch.isValid())
