# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for common CAM operations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureOperationSchema import (
    manufacture_adaptive_defaults_variant,
    manufacture_operation_capability_definition,
)


MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES = {
    "profile": "manufacture.profile",
    "pocket_shape": "manufacture.pocket",
    "pocket_3d": "manufacture.pocket_3d",
    "surface": "manufacture.surface",
    "waterline": "manufacture.waterline",
    "rotary_surface": "manufacture.rotary_surface",
    "mill_facing": "manufacture.face",
    "helix": "manufacture.helix",
    "adaptive": "manufacture.adaptive",
    "slot": "manufacture.slot",
    "drilling": "manufacture.drill",
    "thread_milling": "manufacture.thread_mill",
    "engrave": "manufacture.engrave",
    "deburr": "manufacture.deburr",
    "v_carve": "manufacture.v_carve",
    "array": "manufacture.array",
    "simple_copy": "manufacture.copy_path",
    "set_start_point": "manufacture.start_point",
}


def manufacture_focused_operation_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    variants = {
        variant.operation: variant
        for variant in manufacture_operation_capability_definition().variants
    }
    variants["adaptive"] = manufacture_adaptive_defaults_variant()
    return tuple(
        NativeCapabilityDefinition(
            name=name,
            description=variants[operation].description,
            primary_classification="mutation",
            variants=(variants[operation],),
        )
        for operation, name in MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES.items()
    )


def register_manufacture_focused_operation_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in manufacture_focused_operation_capability_definitions():
        registry.register_definition(definition)


__all__ = [
    "MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES",
    "manufacture_focused_operation_capability_definitions",
    "register_manufacture_focused_operation_capability_definitions",
]
