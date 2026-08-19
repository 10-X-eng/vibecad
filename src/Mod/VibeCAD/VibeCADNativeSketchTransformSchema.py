# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contracts for bounded exact Sketch transformation operations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import NativeCapabilityVariant
from VibeCADNativeDesignSchema import (
    OBJECT_NAME_SCHEMA,
    SIGNED_MM_SCHEMA,
    parameters_schema,
)


def _count_schema() -> dict:
    return {"type": "integer", "minimum": 0, "maximum": 1_000_000}


def _vector_schema() -> dict:
    return parameters_schema(
        {"x": SIGNED_MM_SCHEMA, "y": SIGNED_MM_SCHEMA},
        ("x", "y"),
    )


def translate_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_indices": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": -1_000_000,
                    "maximum": 999_999,
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
            "first_translation_mm": _vector_schema(),
            "copy_count": {"type": "integer", "minimum": 0, "maximum": 9_999},
            "second_translation_mm": _vector_schema(),
            "row_count": {"type": "integer", "minimum": 1, "maximum": 9_999},
            "constraint_mode": {
                "type": "string",
                "enum": ["copy", "equalize_dimensions"],
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "first_translation_mm",
            "copy_count",
            "second_translation_mm",
            "row_count",
            "constraint_mode",
        ),
    )


def rotate_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_indices": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": -1_000_000,
                    "maximum": 999_999,
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
            "center_mm": _vector_schema(),
            "total_angle": parameters_schema(
                {
                    "value": {
                        "type": "number",
                        "exclusiveMinimum": -360.0,
                        "exclusiveMaximum": 360.0,
                    },
                    "unit": {"type": "string", "enum": ["deg"]},
                },
                ("value", "unit"),
            ),
            "copy_count": {"type": "integer", "minimum": 0, "maximum": 9_999},
            "constraint_mode": {
                "type": "string",
                "enum": ["copy", "equalize_dimensions"],
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "center_mm",
            "total_angle",
            "copy_count",
            "constraint_mode",
        ),
    )


def scale_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_indices": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": -1_000_000,
                    "maximum": 999_999,
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
            "center_mm": _vector_schema(),
            "scale_factor": {
                "type": "number",
                "exclusiveMinimum": 1.0e-7,
                "maximum": 1_000_000.0,
            },
            "keep_originals": {"type": "boolean"},
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "center_mm",
            "scale_factor",
            "keep_originals",
        ),
    )


def offset_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_indices": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": -1_000_000,
                    "maximum": 999_999,
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
            "offset_distance": parameters_schema(
                {
                    "value": {
                        "oneOf": [
                            {
                                "type": "number",
                                "minimum": -1_000_000_000.0,
                                "exclusiveMaximum": -1.0e-7,
                            },
                            {
                                "type": "number",
                                "exclusiveMinimum": 1.0e-7,
                                "maximum": 1_000_000_000.0,
                            },
                        ]
                    },
                    "unit": {"type": "string", "enum": ["mm"]},
                },
                ("value", "unit"),
            ),
            "join_type": {
                "type": "string",
                "enum": ["arc", "intersection"],
            },
            "source_mode": {
                "type": "string",
                "enum": ["keep", "delete", "constrain"],
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "offset_distance",
            "join_type",
            "source_mode",
        ),
    )


def symmetry_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_indices": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": -1_000_000,
                    "maximum": 999_999,
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
            "reference": parameters_schema(
                {
                    "geometry_index": {
                        "type": "integer",
                        "minimum": -1_000_000,
                        "maximum": 999_999,
                        "not": {"const": -2000},
                    },
                    "position": {
                        "type": "string",
                        "enum": ["whole", "start", "end", "center"],
                    },
                },
                ("geometry_index", "position"),
            ),
            "source_mode": {
                "type": "string",
                "enum": ["keep", "delete", "constrain"],
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "reference",
            "source_mode",
        ),
    )


def remove_axis_alignment_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_indices": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 999_999,
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
        ),
    )


