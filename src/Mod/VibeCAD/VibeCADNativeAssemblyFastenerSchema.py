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
_COUNT = {"type": "integer", "minimum": 0, "maximum": 256}
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}


def assembly_fastener_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_FASTENER_CAPABILITY_NAME,
        description=(
            "Create and modify exact standard-fastener occurrences in the "
            "human-active Assembly."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="insert_standard_fastener",
                description=(
                    "Insert one catalog-resolved hidden definition and one visible "
                    "occurrence into the unchanged human-active Assembly."
                ),
                action_ids=frozenset({"VibeCAD_InsertStandardFastener"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExactCatalogFastener",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "assembly": object_reference_schema(),
                        "label": LABEL_SCHEMA,
                        "definition": standard_fastener_definition_schema(),
                        "expected_state_sha256": _STATE_SHA256,
                        "expected_component_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100_000,
                        },
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": _COUNT,
                    },
                    (
                        "assembly",
                        "label",
                        "definition",
                        "expected_state_sha256",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                    ),
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
