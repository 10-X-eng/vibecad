# SPDX-License-Identifier: LGPL-2.1-or-later

"""Durable-state verification for the rolling Native Sketch GUI gate."""

from __future__ import annotations

from typing import Any, Callable

import FreeCADGui as Gui

from VibeCADEditState import active_edit_object
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_active_gui_case import verify_reopened_active
from vibecad_tests.native_sketch_block_gui_case import verify_reopened_block
from vibecad_tests.native_sketch_carbon_copy_gui_case import (
    verify_reopened_carbon_copy,
)
from vibecad_tests.native_sketch_chamfer_gui_case import verify_reopened_chamfer
from vibecad_tests.native_sketch_driving_gui_case import verify_reopened_driving
from vibecad_tests.native_sketch_equal_gui_case import verify_reopened_equal
from vibecad_tests.native_sketch_extend_gui_case import verify_reopened_extend
from vibecad_tests.native_sketch_fillet_gui_case import verify_reopened_fillet
from vibecad_tests.native_sketch_geometry_reopen_gui_case import (
    verify_reopened_geometry_cases,
)
from vibecad_tests.native_sketch_geometry_gui_support import process_events
from vibecad_tests.native_sketch_group_gui_case import verify_reopened_group
from vibecad_tests.native_sketch_intersection_gui_case import (
    verify_reopened_intersection,
)
from vibecad_tests.native_sketch_projection_gui_case import verify_reopened_projection
from vibecad_tests.native_sketch_rolling_context import reopen_separate_sketch_case
from vibecad_tests.native_sketch_symmetric_gui_case import verify_reopened_symmetric
from vibecad_tests.native_sketch_split_gui_case import verify_reopened_split
from vibecad_tests.native_sketch_trim_gui_case import verify_reopened_trim
from vibecad_tests.native_sketch_translate_gui_case import verify_reopened_translate
from vibecad_tests.native_sketch_rotate_gui_case import verify_reopened_rotate
from vibecad_tests.native_sketch_scale_gui_case import verify_reopened_scale
from vibecad_tests.native_sketch_offset_gui_case import verify_reopened_offset
from vibecad_tests.native_sketch_symmetry_transform_gui_case import (
    verify_reopened_symmetry_transform,
)
from vibecad_tests.native_sketch_axis_alignment_gui_case import (
    verify_reopened_axis_alignment,
)
from vibecad_tests.native_sketch_nurbs_conversion_gui_case import (
    verify_reopened_nurbs_conversion,
)
from vibecad_tests.native_sketch_bspline_degree_gui_case import (
    verify_reopened_bspline_degree,
)
from vibecad_tests.native_sketch_bspline_degree_decrease_gui_case import (
    verify_reopened_bspline_degree_decrease,
)
from vibecad_tests.native_sketch_bspline_knot_multiplicity_increase_gui_case import (
    verify_reopened_bspline_knot_multiplicity_increase,
)
from vibecad_tests.native_sketch_bspline_knot_multiplicity_decrease_gui_case import (
    verify_reopened_bspline_knot_multiplicity_decrease,
)
from vibecad_tests.native_sketch_bspline_knot_insertion_gui_case import (
    verify_reopened_bspline_knot_insertion,
)
from vibecad_tests.native_sketch_join_gui_case import verify_reopened_join
from vibecad_tests.native_sketch_inspect_gui_case import verify_reopened_inspect
from vibecad_tests.native_sketch_arc_overlay_gui_case import (
    verify_reopened_arc_overlay,
)
from vibecad_tests.native_sketch_bspline_degree_visibility_gui_case import (
    verify_reopened_bspline_degree_visibility,
)
from vibecad_tests.native_sketch_bspline_control_polygon_visibility_gui_case import (
    verify_reopened_bspline_control_polygon_visibility,
)
from vibecad_tests.native_sketch_bspline_curvature_comb_visibility_gui_case import (
    verify_reopened_bspline_curvature_comb_visibility,
)
from vibecad_tests.native_sketch_bspline_knot_multiplicity_visibility_gui_case import (
    verify_reopened_bspline_knot_multiplicity_visibility,
)
from vibecad_tests.native_sketch_bspline_pole_weight_visibility_gui_case import (
    verify_reopened_bspline_pole_weight_visibility,
)
from vibecad_tests.native_sketch_internal_alignment_gui_case import (
    verify_reopened_internal_alignment,
)
from vibecad_tests.native_sketch_virtual_space_gui_case import (
    verify_reopened_virtual_space,
)


def _verify_reopened_view_actions(sketch: Any, expected: dict[str, Any]) -> None:
    assert int(sketch.GeometryCount) == expected["geometry_count"]
    assert int(sketch.ConstraintCount) == expected["constraint_count"]
    assert bool(sketch.ViewObject.SectionView) is expected["section_visible"]
    assert bool(sketch.isValid())


