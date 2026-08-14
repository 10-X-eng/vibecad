# SPDX-License-Identifier: LGPL-2.1-or-later

"""Separate-sketch cases for the rolling Native Sketch GUI lifecycle gate."""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
import Part
import SketcherGui

from typing import Any, Callable

import VibeCADNativeSketchConstraintRuntime as ConstraintRuntimeModule
import VibeCADNativeSketchGeometryRuntime as GeometryRuntimeModule
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeSketchInternalAlignmentTarget import OPERATION
from vibecad_tests.native_sketch_active_gui_case import exercise_active_case
from vibecad_tests.native_sketch_block_gui_case import exercise_block_case
from vibecad_tests.native_sketch_carbon_copy_gui_case import exercise_carbon_copy_case
from vibecad_tests.native_sketch_chamfer_gui_case import exercise_chamfer_case
from vibecad_tests.native_sketch_driving_gui_case import exercise_driving_case
from vibecad_tests.native_sketch_delete_geometry_gui_case import (
    exercise_delete_geometry_case,
)
from vibecad_tests.native_sketch_equal_gui_case import exercise_equal_case
from vibecad_tests.native_sketch_extend_gui_case import exercise_extend_case
from vibecad_tests.native_sketch_fillet_gui_case import exercise_fillet_case
from vibecad_tests.native_sketch_group_gui_case import exercise_group_case
from vibecad_tests.native_sketch_intersection_gui_case import exercise_intersection_case
from vibecad_tests.native_sketch_projection_gui_case import exercise_projection_case
from vibecad_tests.native_sketch_rolling_context import open_separate_sketch_case
from vibecad_tests.native_sketch_symmetric_gui_case import exercise_symmetric_case
from vibecad_tests.native_sketch_split_gui_case import exercise_split_case
from vibecad_tests.native_sketch_trim_gui_case import exercise_trim_case
from vibecad_tests.native_sketch_translate_gui_case import exercise_translate_case
from vibecad_tests.native_sketch_rotate_gui_case import exercise_rotate_case
from vibecad_tests.native_sketch_scale_gui_case import exercise_scale_case
from vibecad_tests.native_sketch_offset_gui_case import exercise_offset_case
from vibecad_tests.native_sketch_symmetry_transform_gui_case import (
    exercise_symmetry_transform_case,
)
from vibecad_tests.native_sketch_axis_alignment_gui_case import (
    exercise_axis_alignment_case,
)
from vibecad_tests.native_sketch_nurbs_conversion_gui_case import (
    exercise_nurbs_conversion_case,
)
from vibecad_tests.native_sketch_bspline_degree_gui_case import (
    exercise_bspline_degree_case,
)
from vibecad_tests.native_sketch_bspline_degree_decrease_gui_case import (
    exercise_bspline_degree_decrease_case,
)
from vibecad_tests.native_sketch_bspline_knot_multiplicity_increase_gui_case import (
    exercise_bspline_knot_multiplicity_increase_case,
)
from vibecad_tests.native_sketch_bspline_knot_multiplicity_decrease_gui_case import (
    exercise_bspline_knot_multiplicity_decrease_case,
)
from vibecad_tests.native_sketch_bspline_knot_insertion_gui_case import (
    exercise_bspline_knot_insertion_case,
)
from vibecad_tests.native_sketch_join_gui_case import exercise_join_case
from vibecad_tests.native_sketch_inspect_gui_case import exercise_inspect_case
from vibecad_tests.native_sketch_arc_overlay_gui_case import exercise_arc_overlay_case
from vibecad_tests.native_sketch_bspline_degree_visibility_gui_case import (
    exercise_bspline_degree_visibility_case,
)
from vibecad_tests.native_sketch_bspline_control_polygon_visibility_gui_case import (
    exercise_bspline_control_polygon_visibility_case,
)
from vibecad_tests.native_sketch_bspline_curvature_comb_visibility_gui_case import (
    exercise_bspline_curvature_comb_visibility_case,
)
from vibecad_tests.native_sketch_bspline_knot_multiplicity_visibility_gui_case import (
    exercise_bspline_knot_multiplicity_visibility_case,
)
from vibecad_tests.native_sketch_bspline_pole_weight_visibility_gui_case import (
    exercise_bspline_pole_weight_visibility_case,
)
from vibecad_tests.native_sketch_internal_alignment_gui_case import (
    exercise_internal_alignment_case,
)
from vibecad_tests.native_sketch_virtual_space_gui_case import (
    exercise_virtual_space_case,
)


def _exercise_internal_alignment_case(**kwargs) -> dict[str, Any]:
    def install_failing_verifier():
        handlers = GeometryRuntimeModule._OPERATIONS[OPERATION]

        def fail(_document, _draft):
            raise RuntimeError("forced internal-geometry postcondition failure")

        GeometryRuntimeModule._OPERATIONS[OPERATION] = (*handlers[:3], fail, handlers[4])

        def restore() -> None:
            GeometryRuntimeModule._OPERATIONS[OPERATION] = handlers

        return restore

    return exercise_internal_alignment_case(
        **kwargs,
        install_failing_verifier=install_failing_verifier,
    )


