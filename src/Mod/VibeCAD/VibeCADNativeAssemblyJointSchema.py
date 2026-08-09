# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact Assembly joint mutations."""

from __future__ import annotations

from VibeCADNativeAssemblyDistanceJoint import DISTANCE_MODES
from VibeCADNativeAssemblyGrounding import MAX_GROUNDING_TARGETS
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import placement_schema


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
_CONNECTOR_PATH = {
    "type": "string",
    "maxLength": 512,
    "pattern": (
        r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*)\.)*"
        r"(?:(?:Face|Edge|Vertex)[1-9][0-9]*)?$"
    ),
}
_GROUNDING_TARGET = {
    "type": "object",
    "properties": {
        "component": _OBJECT_REF,
        "expected_grounded": {"type": "boolean"},
    },
    "required": ["component", "expected_grounded"],
    "additionalProperties": False,
}
_JOINT_CONNECTOR = {
    "type": "object",
    "properties": {
        "component": _OBJECT_REF,
        "element_path": _CONNECTOR_PATH,
        "anchor_path": _CONNECTOR_PATH,
        "offset": placement_schema(),
        "expected_component_placement": placement_schema(),
    },
    "required": [
        "component",
        "element_path",
        "anchor_path",
        "offset",
        "expected_component_placement",
    ],
    "additionalProperties": False,
}
_ANGLE_LIMIT = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        "degrees": {
            "type": "number",
            "minimum": -180.0,
            "maximum": 180.0,
        },
    },
    "required": ["enabled", "degrees"],
    "additionalProperties": False,
}
_LENGTH_LIMIT = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        "mm": {
            "type": "number",
            "minimum": -1_000_000.0,
            "maximum": 1_000_000.0,
        },
    },
    "required": ["enabled", "mm"],
    "additionalProperties": False,
}
_LINEAR_LIMITS = {
    "type": "object",
    "properties": {
        "minimum": _LENGTH_LIMIT,
        "maximum": _LENGTH_LIMIT,
    },
    "required": ["minimum", "maximum"],
    "additionalProperties": False,
}
_REVOLUTE_LIMITS = {
    "type": "object",
    "properties": {
        "minimum": _ANGLE_LIMIT,
        "maximum": _ANGLE_LIMIT,
    },
    "required": ["minimum", "maximum"],
    "additionalProperties": False,
}
_CYLINDRICAL_LIMITS = {
    "type": "object",
    "properties": {
        "length": _LINEAR_LIMITS,
        "angle": _REVOLUTE_LIMITS,
    },
    "required": ["length", "angle"],
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
            NativeCapabilityVariant(
                operation="create_fixed",
                description=(
                    "Create one native Fixed joint between exact component-rooted "
                    "connectors, full attachment offsets, and expected live state "
                    "without opening the human task dialog or changing selection."
                ),
                action_ids=frozenset({"Assembly_CreateJointFixed"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type=(
                    "HumanActiveAssemblyExactFixedJointConnectorPairAndExpectedState"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "first": _JOINT_CONNECTOR,
                        "second": _JOINT_CONNECTOR,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "reverse": {"type": "boolean"},
                        "expected_component_count": _COUNT,
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 256,
                        },
                        "expected_solve_on_creation": {"type": "boolean"},
                    },
                    "required": [
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_revolute",
                description=(
                    "Create one native Revolute joint from exact component-rooted "
                    "connectors, full offsets, reverse state, angular limits, and "
                    "expected live Assembly state without changing selection."
                ),
                action_ids=frozenset({"Assembly_CreateJointRevolute"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type=(
                    "HumanActiveAssemblyExactRevoluteJointConnectorPairAndExpectedState"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "first": _JOINT_CONNECTOR,
                        "second": _JOINT_CONNECTOR,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "reverse": {"type": "boolean"},
                        "limits": _REVOLUTE_LIMITS,
                        "expected_component_count": _COUNT,
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 256,
                        },
                        "expected_solve_on_creation": {"type": "boolean"},
                    },
                    "required": [
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "limits",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_cylindrical",
                description=(
                    "Create one native Cylindrical joint from exact component-rooted "
                    "connectors, full offsets, reverse state, independent linear and "
                    "angular limits, and expected live Assembly state without "
                    "changing selection."
                ),
                action_ids=frozenset({"Assembly_CreateJointCylindrical"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type=(
                    "HumanActiveAssemblyExactCylindricalJointConnectorPairAndExpectedState"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "first": _JOINT_CONNECTOR,
                        "second": _JOINT_CONNECTOR,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "reverse": {"type": "boolean"},
                        "limits": _CYLINDRICAL_LIMITS,
                        "expected_component_count": _COUNT,
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 256,
                        },
                        "expected_solve_on_creation": {"type": "boolean"},
                    },
                    "required": [
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "limits",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_slider",
                description=(
                    "Create one native Slider joint from exact component-rooted "
                    "connectors, full offsets, reverse state, independent linear "
                    "limits, and expected live Assembly state without changing "
                    "selection."
                ),
                action_ids=frozenset({"Assembly_CreateJointSlider"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type=(
                    "HumanActiveAssemblyExactSliderJointConnectorPairAndExpectedState"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "first": _JOINT_CONNECTOR,
                        "second": _JOINT_CONNECTOR,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "reverse": {"type": "boolean"},
                        "limits": _LINEAR_LIMITS,
                        "expected_component_count": _COUNT,
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 256,
                        },
                        "expected_solve_on_creation": {"type": "boolean"},
                    },
                    "required": [
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "limits",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_ball",
                description=(
                    "Create one native Ball joint from exact component-rooted "
                    "connectors, full attachment offsets, and expected live "
                    "Assembly state without exposing inapplicable reverse, "
                    "rotation, distance, or limit controls."
                ),
                action_ids=frozenset({"Assembly_CreateJointBall"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type=(
                    "HumanActiveAssemblyExactBallJointConnectorPairAndExpectedState"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "first": _JOINT_CONNECTOR,
                        "second": _JOINT_CONNECTOR,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "expected_component_count": _COUNT,
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 256,
                        },
                        "expected_solve_on_creation": {"type": "boolean"},
                    },
                    "required": [
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_distance",
                description=(
                    "Create one native geometry-aware Distance joint from exact "
                    "component-rooted connectors, full attachment offsets, a "
                    "signed distance, reverse state, the expected derived geometry "
                    "mode, and expected live Assembly state."
                ),
                action_ids=frozenset({"Assembly_CreateJointDistance"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type=(
                    "HumanActiveAssemblyExactDistanceJointConnectorPairGeometryModeAndExpectedState"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "first": _JOINT_CONNECTOR,
                        "second": _JOINT_CONNECTOR,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "reverse": {"type": "boolean"},
                        "distance_mm": {
                            "type": "number",
                            "minimum": -1_000_000.0,
                            "maximum": 1_000_000.0,
                        },
                        "expected_distance_mode": {
                            "type": "string",
                            "enum": sorted(DISTANCE_MODES),
                        },
                        "expected_component_count": _COUNT,
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 256,
                        },
                        "expected_solve_on_creation": {"type": "boolean"},
                    },
                    "required": [
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "distance_mm",
                        "expected_distance_mode",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_parallel",
                description=(
                    "Create one native Parallel joint from exact component-rooted "
                    "connectors, full attachment offsets, reverse state, and "
                    "expected live Assembly state."
                ),
                action_ids=frozenset({"Assembly_CreateJointParallel"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type=(
                    "HumanActiveAssemblyExactParallelJointConnectorPairAndExpectedState"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "assembly": _OBJECT_REF,
                        "first": _JOINT_CONNECTOR,
                        "second": _JOINT_CONNECTOR,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "reverse": {"type": "boolean"},
                        "expected_component_count": _COUNT,
                        "expected_grounded_count": _COUNT,
                        "expected_joint_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 256,
                        },
                        "expected_solve_on_creation": {"type": "boolean"},
                    },
                    "required": [
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
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
