# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for contextual Sketch geometry."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchArbitraryRegularPolygon import (
    create_sketch_arbitrary_regular_polygon,
    preflight_sketch_arbitrary_regular_polygon,
    prepare_sketch_arbitrary_regular_polygon,
    verify_sketch_arbitrary_regular_polygon,
)
from VibeCADNativeSketchEllipticalArc import (
    create_sketch_elliptical_arc,
    preflight_sketch_elliptical_arc,
    prepare_sketch_elliptical_arc,
    verify_sketch_elliptical_arc,
)
from VibeCADNativeSketchEllipse import (
    create_sketch_ellipse,
    preflight_sketch_ellipse,
    prepare_sketch_ellipse,
    verify_sketch_ellipse,
)
from VibeCADNativeSketchArc import (
    create_sketch_arc,
    preflight_sketch_arc,
    prepare_sketch_arc,
    verify_sketch_arc,
)
from VibeCADNativeSketchArcSlot import (
    create_sketch_arc_slot,
    preflight_sketch_arc_slot,
    prepare_sketch_arc_slot,
    verify_sketch_arc_slot,
)
from VibeCADNativeSketchBSpline import (
    create_sketch_bspline,
    preflight_sketch_bspline,
    prepare_sketch_bspline,
    verify_sketch_bspline,
)
from VibeCADNativeSketchCircle import (
    create_sketch_circle,
    preflight_sketch_circle,
    prepare_sketch_circle,
    verify_sketch_circle,
)
from VibeCADNativeSketchCarbonCopy import (
    create_carbon_copy,
    preflight_carbon_copy,
    prepare_carbon_copy,
    verify_carbon_copy,
)
from VibeCADNativeSketchConstruction import (
    create_sketch_construction,
    preflight_sketch_construction,
    prepare_sketch_construction,
    verify_sketch_construction,
)
from VibeCADNativeSketchChamfer import (
    create_sketch_chamfer,
    preflight_sketch_chamfer,
    prepare_sketch_chamfer,
    verify_sketch_chamfer,
)
from VibeCADNativeSketchFillet import (
    create_sketch_fillet,
    preflight_sketch_fillet,
    prepare_sketch_fillet,
    verify_sketch_fillet,
)
from VibeCADNativeSketchCenterRectangle import (
    create_sketch_center_rectangle,
    preflight_sketch_center_rectangle,
    prepare_sketch_center_rectangle,
    verify_sketch_center_rectangle,
)
from VibeCADNativeSketchLine import (
    create_sketch_line,
    preflight_sketch_line,
    prepare_sketch_line,
    verify_sketch_line,
)
from VibeCADNativeSketchOblong import (
    create_sketch_oblong,
    preflight_sketch_oblong,
    prepare_sketch_oblong,
    verify_sketch_oblong,
)
from VibeCADNativeSketchOctagon import (
    create_sketch_octagon,
    preflight_sketch_octagon,
    prepare_sketch_octagon,
    verify_sketch_octagon,
)
from VibeCADNativeSketchHyperbolicArc import (
    create_sketch_hyperbolic_arc,
    preflight_sketch_hyperbolic_arc,
    prepare_sketch_hyperbolic_arc,
    verify_sketch_hyperbolic_arc,
)
from VibeCADNativeSketchInterpolatedBSpline import (
    create_sketch_interpolated_bspline,
    preflight_sketch_interpolated_bspline,
    prepare_sketch_interpolated_bspline,
    verify_sketch_interpolated_bspline,
)
from VibeCADNativeSketchHeptagon import (
    create_sketch_heptagon,
    preflight_sketch_heptagon,
    prepare_sketch_heptagon,
    verify_sketch_heptagon,
)
from VibeCADNativeSketchHexagon import (
    create_sketch_hexagon,
    preflight_sketch_hexagon,
    prepare_sketch_hexagon,
    verify_sketch_hexagon,
)
from VibeCADNativeSketchParabolicArc import (
    create_sketch_parabolic_arc,
    preflight_sketch_parabolic_arc,
    prepare_sketch_parabolic_arc,
    verify_sketch_parabolic_arc,
)
from VibeCADNativeSketchPeriodicBSpline import (
    create_sketch_periodic_bspline,
    preflight_sketch_periodic_bspline,
    prepare_sketch_periodic_bspline,
    verify_sketch_periodic_bspline,
)
from VibeCADNativeSketchPeriodicInterpolatedBSpline import (
    create_sketch_periodic_interpolated_bspline,
    preflight_sketch_periodic_interpolated_bspline,
    prepare_sketch_periodic_interpolated_bspline,
    verify_sketch_periodic_interpolated_bspline,
)
from VibeCADNativeSketchPentagon import (
    create_sketch_pentagon,
    preflight_sketch_pentagon,
    prepare_sketch_pentagon,
    verify_sketch_pentagon,
)
from VibeCADNativeSketchPoint import (
    create_sketch_point,
    preflight_sketch_point,
    prepare_sketch_point,
    verify_sketch_point,
)
from VibeCADNativeSketchPolyline import (
    create_sketch_polyline,
    preflight_sketch_polyline,
    prepare_sketch_polyline,
    verify_sketch_polyline,
)
from VibeCADNativeSketchRectangle import (
    create_sketch_rectangle,
    preflight_sketch_rectangle,
    prepare_sketch_rectangle,
    verify_sketch_rectangle,
)
from VibeCADNativeSketchSquare import (
    create_sketch_square,
    preflight_sketch_square,
    prepare_sketch_square,
    verify_sketch_square,
)
from VibeCADNativeSketchSlot import (
    create_sketch_slot,
    preflight_sketch_slot,
    prepare_sketch_slot,
    verify_sketch_slot,
)
from VibeCADNativeSketchThreePointArc import (
    create_sketch_three_point_arc,
    preflight_sketch_three_point_arc,
    prepare_sketch_three_point_arc,
    verify_sketch_three_point_arc,
)
from VibeCADNativeSketchThreePointCircle import (
    create_sketch_three_point_circle,
    preflight_sketch_three_point_circle,
    prepare_sketch_three_point_circle,
    verify_sketch_three_point_circle,
)
from VibeCADNativeSketchThreePointEllipse import (
    create_sketch_three_point_ellipse,
    preflight_sketch_three_point_ellipse,
    prepare_sketch_three_point_ellipse,
    verify_sketch_three_point_ellipse,
)
from VibeCADNativeSketchText import (
    create_sketch_text,
    preflight_sketch_text,
    prepare_sketch_text,
    verify_sketch_text,
)
from VibeCADNativeSketchTrim import (
    create_sketch_trim,
    preflight_sketch_trim,
    prepare_sketch_trim,
    verify_sketch_trim,
)
from VibeCADNativeSketchSplit import (
    create_sketch_split,
    preflight_sketch_split,
    prepare_sketch_split,
    verify_sketch_split,
)
from VibeCADNativeSketchExtend import (
    create_sketch_extend,
    preflight_sketch_extend,
    prepare_sketch_extend,
    verify_sketch_extend,
)
from VibeCADNativeSketchProjection import (
    create_sketch_projection,
    preflight_sketch_projection,
    prepare_sketch_projection,
    verify_sketch_projection,
)
from VibeCADNativeSketchIntersection import (
    create_sketch_intersection,
    preflight_sketch_intersection,
    prepare_sketch_intersection,
    verify_sketch_intersection,
)
from VibeCADNativeSketchInternalAlignment import (
    create_sketch_internal_alignment,
    preflight_internal_alignment,
    prepare_internal_alignment,
    verify_sketch_internal_alignment,
)
from VibeCADNativeSketchInternalAlignmentTarget import (
    FIELDS as INTERNAL_ALIGNMENT_FIELDS,
    OPERATION as INTERNAL_ALIGNMENT_OPERATION,
)
from VibeCADNativeSketchTriangle import (
    create_sketch_triangle,
    preflight_sketch_triangle,
    prepare_sketch_triangle,
    verify_sketch_triangle,
)
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeSketchTransformRuntime import (
    TRANSFORM_OPERATIONS,
    TRANSFORM_OUTER_FIELDS,
)


