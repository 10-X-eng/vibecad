# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI parity coverage for B-spline knot-label visibility."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
from pivy import coin

from VibeCADNativeSketchPresentationState import (
    BSPLINE_KNOT_MULTIPLICITY_PREFERENCE,
    SKETCH_GENERAL_PREFERENCES,
)
from vibecad_tests.native_sketch_bspline_presentation_gui_support import (
    CUBIC_POLES,
    add_cubic_bspline,
)
from vibecad_tests.native_sketch_presentation_gui_support import (
    bspline_information_switches,
    preference_snapshot,
    restore_preference,
    sketch_presentation_model_state,
    switch_states,
    text_switch_translations,
    text_switch_values,
)


def _arguments(sketch: Any, *, expected: bool, visible: bool) -> dict[str, Any]:
    return {
        "operation": "bspline_knot_multiplicity",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "expected_visible": expected,
        "visible": visible,
    }


def _assert_knot_labels(nodes: tuple[Any, ...]) -> None:
    assert len(nodes) == 2
    assert text_switch_values(nodes) == (("(4)",), ("(4)",))
    translations = text_switch_translations(nodes)
    assert len(translations) == 2
    for translation, pole in zip(
        translations,
        (CUBIC_POLES[0], CUBIC_POLES[-1]),
        strict=True,
    ):
        assert abs(translation[0] - pole[0]) <= 1.0e-8
        assert abs(translation[1] - pole[1]) <= 1.0e-8
        assert abs(translation[2] - 0.004) <= 1.0e-8


def exercise_bspline_knot_multiplicity_visibility_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    original = preference_snapshot(BSPLINE_KNOT_MULTIPLICITY_PREFERENCE, True)
    try:
        group = App.ParamGet(SKETCH_GENERAL_PREFERENCES)
        group.SetBool(BSPLINE_KNOT_MULTIPLICITY_PREFERENCE, True)
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
        geometry = sketch.Geometry[spline]
        assert tuple(int(value) for value in geometry.getMultiplicities()) == (4, 4)
        assert tuple(float(value) for value in geometry.getKnots()) == (0.0, 1.0)
        layers = bspline_information_switches(sketch, spline)
        _assert_knot_labels(layers["knot_multiplicity"])
        assert switch_states(layers["knot_multiplicity"]) == (
            coin.SO_SWITCH_ALL,
            coin.SO_SWITCH_ALL,
        )

        hidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-bspline-knot-multiplicity-hide",
        )
        process_events(24)
        assert hidden["changed"] is True
        assert hidden["previous_visible"] is True
        assert hidden["visible"] is False
        assert hidden["internal_b_spline_count"] == 1
        assert hidden["external_b_spline_count"] == 0
        layers = bspline_information_switches(sketch, spline)
        _assert_knot_labels(layers["knot_multiplicity"])
        assert switch_states(layers["knot_multiplicity"]) == (
            coin.SO_SWITCH_NONE,
            coin.SO_SWITCH_NONE,
        )

        stale = native_call(
            _arguments(sketch, expected=True, visible=True),
            succeeds=False,
            call_id="rolling-bspline-knot-multiplicity-stale",
        )
        assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
        unchanged = native_call(
            _arguments(sketch, expected=False, visible=False),
            call_id="rolling-bspline-knot-multiplicity-no-op",
        )
        assert unchanged["changed"] is False

        assert Gui.isCommandActive("Sketcher_BSplineKnotMultiplicity")
        Gui.runCommand("Sketcher_BSplineKnotMultiplicity")
        process_events(24)
        layers = bspline_information_switches(sketch, spline)
        _assert_knot_labels(layers["knot_multiplicity"])
        assert switch_states(layers["knot_multiplicity"]) == (
            coin.SO_SWITCH_ALL,
            coin.SO_SWITCH_ALL,
        )

        rehidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-bspline-knot-multiplicity-rehide",
        )
        process_events(24)
        assert rehidden["changed"] is True
        layers = bspline_information_switches(sketch, spline)
        assert switch_states(layers["knot_multiplicity"]) == (
            coin.SO_SWITCH_NONE,
            coin.SO_SWITCH_NONE,
        )

        reshown = native_call(
            _arguments(sketch, expected=False, visible=True),
            call_id="rolling-bspline-knot-multiplicity-reshow",
        )
        process_events(24)
        assert reshown["changed"] is True
        layers = bspline_information_switches(sketch, spline)
        _assert_knot_labels(layers["knot_multiplicity"])
        assert switch_states(layers["knot_multiplicity"]) == (
            coin.SO_SWITCH_ALL,
            coin.SO_SWITCH_ALL,
        )

        assert sketch_presentation_model_state(sketch) == before
        assert tuple(Gui.Selection.getSelectionEx(document.Name)) == selection_before
        assert int(document.UndoCount) == undo_before
        assert (
            int(document.getBookedTransactionID()),
            bool(document.HasPendingTransaction),
        ) == transaction_before
        assert edit_boundary(document, sketch, controller) == boundary
        return {
            "state": before,
            "spline_index": spline,
            "knots": (0.0, 1.0),
            "multiplicities": (4, 4),
        }
    finally:
        restore_preference(BSPLINE_KNOT_MULTIPLICITY_PREFERENCE, original)
        process_events(16)


def verify_reopened_bspline_knot_multiplicity_visibility(
    sketch: Any,
    expected: dict[str, Any],
) -> None:
    assert sketch_presentation_model_state(sketch) == expected["state"]
    spline = int(expected["spline_index"])
    assert spline == 0
    geometry = sketch.Geometry[spline]
    assert geometry.TypeId == "Part::GeomBSplineCurve"
    assert tuple(float(value) for value in geometry.getKnots()) == tuple(
        expected["knots"]
    )
    assert tuple(int(value) for value in geometry.getMultiplicities()) == tuple(
        expected["multiplicities"]
    )