def convert_to_nurbs_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_indices": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": -1_000_000,
                    "maximum": 999_999,
                    "not": {"enum": [-2, -1]},
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
        ),
    )


def increase_bspline_degree_parameters() -> dict:
    return remove_axis_alignment_parameters()


def decrease_bspline_degree_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 999_999,
            },
            "maximum_deviation_mm": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1_000_000.0,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_index",
            "maximum_deviation_mm",
        ),
    )


def _bspline_knot_multiplicity_parameters(*, maximum_deviation: bool) -> dict:
    fields = {
        "sketch": parameters_schema(
            {"object_name": OBJECT_NAME_SCHEMA},
            ("object_name",),
        ),
        "expected_geometry_count": _count_schema(),
        "expected_constraint_count": _count_schema(),
        "expected_external_reference_count": _count_schema(),
        "expected_external_geometry_count": _count_schema(),
        "geometry_index": {
            "type": "integer",
            "minimum": 0,
            "maximum": 999_999,
        },
        "knot_index": {
            "type": "integer",
            "minimum": 0,
            "maximum": 999_999,
        },
    }
    required = [
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_index",
        "knot_index",
    ]
    if maximum_deviation:
        fields["maximum_deviation_mm"] = {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1_000_000.0,
        }
        required.append("maximum_deviation_mm")
    return parameters_schema(fields, tuple(required))


def increase_bspline_knot_multiplicity_parameters() -> dict:
    return _bspline_knot_multiplicity_parameters(maximum_deviation=False)


def decrease_bspline_knot_multiplicity_parameters() -> dict:
    return _bspline_knot_multiplicity_parameters(maximum_deviation=True)


def insert_bspline_knot_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "geometry_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 999_999,
            },
            "parameter": {
                "type": "number",
                "minimum": -1_000_000_000.0,
                "maximum": 1_000_000_000.0,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_index",
            "parameter",
        ),
    )


def join_curves_parameters() -> dict:
    endpoint = parameters_schema(
        {
            "geometry_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 999_999,
            },
            "endpoint": {"type": "string", "enum": ["start", "end"]},
        },
        ("geometry_index", "endpoint"),
    )
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "first": endpoint,
            "second": endpoint,
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "first",
            "second",
        ),
    )


