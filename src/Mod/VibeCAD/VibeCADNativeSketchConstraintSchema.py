# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for contextual Sketch constraints."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema
from VibeCADNativeSketchConstraintCurveSchema import (
    active_parameters as _active_parameters,
    block_parameters as _block_parameters,
    driving_parameters as _driving_parameters,
    equal_parameters as _equal_parameters,
    group_parameters as _group_parameters,
    perpendicular_parameters as _perpendicular_parameters,
    symmetric_parameters as _symmetric_parameters,
    tangent_parameters as _tangent_parameters,
)
from VibeCADNativeSketchConstraintSchemaCommon import (
    element_schema as _element_schema,
)
from VibeCADNativeSketchVirtualSpaceSchema import sketch_virtual_space_variant


def _dimension_parameters() -> dict:
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
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "selection": {
                "type": "array",
                "items": _element_schema(),
                "minItems": 1,
                "maxItems": 2,
            },
            "expected_inference": {
                "type": "string",
                "enum": ["distance_x", "distance_y", "distance", "angle"],
            },
            "dimension": parameters_schema(
                {
                    "value": {
                        "type": "number",
                        "minimum": 1.0e-7,
                        "maximum": 1_000_000.0,
                    },
                    "unit": {"type": "string", "enum": ["mm", "deg"]},
                },
                ("value", "unit"),
            ),
            "driving": {"type": "boolean"},
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "expected_inference",
            "dimension",
            "driving",
        ),
    )


def _axis_distance_parameters() -> dict:
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
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "selection": {
                "type": "array",
                "items": _element_schema(),
                "minItems": 1,
                "maxItems": 2,
            },
            "dimension": parameters_schema(
                {
                    "value": {
                        "type": "number",
                        "minimum": -1_000_000.0,
                        "maximum": 1_000_000.0,
                    },
                    "unit": {"type": "string", "const": "mm"},
                },
                ("value", "unit"),
            ),
            "driving": {"type": "boolean"},
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "dimension",
            "driving",
        ),
    )


def _circular_size_parameters(*, include_expected_constraint: bool) -> dict:
    properties = {
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
        "expected_external_geometry_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1_000_000,
        },
        "selection": {
            "type": "array",
            "items": _element_schema(),
            "minItems": 1,
            "maxItems": 1,
        },
    }
    required = [
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
    ]
    if include_expected_constraint:
        properties["expected_constraint"] = {
            "type": "string",
            "enum": ["radius", "diameter"],
        }
        required.append("expected_constraint")
    properties.update(
        {
            "dimension": parameters_schema(
                {
                    "value": {
                        "type": "number",
                        "minimum": 1.0e-7,
                        "maximum": 1_000_000.0,
                    },
                    "unit": {"type": "string", "const": "mm"},
                },
                ("value", "unit"),
            ),
            "driving": {"type": "boolean"},
        }
    )
    required.extend(("dimension", "driving"))
    return parameters_schema(properties, tuple(required))


def _radiam_parameters() -> dict:
    return _circular_size_parameters(include_expected_constraint=True)


def _radius_parameters() -> dict:
    return _circular_size_parameters(include_expected_constraint=False)


def _diameter_parameters() -> dict:
    return _circular_size_parameters(include_expected_constraint=False)


def _angle_parameters() -> dict:
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
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "selection": {
                "type": "array",
                "items": _element_schema(),
                "minItems": 1,
                "maxItems": 3,
            },
            "expected_form": {
                "type": "string",
                "enum": [
                    "line_orientation",
                    "circular_arc_span",
                    "line_line",
                    "via_point",
                ],
            },
            "dimension": parameters_schema(
                {
                    "value": {
                        "type": "number",
                        "minimum": -180.0,
                        "maximum": 360.0,
                    },
                    "unit": {"type": "string", "const": "deg"},
                },
                ("value", "unit"),
            ),
            "driving": {"type": "boolean"},
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "expected_form",
            "dimension",
            "driving",
        ),
    )


def _lock_xy_schema() -> dict:
    coordinate = {
        "type": "number",
        "minimum": -1_000_000.0,
        "maximum": 1_000_000.0,
    }
    return parameters_schema(
        {"x": coordinate, "y": coordinate},
        ("x", "y"),
    )


def _lock_target_schema() -> dict:
    return {
        "oneOf": [
            parameters_schema(
                {
                    "form": {"type": "string", "const": "absolute"},
                    "point": _element_schema(),
                    "expected_position_mm": _lock_xy_schema(),
                },
                ("form", "point", "expected_position_mm"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "relative"},
                    "point": _element_schema(),
                    "reference": _element_schema(),
                    "expected_offset_mm": _lock_xy_schema(),
                },
                ("form", "point", "reference", "expected_offset_mm"),
            ),
        ]
    }


