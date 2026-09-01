# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for CAM tool mutations."""

from __future__ import annotations

from dataclasses import replace

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeManufactureToolSchema import manufacture_tool_capability_definition


MANUFACTURE_FOCUSED_TOOL_CAPABILITIES = {
    "create_controller": "manufacture.add_tool",
    "update_controller": "manufacture.set_controller",
    "update_tool_bit": "manufacture.update_tool",
}


def _focused_variant(
    operation: str,
    variant: NativeCapabilityVariant,
) -> NativeCapabilityVariant:
    if operation not in {"update_controller", "update_tool_bit"}:
        return variant
    parameters = dict(variant.parameters)
    properties = dict(parameters["properties"])
    if operation == "update_controller":
        controller = dict(properties["controller"])
        controller_properties = dict(controller["properties"])
        controller_properties["tool_number"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": 10_000,
        }
        controller["properties"] = controller_properties
        properties["controller"] = controller
    else:
        changes = dict(properties["property_changes"])
        item = dict(changes["items"])
        item_properties = dict(item["properties"])
        item_properties["value"] = {
            "oneOf": [
                {"type": "number"},
                {"type": "string", "maxLength": 320},
                {"type": "boolean"},
            ]
        }
        item["properties"] = item_properties
        changes["items"] = item
        properties["property_changes"] = changes
    parameters["properties"] = properties
    return replace(variant, parameters=parameters)


def manufacture_focused_tool_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    variants = {
        variant.operation: variant
        for variant in manufacture_tool_capability_definition().variants
    }
    return tuple(
        NativeCapabilityDefinition(
            name=name,
            description=variants[operation].description,
            primary_classification="mutation",
            variants=(_focused_variant(operation, variants[operation]),),
        )
        for operation, name in MANUFACTURE_FOCUSED_TOOL_CAPABILITIES.items()
    )


def register_manufacture_focused_tool_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in manufacture_focused_tool_capability_definitions():
        registry.register_definition(definition)


__all__ = [
    "MANUFACTURE_FOCUSED_TOOL_CAPABILITIES",
    "manufacture_focused_tool_capability_definitions",
    "register_manufacture_focused_tool_capability_definitions",
]
