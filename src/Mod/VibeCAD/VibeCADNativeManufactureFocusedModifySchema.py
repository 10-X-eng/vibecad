# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for CAM operation edits and dress-ups."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureModifySchema import (
    manufacture_modify_capability_definition,
)


MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES = {
    "set_active": "manufacture.operations",
    "copy_operations": "manufacture.operations",
    "array_dressup": "manufacture.dressup",
    "axis_map_dressup": "manufacture.dressup",
    "dogbone_dressup": "manufacture.dressup",
    "drag_knife_dressup": "manufacture.dressup",
    "lead_in_out_dressup": "manufacture.dressup",
    "path_boundary_dressup": "manufacture.dressup",
    "mirror_dressup": "manufacture.dressup",
    "ramp_entry_dressup": "manufacture.dressup",
    "tag_dressup": "manufacture.dressup",
    "z_correct_dressup": "manufacture.dressup",
}
_DESCRIPTIONS = {
    "manufacture.operations": "Enable, disable, or copy exact Job operations.",
    "manufacture.dressup": (
        "Add parametric entry, holding, mapping, boundary, correction, or relief "
        "motion to one exact Job operation."
    ),
}


def manufacture_focused_modify_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    variants = manufacture_modify_capability_definition().variants
    names = tuple(dict.fromkeys(MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES.values()))
    return tuple(
        NativeCapabilityDefinition(
            name=name,
            description=_DESCRIPTIONS[name],
            primary_classification="mutation",
            variants=tuple(
                variant
                for variant in variants
                if MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES[variant.operation] == name
            ),
        )
        for name in names
    )


def register_manufacture_focused_modify_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in manufacture_focused_modify_capability_definitions():
        registry.register_definition(definition)


__all__ = [
    "MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES",
    "manufacture_focused_modify_capability_definitions",
    "register_manufacture_focused_modify_capability_definitions",
]