def _lock_parameters() -> dict:
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
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "target": _lock_target_schema(),
            "driving": {"type": "boolean"},
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
            "driving",
        ),
    )


def _coincident_target_schema() -> dict:
    point = _element_schema(("start", "end", "center"))
    whole_curve = _element_schema(("whole",))
    return {
        "oneOf": [
            parameters_schema(
                {
                    "form": {"type": "string", "const": "point_point"},
                    "first_point": point,
                    "second_point": point,
                },
                ("form", "first_point", "second_point"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "point_on_object"},
                    "point": point,
                    "curve": whole_curve,
                },
                ("form", "point", "curve"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "concentric"},
                    "first_curve": whole_curve,
                    "second_curve": whole_curve,
                },
                ("form", "first_curve", "second_curve"),
            ),
        ]
    }


def _coincident_parameters() -> dict:
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
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "target": _coincident_target_schema(),
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        ),
    )


def _alignment_parameters(*, automatic: bool) -> dict:
    properties = {
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
        "expected_external_geometry_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1_000_000,
        },
        "selection": {
            "oneOf": [
                {
                    "type": "array",
                    "items": _element_schema(("whole",)),
                    "minItems": 1,
                    "maxItems": 1,
                },
                {
                    "type": "array",
                    "items": _element_schema(("start", "end", "center")),
                    "minItems": 2,
                    "maxItems": 2,
                },
            ]
        },
    }
    required = [
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
    ]
    if automatic:
        properties["expected_inference"] = {
            "type": "string",
            "enum": ["horizontal", "vertical"],
        }
        required.append("expected_inference")
    return parameters_schema(properties, tuple(required))


def _horizontal_vertical_parameters() -> dict:
    return _alignment_parameters(automatic=True)


def _horizontal_parameters() -> dict:
    return _alignment_parameters(automatic=False)


def _vertical_parameters() -> dict:
    return _alignment_parameters(automatic=False)


def _parallel_parameters() -> dict:
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
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "selection": {
                "type": "array",
                "items": _element_schema(("whole",)),
                "minItems": 2,
                "maxItems": 2,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        ),
    )


