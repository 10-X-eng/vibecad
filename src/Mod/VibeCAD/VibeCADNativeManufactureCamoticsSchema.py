# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for optional CAMotics inspection and launch."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_CAMOTICS_CAPABILITY_NAME = "manufacture.camotics"
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
_RESOLUTION = {
    "type": "string",
    "enum": ["low", "medium", "high"],
    "description": "CAMotics' explicit low, medium, or high stock resolution.",
}
_REQUEST = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "read_result"},
                "resolution": _RESOLUTION,
            },
            ("kind", "resolution"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "launch"},
                "resolution": _RESOLUTION,
            },
            ("kind", "resolution"),
        ),
    ],
    "description": (
        "read_result runs CAMotics and returns bounded path/surface facts without "
        "changing the document; launch opens the same frozen program in the fixed "
        "installed CAMotics application using host-owned temporary files."
    ),
}


def manufacture_camotics_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
        description="Inspect or open one exact ordered CAM program with CAMotics.",
        primary_classification="view",
        preserve_operation_discriminator=True,
        variants=(
            NativeCapabilityVariant(
                operation="camotics",
                description=(
                    "Read a bounded CAMotics result or launch the fixed installed "
                    "application for one exact Job and ordered active operations."
                ),
                action_ids=frozenset({"CAM_Camotics"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOrderedActiveOperationsCamoticsRequest"
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
                            "description": (
                                "Distinct exact active operations in their current "
                                "direct Job order."
                            ),
                        },
                        "request": _REQUEST,
                    },
                    ("job", "operations", "request"),
                ),
            ),
        ),
    )


def register_manufacture_camotics_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_camotics_capability_definition())
