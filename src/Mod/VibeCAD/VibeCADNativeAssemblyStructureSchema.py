# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact Assembly structure mutations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import placement_schema


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
_SOURCE_REF = {
    "type": "object",
    "properties": {
        "document_uid": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
        },
        "document_name": _OBJECT_NAME,
        "object_name": _OBJECT_NAME,
        "object_id": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2_147_483_647,
        },
    },
    "required": [
        "document_uid",
        "document_name",
        "object_name",
        "object_id",
    ],
    "additionalProperties": False,
}
_EXPECTED_COMPONENT_COUNT = {
    "type": "integer",
    "minimum": 0,
    "maximum": 100_000,
}
_EXPECTED_JOINT_COUNT = {
    "type": "integer",
    "minimum": 0,
    "maximum": 256,
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
            NativeCapabilityVariant(
                operation="insert_component",
                description=(
                    "Insert one exact existing Part, Body, primitive, or Assembly "
                    "into the human-active Assembly without changing activation."
                ),
                action_ids=frozenset({"Assembly_InsertLink"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyExactSourceAndExpectedCount",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "source": _SOURCE_REF,
                        "label": _LABEL,
                        "placement": placement_schema(),
                        "rigid": {
                            "oneOf": [
                                {"type": "boolean"},
                                {"type": "null"},
                            ],
                        },
                        "expected_component_count": _EXPECTED_COMPONENT_COUNT,
                    },
                    "required": [
                        "assembly",
                        "source",
                        "label",
                        "placement",
                        "rigid",
                        "expected_component_count",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_part",
                description=(
                    "Create one empty current-document Part and Body and insert "
                    "its occurrence into the human-active Assembly."
                ),
                action_ids=frozenset({"Assembly_InsertNewPart"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExpectedCount",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "label": _LABEL,
                        "placement": placement_schema(),
                        "expected_component_count": _EXPECTED_COMPONENT_COUNT,
                    },
                    "required": [
                        "assembly",
                        "label",
                        "placement",
                        "expected_component_count",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="solve_assembly",
                description=(
                    "Run the native solver for the exact human-active Assembly "
                    "and verify every bounded placement before commit."
                ),
                action_ids=frozenset({"Assembly_SolveAssembly"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExactSolverState",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "expected_solver_state_sha256": {
                            "type": "string",
                            "minLength": 64,
                            "maxLength": 64,
                            "pattern": r"^[0-9a-f]{64}$",
                        },
                        "expected_component_count": _EXPECTED_COMPONENT_COUNT,
                        "expected_grounded_count": _EXPECTED_JOINT_COUNT,
                        "expected_joint_count": _EXPECTED_JOINT_COUNT,
                    },
                    "required": [
                        "assembly",
                        "expected_solver_state_sha256",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
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
