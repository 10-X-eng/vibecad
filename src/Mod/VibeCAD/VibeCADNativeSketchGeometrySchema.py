# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for contextual Sketch geometry."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    OBJECT_NAME_SCHEMA,
    POSITIVE_MM_SCHEMA,
    SIGNED_MM_SCHEMA,
    parameters_schema,
)
from VibeCADNativeSketchExternalSchema import external_geometry_parameters
from VibeCADNativeSketchCarbonCopySchema import carbon_copy_parameters
from VibeCADNativeSketchInternalAlignmentSchema import (
    sketch_internal_alignment_variants,
)
from VibeCADNativeSketchTransformSchema import sketch_transform_variants


def _point_parameters() -> dict:
    return _geometry_parameters(
        {
            "position_mm": _point_2d_schema(),
        },
        ("position_mm",),
    )


def _point_2d_schema() -> dict:
    return parameters_schema(
        {"x": SIGNED_MM_SCHEMA, "y": SIGNED_MM_SCHEMA},
        ("x", "y"),
    )


def _geometry_parameters(properties: dict, required: tuple[str, ...]) -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            **properties,
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            *required,
        ),
    )


def _fillet_chamfer_parameters(size_field: str) -> dict:
    curve = parameters_schema(
        {
            "geometry_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 999_999,
            },
            "reference_point_mm": _point_2d_schema(),
        },
        ("geometry_index", "reference_point_mm"),
    )
    target = {
        "oneOf": [
            parameters_schema(
                {
                    "form": {"type": "string", "const": "corner"},
                    "geometry_index": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 999_999,
                    },
                    "position": {"type": "string", "enum": ["start", "end"]},
                },
                ("form", "geometry_index", "position"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "curve_pair"},
                    "curves": {
                        "type": "array",
                        "items": curve,
                        "minItems": 2,
                        "maxItems": 2,
                    },
                },
                ("form", "curves"),
            ),
        ]
    }
    return _geometry_parameters(
        {
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "target": target,
            size_field: {
                **POSITIVE_MM_SCHEMA,
                "description": (
                    "Fillet radius in millimeters."
                    if size_field == "radius_mm"
                    else "Equal-leg Chamfer distance in millimeters."
                ),
            },
            "preserve_corner": {"type": "boolean"},
        },
        (
            "expected_external_geometry_count",
            "target",
            size_field,
            "preserve_corner",
        ),
    )


def _line_parameters() -> dict:
    return _geometry_parameters(
        {"start_mm": _point_2d_schema(), "end_mm": _point_2d_schema()},
        ("start_mm", "end_mm"),
    )


def _polyline_parameters() -> dict:
    return _geometry_parameters(
        {
            "vertices_mm": {
                "type": "array",
                "items": _point_2d_schema(),
                "minItems": 2,
                "maxItems": 65,
            },
            "closed": {"type": "boolean"},
        },
        ("vertices_mm", "closed"),
    )


def _arc_parameters() -> dict:
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "radius_mm": POSITIVE_MM_SCHEMA,
            "start_angle_degrees": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 360.0,
            },
            "end_angle_degrees": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 360.0,
                "description": (
                    "Counterclockwise arc end angle; must differ from "
                    "start_angle_degrees."
                ),
            },
        },
        (
            "center_mm",
            "radius_mm",
            "start_angle_degrees",
            "end_angle_degrees",
        ),
    )


def _three_point_arc_parameters() -> dict:
    return _geometry_parameters(
        {
            "first_endpoint_mm": _point_2d_schema(),
            "second_endpoint_mm": _point_2d_schema(),
            "rim_point_mm": _point_2d_schema(),
        },
        ("first_endpoint_mm", "second_endpoint_mm", "rim_point_mm"),
    )


def _elliptical_arc_parameters() -> dict:
    angle = {
        "type": "number",
        "minimum": 0.0,
        "maximum": 360.0,
    }
    sweep = {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "exclusiveMaximum": 360.0,
    }
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "major_radius_mm": POSITIVE_MM_SCHEMA,
            "minor_radius_mm": POSITIVE_MM_SCHEMA,
            "rotation_degrees": angle,
            "start_parameter_degrees": angle,
            "sweep_parameter_degrees": sweep,
        },
        (
            "center_mm",
            "major_radius_mm",
            "minor_radius_mm",
            "rotation_degrees",
            "start_parameter_degrees",
            "sweep_parameter_degrees",
        ),
    )