_EXTERNAL_OPERATION_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "source",
        "role",
    }
)
_OUTER_FIELDS = {
    "create_point": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "position_mm",
        }
    ),
    "create_line": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "start_mm",
            "end_mm",
        }
    ),
    "create_polyline": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "vertices_mm",
            "closed",
        }
    ),
    "create_arc": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "radius_mm",
            "start_angle_degrees",
            "sweep_angle_degrees",
        }
    ),
    "create3_point_arc": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "first_endpoint_mm",
            "second_endpoint_mm",
            "rim_point_mm",
        }
    ),
    "create_arc_of_ellipse": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "major_radius_mm",
            "minor_radius_mm",
            "rotation_degrees",
            "start_parameter_degrees",
            "sweep_parameter_degrees",
        }
    ),
    "create_arc_of_hyperbola": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "major_radius_mm",
            "minor_radius_mm",
            "rotation_degrees",
            "start_parameter",
            "end_parameter",
        }
    ),
    "create_arc_of_parabola": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "vertex_mm",
            "focal_length_mm",
            "rotation_degrees",
            "start_parameter_mm",
            "end_parameter_mm",
        }
    ),
    "create_circle": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "radius_mm",
        }
    ),
    "create3_point_circle": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "first_point_mm",
            "second_point_mm",
            "third_point_mm",
        }
    ),
    "create_ellipse": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "major_radius_mm",
            "minor_radius_mm",
            "rotation_degrees",
        }
    ),
    "create3_point_ellipse": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "first_axis_endpoint_mm",
            "second_axis_endpoint_mm",
            "rim_point_mm",
        }
    ),
    "create_rectangle": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "first_corner_mm",
            "opposite_corner_mm",
        }
    ),
    "create_center_rectangle": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "corner_mm",
        }
    ),
    "create_oblong": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "first_corner_mm",
            "opposite_corner_mm",
            "radius_mm",
        }
    ),
    "create_triangle": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "corner_mm",
        }
    ),
    "create_square": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "corner_mm",
        }
    ),
    "create_pentagon": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "corner_mm",
        }
    ),
    "create_hexagon": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "corner_mm",
        }
    ),
    "create_heptagon": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "corner_mm",
        }
    ),
    "create_octagon": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "corner_mm",
        }
    ),
    "create_regular_polygon": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "corner_mm",
            "side_count",
        }
    ),
    "create_slot": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "start_center_mm",
            "end_center_mm",
            "radius_mm",
        }
    ),
    "create_arc_slot": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "center_mm",
            "centerline_radius_mm",
            "start_angle_degrees",
            "sweep_angle_degrees",
            "slot_radius_mm",
        }
    ),
    "create_b_spline": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "control_points_mm",
            "degree",
        }
    ),
    "create_periodic_b_spline": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "control_points_mm",
            "degree",
        }
    ),
    "create_b_spline_by_interpolation": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "interpolation_points_mm",
        }
    ),
    "create_periodic_b_spline_by_interpolation": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "interpolation_points_mm",
        }
    ),
    "create_text": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "text",
            "font_name",
            "handle_start_mm",
            "handle_end_mm",
            "sizing_mode",
        }
    ),
    "toggle_construction": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "targets",
        }
    ),
    "create_fillet": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
            "preserve_corner",
        }
    ),
    "create_chamfer": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
            "preserve_corner",
        }
    ),
    "trim": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        }
    ),
    "split": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        }
    ),
    "extend": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        }
    ),
    "project_external_geometry": _EXTERNAL_OPERATION_FIELDS,
    "intersect_external_geometry": _EXTERNAL_OPERATION_FIELDS,
    "carbon_copy": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "source_sketch",
            "expected_source_geometry_count",
            "expected_source_constraint_count",
            "expected_source_external_reference_count",
            "expected_source_external_geometry_count",
            "geometry_mode",
            "reference_permission",
        }
    ),
}
_OUTER_FIELDS.update(TRANSFORM_OUTER_FIELDS)
_OUTER_FIELDS[INTERNAL_ALIGNMENT_OPERATION] = INTERNAL_ALIGNMENT_FIELDS

