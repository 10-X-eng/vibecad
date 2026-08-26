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
_COUNT = {"type": "integer", "minimum": 0, "maximum": 256}
_STATE_DIGEST = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}


def _parameters(
    *,
    component: bool = False,
    legacy_component: bool = False,
    category_count_field: str | None = None,
) -> dict:
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
    if component or legacy_component:
        properties["component"] = _OBJECT_REF
        required.append("component")
    if legacy_component:
        properties.update(
            {
                "assembly": _OBJECT_REF,
                "expected_joint_graph_state_sha256": _STATE_DIGEST,
                "expected_component_count": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100_000,
                },
                "expected_joint_count": _COUNT,
            }
        )
        required = list(properties)
    elif category_count_field:
        properties.update(
            {
                "assembly": _OBJECT_REF,
                "expected_diagnosis_state_sha256": _STATE_DIGEST,
                "expected_component_count": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100_000,
                },
                "expected_grounded_count": _COUNT,
                "expected_joint_count": _COUNT,
                category_count_field: _COUNT,
            }
        )
        # The frozen-state fields form the deprecated compatibility shape. The
        # current shape omits all of them and captures the active state itself;
        # runtime validation below enforces that callers cannot send a partial
        # legacy shape.
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
    category_count_field: str | None = None,
    legacy_component: bool = False,
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
        parameters=_parameters(
            component=component,
            legacy_component=legacy_component,
            category_count_field=category_count_field,
        ),
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
                category_count_field="expected_conflicting_count",
            ),
            _variant(
                "select_redundant_constraints",
                "Read fully redundant joints.",
                "Assembly_SelectRedundantConstraints",
                category_count_field="expected_redundant_count",
            ),
            _variant(
                "select_partially_redundant_constraints",
                "Read partially redundant joints.",
                "Assembly_SelectPartiallyRedundantConstraints",
                category_count_field="expected_partially_redundant_count",
            ),
            _variant(
                "select_malformed_constraints",
                "Read malformed joints.",
                "Assembly_SelectMalformedConstraints",
                category_count_field="expected_malformed_count",
            ),
            _variant(
                "select_joints_of_component",
                "Read joints attached to one component using the legacy frozen-state contract.",
                "Assembly_SelectJointsOfComponent",
                legacy_component=True,
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
