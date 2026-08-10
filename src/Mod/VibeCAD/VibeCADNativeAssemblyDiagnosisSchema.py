# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for exact Assembly solver-diagnosis reads."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


_OBJECT_REF = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
        }
    },
    "required": ["object_name"],
    "additionalProperties": False,
}
_COUNT = {
    "type": "integer",
    "minimum": 0,
    "maximum": 256,
}


def _parameters(category_count_field: str) -> dict:
    properties = {
        "assembly": _OBJECT_REF,
        "expected_diagnosis_state_sha256": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "pattern": r"^[0-9a-f]{64}$",
        },
        "expected_component_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100_000,
        },
        "expected_grounded_count": _COUNT,
        "expected_joint_count": _COUNT,
        category_count_field: _COUNT,
        "offset": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 32,
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def assembly_diagnosis_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="assembly.diagnose",
        description=(
            "Read the exact most-recent native Assembly solver diagnosis without "
            "changing the human's selection."
        ),
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="select_conflicting_constraints",
                description=(
                    "Read one exact bounded page of joints the human conflict "
                    "selection command identifies."
                ),
                action_ids=frozenset({"Assembly_SelectConflictingConstraints"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExactSolverDiagnosis",
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters("expected_conflicting_count"),
            ),
            NativeCapabilityVariant(
                operation="select_redundant_constraints",
                description=(
                    "Read one exact bounded page of joints the human redundant "
                    "selection command identifies."
                ),
                action_ids=frozenset({"Assembly_SelectRedundantConstraints"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExactSolverDiagnosis",
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters("expected_redundant_count"),
            ),
            NativeCapabilityVariant(
                operation="select_partially_redundant_constraints",
                description=(
                    "Read one exact bounded page of joints the human partial-"
                    "redundancy selection command identifies."
                ),
                action_ids=frozenset(
                    {"Assembly_SelectPartiallyRedundantConstraints"}
                ),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExactSolverDiagnosis",
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters("expected_partially_redundant_count"),
            ),
            NativeCapabilityVariant(
                operation="select_malformed_constraints",
                description=(
                    "Read one exact bounded page of joints the human malformed "
                    "selection command identifies after a Fixed-bundle drag solve."
                ),
                action_ids=frozenset({"Assembly_SelectMalformedConstraints"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyAndExactSolverDiagnosis",
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters("expected_malformed_count"),
            ),
        ),
    )


def register_assembly_diagnosis_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_diagnosis_capability_definition())
