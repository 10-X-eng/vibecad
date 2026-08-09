# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI parity coverage for circular arc presentation helpers."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
import Part
from pivy import coin

from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchMutationState import geometry_records_without_tags
from VibeCADNativeSketchPresentationState import (
    ARC_OVERLAY_PREFERENCE,
    SKETCH_GENERAL_PREFERENCES,
)
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)


def _preference_group() -> Any:
    return App.ParamGet(SKETCH_GENERAL_PREFERENCES)


def _preference_snapshot() -> tuple[bool, bool]:
    group = _preference_group()
    present = ARC_OVERLAY_PREFERENCE in tuple(group.GetBools())
    return present, bool(group.GetBool(ARC_OVERLAY_PREFERENCE, False))


def _restore_preference(snapshot: tuple[bool, bool]) -> None:
    present, visible = snapshot
    group = _preference_group()
    if present:
        group.SetBool(ARC_OVERLAY_PREFERENCE, visible)
    else:
        group.RemBool(ARC_OVERLAY_PREFERENCE)


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


def _information_switches(sketch: Any) -> tuple[int, ...]:
    assert Gui.activeDocument().getInEdit() is not None
    search = coin.SoSearchAction()
    search.setName("InformationGroup")
    search.setInterest(coin.SoSearchAction.ALL)
    search.setSearchingAll(True)
    search.apply(Gui.activeDocument().activeView().getSceneGraph())
    paths = search.getPaths()
    assert paths.getLength() == 1, paths.getLength()
    group = coin.cast(paths[0].getTail(), "SoGroup")
    values = []
    for index in range(int(group.getNumChildren())):
        child = group.getChild(index)
        field = getattr(child, "whichChild", None)
        if field is not None:
            values.append(int(field.getValue()))
    return tuple(values)


def _arguments(sketch: Any, *, expected: bool, visible: bool) -> dict[str, Any]:
    return {
        "operation": "arc_overlay",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "expected_visible": expected,
        "visible": visible,
    }


def exercise_arc_overlay_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    original_preference = _preference_snapshot()
    try:
        group = _preference_group()
        group.SetBool(ARC_OVERLAY_PREFERENCE, False)
        process_events(16)
        first = int(
            sketch.addGeometry(
                Part.ArcOfCircle(
                    Part.Circle(App.Vector(0, 0), App.Vector(0, 0, 1), 8.0),
                    0.15,
                    2.1,
                ),
                False,
            )
        )
        second = int(
            sketch.addGeometry(
                Part.ArcOfCircle(
                    Part.Circle(App.Vector(22, 4), App.Vector(0, 0, 1), 5.0),
                    2.4,
                    5.5,
                ),
                False,
            )
        )
        assert (first, second) == (0, 1)
        document.recompute()
        process_events(32)
        document.clearUndos()

        before = _state(sketch)
        selection_before = tuple(Gui.Selection.getSelectionEx(document.Name))
        undo_before = int(document.UndoCount)
        transaction_before = (
            int(document.getBookedTransactionID()),
            bool(document.HasPendingTransaction),
        )
        hidden = _information_switches(sketch)
        assert hidden == (coin.SO_SWITCH_NONE, coin.SO_SWITCH_NONE), hidden

        shown = native_call(
            _arguments(sketch, expected=False, visible=True),
            call_id="rolling-arc-overlay-show",
        )
        process_events(24)
        assert shown["changed"] is True
        assert shown["previous_visible"] is False
        assert shown["visible"] is True
        assert shown["internal_arc_count"] == 2
        assert shown["external_arc_count"] == 0
        visible = _information_switches(sketch)
        assert visible == (coin.SO_SWITCH_ALL, coin.SO_SWITCH_ALL), visible

        stale = native_call(
            _arguments(sketch, expected=False, visible=False),
            succeeds=False,
            call_id="rolling-arc-overlay-stale",
        )
        assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
        unchanged = native_call(
            _arguments(sketch, expected=True, visible=True),
            call_id="rolling-arc-overlay-no-op",
        )
        assert unchanged["changed"] is False

        assert Gui.isCommandActive("Sketcher_ArcOverlay")
        Gui.runCommand("Sketcher_ArcOverlay")
        process_events(24)
        human_hidden = _information_switches(sketch)
        assert human_hidden == (coin.SO_SWITCH_NONE, coin.SO_SWITCH_NONE), human_hidden

        reshown = native_call(
            _arguments(sketch, expected=False, visible=True),
            call_id="rolling-arc-overlay-reshow",
        )
        process_events(24)
        assert reshown["changed"] is True
        assert _information_switches(sketch) == (
            coin.SO_SWITCH_ALL,
            coin.SO_SWITCH_ALL,
        )
        rehidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-arc-overlay-rehide",
        )
        process_events(24)
        assert rehidden["changed"] is True
        assert _information_switches(sketch) == (
            coin.SO_SWITCH_NONE,
            coin.SO_SWITCH_NONE,
        )

        assert _state(sketch) == before
        assert tuple(Gui.Selection.getSelectionEx(document.Name)) == selection_before
        assert int(document.UndoCount) == undo_before
        assert (
            int(document.getBookedTransactionID()),
            bool(document.HasPendingTransaction),
        ) == transaction_before
        assert edit_boundary(document, sketch, controller) == boundary
        return {"state": before, "arc_indices": (first, second)}
    finally:
        _restore_preference(original_preference)
        process_events(16)


def verify_reopened_arc_overlay(sketch: Any, expected: dict[str, Any]) -> None:
    assert _state(sketch) == expected["state"]
    assert expected["arc_indices"] == (0, 1)
    assert all(
        sketch.Geometry[index].TypeId == "Part::GeomArcOfCircle"
        for index in expected["arc_indices"]
    )
