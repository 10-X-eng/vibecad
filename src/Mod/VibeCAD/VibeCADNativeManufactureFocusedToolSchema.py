# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for CAM tool mutations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureToolSchema import manufacture_tool_capability_definition


MANUFACTURE_FOCUSED_TOOL_CAPABILITIES = {
    "create_controller": "manufacture.add_tool",
    "update_controller": "manufacture.set_controller",
    "update_tool_bit": "manufacture.update_tool",
}


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
            variants=(variants[operation],),
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
