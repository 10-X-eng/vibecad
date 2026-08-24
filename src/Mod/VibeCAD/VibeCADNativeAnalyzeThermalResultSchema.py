# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for completed thermal results."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _OBJECT_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_TEMPERATURE_RESULTS = "analyze.temperature_results"
ANALYZE_SHOW_TEMPERATURE = "analyze.show_temperature"


def _definition(
    name: str,
    description: str,
    classification: str,
    operation: str,
    action_id: str,
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
                exact_target_type="CurrentNamedTemperatureResult",
                transaction_behavior=(
                    "none" if classification == "read" else "presentation"
                ),
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {"result_name": _OBJECT_NAME},
                    "required": ["result_name"],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def analyze_thermal_result_capability_definitions(
) -> tuple[NativeCapabilityDefinition, ...]:
    return (
        _definition(
            ANALYZE_TEMPERATURE_RESULTS,
            "Read the temperature range.",
            "read",
            "read",
            "VibeCAD_AnalyzeReadTemperatureResult",
        ),
        _definition(
            ANALYZE_SHOW_TEMPERATURE,
            "Show temperature.",
            "view",
            "show",
            "VibeCAD_AnalyzeShowTemperatureResult",
        ),
    )


def register_analyze_thermal_result_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in analyze_thermal_result_capability_definitions():
        registry.register_definition(definition)
