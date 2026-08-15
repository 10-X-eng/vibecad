# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp Native contract for legacy FEM result presentation."""

from __future__ import annotations

from VibeCADNativeAnalyzeResultState import RESULT_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_PRESENTATION_CAPABILITY_NAME = "analyze.presentation"

_DIGEST = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_CLIPPING_STATE = {
    "expected_clipping_state_sha256": _DIGEST,
    "expected_clipping_plane_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 1024,
    },
}

_FIELDS = [
    "none",
    "displacement_magnitude",
    "displacement_x",
    "displacement_y",
    "displacement_z",
    "temperature",
    "von_mises_stress",
    "maximum_principal_stress",
    "minimum_principal_stress",
    "maximum_shear_stress",
    "mass_flow_rate",
    "network_pressure",
    "equivalent_plastic_strain",
]


def analyze_presentation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_PRESENTATION_CAPABILITY_NAME,
        description=(
            "Control exact FEM result and clipping presentation without opening "
            "task UI or returning native render data."
        ),
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="show_result",
                description=(
                    "Atomically choose the displayed scalar field, deformation scale, "
                    "and visibility for one exact legacy FEM result."
                ),
                action_ids=frozenset({"FEM_ResultShow"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactLegacyFemResultAndResultMeshPresentation",
                transaction_behavior="presentation",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "result": RESULT_TARGET,
                        "field": {"type": "string", "enum": _FIELDS},
                        "deformation_scale": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1_000_000.0,
                        },
                        "visible": {"type": "boolean"},
                    },
                    "required": [
                        "result",
                        "field",
                        "deformation_scale",
                        "visible",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="set_post_auto_recompute",
                description=(
                    "Set immediate post-pipeline recompute only when the exact current "
                    "preference still matches the turn-start value."
                ),
                action_ids=frozenset({"FEM_PostApplyChanges"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="FemPostAutoRecomputePreferenceState",
                transaction_behavior="presentation",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "expected_enabled": {"type": "boolean"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["expected_enabled", "enabled"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="add_clipping_plane",
                description=(
                    "Add one clipping manipulator on an exact current shape face, "
                    "optionally reversing the face normal."
                ),
                action_ids=frozenset({"FEM_ClippingPlaneAdd"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactShapeFaceAndActiveViewClippingGraph",
                transaction_behavior="presentation",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "face": {
                            "type": "object",
                            "properties": {
                                "object_name": _OBJECT_NAME,
                                "face_index": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 1_000_000,
                                },
                                "expected_state_sha256": _DIGEST,
                            },
                            "required": [
                                "object_name",
                                "face_index",
                                "expected_state_sha256",
                            ],
                            "additionalProperties": False,
                        },
                        "reverse": {"type": "boolean"},
                        **_CLIPPING_STATE,
                    },
                    "required": [
                        "face",
                        "reverse",
                        "expected_clipping_state_sha256",
                        "expected_clipping_plane_count",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="remove_all_clipping_planes",
                description=(
                    "Remove every clipping plane from the exact current active-view graph."
                ),
                action_ids=frozenset({"FEM_ClippingPlaneRemoveAll"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactActiveViewClippingGraph",
                transaction_behavior="presentation",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": dict(_CLIPPING_STATE),
                    "required": [
                        "expected_clipping_state_sha256",
                        "expected_clipping_plane_count",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_analyze_presentation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_presentation_capability_definition())
