# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for creating an Assembly bill of materials."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeAssemblyBom import DEFAULT_BOM_COLUMNS


ASSEMBLY_BOM_CAPABILITY_NAME = "assembly.bom"
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_COLUMNS = {
    "type": "array",
    "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 129,
        "pattern": (
            r"^(?:\.[A-Za-z_][A-Za-z0-9_]{0,127}|"
            r"[^.\x00-\x1f\x7f][^\x00-\x1f\x7f]*)$"
        ),
    },
    "minItems": 1,
    "maxItems": 32,
    "uniqueItems": True,
    "default": list(DEFAULT_BOM_COLUMNS),
}


def assembly_bom_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_BOM_CAPABILITY_NAME,
        description="Create a BOM for the active Assembly.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description="Create a BOM for the active Assembly.",
                action_ids=frozenset({"Assembly_CreateBom"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyExactBomStateAndSourceGraph",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "columns": _COLUMNS,
                        "label": {**_LABEL, "default": "Bill of Materials"},
                        "detail_subassemblies": {
                            "type": "boolean",
                            "default": True,
                        },
                        "detail_parts": {"type": "boolean", "default": True},
                        "only_parts": {
                            "description": "Part containers and subassemblies only.",
                            "type": "boolean",
                            "default": False,
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_assembly_bom_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_bom_capability_definition())