_OPERATIONS = {
    "create_point": (
        prepare_sketch_point,
        preflight_sketch_point,
        create_sketch_point,
        verify_sketch_point,
        "Create Native Sketch Point",
    ),
    "create_line": (
        prepare_sketch_line,
        preflight_sketch_line,
        create_sketch_line,
        verify_sketch_line,
        "Create Native Sketch Line",
    ),
    "create_polyline": (
        prepare_sketch_polyline,
        preflight_sketch_polyline,
        create_sketch_polyline,
        verify_sketch_polyline,
        "Create Native Sketch Polyline",
    ),
    "create_arc": (
        prepare_sketch_arc,
        preflight_sketch_arc,
        create_sketch_arc,
        verify_sketch_arc,
        "Create Native Sketch Arc",
    ),
    "create3_point_arc": (
        prepare_sketch_three_point_arc,
        preflight_sketch_three_point_arc,
        create_sketch_three_point_arc,
        verify_sketch_three_point_arc,
        "Create Native Sketch Three-Point Arc",
    ),
    "create_arc_of_ellipse": (
        prepare_sketch_elliptical_arc,
        preflight_sketch_elliptical_arc,
        create_sketch_elliptical_arc,
        verify_sketch_elliptical_arc,
        "Create Native Sketch Elliptical Arc",
    ),
    "create_arc_of_hyperbola": (
        prepare_sketch_hyperbolic_arc,
        preflight_sketch_hyperbolic_arc,
        create_sketch_hyperbolic_arc,
        verify_sketch_hyperbolic_arc,
        "Create Native Sketch Hyperbolic Arc",
    ),
    "create_arc_of_parabola": (
        prepare_sketch_parabolic_arc,
        preflight_sketch_parabolic_arc,
        create_sketch_parabolic_arc,
        verify_sketch_parabolic_arc,
        "Create Native Sketch Parabolic Arc",
    ),
    "create_circle": (
        prepare_sketch_circle,
        preflight_sketch_circle,
        create_sketch_circle,
        verify_sketch_circle,
        "Create Native Sketch Circle",
    ),
    "create3_point_circle": (
        prepare_sketch_three_point_circle,
        preflight_sketch_three_point_circle,
        create_sketch_three_point_circle,
        verify_sketch_three_point_circle,
        "Create Native Sketch Three-Point Circle",
    ),
    "create_ellipse": (
        prepare_sketch_ellipse,
        preflight_sketch_ellipse,
        create_sketch_ellipse,
        verify_sketch_ellipse,
        "Create Native Sketch Ellipse",
    ),
    "create3_point_ellipse": (
        prepare_sketch_three_point_ellipse,
        preflight_sketch_three_point_ellipse,
        create_sketch_three_point_ellipse,
        verify_sketch_three_point_ellipse,
        "Create Native Sketch Three-Point Ellipse",
    ),
    "create_rectangle": (
        prepare_sketch_rectangle,
        preflight_sketch_rectangle,
        create_sketch_rectangle,
        verify_sketch_rectangle,
        "Create Native Sketch Rectangle",
    ),
    "create_center_rectangle": (
        prepare_sketch_center_rectangle,
        preflight_sketch_center_rectangle,
        create_sketch_center_rectangle,
        verify_sketch_center_rectangle,
        "Create Native Sketch Center Rectangle",
    ),
    "create_oblong": (
        prepare_sketch_oblong,
        preflight_sketch_oblong,
        create_sketch_oblong,
        verify_sketch_oblong,
        "Create Native Sketch Oblong",
    ),
    "create_triangle": (
        prepare_sketch_triangle,
        preflight_sketch_triangle,
        create_sketch_triangle,
        verify_sketch_triangle,
        "Create Native Sketch Triangle",
    ),
    "create_square": (
        prepare_sketch_square,
        preflight_sketch_square,
        create_sketch_square,
        verify_sketch_square,
        "Create Native Sketch Square",
    ),
    "create_pentagon": (
        prepare_sketch_pentagon,
        preflight_sketch_pentagon,
        create_sketch_pentagon,
        verify_sketch_pentagon,
        "Create Native Sketch Pentagon",
    ),
    "create_hexagon": (
        prepare_sketch_hexagon,
        preflight_sketch_hexagon,
        create_sketch_hexagon,
        verify_sketch_hexagon,
        "Create Native Sketch Hexagon",
    ),
    "create_heptagon": (
        prepare_sketch_heptagon,
        preflight_sketch_heptagon,
        create_sketch_heptagon,
        verify_sketch_heptagon,
        "Create Native Sketch Heptagon",
    ),
    "create_octagon": (
        prepare_sketch_octagon,
        preflight_sketch_octagon,
        create_sketch_octagon,
        verify_sketch_octagon,
        "Create Native Sketch Octagon",
    ),
    "create_regular_polygon": (
        prepare_sketch_arbitrary_regular_polygon,
        preflight_sketch_arbitrary_regular_polygon,
        create_sketch_arbitrary_regular_polygon,
        verify_sketch_arbitrary_regular_polygon,
        "Create Native Sketch Regular Polygon",
    ),
    "create_slot": (
        prepare_sketch_slot,
        preflight_sketch_slot,
        create_sketch_slot,
        verify_sketch_slot,
        "Create Native Sketch Slot",
    ),
    "create_arc_slot": (
        prepare_sketch_arc_slot,
        preflight_sketch_arc_slot,
        create_sketch_arc_slot,
        verify_sketch_arc_slot,
        "Create Native Sketch Arc Slot",
    ),
    "create_b_spline": (
        prepare_sketch_bspline,
        preflight_sketch_bspline,
        create_sketch_bspline,
        verify_sketch_bspline,
        "Create Native Sketch B-Spline",
    ),
    "create_periodic_b_spline": (
        prepare_sketch_periodic_bspline,
        preflight_sketch_periodic_bspline,
        create_sketch_periodic_bspline,
        verify_sketch_periodic_bspline,
        "Create Native Sketch Periodic B-Spline",
    ),
    "create_b_spline_by_interpolation": (
        prepare_sketch_interpolated_bspline,
        preflight_sketch_interpolated_bspline,
        create_sketch_interpolated_bspline,
        verify_sketch_interpolated_bspline,
        "Create Native Sketch Interpolated B-Spline",
    ),
    "create_periodic_b_spline_by_interpolation": (
        prepare_sketch_periodic_interpolated_bspline,
        preflight_sketch_periodic_interpolated_bspline,
        create_sketch_periodic_interpolated_bspline,
        verify_sketch_periodic_interpolated_bspline,
        "Create Native Sketch Periodic Interpolated B-Spline",
    ),
    "create_text": (
        prepare_sketch_text,
        preflight_sketch_text,
        create_sketch_text,
        verify_sketch_text,
        "Create Native Sketch Text",
    ),
    "toggle_construction": (
        prepare_sketch_construction,
        preflight_sketch_construction,
        create_sketch_construction,
        verify_sketch_construction,
        "Toggle Native Sketch Construction",
    ),
    "create_fillet": (
        prepare_sketch_fillet,
        preflight_sketch_fillet,
        create_sketch_fillet,
        verify_sketch_fillet,
        "Create Native Sketch Fillet",
    ),
    "create_chamfer": (
        prepare_sketch_chamfer,
        preflight_sketch_chamfer,
        create_sketch_chamfer,
        verify_sketch_chamfer,
        "Create Native Sketch Chamfer",
    ),
    "trim": (
        prepare_sketch_trim,
        preflight_sketch_trim,
        create_sketch_trim,
        verify_sketch_trim,
        "Trim Native Sketch Geometry",
    ),
    "split": (
        prepare_sketch_split,
        preflight_sketch_split,
        create_sketch_split,
        verify_sketch_split,
        "Split Native Sketch Geometry",
    ),
    "extend": (
        prepare_sketch_extend,
        preflight_sketch_extend,
        create_sketch_extend,
        verify_sketch_extend,
        "Extend Native Sketch Geometry",
    ),
    "project_external_geometry": (
        prepare_sketch_projection,
        preflight_sketch_projection,
        create_sketch_projection,
        verify_sketch_projection,
        "Project Native Sketch External Geometry",
    ),
    "intersect_external_geometry": (
        prepare_sketch_intersection,
        preflight_sketch_intersection,
        create_sketch_intersection,
        verify_sketch_intersection,
        "Intersect Native Sketch External Geometry",
    ),
    "carbon_copy": (
        prepare_carbon_copy,
        preflight_carbon_copy,
        create_carbon_copy,
        verify_carbon_copy,
        "Create Native Sketch Carbon Copy",
    ),
}
_OPERATIONS.update(TRANSFORM_OPERATIONS)
_OPERATIONS[INTERNAL_ALIGNMENT_OPERATION] = (
    prepare_internal_alignment,
    preflight_internal_alignment,
    create_sketch_internal_alignment,
    verify_sketch_internal_alignment,
    "Toggle Native Sketch Internal Geometry",
)


class NativeSketchGeometryRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_geometry(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        handlers = _OPERATIONS.get(operation)
        if handlers is None:
            raise NativeSketchError("That Sketch geometry operation is unavailable.")
        prepare, preflight, create, verify, transaction_name = handlers
        spec = prepare(self._context.document_uid, values)
        prepared = preflight(self._context, spec)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=transaction_name,
            mutate=lambda document: create(document, prepared),
            verify=verify,
        )
