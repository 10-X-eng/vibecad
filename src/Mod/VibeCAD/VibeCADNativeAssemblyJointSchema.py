# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact Assembly joint mutations."""

from __future__ import annotations

from VibeCADNativeAssemblyGrounding import MAX_GROUNDING_TARGETS
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


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
_COUNT = {"type": "integer", "minimum": 0, "maximum": 100_000}
_GROUNDING_TARGET = {
    "type": "object",
    "properties": {
        "component": _OBJECT_REF,
        "expected_grounded": {"type": "boolean"},
    },
    "required": ["component", "expected_grounded"],
    "additionalProperties": False,
}


def assembly_joint_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="assembly.joint",
        description=(
            "Create and change exact joints in the human-selected Assemble ribbon."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="set_grounded",
                description=(
                    "Ground or unground exact active Assembly components as one "
                    "atomic desired-state operation without changing activation. "
                    "Each expected_grounded value must be the current state and "
                    "opposite the requested grounded value."
                ),
                action_ids=frozenset({"Assembly_ToggleGrounded"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanActiveAssemblyExactComponentsAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "targets": {
                            "type": "array",
                            "items": _GROUNDING_TARGET,
                            "minItems": 1,
                            "maxItems": MAX_GROUNDING_TARGETS,
                            "uniqueItems": True,
                        },
                        "grounded": {"type": "boolean"},
                        "expected_component_count": _COUNT,
                        "expected_grounded_count": _COUNT,
                    },
                    "required": [
                        "assembly",
                        "targets",
                        "grounded",
                        "expected_component_count",
                        "expected_grounded_count",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_assembly_joint_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_joint_capability_definition())