def sketch_constraint_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="sketch.constraint",
        description="Constrain exact elements in the one human-opened Sketch.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="infer_dimension",
                description=(
                    "Infer one unambiguous DistanceX, DistanceY, Distance, or Angle."
                ),
                action_ids=frozenset({"Sketcher_Dimension"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactElementsAndExpectedInference",
                transaction_behavior="document",
                background_required=False,
                parameters=_dimension_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_distance_x",
                description=(
                    "Set one exact point X coordinate or horizontal point-to-point "
                    "distance."
                ),
                action_ids=frozenset({"Sketcher_ConstrainDistanceX"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactHorizontalDistance",
                transaction_behavior="document",
                background_required=False,
                parameters=_axis_distance_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_distance_y",
                description=(
                    "Set one exact point Y coordinate or vertical point-to-point "
                    "distance."
                ),
                action_ids=frozenset({"Sketcher_ConstrainDistanceY"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactVerticalDistance",
                transaction_behavior="document",
                background_required=False,
                parameters=_axis_distance_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_distance",
                description=(
                    "Set one exact point/axis, point/point, length, or supported "
                    "point/curve or curve/curve distance."
                ),
                action_ids=frozenset({"Sketcher_ConstrainDistance"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactGeneralDistance",
                transaction_behavior="document",
                background_required=False,
                parameters=_axis_distance_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_radius_diameter",
                description=(
                    "Set Diameter on one exact whole circle or Radius on one exact "
                    "whole circular arc."
                ),
                action_ids=frozenset({"Sketcher_ConstrainRadiam"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactCircleOrCircularArcSize",
                transaction_behavior="document",
                background_required=False,
                parameters=_radiam_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_radius",
                description=("Set Radius on one exact whole circle or circular arc."),
                action_ids=frozenset({"Sketcher_ConstrainRadius"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactCircleOrCircularArcRadius",
                transaction_behavior="document",
                background_required=False,
                parameters=_radius_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_diameter",
                description=("Set Diameter on one exact whole circle or circular arc."),
                action_ids=frozenset({"Sketcher_ConstrainDiameter"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactCircleOrCircularArcDiameter",
                transaction_behavior="document",
                background_required=False,
                parameters=_diameter_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_angle",
                description=(
                    "Set one exact line orientation, circular-arc span, directed "
                    "line-line angle, or angle via an on-curve point."
                ),
                action_ids=frozenset({"Sketcher_ConstrainAngle"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactAngleForm",
                transaction_behavior="document",
                background_required=False,
                parameters=_angle_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_lock",
                description=(
                    "Lock one exact point at its expected current position or at "
                    "its expected current offset from one exact reference point."
                ),
                action_ids=frozenset({"Sketcher_ConstrainLock"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactPointLock",
                transaction_behavior="document",
                background_required=False,
                parameters=_lock_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_coincident",
                description=(
                    "Make one exact point pair coincident, put one exact point "
                    "on one whole curve, or make two exact conics concentric."
                ),
                action_ids=frozenset({"Sketcher_ConstrainCoincidentUnified"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactCoincidenceForm",
                transaction_behavior="document",
                background_required=False,
                parameters=_coincident_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_horizontal_vertical",
                description=(
                    "Infer and apply Horizontal or Vertical to one exact whole "
                    "line or one exact ordered point pair."
                ),
                action_ids=frozenset({"Sketcher_ConstrainHorVer"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactHorizontalVerticalInference",
                transaction_behavior="document",
                background_required=False,
                parameters=_horizontal_vertical_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_horizontal",
                description=(
                    "Apply Horizontal to one exact whole line or one exact ordered "
                    "point pair."
                ),
                action_ids=frozenset({"Sketcher_ConstrainHorizontal"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactHorizontalAlignment",
                transaction_behavior="document",
                background_required=False,
                parameters=_horizontal_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_vertical",
                description=(
                    "Apply Vertical to one exact whole line or one exact ordered "
                    "point pair."
                ),
                action_ids=frozenset({"Sketcher_ConstrainVertical"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactVerticalAlignment",
                transaction_behavior="document",
                background_required=False,
                parameters=_vertical_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_parallel",
                description=(
                    "Apply Parallel to exactly two ordered whole straight lines, "
                    "with at least one editable internal line."
                ),
                action_ids=frozenset({"Sketcher_ConstrainParallel"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactOrderedLinePair",
                transaction_behavior="document",
                background_required=False,
                parameters=_parallel_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_perpendicular",
                description=(
                    "Apply one exact Perpendicular form without inferred geometry; "
                    "explicit via-point targets report any required support constraints."
                ),
                action_ids=frozenset({"Sketcher_ConstrainPerpendicular"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactPerpendicularForm",
                transaction_behavior="document",
                background_required=False,
                parameters=_perpendicular_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_tangent",
                description=(
                    "Apply one exact Tangent form without inferred geometry or "
                    "replace one explicitly named support constraint."
                ),
                action_ids=frozenset({"Sketcher_ConstrainTangent"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactTangentFormOrReplacement",
                transaction_behavior="document",
                background_required=False,
                parameters=_tangent_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_equal",
                description=(
                    "Apply an atomic adjacent Equal chain to two through seventeen "
                    "ordered compatible whole edges, including B-spline pole weights."
                ),
                action_ids=frozenset({"Sketcher_ConstrainEqual"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactOrderedCompatibleEdgeChain",
                transaction_behavior="document",
                background_required=False,
                parameters=_equal_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_symmetric",
                description=(
                    "Reflect two exact points or one open curve's endpoints about "
                    "one exact whole straight line, Sketch axis, or exact point."
                ),
                action_ids=frozenset({"Sketcher_ConstrainSymmetric"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactSymmetricForm",
                transaction_behavior="document",
                background_required=False,
                parameters=_symmetric_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_block",
                description=(
                    "Freeze one through sixteen exact internal whole edges at their "
                    "current geometry without moving them."
                ),
                action_ids=frozenset({"Sketcher_ConstrainBlock"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactInternalWholeEdgeSet",
                transaction_behavior="document",
                background_required=False,
                parameters=_block_parameters(),
            ),
            NativeCapabilityVariant(
                operation="constrain_group",
                description=(
                    "Group two through sixteen exact whole internal geometries under "
                    "one generated construction-line handle."
                ),
                action_ids=frozenset({"Sketcher_ConstrainGroup"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactInternalWholeGeometrySet",
                transaction_behavior="document",
                background_required=False,
                parameters=_group_parameters(),
            ),
            NativeCapabilityVariant(
                operation="toggle_driving_reference",
                description=(
                    "Toggle one through sixteen exact dimensional constraints between "
                    "driving and reference states without changing the human creation mode."
                ),
                action_ids=frozenset({"Sketcher_ToggleDrivingConstraint"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactDimensionalConstraintStates",
                transaction_behavior="document",
                background_required=False,
                parameters=_driving_parameters(),
            ),
            NativeCapabilityVariant(
                operation="toggle_active_inactive",
                description=(
                    "Toggle one through sixteen exact constraints between active "
                    "and inactive enforcement states."
                ),
                action_ids=frozenset({"Sketcher_ToggleActiveConstraint"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactConstraintActiveStates",
                transaction_behavior="document",
                background_required=False,
                parameters=_active_parameters(),
            ),
            sketch_virtual_space_variant(),
        ),
    )


def register_sketch_constraint_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(sketch_constraint_capability_definition())
