# SPDX-License-Identifier: LGPL-2.1-or-later

"""Frozen provider-turn fixture for Native Sketch GUI integration gates."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeSketchConstraintSchema import (
    sketch_constraint_capability_definition,
)
from VibeCADNativeSketchCleanupSchema import sketch_cleanup_capability_definitions
from VibeCADNativeSketchControlSchema import sketch_control_capability_definition
from VibeCADNativeSketchGeometrySchema import sketch_geometry_capability_definition
from VibeCADNativeSketchInspectSchema import sketch_inspect_capability_definition
from VibeCADNativeSketchPresentationSchema import (
    sketch_presentation_capability_definition,
)
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot


def provider_turn(surface) -> NativeTurnSnapshot:
    geometry = sketch_geometry_capability_definition()
    cleanup = sketch_cleanup_capability_definitions()
    constraint = sketch_constraint_capability_definition()
    control = sketch_control_capability_definition()
    inspect = sketch_inspect_capability_definition()
    presentation = sketch_presentation_capability_definition()
    provider = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=(
            geometry.name,
            *(definition.name for definition in cleanup),
            constraint.name,
            control.name,
            inspect.name,
            presentation.name,
        ),
        schemas=(
            geometry.provider_schema(
                (
                    "create_point",
                    "create_line",
                    "create_polyline",
                    "create_arc",
                    "create3_point_arc",
                    "create_arc_of_ellipse",
                    "create_arc_of_hyperbola",
                    "create_arc_of_parabola",
                    "create_circle",
                    "create3_point_circle",
                    "create_ellipse",
                    "create3_point_ellipse",
                    "create_rectangle",
                    "create_center_rectangle",
                    "create_oblong",
                    "create_triangle",
                    "create_square",
                    "create_pentagon",
                    "create_hexagon",
                    "create_heptagon",
                    "create_octagon",
                    "create_regular_polygon",
                    "create_slot",
                    "create_arc_slot",
                    "create_b_spline",
                    "create_periodic_b_spline",
                    "create_b_spline_by_interpolation",
                    "create_periodic_b_spline_by_interpolation",
                    "create_text",
                    "toggle_construction",
                    "create_fillet",
                    "create_chamfer",
                    "project_external_geometry",
                    "intersect_external_geometry",
                    "carbon_copy",
                    "translate",
                    "rotate",
                    "scale",
                    "offset",
                    "symmetry",
                    "remove_axis_alignment",
                    "convert_to_nurbs",
                    "increase_bspline_degree",
                    "decrease_bspline_degree",
                    "increase_bspline_knot_multiplicity",
                    "decrease_bspline_knot_multiplicity",
                    "insert_bspline_knot",
                    "join_curves",
                    "restore_internal_alignment_geometry",
                )
            ),
            *(
                definition.provider_schema(
                    tuple(variant.operation for variant in definition.variants)
                )
                for definition in cleanup
            ),
            constraint.provider_schema(
                (
                    "infer_dimension",
                    "constrain_distance_x",
                    "constrain_distance_y",
                    "constrain_distance",
                    "constrain_radius_diameter",
                    "constrain_radius",
                    "constrain_diameter",
                    "constrain_angle",
                    "constrain_lock",
                    "constrain_coincident",
                    "constrain_horizontal_vertical",
                    "constrain_horizontal",
                    "constrain_vertical",
                    "constrain_parallel",
                    "constrain_perpendicular",
                    "constrain_tangent",
                    "constrain_equal",
                    "constrain_symmetric",
                    "constrain_block",
                    "constrain_group",
                    "toggle_driving_reference",
                    "toggle_active_inactive",
                    "set_virtual_space",
                )
            ),
            control.provider_schema(("leave",)),
            inspect.provider_schema(("select_constraints", "select_elements")),
            presentation.provider_schema(
                (
                    "align_view_to_sketch",
                    "section_view",
                    "arc_overlay",
                    "bspline_degree",
                    "bspline_control_polygon",
                    "bspline_curvature_comb",
                    "bspline_knot_multiplicity",
                    "bspline_pole_weight",
                )
            ),
        ),
        human_only_action_ids=("Sketcher_CancelSketch",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider)
