# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact read-only Assembly link inspection."""

from __future__ import annotations

from dataclasses import replace

from VibeCADNativeAssemblyInspect import MAX_JOINT_CONNECTOR_PAIRS
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ASSEMBLY_INSPECT_CAPABILITY_NAME = "assembly.inspect"
ASSEMBLY_LINKED_ASSEMBLY_CAPABILITY_NAME = "assembly.linked_assembly"
ASSEMBLY_CONNECTORS_CAPABILITY_NAME = "assembly.connectors"

_OBJECT_REF = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
        }
    },
    "required": ["object_name"],
    "additionalProperties": False,
}


def _connector_parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "component": _OBJECT_REF,
            "joint_type": {
                "type": "string",
                "description": "Joint being created.",
                "enum": [
                    "fixed",
                    "revolute",
                    "cylindrical",
                    "slider",
                    "ball",
                    "distance",
                    "parallel",
                    "perpendicular",
                    "angle",
                    "rack_pinion",
                    "screw",
                    "belt",
                    "gears",
                ],
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4096,
                "default": 0,
            },
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 48,
                "default": 48,
            },
        },
        "required": ["component", "joint_type"],
        "additionalProperties": False,
    }


def _connector_pair_parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "first_component": _OBJECT_REF,
            "second_component": _OBJECT_REF,
            "joint_type": _connector_parameters()["properties"]["joint_type"],
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_JOINT_CONNECTOR_PAIRS,
                "default": 12,
            },
        },
        "required": ["first_component", "second_component", "joint_type"],
        "additionalProperties": False,
    }


def assembly_inspect_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_LINKED_ASSEMBLY_CAPABILITY_NAME,
        description="Read a nested AssemblyLink's source Assembly.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="linked_source",
                description="Read an AssemblyLink component's source assembly.",
                action_ids=frozenset({"Assembly_LinkSelectLinked"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="HumanSelectionExactActiveAssemblyLink",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {"link": _OBJECT_REF},
                    "required": ["link"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def _legacy_assembly_inspect_capability_definition() -> NativeCapabilityDefinition:
    return replace(
        assembly_inspect_capability_definition(),
        name=ASSEMBLY_INSPECT_CAPABILITY_NAME,
    )


def assembly_connectors_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_CONNECTORS_CAPABILITY_NAME,
        description="Find compatible endpoint pairs for a joint.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="find",
                description="Find endpoint pairs between two components.",
                action_ids=frozenset({"VibeCAD_NativeAssemblyConnectors"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveAssemblyComponentGeometry",
                transaction_behavior="none",
                background_required=False,
                parameters=_connector_pair_parameters(),
            ),
        ),
    )


def register_assembly_inspect_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_inspect_capability_definition())
    registry.register_definition(_legacy_assembly_inspect_capability_definition())


def register_assembly_connectors_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(assembly_connectors_capability_definition())
