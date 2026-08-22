# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for Assembly joint diagnosis."""

from __future__ import annotations

from VibeCADNativeAssemblyComponentJoints import (
    DEFAULT_COMPONENT_JOINT_PAGE,
    MAX_COMPONENT_JOINT_PAGE,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_OBJECT_REF = {
    "type": "object",
    "properties": {"object_name": _OBJECT_NAME},
    "required": ["object_name"],
    "additionalProperties": False,
}


def _parameters(*, component: bool = False) -> dict:
    properties = {
        "offset": {"type": "integer", "minimum": 0, "maximum": 255, "default": 0},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_COMPONENT_JOINT_PAGE if component else 100,
            "default": DEFAULT_COMPONENT_JOINT_PAGE if component else 32,
        },
    }
    required = []
    if component:
        properties["component"] = _OBJECT_REF
        required.append("component")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _variant(
    operation: str,
    description: str,
    action_id: str,
    *,
    component: bool = False,
    provider_supplemental: bool = False,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"assemble"}),
        exact_target_type=(
            "HumanActiveAssemblyAndExactComponentJointGraph"
            if component
            else "HumanActiveAssemblyAndExactSolverDiagnosis"
        ),
        transaction_behavior="none",
        background_required=False,
        parameters=_parameters(component=component),
        provider_supplemental=provider_supplemental,
    )


def assembly_diagnosis_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="assembly.diagnose",
        description="Read Assembly joint health.",
        primary_classification="read",
        variants=(
            _variant(
                "select_conflicting_constraints",
                "Read joints with unsatisfied constraints.",
                "Assembly_SelectConflictingConstraints",
            ),
            _variant(
                "select_redundant_constraints",
                "Read fully redundant joints.",
                "Assembly_SelectRedundantConstraints",
            ),
            _variant(
                "select_partially_redundant_constraints",
                "Read partially redundant joints.",
                "Assembly_SelectPartiallyRedundantConstraints",
            ),
            _variant(
                "select_malformed_constraints",
                "Read malformed joints.",
                "Assembly_SelectMalformedConstraints",
            ),
            _variant(
                "read",
                "Read component, joint, solver, and degree-of-freedom counts.",
                "Assembly_SelectConflictingConstraints",
                provider_supplemental=True,
            ),
        ),
    )


def assembly_component_joints_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="assembly.component_joints",
        description="Read joints and coupling names for one component.",
        primary_classification="read",
        variants=(
            _variant(
                "read",
                "Read joints attached to one component.",
                "Assembly_SelectJointsOfComponent",
                component=True,
            ),
        ),
    )


def register_assembly_diagnosis_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_diagnosis_capability_definition())
    registry.register_definition(assembly_component_joints_capability_definition())
