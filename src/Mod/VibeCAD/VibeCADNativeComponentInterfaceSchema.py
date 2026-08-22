# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for component-interface publication."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import object_reference_schema, parameters_schema
from vibescript_assembly_api import JOINT_TYPES


COMPONENT_INTERFACE_CAPABILITY_NAME = "component.interface"
COMPONENT_INTERFACES_CAPABILITY_NAME = "component.interfaces"


def component_interface_capability_definition() -> NativeCapabilityDefinition:
    fields = {
        "component": object_reference_schema(),
        "lcs": object_reference_schema(),
        "name": {
            "type": "string",
            "maxLength": 64,
            "pattern": r"^[A-Za-z][A-Za-z0-9_]{0,63}$",
        },
        "kind": {
            "type": "string",
            "enum": ["axis", "plane", "point", "frame"],
        },
        "allowed_joints": {
            "type": "array",
            "items": {"type": "string", "enum": list(JOINT_TYPES)},
            "maxItems": len(JOINT_TYPES),
            "uniqueItems": True,
        },
        "compatibility": {
            "type": "string",
            "maxLength": 128,
            "pattern": r"^(?:|[A-Za-z0-9][A-Za-z0-9_.:-]{0,127})$",
        },
    }
    return NativeCapabilityDefinition(
        name=COMPONENT_INTERFACE_CAPABILITY_NAME,
        description="Publish an LCS returned by component.interfaces.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="publish_interface",
                description="Publish or update the exact LCS.",
                action_ids=frozenset({"VibeCAD_PublishInterface"}),
                surface_ids=frozenset({"model", "assemble"}),
                exact_target_type="Component + LocalCoordinateSystem",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(fields, tuple(fields)),
            ),
        ),
    )


def component_interfaces_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=COMPONENT_INTERFACES_CAPABILITY_NAME,
        description="Find LCS references and published interfaces.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="find",
                description="Find publishable component LCS resources.",
                action_ids=frozenset({"VibeCAD_PublishInterface"}),
                surface_ids=frozenset({"model", "assemble"}),
                exact_target_type="Component LocalCoordinateSystem resources",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_component_interface_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(component_interface_capability_definition())


def register_component_interfaces_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(component_interfaces_capability_definition())