def _exercise_virtual_space_case(*, sketch, document, **kwargs) -> dict[str, Any]:
    assert sketch.addGeometry(
        Part.LineSegment(App.Vector(-5, 0), App.Vector(5, 0)),
        False,
    ) == 0
    document.recompute()

    def install_failing_verifier():
        verifier = ConstraintRuntimeModule.verify_sketch_virtual_space_constraints

        def fail(_document, _draft):
            raise RuntimeError("forced virtual-space postcondition failure")

        ConstraintRuntimeModule.verify_sketch_virtual_space_constraints = fail

        def restore() -> None:
            ConstraintRuntimeModule.verify_sketch_virtual_space_constraints = verifier

        return restore

    return exercise_virtual_space_case(
        sketch=sketch,
        document=document,
        **kwargs,
        read_view=SketcherGui.getActiveSketchVirtualSpace,
        install_failing_verifier=install_failing_verifier,
    )


def _exercise_view_actions_case(
    *,
    sketch,
    document,
    native_call,
    process_events,
    edit_boundary,
    boundary,
    controller,
) -> dict[str, Any]:
    base = {
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": 0,
        "expected_constraint_count": 0,
        "expected_external_geometry_count": 0,
    }
    document.clearUndos()
    undo_before = int(document.UndoCount)
    Gui.activeDocument().activeView().viewAxonometric()
    process_events(12)
    aligned = native_call({"operation": "align_view_to_sketch", **base})
    assert aligned["changed"] is True
    assert len(aligned["camera_orientation_xyzw"]) == 4
    assert int(document.UndoCount) == undo_before

    before = bool(sketch.ViewObject.SectionView)
    shown = native_call(
        {
            "operation": "section_view",
            **base,
            "expected_visible": before,
            "visible": not before,
        }
    )
    assert shown["changed"] is True
    assert bool(sketch.ViewObject.SectionView) is not before
    stale = native_call(
        {
            "operation": "section_view",
            **base,
            "expected_visible": before,
            "visible": before,
        },
        succeeds=False,
    )
    assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
    restored = native_call(
        {
            "operation": "section_view",
            **base,
            "expected_visible": not before,
            "visible": before,
        }
    )
    assert restored["changed"] is True
    assert bool(sketch.ViewObject.SectionView) is before
    assert int(document.UndoCount) == undo_before
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "geometry_count": 0,
        "constraint_count": 0,
        "section_visible": before,
    }


_CASES = (
    ("EqualSketch", exercise_equal_case),
    ("SymmetricSketch", exercise_symmetric_case),
    ("BlockSketch", exercise_block_case),
    ("GroupSketch", exercise_group_case),
    ("DrivingSketch", exercise_driving_case),
    ("ActiveSketch", exercise_active_case),
    ("FilletSketch", exercise_fillet_case),
    ("ChamferSketch", exercise_chamfer_case),
    ("TrimSketch", exercise_trim_case),
    ("SplitSketch", exercise_split_case),
    ("ExtendSketch", exercise_extend_case),
    ("DeleteGeometrySketch", exercise_delete_geometry_case),
    ("ProjectionSketch", exercise_projection_case),
    ("IntersectionSketch", exercise_intersection_case),
    ("CarbonCopySketch", exercise_carbon_copy_case),
    ("TranslateSketch", exercise_translate_case),
    ("RotateSketch", exercise_rotate_case),
    ("ScaleSketch", exercise_scale_case),
    ("OffsetSketch", exercise_offset_case),
    ("SymmetrySketch", exercise_symmetry_transform_case),
    ("AxisAlignmentSketch", exercise_axis_alignment_case),
    ("NURBSConversionSketch", exercise_nurbs_conversion_case),
    ("BSplineDegreeSketch", exercise_bspline_degree_case),
    ("BSplineDegreeDecreaseSketch", exercise_bspline_degree_decrease_case),
    (
        "BSplineKnotMultiplicityIncreaseSketch",
        exercise_bspline_knot_multiplicity_increase_case,
    ),
    (
        "BSplineKnotMultiplicityDecreaseSketch",
        exercise_bspline_knot_multiplicity_decrease_case,
    ),
    ("BSplineKnotInsertionSketch", exercise_bspline_knot_insertion_case),
    ("JoinSketch", exercise_join_case),
    ("InspectSketch", exercise_inspect_case),
    ("ArcOverlaySketch", exercise_arc_overlay_case),
    ("BSplineDegreeViewSketch", exercise_bspline_degree_visibility_case),
    (
        "BSplineControlPolygonViewSketch",
        exercise_bspline_control_polygon_visibility_case,
    ),
    (
        "BSplineCurvatureCombViewSketch",
        exercise_bspline_curvature_comb_visibility_case,
    ),
    (
        "BSplineKnotMultiplicityViewSketch",
        exercise_bspline_knot_multiplicity_visibility_case,
    ),
    (
        "BSplinePoleWeightViewSketch",
        exercise_bspline_pole_weight_visibility_case,
    ),
    ("InternalAlignmentSketch", _exercise_internal_alignment_case),
    ("VirtualSpaceSketch", _exercise_virtual_space_case),
    ("ViewActionsSketch", _exercise_view_actions_case),
)


def exercise_separate_sketch_cases(
    *,
    document: Any,
    controller: Any,
    active_call_state: dict[str, Any],
    dispatcher_for_surface: Callable[[Any], Any],
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
) -> dict[str, dict[str, Any]]:
    results = {}
    for name, exercise in _CASES:
        sketch, surface, boundary = open_separate_sketch_case(
            document,
            controller,
            name,
        )
        active_call_state.update(
            frozen_surface=NativeSurfaceSnapshot.from_surface(surface),
            dispatcher=dispatcher_for_surface(surface),
            sketch=sketch,
            boundary=boundary,
        )
        results[name] = exercise(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=process_events,
            edit_boundary=edit_boundary,
            boundary=boundary,
            controller=controller,
        )
    return results
