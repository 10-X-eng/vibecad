# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider contract for Native Assembly simulation playback."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


_OBJECT_REF = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
        }
    },
    "required": ["object_name"],
    "additionalProperties": False,
}
_PLAYBACK_ID = {
    "type": "string",
    "minLength": 32,
    "maxLength": 32,
    "pattern": r"^[0-9a-f]{32}$",
}
_TIME = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
}
_DIRECTION = {
    "type": "string",
    "enum": ["forward", "backward"],
}


def _parameters(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _control_parameters(*, time: bool = False, direction: bool = False) -> dict:
    properties = {"playback_id": _PLAYBACK_ID}
    required = ["playback_id"]
    if time:
        properties["time_seconds"] = _TIME
        required.append("time_seconds")
    if direction:
        properties["direction"] = _DIRECTION
        required.append("direction")
    return _parameters(properties, tuple(required))


def assembly_playback_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="assembly.playback",
        description=(
            "Show a motion study at a time, or seek, step, play, pause, and close "
            "active playback."
        ),
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="show",
                description="Show a motion study at an optional time.",
                action_ids=frozenset({"AssemblyContextPlaySimulation"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveAssemblySimulationAndFrozenState",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_parameters(
                    {
                        "simulation": _OBJECT_REF,
                        "time_seconds": _TIME,
                        "mode": {
                            "type": "string",
                            "enum": ["hold", "forward", "backward"],
                            "default": "hold",
                        },
                    },
                    ("simulation",),
                ),
            ),
            NativeCapabilityVariant(
                operation="seek",
                description="Move active playback to a time.",
                action_ids=frozenset({"AssemblySimulationSeek"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="NativeOwnedAssemblyPlaybackAndTime",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_control_parameters(time=True),
            ),
            NativeCapabilityVariant(
                operation="step",
                description="Step active playback one frame.",
                action_ids=frozenset({"AssemblySimulationStep"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="NativeOwnedAssemblyPlayback",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_control_parameters(direction=True),
            ),
            NativeCapabilityVariant(
                operation="play",
                description="Play active playback forward or backward.",
                action_ids=frozenset({"AssemblySimulationPlay"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="NativeOwnedAssemblyPlayback",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_control_parameters(direction=True),
            ),
            NativeCapabilityVariant(
                operation="pause",
                description="Pause active playback.",
                action_ids=frozenset({"AssemblySimulationPause"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="NativeOwnedAssemblyPlayback",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_control_parameters(),
            ),
            NativeCapabilityVariant(
                operation="close",
                description="Close active playback.",
                action_ids=frozenset({"AssemblySimulationClose"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="NativeOwnedAssemblyPlayback",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_control_parameters(),
            ),
        ),
    )


def register_assembly_playback_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_playback_capability_definition())