def sketch_transform_variants() -> tuple[NativeCapabilityVariant, ...]:
    return (
        NativeCapabilityVariant(
            operation="translate",
            description=(
                "Move exact Sketch geometry or create a bounded one- or two-vector array "
                "with explicit copied or Equal dimensional-constraint behavior."
            ),
            action_ids=frozenset({"Sketcher_Translate"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactTranslateTargetsAndExpectedState",
            transaction_behavior="document",
            background_required=False,
            parameters=translate_parameters(),
        ),
        NativeCapabilityVariant(
            operation="rotate",
            description=(
                "Rotate exact Sketch geometry or create a bounded polar array around an "
                "explicit center with copied or Equal dimensional-constraint behavior."
            ),
            action_ids=frozenset({"Sketcher_Rotate"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactRotateTargetsAndExpectedState",
            transaction_behavior="document",
            background_required=False,
            parameters=rotate_parameters(),
        ),
        NativeCapabilityVariant(
            operation="scale",
            description=(
                "Scale exact Sketch geometry about an explicit center, either replacing "
                "the selected geometry or retaining it alongside the scaled copies."
            ),
            action_ids=frozenset({"Sketcher_Scale"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactScaleTargetsAndExpectedState",
            transaction_behavior="document",
            background_required=False,
            parameters=scale_parameters(),
        ),
        NativeCapabilityVariant(
            operation="offset",
            description=(
                "Offset exact line, circle, or circular-arc Sketch geometry by a "
                "signed distance, with explicit corner joins and source handling."
            ),
            action_ids=frozenset({"Sketcher_Offset"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactOffsetTargetsAndExpectedState",
            transaction_behavior="document",
            background_required=False,
            parameters=offset_parameters(),
        ),
        NativeCapabilityVariant(
            operation="symmetry",
            description=(
                "Mirror exact Sketch geometry about one exact line, axis, or point, "
                "with explicit source retention, deletion, or symmetry constraints."
            ),
            action_ids=frozenset({"Sketcher_Symmetry"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactSymmetryTargetsAndExpectedState",
            transaction_behavior="document",
            background_required=False,
            parameters=symmetry_parameters(),
        ),
        NativeCapabilityVariant(
            operation="remove_axis_alignment",
            description=(
                "Remove horizontal, vertical, and axis relationships from exact "
                "internal Sketch geometry while preserving relative relationships."
            ),
            action_ids=frozenset({"Sketcher_RemoveAxesAlignment"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type=(
                "ActiveSketchExactInternalGeometryAndExpectedConstraintState"
            ),
            transaction_behavior="document",
            background_required=False,
            parameters=remove_axis_alignment_parameters(),
        ),
        NativeCapabilityVariant(
            operation="convert_to_nurbs",
            description=(
                "Convert exact internal or external Sketch edges to B-splines, "
                "including the human command's control-point and knot exposure."
            ),
            action_ids=frozenset({"Sketcher_BSplineConvertToNURBS"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactConvertibleEdgesAndExpectedState",
            transaction_behavior="document",
            background_required=False,
            parameters=convert_to_nurbs_parameters(),
        ),
        NativeCapabilityVariant(
            operation="increase_bspline_degree",
            description=(
                "Increase the degree of exact internal B-spline edges by one while "
                "preserving their shape, durable identity, and existing metadata."
            ),
            action_ids=frozenset({"Sketcher_BSplineIncreaseDegree"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactBSplineEdgesAndExpectedState",
            transaction_behavior="document",
            background_required=False,
            parameters=increase_bspline_degree_parameters(),
        ),
        NativeCapabilityVariant(
            operation="decrease_bspline_degree",
            description="Approximate one B-spline at a lower degree within a deviation limit.",
            action_ids=frozenset({"Sketcher_BSplineDecreaseDegree"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type=("ActiveSketchExactBSplineAndMaximumDeviation"),
            transaction_behavior="document",
            background_required=False,
            parameters=decrease_bspline_degree_parameters(),
        ),
        NativeCapabilityVariant(
            operation="increase_bspline_knot_multiplicity",
            description=(
                "Increase one exact zero-based B-spline knot multiplicity by one "
                "while preserving curve shape, durable identity, and helper state."
            ),
            action_ids=frozenset({"Sketcher_BSplineIncreaseKnotMultiplicity"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactBSplineKnotAndExpectedState",
            transaction_behavior="document",
            background_required=False,
            parameters=increase_bspline_knot_multiplicity_parameters(),
        ),
        NativeCapabilityVariant(
            operation="decrease_bspline_knot_multiplicity",
            description="Decrease one B-spline knot multiplicity within a deviation limit.",
            action_ids=frozenset({"Sketcher_BSplineDecreaseKnotMultiplicity"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactBSplineKnotAndMaximumDeviation",
            transaction_behavior="document",
            background_required=False,
            parameters=decrease_bspline_knot_multiplicity_parameters(),
        ),
        NativeCapabilityVariant(
            operation="insert_bspline_knot",
            description=(
                "Insert one knot at an exact B-spline parameter, increasing an "
                "existing knot by one when the parameter already identifies it."
            ),
            action_ids=frozenset({"Sketcher_BSplineInsertKnot"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactBSplineAndParameter",
            transaction_behavior="document",
            background_required=False,
            parameters=insert_bspline_knot_parameters(),
        ),
        NativeCapabilityVariant(
            operation="join_curves",
            description=(
                "Join two exact open Sketch curve endpoints into one B-spline, "
                "deriving C0 or C1 continuity from the existing endpoint constraints."
            ),
            action_ids=frozenset({"Sketcher_JoinCurves"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactCurveEndpointPair",
            transaction_behavior="document",
            background_required=False,
            parameters=join_curves_parameters(),
        ),
    )
