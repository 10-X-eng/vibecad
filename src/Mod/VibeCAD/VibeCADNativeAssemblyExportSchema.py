# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for human-authorized Assembly file export."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ASSEMBLY_EXPORT_CAPABILITY_NAME = "assembly.export"


def assembly_export_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_EXPORT_CAPABILITY_NAME,
        description="Export the active Assembly as ASMT.",
        primary_classification="export",
        variants=(
            NativeCapabilityVariant(
                operation="asmt",
                description="Export the active Assembly as ASMT.",
                action_ids=frozenset({"Assembly_ExportASMT"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndAuthorizedOutputPath",
                transaction_behavior="output",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
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
