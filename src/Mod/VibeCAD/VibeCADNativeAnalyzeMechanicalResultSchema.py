# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for structural solver results."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _OBJECT_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_MECHANICAL_RESULTS = "analyze.mechanical_results"
ANALYZE_SHOW_MECHANICAL = "analyze.show_mechanical"
_FIELDS = ["von_mises_stress", "displacement_magnitude"]


def _definition(
    name: str,
    description: str,
    classification: str,
    operation: str,
    action_id: str,
    properties: dict,
    required: tuple[str, ...],
) -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification=classification,
        variants=(
            NativeCapabilityVariant(
                operation=operation,
                description=description,
                action_ids=frozenset({action_id}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="CurrentNamedMechanicalResult",
                transaction_behavior=(
                    "none" if classification == "read" else "presentation"
                ),
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": properties,
                    "required": list(required),
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def analyze_mechanical_result_capability_definitions(
) -> tuple[NativeCapabilityDefinition, ...]:
    result_name = {**_OBJECT_NAME}
    return (
        _definition(
            ANALYZE_MECHANICAL_RESULTS,
            "Read stress and displacement ranges.",
            "read",
            "read",
            "VibeCAD_AnalyzeReadMechanicalResult",
            {"result_name": result_name},
            ("result_name",),
        ),
        _definition(
            ANALYZE_SHOW_MECHANICAL,
            "Show stress or displacement.",
            "view",
            "show",
            "VibeCAD_AnalyzeShowMechanicalResult",
            {
                "result_name": result_name,
                "field": {"type": "string", "enum": _FIELDS},
            },
            ("result_name", "field"),
        ),
    )


def register_analyze_mechanical_result_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in analyze_mechanical_result_capability_definitions():
        registry.register_definition(definition)
