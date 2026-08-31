# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for CAM inspection."""

from __future__ import annotations

from dataclasses import replace

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeManufactureInspectSchema import (
    manufacture_inspect_capability_definition,
)


MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES = {
    "list_setups": "manufacture.setups",
    "list_remaining_stock": "manufacture.remaining_stock",
    "read_job": "manufacture.read_setup",
    "search_setup_options": "manufacture.setup_options",
    "validate_job": "manufacture.validate",
    "inspect_toolpath": "manufacture.toolpath",
    "detect_loop": "manufacture.loop",
    "read_model_geometry": "manufacture.geometry",
    "read_thread_catalog": "manufacture.threads",
}
_SHARED_CAPABILITIES = frozenset(
    {
        "manufacture.setups",
        "manufacture.setup_options",
        "manufacture.geometry",
    }
)


def _focused_variant(
    operation: str,
    variant: NativeCapabilityVariant,
) -> NativeCapabilityVariant:
    if operation != "read_model_geometry":
        return variant
    parameters = dict(variant.parameters)
    properties = dict(parameters["properties"])
    properties["page_size"] = {
        **properties["page_size"],
        "maximum": 24,
        "default": 24,
        "description": "Return 1 through 24 ordered elements.",
    }
    parameters["properties"] = properties
    return replace(variant, parameters=parameters)


def manufacture_focused_inspect_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    variants = {
        variant.operation: variant
        for variant in manufacture_inspect_capability_definition().variants
    }
    return tuple(
        NativeCapabilityDefinition(
            name=name,
            description=variants[operation].description,
            primary_classification="read",
            variants=(_focused_variant(operation, variants[operation]),),
        )
        for operation, name in MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES.items()
    )


def register_manufacture_focused_inspect_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in manufacture_focused_inspect_capability_definitions():
        if definition.name in _SHARED_CAPABILITIES:
            registry.register_shared_definition(definition)
        else:
            registry.register_definition(definition)


__all__ = [
    "MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES",
    "manufacture_focused_inspect_capability_definitions",
    "register_manufacture_focused_inspect_capability_definitions",
]
