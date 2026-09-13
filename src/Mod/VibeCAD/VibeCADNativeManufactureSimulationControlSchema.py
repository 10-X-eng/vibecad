# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact task control for a Native-opened CAM simulation."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME = "manufacture.close_simulation"


def manufacture_simulation_control_capability_definition() -> (
    NativeCapabilityDefinition
):
    return NativeCapabilityDefinition(
        name=MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
        description="Close the exact Native-opened interactive CAM simulation.",
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="close",
                description="Close the active simulation and restore the model view.",
                action_ids=frozenset({"CAMSimulationClose"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="NativeOwnedCamSimulation",
                transaction_behavior="presentation",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "simulation_id": {
                            "type": "string",
                            "pattern": r"^[0-9a-f]{32}$",
                            "minLength": 32,
                            "maxLength": 32,
                        }
                    },
                    "required": ["simulation_id"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_manufacture_simulation_control_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(
        manufacture_simulation_control_capability_definition()
    )


__all__ = [
    "MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME",
    "manufacture_simulation_control_capability_definition",
    "register_manufacture_simulation_control_capability_definition",
]
