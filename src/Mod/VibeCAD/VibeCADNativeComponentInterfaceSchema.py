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
from VibeCADReferenceContracts import (
    INTERFACE_FIT_CLASSES,
    INTERFACE_FIT_SCHEMA,
    INTERFACE_COUPLING_PARAMETERS_SCHEMA,
    INTERFACE_JOINT_PARAMETERS_SCHEMA,
)


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
            "enum": [
                "axis", "bearing_face", "bearing_seat", "bolt_pattern",
                "bore", "electrical_connector", "fixture", "fluid_port",
                "frame", "mounting_pattern", "plane", "planar_mate", "point",
                "shaft", "shaft_seat", "thread", "thread_axis", "tool",
            ],
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
        "fit": {
            "type": "object",
            "properties": {
                "schema": {"type": "string", "enum": [INTERFACE_FIT_SCHEMA]},
                "fit_class": {"type": "string", "enum": sorted(INTERFACE_FIT_CLASSES)},
                "designation": {"type": "string", "maxLength": 96},
                "minimum_clearance_mm": {"type": "number"},
                "maximum_clearance_mm": {"type": "number"},
            },
            "required": ["schema", "fit_class"],
            "additionalProperties": False,
        },
        "joint_parameters": {
            "type": "object",
            "properties": {
                "schema": {
                    "type": "string",
                    "enum": [INTERFACE_JOINT_PARAMETERS_SCHEMA],
                },
                "values": {
                    "type": "object",
                    "properties": {
                        "distance": {
                            "type": "object",
                            "properties": {"distance_mm": {"type": "number"}},
                            "required": ["distance_mm"],
                            "additionalProperties": False,
                        },
                        "angle": {
                            "type": "object",
                            "properties": {"angle_degrees": {"type": "number"}},
                            "required": ["angle_degrees"],
                            "additionalProperties": False,
                        },
                    },
                    "minProperties": 1,
                    "additionalProperties": False,
                },
            },
            "required": ["schema", "values"],
            "additionalProperties": False,
        },
        "coupling_parameters": {
            "type": "object",
            "properties": {
                "schema": {
                    "type": "string",
                    "enum": [INTERFACE_COUPLING_PARAMETERS_SCHEMA],
                },
                "values": {
                    "type": "object",
                    "properties": {
                        "rack_pinion": {
                            "type": "object",
                            "properties": {
                                "pitch_radius_mm": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                    "maximum": 1000000,
                                }
                            },
                            "additionalProperties": False,
                        },
                        "screw": {
                            "type": "object",
                            "properties": {
                                "lead_mm": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                    "maximum": 1000000,
                                }
                            },
                            "required": ["lead_mm"],
                            "additionalProperties": False,
                        },
                        "gears": {
                            "type": "object",
                            "properties": {
                                "pitch_radius_mm": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                    "maximum": 1000000,
                                }
                            },
                            "required": ["pitch_radius_mm"],
                            "additionalProperties": False,
                        },
                        "belt": {
                            "type": "object",
                            "properties": {
                                "pitch_radius_mm": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                    "maximum": 1000000,
                                }
                            },
                            "required": ["pitch_radius_mm"],
                            "additionalProperties": False,
                        },
                    },
                    "minProperties": 1,
                    "additionalProperties": False,
                },
            },
            "required": ["schema", "values"],
            "additionalProperties": False,
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
                parameters=parameters_schema(
                    fields,
                    tuple(
                        key for key in fields
                        if key not in {
                            "fit", "joint_parameters", "coupling_parameters"
                        }
                    ),
                ),
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