def _hyperbolic_arc_parameters() -> dict:
    parameter = {"type": "number", "minimum": -20.0, "maximum": 20.0}
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "major_radius_mm": POSITIVE_MM_SCHEMA,
            "minor_radius_mm": POSITIVE_MM_SCHEMA,
            "rotation_degrees": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 360.0,
            },
            "start_parameter": parameter,
            "end_parameter": parameter,
        },
        (
            "center_mm",
            "major_radius_mm",
            "minor_radius_mm",
            "rotation_degrees",
            "start_parameter",
            "end_parameter",
        ),
    )


def _parabolic_arc_parameters() -> dict:
    return _geometry_parameters(
        {
            "vertex_mm": _point_2d_schema(),
            "focal_length_mm": POSITIVE_MM_SCHEMA,
            "rotation_degrees": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 360.0,
            },
            "start_parameter_mm": SIGNED_MM_SCHEMA,
            "end_parameter_mm": SIGNED_MM_SCHEMA,
        },
        (
            "vertex_mm",
            "focal_length_mm",
            "rotation_degrees",
            "start_parameter_mm",
            "end_parameter_mm",
        ),
    )


def _circle_parameters() -> dict:
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "radius_mm": POSITIVE_MM_SCHEMA,
        },
        ("center_mm", "radius_mm"),
    )


def _three_point_circle_parameters() -> dict:
    return _geometry_parameters(
        {
            "first_point_mm": _point_2d_schema(),
            "second_point_mm": _point_2d_schema(),
            "third_point_mm": _point_2d_schema(),
        },
        ("first_point_mm", "second_point_mm", "third_point_mm"),
    )


def _ellipse_parameters() -> dict:
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "major_radius_mm": POSITIVE_MM_SCHEMA,
            "minor_radius_mm": POSITIVE_MM_SCHEMA,
            "rotation_degrees": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 360.0,
                "default": 0.0,
            },
        },
        ("center_mm", "major_radius_mm", "minor_radius_mm"),
    )


def _three_point_ellipse_parameters() -> dict:
    return _geometry_parameters(
        {
            "first_axis_endpoint_mm": _point_2d_schema(),
            "second_axis_endpoint_mm": _point_2d_schema(),
            "rim_point_mm": _point_2d_schema(),
        },
        ("first_axis_endpoint_mm", "second_axis_endpoint_mm", "rim_point_mm"),
    )


def _rectangle_parameters() -> dict:
    return _geometry_parameters(
        {
            "first_corner_mm": _point_2d_schema(),
            "opposite_corner_mm": _point_2d_schema(),
        },
        ("first_corner_mm", "opposite_corner_mm"),
    )


def _center_rectangle_parameters() -> dict:
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "corner_mm": _point_2d_schema(),
        },
        ("center_mm", "corner_mm"),
    )


def _rounded_rectangle_parameters() -> dict:
    return _geometry_parameters(
        {
            "first_corner_mm": _point_2d_schema(),
            "opposite_corner_mm": _point_2d_schema(),
            "corner_radius_mm": POSITIVE_MM_SCHEMA,
        },
        ("first_corner_mm", "opposite_corner_mm", "corner_radius_mm"),
    )


def _regular_polygon_parameters() -> dict:
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "corner_mm": _point_2d_schema(),
        },
        ("center_mm", "corner_mm"),
    )


def _arbitrary_regular_polygon_parameters() -> dict:
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "corner_mm": _point_2d_schema(),
            "side_count": {
                "type": "integer",
                "minimum": 3,
                "maximum": 9_999,
            },
        },
        ("center_mm", "corner_mm", "side_count"),
    )


def _slot_parameters() -> dict:
    return _geometry_parameters(
        {
            "start_center_mm": _point_2d_schema(),
            "end_center_mm": _point_2d_schema(),
            "radius_mm": POSITIVE_MM_SCHEMA,
        },
        ("start_center_mm", "end_center_mm", "radius_mm"),
    )


def _arc_slot_parameters() -> dict:
    signed_sweep = {
        "anyOf": [
            {
                "type": "number",
                "exclusiveMinimum": -360.0,
                "exclusiveMaximum": 0.0,
            },
            {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "exclusiveMaximum": 360.0,
            },
        ]
    }
    return _geometry_parameters(
        {
            "center_mm": _point_2d_schema(),
            "centerline_radius_mm": POSITIVE_MM_SCHEMA,
            "start_angle_degrees": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 360.0,
            },
            "sweep_angle_degrees": signed_sweep,
            "slot_radius_mm": POSITIVE_MM_SCHEMA,
        },
        (
            "center_mm",
            "centerline_radius_mm",
            "start_angle_degrees",
            "sweep_angle_degrees",
            "slot_radius_mm",
        ),
    )


