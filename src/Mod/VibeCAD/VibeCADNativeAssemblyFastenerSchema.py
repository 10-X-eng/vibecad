# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for standard fasteners on the Assemble ribbon."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    object_reference_schema,
    parameters_schema,
)
from VibeCADNativeModelFastenerSchema import standard_fastener_definition_schema


ASSEMBLY_FASTENER_CAPABILITY_NAME = "assembly.fastener"
ASSEMBLY_FASTENER_EDIT_CAPABILITY_NAME = "assembly.fastener_edit"


def assembly_fastener_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_FASTENER_CAPABILITY_NAME,
        description="Insert a standard fastener into the active Assembly.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="insert_standard_fastener",
                description="Insert one standard-fastener occurrence.",
                action_ids=frozenset({"VibeCAD_InsertStandardFastener"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExactCatalogFastener",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "definition": standard_fastener_definition_schema(),
                    },
                    ("label", "definition"),
                ),
            ),
        ),
    )


def assembly_fastener_edit_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_FASTENER_EDIT_CAPABILITY_NAME,
        description="Edit an existing standard fastener occurrence.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="edit_standard_fastener",
                description="Edit one standard-fastener occurrence in place.",
                action_ids=frozenset({"VibeCAD_EditStandardFastener"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="SelectedAssemblyFastenerOccurrenceAndDefinition",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "occurrence": object_reference_schema(),
                        "label": LABEL_SCHEMA,
                        "definition": standard_fastener_definition_schema(),
                    },
                    ("occurrence", "label", "definition"),
                ),
            ),
        ),
    )


def register_assembly_fastener_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_fastener_capability_definition())
    registry.register_definition(assembly_fastener_edit_capability_definition())
