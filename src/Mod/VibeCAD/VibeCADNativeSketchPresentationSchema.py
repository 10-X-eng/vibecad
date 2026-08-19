# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for Sketch edit-mode presentation state."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema


def _presentation_parameters() -> dict:
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
            "expected_visible": {"type": "boolean"},
            "visible": {"type": "boolean"},
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "expected_visible",
            "visible",
        ),
    )


def _alignment_parameters() -> dict:
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
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
        ),
    )


def sketch_presentation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="sketch.presentation",
        description="Set presentation state in the active Sketch.",
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="align_view_to_sketch",
                description="Align the camera perpendicular to the active Sketch plane.",
                action_ids=frozenset({"Sketcher_ViewSketch"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactViewAlignment",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_alignment_parameters(),
            ),
            NativeCapabilityVariant(
                operation="section_view",
                description="Set active Sketch section clipping visibility explicitly.",
                action_ids=frozenset({"Sketcher_ViewSection"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactSectionViewState",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_presentation_parameters(),
            ),
            NativeCapabilityVariant(
                operation="arc_overlay",
                description=(
                    "Set circular helper visibility for Sketch arcs explicitly."
                ),
                action_ids=frozenset({"Sketcher_ArcOverlay"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactPresentationState",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_presentation_parameters(),
            ),
            NativeCapabilityVariant(
                operation="bspline_degree",
                description="Set B-spline degree-information visibility explicitly.",
                action_ids=frozenset({"Sketcher_BSplineDegree"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactPresentationState",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_presentation_parameters(),
            ),
            NativeCapabilityVariant(
                operation="bspline_control_polygon",
                description="Set B-spline control-polygon visibility explicitly.",
                action_ids=frozenset({"Sketcher_BSplinePolygon"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactPresentationState",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_presentation_parameters(),
            ),
            NativeCapabilityVariant(
                operation="bspline_curvature_comb",
                description="Set B-spline curvature-comb visibility explicitly.",
                action_ids=frozenset({"Sketcher_BSplineComb"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactPresentationState",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_presentation_parameters(),
            ),
            NativeCapabilityVariant(
                operation="bspline_knot_multiplicity",
                description="Set B-spline knot-multiplicity visibility explicitly.",
                action_ids=frozenset({"Sketcher_BSplineKnotMultiplicity"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactPresentationState",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_presentation_parameters(),
            ),
            NativeCapabilityVariant(
                operation="bspline_pole_weight",
                description="Set B-spline pole-weight visibility explicitly.",
                action_ids=frozenset({"Sketcher_BSplinePoleWeight"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactPresentationState",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_presentation_parameters(),
            ),
        ),
    )


def register_sketch_presentation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(sketch_presentation_capability_definition())