def _control_bspline_parameters() -> dict:
    return _geometry_parameters(
        {
            "control_points_mm": {
                "type": "array",
                "items": _point_2d_schema(),
                "minItems": 2,
                "maxItems": 24,
            },
            "degree": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
            },
        },
        ("control_points_mm", "degree"),
    )


def _interpolated_bspline_parameters() -> dict:
    return _geometry_parameters(
        {
            "interpolation_points_mm": {
                "type": "array",
                "items": _point_2d_schema(),
                "minItems": 2,
                "maxItems": 24,
            },
        },
        ("interpolation_points_mm",),
    )


def _text_parameters() -> dict:
    return _geometry_parameters(
        {
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
            },
            "font_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
            "handle_start_mm": _point_2d_schema(),
            "handle_end_mm": _point_2d_schema(),
            "sizing_mode": {
                "type": "string",
                "enum": ["width", "height"],
            },
        },
        (
            "text",
            "font_name",
            "handle_start_mm",
            "handle_end_mm",
            "sizing_mode",
        ),
    )


def _construction_parameters() -> dict:
    target = parameters_schema(
        {
            "geometry_index": {
                "anyOf": [
                    {"type": "integer", "minimum": 0, "maximum": 999_999},
                    {
                        "type": "integer",
                        "minimum": -1_000_000,
                        "maximum": -3,
                    },
                ]
            },
            "expected_state": {"type": "boolean"},
        },
        ("geometry_index", "expected_state"),
    )
    return _geometry_parameters(
        {
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "targets": {
                "type": "array",
                "items": target,
                "minItems": 1,
                "maxItems": 64,
            },
        },
        ("expected_external_geometry_count", "targets"),
    )