_CASE_VERIFIERS: dict[str, Callable[[Any, dict[str, Any]], None]] = {
    "EqualSketch": verify_reopened_equal,
    "SymmetricSketch": verify_reopened_symmetric,
    "BlockSketch": verify_reopened_block,
    "GroupSketch": verify_reopened_group,
    "DrivingSketch": verify_reopened_driving,
    "ActiveSketch": verify_reopened_active,
    "FilletSketch": verify_reopened_fillet,
    "ChamferSketch": verify_reopened_chamfer,
    "TrimSketch": verify_reopened_trim,
    "SplitSketch": verify_reopened_split,
    "ExtendSketch": verify_reopened_extend,
    "ProjectionSketch": verify_reopened_projection,
    "IntersectionSketch": verify_reopened_intersection,
    "CarbonCopySketch": verify_reopened_carbon_copy,
    "TranslateSketch": verify_reopened_translate,
    "RotateSketch": verify_reopened_rotate,
    "ScaleSketch": verify_reopened_scale,
    "OffsetSketch": verify_reopened_offset,
    "SymmetrySketch": verify_reopened_symmetry_transform,
    "AxisAlignmentSketch": verify_reopened_axis_alignment,
    "NURBSConversionSketch": verify_reopened_nurbs_conversion,
    "BSplineDegreeSketch": verify_reopened_bspline_degree,
    "BSplineDegreeDecreaseSketch": verify_reopened_bspline_degree_decrease,
    "BSplineKnotMultiplicityIncreaseSketch": (
        verify_reopened_bspline_knot_multiplicity_increase
    ),
    "BSplineKnotMultiplicityDecreaseSketch": (
        verify_reopened_bspline_knot_multiplicity_decrease
    ),
    "BSplineKnotInsertionSketch": verify_reopened_bspline_knot_insertion,
    "JoinSketch": verify_reopened_join,
    "InspectSketch": verify_reopened_inspect,
    "ArcOverlaySketch": verify_reopened_arc_overlay,
    "BSplineDegreeViewSketch": verify_reopened_bspline_degree_visibility,
    "BSplineControlPolygonViewSketch": (
        verify_reopened_bspline_control_polygon_visibility
    ),
    "BSplineCurvatureCombViewSketch": (
        verify_reopened_bspline_curvature_comb_visibility
    ),
    "BSplineKnotMultiplicityViewSketch": (
        verify_reopened_bspline_knot_multiplicity_visibility
    ),
    "BSplinePoleWeightViewSketch": verify_reopened_bspline_pole_weight_visibility,
    "InternalAlignmentSketch": verify_reopened_internal_alignment,
    "VirtualSpaceSketch": verify_reopened_virtual_space,
    "ViewActionsSketch": _verify_reopened_view_actions,
}


def verify_rolling_reopen(
    document: Any,
    controller: Any,
    *,
    main_sketch_name: str,
    main_state: dict[str, Any],
    separate_states: dict[str, dict[str, Any]],
) -> None:
    """Verify every rolling sketch after one shared FCStd save/reopen cycle."""

    assert set(separate_states) == set(_CASE_VERIFIERS), separate_states

    sketch = document.getObject(main_sketch_name)
    assert sketch is not None
    assert Gui.activeDocument().setEdit(sketch.Name)
    process_events(24)
    assert read_active_ribbon_surface(controller).surface_id == "sketch.edit"
    assert active_edit_object() is sketch
    assert int(sketch.GeometryCount) == 240
    assert int(sketch.ConstraintCount) == 291
    verify_reopened_geometry_cases(sketch, main_state)

    for name, verifier in _CASE_VERIFIERS.items():
        separate = reopen_separate_sketch_case(document, controller, name)
        if name == "EqualSketch":
            assert int(separate.GeometryCount) == 16
            assert int(separate.ConstraintCount) == 11
        elif name == "SymmetricSketch":
            assert int(separate.GeometryCount) == 21
            assert int(separate.ConstraintCount) == 11
        verifier(separate, separate_states[name])

    reopened_surface = read_active_ribbon_surface(controller)
    for action_id in (
        "Sketcher_Translate",
        "Sketcher_Rotate",
        "Sketcher_Scale",
        "Sketcher_Offset",
        "Sketcher_Symmetry",
        "Sketcher_RemoveAxesAlignment",
        "Sketcher_BSplineConvertToNURBS",
        "Sketcher_BSplineIncreaseDegree",
        "Sketcher_BSplineDecreaseDegree",
        "Sketcher_BSplineIncreaseKnotMultiplicity",
        "Sketcher_BSplineInsertKnot",
        "Sketcher_RestoreInternalAlignmentGeometry",
        "Sketcher_SwitchVirtualSpace",
        "Sketcher_ViewSketch",
        "Sketcher_ViewSection",
    ):
        assert action_id in reopened_surface.command_ids
    reopened_production = resolve_native_provider_surface(
        reopened_surface,
        build_native_capability_registry(),
    )
    assert reopened_production.available is True
    assert reopened_production.missing_action_ids == ()
    assert reopened_production.incomplete_definition_names == ()
    assert reopened_production.tool_names
    assert reopened_production.schemas
