# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for CAM post output scope."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufacturePostSchema import (
    manufacture_post_capability_definition,
)


MANUFACTURE_FOCUSED_POST_CAPABILITIES = {
    "complete_job": "manufacture.post_job",
    "selected_operations": "manufacture.post_selected",
}
_DESCRIPTIONS = {
    "manufacture.post_job": "Post every active operation in one exact CAM Job.",
    "manufacture.post_selected": (
        "Post an ordered subset of active operations in one exact CAM Job."
    ),
}


def manufacture_focused_post_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    variants = manufacture_post_capability_definition().variants
    return tuple(
        NativeCapabilityDefinition(
            name=name,
            description=_DESCRIPTIONS[name],
            primary_classification="export",
            variants=tuple(
                variant
                for variant in variants
                if MANUFACTURE_FOCUSED_POST_CAPABILITIES[variant.operation] == name
            ),
        )
        for name in dict.fromkeys(MANUFACTURE_FOCUSED_POST_CAPABILITIES.values())
    )


def register_manufacture_focused_post_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in manufacture_focused_post_capability_definitions():
        registry.register_definition(definition)


__all__ = [
    "MANUFACTURE_FOCUSED_POST_CAPABILITIES",
    "manufacture_focused_post_capability_definitions",
    "register_manufacture_focused_post_capability_definitions",
]
