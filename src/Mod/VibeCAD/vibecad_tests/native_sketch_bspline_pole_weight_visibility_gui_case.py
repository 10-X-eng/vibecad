# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI parity coverage for B-spline pole-weight visibility."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
from pivy import coin

from VibeCADNativeSketchPresentationState import (
    BSPLINE_POLE_WEIGHT_PREFERENCE,
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
        "operation": "bspline_pole_weight",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "expected_visible": expected,
        "visible": visible,
    }


def _assert_pole_weight_labels(
    nodes: tuple[Any, ...],
    weights: tuple[float, ...],
) -> None:
    assert len(nodes) == len(CUBIC_POLES) == len(weights)
    labels = text_switch_values(nodes)
    translations = text_switch_translations(nodes)
    for label, translation, pole, weight in zip(
        labels,
        translations,
        CUBIC_POLES,
        weights,
        strict=True,
    ):
        assert len(label) == 2 and label[0] == ""
        encoded = label[1]
        assert encoded.startswith("[") and encoded.endswith("]")
        number = encoded[1:-1]
        decimals = len(number.partition(".")[2])
        assert decimals >= 0
        tolerance = 0.5 * (10.0 ** (-decimals)) + 1.0e-12
        assert abs(float(number) - weight) <= tolerance
        assert abs(translation[0] - pole[0]) <= 1.0e-8
        assert abs(translation[1] - pole[1]) <= 1.0e-8
        assert abs(translation[2] - 0.004) <= 1.0e-8


def exercise_bspline_pole_weight_visibility_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    original = preference_snapshot(BSPLINE_POLE_WEIGHT_PREFERENCE, True)
    try:
        group = App.ParamGet(SKETCH_GENERAL_PREFERENCES)
        group.SetBool(BSPLINE_POLE_WEIGHT_PREFERENCE, True)
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
        weights = tuple(float(value) for value in sketch.Geometry[spline].getWeights())
        assert len(weights) == 4 and all(value > 0.0 for value in weights)
        layers = bspline_information_switches(sketch, spline)
        _assert_pole_weight_labels(layers["pole_weight"], weights)
        assert switch_states(layers["pole_weight"]) == (coin.SO_SWITCH_ALL,) * 4

        hidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-bspline-pole-weight-hide",
        )
        process_events(24)
        assert hidden["changed"] is True
        assert hidden["previous_visible"] is True
        assert hidden["visible"] is False
        assert hidden["internal_b_spline_count"] == 1
        assert hidden["external_b_spline_count"] == 0
        layers = bspline_information_switches(sketch, spline)
        _assert_pole_weight_labels(layers["pole_weight"], weights)
        assert switch_states(layers["pole_weight"]) == (coin.SO_SWITCH_NONE,) * 4

        stale = native_call(
            _arguments(sketch, expected=True, visible=True),
            succeeds=False,
            call_id="rolling-bspline-pole-weight-stale",
        )
        assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
        unchanged = native_call(
            _arguments(sketch, expected=False, visible=False),
            call_id="rolling-bspline-pole-weight-no-op",
        )
        assert unchanged["changed"] is False

        assert Gui.isCommandActive("Sketcher_BSplinePoleWeight")
        Gui.runCommand("Sketcher_BSplinePoleWeight")
        process_events(24)
        layers = bspline_information_switches(sketch, spline)
        _assert_pole_weight_labels(layers["pole_weight"], weights)
        assert switch_states(layers["pole_weight"]) == (coin.SO_SWITCH_ALL,) * 4

        rehidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-bspline-pole-weight-rehide",
        )
        process_events(24)
        assert rehidden["changed"] is True
        layers = bspline_information_switches(sketch, spline)
        assert switch_states(layers["pole_weight"]) == (coin.SO_SWITCH_NONE,) * 4

        reshown = native_call(
            _arguments(sketch, expected=False, visible=True),
            call_id="rolling-bspline-pole-weight-reshow",
        )
        process_events(24)
        assert reshown["changed"] is True
        layers = bspline_information_switches(sketch, spline)
        _assert_pole_weight_labels(layers["pole_weight"], weights)
        assert switch_states(layers["pole_weight"]) == (coin.SO_SWITCH_ALL,) * 4

        assert sketch_presentation_model_state(sketch) == before
        assert tuple(Gui.Selection.getSelectionEx(document.Name)) == selection_before
        assert int(document.UndoCount) == undo_before
        assert (
            int(document.getBookedTransactionID()),
            bool(document.HasPendingTransaction),
        ) == transaction_before
        assert edit_boundary(document, sketch, controller) == boundary
        return {"state": before, "spline_index": spline, "weights": weights}
    finally:
        restore_preference(BSPLINE_POLE_WEIGHT_PREFERENCE, original)
        process_events(16)


def verify_reopened_bspline_pole_weight_visibility(
    sketch: Any,
    expected: dict[str, Any],
) -> None:
    assert sketch_presentation_model_state(sketch) == expected["state"]
    spline = int(expected["spline_index"])
    assert spline == 0
    geometry = sketch.Geometry[spline]
    assert geometry.TypeId == "Part::GeomBSplineCurve"
    weights = tuple(float(value) for value in geometry.getWeights())
    assert len(weights) == len(expected["weights"])
    assert all(
        abs(actual - wanted) <= 1.0e-10
        for actual, wanted in zip(weights, expected["weights"], strict=True)
    )
