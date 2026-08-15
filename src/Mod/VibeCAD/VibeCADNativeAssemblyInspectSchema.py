# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact read-only Assembly link inspection."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ASSEMBLY_INSPECT_CAPABILITY_NAME = "assembly.inspect"

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


def assembly_inspect_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_INSPECT_CAPABILITY_NAME,
        description=(
            "Read exact Assembly relationships selected by the human without "
            "changing selection, documents, views, workbenches, or ribbons."
        ),
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="linked_source",
                description=(
                    "Read the exact active linked Assembly behind the one "
                    "human-selected AssemblyLink without navigating to it."
                ),
                action_ids=frozenset({"Assembly_LinkSelectLinked"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanSelectionExactActiveAssemblyLink",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {"link": _OBJECT_REF},
                    "required": ["link"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_assembly_inspect_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_inspect_capability_definition())
