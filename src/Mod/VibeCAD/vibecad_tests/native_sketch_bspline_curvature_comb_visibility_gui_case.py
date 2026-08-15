# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI parity coverage for B-spline curvature-comb visibility."""

from __future__ import annotations

from math import hypot
from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
from pivy import coin

from VibeCADNativeSketchPresentationState import (
    BSPLINE_CURVATURE_COMB_PREFERENCE,
    SKETCH_GENERAL_PREFERENCES,
)
from vibecad_tests.native_sketch_bspline_presentation_gui_support import (
    CUBIC_POLES,
    add_cubic_bspline,
)
from vibecad_tests.native_sketch_presentation_gui_support import (
    bspline_information_switches,
    polygon_switch_geometry,
    preference_snapshot,
    restore_preference,
    sketch_presentation_model_state,
    switch_states,
)


def _arguments(sketch: Any, *, expected: bool, visible: bool) -> dict[str, Any]:
    return {
        "operation": "bspline_curvature_comb",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": int(sketch.GeometryCount),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "expected_visible": expected,
        "visible": visible,
    }


def _assert_curvature_comb_geometry(nodes: tuple[Any, ...]) -> float:
    geometry = polygon_switch_geometry(nodes)
    assert len(geometry) == 1
    assert geometry[0]["vertex_counts"] == (2,) * 64 + (64,)
    points = geometry[0]["points"]
    assert len(points) == 192
    assert all(abs(point[2] - 0.004) <= 1.0e-8 for point in points), points[:4]
    for index in range(64):
        radial_end = points[2 * index + 1]
        spine_point = points[128 + index]
        assert all(
            abs(radial_end[axis] - spine_point[axis]) <= 1.0e-8 for axis in range(3)
        )
    curve_points = points[:128:2]
    assert (
        hypot(
            curve_points[0][0] - CUBIC_POLES[0][0],
            curve_points[0][1] - CUBIC_POLES[0][1],
        )
        <= 1.0e-4
    )
    assert (
        hypot(
            curve_points[-1][0] - CUBIC_POLES[-1][0],
            curve_points[-1][1] - CUBIC_POLES[-1][1],
        )
        <= 1.0e-4
    )
    radial_lengths = tuple(
        hypot(
            points[2 * index][0] - points[2 * index + 1][0],
            points[2 * index][1] - points[2 * index + 1][1],
        )
        for index in range(64)
    )
    maximum = max(radial_lengths)
    assert maximum > 1.0e-4
    return maximum


def exercise_bspline_curvature_comb_visibility_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    original = preference_snapshot(BSPLINE_CURVATURE_COMB_PREFERENCE, True)
    try:
        group = App.ParamGet(SKETCH_GENERAL_PREFERENCES)
        group.SetBool(BSPLINE_CURVATURE_COMB_PREFERENCE, True)
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
        maximum_radial = _assert_curvature_comb_geometry(layers["curvature_comb"])
        assert switch_states(layers["curvature_comb"]) == (coin.SO_SWITCH_ALL,)

        hidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-bspline-curvature-comb-hide",
        )
        process_events(24)
        assert hidden["changed"] is True
        assert hidden["previous_visible"] is True
        assert hidden["visible"] is False
        assert hidden["internal_b_spline_count"] == 1
        assert hidden["external_b_spline_count"] == 0
        layers = bspline_information_switches(sketch, spline)
        assert switch_states(layers["curvature_comb"]) == (coin.SO_SWITCH_NONE,)

        stale = native_call(
            _arguments(sketch, expected=True, visible=True),
            succeeds=False,
            call_id="rolling-bspline-curvature-comb-stale",
        )
        assert stale["error_code"] == "NATIVE_SKETCH_INVALID", stale
        unchanged = native_call(
            _arguments(sketch, expected=False, visible=False),
            call_id="rolling-bspline-curvature-comb-no-op",
        )
        assert unchanged["changed"] is False

        assert Gui.isCommandActive("Sketcher_BSplineComb")
        Gui.runCommand("Sketcher_BSplineComb")
        process_events(24)
        layers = bspline_information_switches(sketch, spline)
        _assert_curvature_comb_geometry(layers["curvature_comb"])
        assert switch_states(layers["curvature_comb"]) == (coin.SO_SWITCH_ALL,)

        rehidden = native_call(
            _arguments(sketch, expected=True, visible=False),
            call_id="rolling-bspline-curvature-comb-rehide",
        )
        process_events(24)
        assert rehidden["changed"] is True
        layers = bspline_information_switches(sketch, spline)
        assert switch_states(layers["curvature_comb"]) == (coin.SO_SWITCH_NONE,)

        reshown = native_call(
            _arguments(sketch, expected=False, visible=True),
            call_id="rolling-bspline-curvature-comb-reshow",
        )
        process_events(24)
        assert reshown["changed"] is True
        layers = bspline_information_switches(sketch, spline)
        _assert_curvature_comb_geometry(layers["curvature_comb"])
        assert switch_states(layers["curvature_comb"]) == (coin.SO_SWITCH_ALL,)

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
            "maximum_radial": maximum_radial,
        }
    finally:
        restore_preference(BSPLINE_CURVATURE_COMB_PREFERENCE, original)
        process_events(16)


def verify_reopened_bspline_curvature_comb_visibility(
    sketch: Any,
    expected: dict[str, Any],
) -> None:
    assert sketch_presentation_model_state(sketch) == expected["state"]
    spline = int(expected["spline_index"])
    assert spline == 0
    geometry = sketch.Geometry[spline]
    assert geometry.TypeId == "Part::GeomBSplineCurve"
    assert int(geometry.Degree) == 3
    assert float(expected["maximum_radial"]) > 1.0e-4
