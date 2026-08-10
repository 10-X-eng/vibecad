# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for human-authorized Assembly file export."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ASSEMBLY_EXPORT_CAPABILITY_NAME = "assembly.export"

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
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_COUNT = {"type": "integer", "minimum": 0, "maximum": 256}


def assembly_export_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_EXPORT_CAPABILITY_NAME,
        description=(
            "Export the exact human-active Assembly only after VibeCAD asks "
            "the human to authorize one destination. Never accepts a path from AI."
        ),
        primary_classification="export",
        variants=(
            NativeCapabilityVariant(
                operation="asmt",
                description=(
                    "Ask the human for an ASMT destination, then atomically export "
                    "the unchanged active Assembly through its native serializer."
                ),
                action_ids=frozenset({"Assembly_ExportASMT"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndAuthorizedOutputPath",
                transaction_behavior="output",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "expected_state_sha256": _STATE_SHA256,
                        "expected_component_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100_000,
                        },
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": _COUNT,
                    },
                    "required": [
                        "assembly",
                        "expected_state_sha256",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_assembly_export_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_export_capability_definition())
