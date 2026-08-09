# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact Assembly structure mutations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


_LABEL = {
    "type": "string",
    "minLength": 1,
    "maxLength": 160,
}
_OBJECT_NAME = {
    "type": "string",
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_OBJECT_REF = {
    "type": "object",
    "properties": {"object_name": _OBJECT_NAME},
    "required": ["object_name"],
    "additionalProperties": False,
}


def assembly_structure_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="assembly.structure",
        description=(
            "Create and modify exact structures in the human-selected Assemble ribbon."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_assembly",
                description=(
                    "Create one root or nested native Assembly without changing activation."
                ),
                action_ids=frozenset({"Assembly_CreateAssembly"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExpectedCount",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "label": _LABEL,
                        "parent_assembly": {
                            "oneOf": [_OBJECT_REF, {"type": "null"}],
                        },
                        "expected_assembly_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 10_000,
                        },
                    },
                    "required": [
                        "label",
                        "parent_assembly",
                        "expected_assembly_count",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_assembly_structure_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_structure_capability_definition())
