# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI parity coverage for B-spline degree-information visibility."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
from pivy import coin

from VibeCADNativeSketchPresentationState import (
    BSPLINE_DEGREE_PREFERENCE,
    SKETCH_GENERAL_PREFERENCES,
)
from vibecad_tests.native_sketch_presentation_gui_support import (
    bspline_information_switches,
    preference_snapshot,
    restore_preference,
    sketch_presentation_model_state,
    switch_states,
    text_switch_values,
)
from vibecad_tests.native_sketch_bspline_presentation_gui_support import (
    add_cubic_bspline,
)


def _arguments(sketch: Any, *, expected: bool, visible: bool) -> dict[str, Any]:
    return {
        "operation": "bspline_degree",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "expected_visible": expected,
        "visible": visible,
    }


def exercise_bspline_degree_visibility_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    original_preference = preference_snapshot(BSPLINE_DEGREE_PREFERENCE, True)
    try:
        group = App.ParamGet(SKETCH_GENERAL_PREFERENCES)
        group.SetBool(BSPLINE_DEGREE_PREFERENCE, True)
        process_events(16)
        spline = add_cubic_bspline(sketch)
        assert spline == 0
        document.recompute()
        process_events(32)
        document.clearUndos()

        before = sketch_presentation_model_state(sketch)
        selection_before = tuple(Gui.Selection.getSelectionEx(document.Name))
        undo_before = int(document.UndoCount)
        transaction_before = (
            int(document.getBookedTransactionID()),
            bool(document.HasPendingTransaction),
        )
        layers = bspline_information_switches(sketch, spline)
        assert text_switch_values(layers["degree"]) == (("3",),)
        assert switch_states(layers["degree"]) == (coin.SO_SWITCH_ALL,)

        hidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-bspline-degree-visibility-hide",
        )
        process_events(24)
        assert hidden["changed"] is True
        assert hidden["previous_visible"] is True
        assert hidden["visible"] is False
        assert hidden["internal_b_spline_count"] == 1
        assert hidden["external_b_spline_count"] == 0
        layers = bspline_information_switches(sketch, spline)
        assert text_switch_values(layers["degree"]) == (("3",),)
        assert switch_states(layers["degree"]) == (coin.SO_SWITCH_NONE,)

        stale = native_call(
            _arguments(sketch, expected=True, visible=True),
            succeeds=False,
            call_id="rolling-bspline-degree-visibility-stale",
        )
        assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
        unchanged = native_call(
            _arguments(sketch, expected=False, visible=False),
            call_id="rolling-bspline-degree-visibility-no-op",
        )
        assert unchanged["changed"] is False

        assert Gui.isCommandActive("Sketcher_BSplineDegree")
        Gui.runCommand("Sketcher_BSplineDegree")
        process_events(24)
        layers = bspline_information_switches(sketch, spline)
        assert switch_states(layers["degree"]) == (coin.SO_SWITCH_ALL,)

        rehidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-bspline-degree-visibility-rehide",
        )
        process_events(24)
        assert rehidden["changed"] is True
        layers = bspline_information_switches(sketch, spline)
        assert switch_states(layers["degree"]) == (coin.SO_SWITCH_NONE,)

        reshown = native_call(
            _arguments(sketch, expected=False, visible=True),
            call_id="rolling-bspline-degree-visibility-reshow",
        )
        process_events(24)
        assert reshown["changed"] is True
        layers = bspline_information_switches(sketch, spline)
        assert switch_states(layers["degree"]) == (coin.SO_SWITCH_ALL,)

        assert sketch_presentation_model_state(sketch) == before
        assert tuple(Gui.Selection.getSelectionEx(document.Name)) == selection_before
        assert int(document.UndoCount) == undo_before
        assert (
            int(document.getBookedTransactionID()),
            bool(document.HasPendingTransaction),
        ) == transaction_before
        assert edit_boundary(document, sketch, controller) == boundary
        return {"state": before, "spline_index": spline, "degree": 3}
    finally:
        restore_preference(BSPLINE_DEGREE_PREFERENCE, original_preference)
        process_events(16)


def verify_reopened_bspline_degree_visibility(
    sketch: Any,
    expected: dict[str, Any],
) -> None:
    assert sketch_presentation_model_state(sketch) == expected["state"]
    spline = int(expected["spline_index"])
    assert spline == 0
    geometry = sketch.Geometry[spline]
    assert geometry.TypeId == "Part::GeomBSplineCurve"
    assert int(geometry.Degree) == expected["degree"]