def sketch_geometry_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="sketch.geometry",
        description="Edit geometry in the active Sketch.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_point",
                description="Create one unconstrained non-construction Point.",
                action_ids=frozenset({"Sketcher_CreatePoint"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_point_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_line",
                description="Create one unconstrained non-construction Line.",
                action_ids=frozenset({"Sketcher_CreateLine"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_line_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_polyline",
                description="Create one atomic connected open or closed Polyline.",
                action_ids=frozenset({"Sketcher_CreatePolyline"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_polyline_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_arc",
                description="Create one center-radius circular Arc.",
                action_ids=frozenset({"Sketcher_CreateArc"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_arc_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create3_point_arc",
                description="Create one circular Arc through three exact points.",
                action_ids=frozenset({"Sketcher_Create3PointArc"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_three_point_arc_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_arc_of_ellipse",
                description="Create one elliptical Arc with exposed internal geometry.",
                action_ids=frozenset({"Sketcher_CreateArcOfEllipse"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_elliptical_arc_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_arc_of_hyperbola",
                description="Create one hyperbolic Arc with exposed internal geometry.",
                action_ids=frozenset({"Sketcher_CreateArcOfHyperbola"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_hyperbolic_arc_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_arc_of_parabola",
                description="Create one parabolic Arc with exposed internal geometry.",
                action_ids=frozenset({"Sketcher_CreateArcOfParabola"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_parabolic_arc_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_circle",
                description="Create one center-radius Circle.",
                action_ids=frozenset({"Sketcher_CreateCircle"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_circle_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create3_point_circle",
                description="Create one Circle through three exact points.",
                action_ids=frozenset({"Sketcher_Create3PointCircle"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_three_point_circle_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_ellipse",
                description="Create one center-based Ellipse with exposed internals.",
                action_ids=frozenset({"Sketcher_CreateEllipseByCenter"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_ellipse_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create3_point_ellipse",
                description="Create one Ellipse from an axis pair and rim point.",
                action_ids=frozenset({"Sketcher_CreateEllipseBy3Points"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_three_point_ellipse_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_rectangle",
                description="Create one axis-aligned Rectangle from opposite corners.",
                action_ids=frozenset({"Sketcher_CreateRectangle"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_rectangle_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_center_rectangle",
                description="Create one Rectangle from its center and one corner.",
                action_ids=frozenset({"Sketcher_CreateRectangle_Center"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_center_rectangle_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_rounded_rectangle",
                description=(
                    "Create one axis-aligned rounded Rectangle from opposite corners "
                    "and a corner radius."
                ),
                action_ids=frozenset({"Sketcher_CreateOblong"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_rounded_rectangle_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_triangle",
                description="Create one equilateral Triangle from center and corner.",
                action_ids=frozenset({"Sketcher_CreateTriangle"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_regular_polygon_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_square",
                description="Create one regular Square from center and corner.",
                action_ids=frozenset({"Sketcher_CreateSquare"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_regular_polygon_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_pentagon",
                description="Create one regular Pentagon from center and corner.",
                action_ids=frozenset({"Sketcher_CreatePentagon"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_regular_polygon_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_hexagon",
                description="Create one regular Hexagon from center and corner.",
                action_ids=frozenset({"Sketcher_CreateHexagon"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_regular_polygon_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_heptagon",
                description="Create one regular Heptagon from center and corner.",
                action_ids=frozenset({"Sketcher_CreateHeptagon"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_regular_polygon_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_octagon",
                description="Create one regular Octagon from center and corner.",
                action_ids=frozenset({"Sketcher_CreateOctagon"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_regular_polygon_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_regular_polygon",
                description="Create one regular Polygon with an exact side count.",
                action_ids=frozenset({"Sketcher_CreateRegularPolygon"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_arbitrary_regular_polygon_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_slot",
                description="Create one straight Slot from two centers and a radius.",
                action_ids=frozenset({"Sketcher_CreateSlot"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_slot_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_arc_slot",
                description="Create one rounded-end Arc Slot on a circular centerline.",
                action_ids=frozenset({"Sketcher_CreateArcSlot"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_arc_slot_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_b_spline",
                description="Create one non-periodic control-point B-spline.",
                action_ids=frozenset({"Sketcher_CreateBSpline"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_control_bspline_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_periodic_b_spline",
                description="Create one periodic control-point B-spline.",
                action_ids=frozenset({"Sketcher_CreatePeriodicBSpline"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_control_bspline_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_b_spline_by_interpolation",
                description="Create one non-periodic B-spline through exact points.",
                action_ids=frozenset({"Sketcher_CreateBSplineByInterpolation"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_interpolated_bspline_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_periodic_b_spline_by_interpolation",
                description="Create one periodic B-spline through exact points.",
                action_ids=frozenset({"Sketcher_CreatePeriodicBSplineByInterpolation"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_interpolated_bspline_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_text",
                description=(
                    "Create bounded Text using an installed font name or 'default'."
                ),
                action_ids=frozenset({"Sketcher_CreateText"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_text_parameters(),
            ),
            NativeCapabilityVariant(
                operation="toggle_construction",
                description=(
                    "Toggle exact internal Construction or external defining states."
                ),
                action_ids=frozenset({"Sketcher_ToggleConstruction"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactGeometryAndExpectedStates",
                transaction_behavior="document",
                background_required=False,
                parameters=_construction_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_fillet",
                description=(
                    "Create the human Fillet result at one exact corner or between "
                    "two exact bounded curves."
                ),
                action_ids=frozenset({"Sketcher_CreateFillet"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactFilletTargetAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=_fillet_chamfer_parameters("radius_mm"),
            ),
            NativeCapabilityVariant(
                operation="create_chamfer",
                description=(
                    "Create the human Chamfer result at one exact corner or between "
                    "two exact bounded curves."
                ),
                action_ids=frozenset({"Sketcher_CreateChamfer"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactChamferTargetAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=_fillet_chamfer_parameters("distance_mm"),
            ),
            NativeCapabilityVariant(
                operation="project_external_geometry",
                description=(
                    "Project one exact source subelement or datum into the open Sketch "
                    "with an explicit defining or reference role."
                ),
                action_ids=frozenset({"Sketcher_Projection"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactExternalSourceAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=external_geometry_parameters(),
            ),
            NativeCapabilityVariant(
                operation="intersect_external_geometry",
                description=(
                    "Intersect one exact source subelement or datum with the open "
                    "Sketch plane using an explicit defining or reference role."
                ),
                action_ids=frozenset({"Sketcher_Intersection"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactExternalSourceAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=external_geometry_parameters(),
            ),
            NativeCapabilityVariant(
                operation="carbon_copy",
                description=(
                    "Copy one exact source Sketch's geometry, constraints, expressions, "
                    "and external references using an explicit human modifier mode."
                ),
                action_ids=frozenset({"Sketcher_CarbonCopy"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactCarbonCopySourceAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=carbon_copy_parameters(),
            ),
            *sketch_internal_alignment_variants(),
            *sketch_transform_variants(),
        ),
    )


def register_sketch_geometry_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(sketch_geometry_capability_definition())
