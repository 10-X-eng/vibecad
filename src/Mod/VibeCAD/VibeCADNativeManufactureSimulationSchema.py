# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact background GL CAM simulation."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_SIMULATION_CAPABILITY_NAME = "manufacture.simulation"
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_EXACT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)


def manufacture_simulation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_SIMULATION_CAPABILITY_NAME,
        description=(
            "Prepare and present an exact CAM simulation without changing the "
            "document, History, selection, or visibility."
        ),
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="gl",
                description=(
                    "Open the GL simulator for one exact Job and an explicit ordered "
                    "subset of its active generated operations. Stock, tools, placed "
                    "G-code, and display meshes are prepared in the background."
                ),
                action_ids=frozenset({"CAM_SimulatorGL"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOrderedActiveOperationsAndGlQuality"
                ),
                transaction_behavior="presentation",
                background_required=True,
                parameters=_closed(
                    {
                        "job": _EXACT_TARGET,
                        "operations": {
                            "type": "array",
                            "items": _EXACT_TARGET,
                            "minItems": 1,
                            "maxItems": 64,
                            "uniqueItems": True,
                            "description": "Active generated operations in current Job order.",
                        },
                        "quality": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "description": (
                                "GL simulation quality from 1 (low) through 10 (high), "
                                "matching the human simulator control."
                            ),
                        },
                    },
                    ("job", "operations", "quality"),
                ),
            ),
        ),
    )


def register_manufacture_simulation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_simulation_capability_definition())
