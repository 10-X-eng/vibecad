# SPDX-License-Identifier: LGPL-2.1-or-later

"""Small provider contract for Assembly grounding and joints."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from VibeCADNativeAssemblyGrounding import MAX_GROUNDING_TARGETS
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ASSEMBLY_GROUND_CAPABILITY_NAME = "assembly.ground"
ASSEMBLY_JOINT_CAPABILITY_NAME = "assembly.joint"
ASSEMBLY_RELATION_CAPABILITY_NAME = "assembly.relation"
ASSEMBLY_COUPLING_CAPABILITY_NAME = "assembly.coupling"
ASSEMBLY_RACK_PINION_CAPABILITY_NAME = "assembly.rack_pinion"
ASSEMBLY_SCREW_CAPABILITY_NAME = "assembly.screw"
ASSEMBLY_BELT_CAPABILITY_NAME = "assembly.belt"
ASSEMBLY_GEARS_CAPABILITY_NAME = "assembly.gears"
ASSEMBLY_JOINT_CAPABILITY_NAMES = (
    ASSEMBLY_GROUND_CAPABILITY_NAME,
    ASSEMBLY_JOINT_CAPABILITY_NAME,
    ASSEMBLY_RELATION_CAPABILITY_NAME,
    ASSEMBLY_COUPLING_CAPABILITY_NAME,
    ASSEMBLY_RACK_PINION_CAPABILITY_NAME,
    ASSEMBLY_SCREW_CAPABILITY_NAME,
    ASSEMBLY_BELT_CAPABILITY_NAME,
    ASSEMBLY_GEARS_CAPABILITY_NAME,
)


_OBJECT_NAME = {
    "type": "string",
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_OBJECT_REFERENCE = {
    "type": "object",
    "properties": {"object_name": _OBJECT_NAME},
    "required": ["object_name"],
    "additionalProperties": False,
}
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_SIGNED_MM = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
}
_POSITIVE_MM = {
    "type": "number",
    "minimum": 1.0e-7,
    "maximum": 1_000_000.0,
}
_SIGNED_NONZERO_MM = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
}
_ANGLE = {"type": "number", "minimum": -180.0, "maximum": 180.0}
_ELEMENT_REFERENCE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 512,
    "description": "Geometry endpoint from assembly.connectors.",
    "pattern": (
        r"^(?:[Oo]rigin|(?:(?:[A-Za-z_][A-Za-z0-9_]*)\.)*"
        r"(?:Face|Edge|Vertex)[1-9][0-9]*)$"
    ),
}
_INTERFACE_REFERENCE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 64,
    "pattern": r"^[A-Za-z][A-Za-z0-9_]*$",
    "description": "Named endpoint from assembly.connectors.",
}


def _vector3(minimum: float, maximum: float) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "number", "minimum": minimum, "maximum": maximum},
        "minItems": 3,
        "maxItems": 3,
    }


_OFFSET = {
    "type": "object",
    "properties": {
        "translation_mm": _vector3(-1_000_000.0, 1_000_000.0),
        "rotation_axis": _vector3(-1.0, 1.0),
        "rotation_degrees": {
            "type": "number",
            "minimum": -360.0,
            "maximum": 360.0,
        },
    },
    "required": ["translation_mm", "rotation_axis", "rotation_degrees"],
    "additionalProperties": False,
}
_LEGACY_CONNECTOR = {
    "type": "object",
    "properties": {
        "component": _OBJECT_NAME,
        "element": _ELEMENT_REFERENCE,
        "interface": _INTERFACE_REFERENCE,
        "offset": _OFFSET,
    },
    "required": ["component"],
    "additionalProperties": False,
}
_CONNECTOR_VALUE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 512,
    "description": "Endpoint from assembly.connectors.",
    "pattern": (
        r"^(?:[Oo]rigin|(?:(?:[A-Za-z_][A-Za-z0-9_]*)\.)*"
        r"(?:Face|Edge|Vertex)[1-9][0-9]*|[A-Za-z][A-Za-z0-9_]{0,63})$"
    ),
}
_CONNECTOR = {
    "type": "object",
    "properties": {
        "component": _OBJECT_NAME,
        "connector_type": {
            "type": "string",
            "enum": ["element", "interface"],
        },
        "connector": _CONNECTOR_VALUE,
        "offset": _OFFSET,
    },
    "required": ["component", "connector_type", "connector"],
    "additionalProperties": False,
}
_LINEAR_LIMITS = {
    "type": "object",
    "properties": {
        "minimum_mm": _SIGNED_MM,
        "maximum_mm": _SIGNED_MM,
    },
    "required": [],
    "additionalProperties": False,
}
_ANGULAR_LIMITS = {
    "type": "object",
    "properties": {
        "minimum_degrees": _ANGLE,
        "maximum_degrees": _ANGLE,
    },
    "required": [],
    "additionalProperties": False,
}
_CYLINDRICAL_LIMITS = {
    "type": "object",
    "properties": {
        "minimum_mm": _SIGNED_MM,
        "maximum_mm": _SIGNED_MM,
        "minimum_degrees": _ANGLE,
        "maximum_degrees": _ANGLE,
    },
    "required": [],
    "additionalProperties": False,
}


def _parameters(
    properties: dict[str, Any],
    required: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": deepcopy(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _joint_parameters(
    *,
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
    connector: dict[str, Any] = _LEGACY_CONNECTOR,
) -> dict[str, Any]:
    return _parameters(
        {
            "first": connector,
            "second": connector,
            "label": _LABEL,
            **dict(properties or {}),
        },
        ("first", "second", *required),
    )


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict[str, Any],
    *,
    provider_supplemental: bool = False,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"assemble"}),
        exact_target_type="ActiveAssemblyJointIntent",
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
        provider_supplemental=provider_supplemental,
    )


def _focused_variant(
    description: str,
    action_ids: tuple[str, ...],
    parameters: dict[str, Any],
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation="create",
        description=description,
        action_ids=frozenset(action_ids),
        surface_ids=frozenset({"assemble"}),
        exact_target_type="ActiveAssemblyJointIntent",
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def _legacy_joint_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_JOINT_CAPABILITY_NAME,
        description="Join component origins or copy an assembly.connectors pair.",
        primary_classification="mutation",
        variants=(
            _variant(
                "create_fixed",
                "Lock two endpoints together.",
                "Assembly_CreateJointFixed",
                _joint_parameters(
                    properties={"reverse": {"type": "boolean"}},
                ),
            ),
            _variant(
                "create_revolute",
                "Join two coaxial endpoints with one rotational degree of freedom.",
                "Assembly_CreateJointRevolute",
                _joint_parameters(
                    properties={
                        "reverse": {"type": "boolean"},
                        "limits": _ANGULAR_LIMITS,
                    },
                ),
            ),
            _variant(
                "create_cylindrical",
                "Join two coaxial endpoints with rotation and axial sliding.",
                "Assembly_CreateJointCylindrical",
                _joint_parameters(
                    properties={
                        "reverse": {"type": "boolean"},
                        "limits": _CYLINDRICAL_LIMITS,
                    },
                ),
            ),
            _variant(
                "create_slider",
                "Join two aligned endpoints with one axial translation.",
                "Assembly_CreateJointSlider",
                _joint_parameters(
                    properties={
                        "reverse": {"type": "boolean"},
                        "limits": _LINEAR_LIMITS,
                    },
                ),
            ),
            _variant(
                "create_ball",
                "Join two endpoint centers with free rotation.",
                "Assembly_CreateJointBall",
                _joint_parameters(),
            ),
            _variant(
                "create_distance",
                "Maintain a signed separation between two geometry endpoints.",
                "Assembly_CreateJointDistance",
                _joint_parameters(
                    properties={
                        "distance_mm": _SIGNED_MM,
                        "reverse": {"type": "boolean"},
                    },
                    required=("distance_mm",),
                ),
            ),
            _variant(
                "create_parallel",
                "Keep two endpoint axes parallel.",
                "Assembly_CreateJointParallel",
                _joint_parameters(
                    properties={"reverse": {"type": "boolean"}},
                ),
            ),
            _variant(
                "create_perpendicular",
                "Keep two endpoint axes perpendicular.",
                "Assembly_CreateJointPerpendicular",
                _joint_parameters(),
            ),
            _variant(
                "create_angle",
                "Maintain an angle between two endpoint axes.",
                "Assembly_CreateJointAngle",
                _joint_parameters(
                    properties={"angle_degrees": _ANGLE},
                    required=("angle_degrees",),
                ),
            ),
            _variant(
                "create_rack_pinion",
                "Couple a rack Slider joint to a pinion Revolute joint.",
                "Assembly_CreateJointRackPinion",
                _joint_parameters(
                    properties={
                        "rack_slider_joint": _OBJECT_NAME,
                        "pinion_revolute_joint": _OBJECT_NAME,
                        "pitch_radius_mm": _SIGNED_NONZERO_MM,
                    },
                    required=(
                        "rack_slider_joint",
                        "pinion_revolute_joint",
                        "pitch_radius_mm",
                    ),
                ),
            ),
            _variant(
                "create_screw",
                "Couple a Slider joint to a coaxial Revolute joint by thread pitch.",
                "Assembly_CreateJointScrew",
                _joint_parameters(
                    properties={
                        "slider_joint": _OBJECT_NAME,
                        "screw_revolute_joint": _OBJECT_NAME,
                        "thread_pitch_mm": _SIGNED_NONZERO_MM,
                    },
                    required=(
                        "slider_joint",
                        "screw_revolute_joint",
                        "thread_pitch_mm",
                    ),
                ),
            ),
            _variant(
                "create_belt",
                "Couple two Revolute pulley joints in the same rotation direction.",
                "Assembly_CreateJointBelt",
                _joint_parameters(
                    properties={
                        "first_revolute_joint": _OBJECT_NAME,
                        "second_revolute_joint": _OBJECT_NAME,
                        "radius1_mm": _POSITIVE_MM,
                        "radius2_mm": _POSITIVE_MM,
                    },
                    required=(
                        "first_revolute_joint",
                        "second_revolute_joint",
                        "radius1_mm",
                        "radius2_mm",
                    ),
                ),
            ),
            _variant(
                "create_gears",
                "Couple two Revolute gear joints in opposite rotation directions.",
                "Assembly_CreateJointGears",
                _joint_parameters(
                    properties={
                        "first_revolute_joint": _OBJECT_NAME,
                        "second_revolute_joint": _OBJECT_NAME,
                        "radius1_mm": _POSITIVE_MM,
                        "radius2_mm": _POSITIVE_MM,
                    },
                    required=(
                        "first_revolute_joint",
                        "second_revolute_joint",
                        "radius1_mm",
                        "radius2_mm",
                    ),
                ),
            ),
        ),
    )


def assembly_joint_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_JOINT_CAPABILITY_NAME,
        description="Create a component joint.",
        primary_classification="mutation",
        variants=(
            _focused_variant(
                "Create a fixed, revolute, cylindrical, slider, or ball joint.",
                (
                    "Assembly_CreateJointFixed",
                    "Assembly_CreateJointRevolute",
                    "Assembly_CreateJointCylindrical",
                    "Assembly_CreateJointSlider",
                    "Assembly_CreateJointBall",
                ),
                _joint_parameters(
                    properties={
                        "joint_type": {
                            "type": "string",
                            "enum": [
                                "fixed",
                                "revolute",
                                "cylindrical",
                                "slider",
                                "ball",
                            ],
                        },
                        "reverse": {"type": "boolean"},
                        "limits": {
                            "type": "object",
                            "properties": {
                                "minimum_mm": _SIGNED_MM,
                                "maximum_mm": _SIGNED_MM,
                                "minimum_degrees": _ANGLE,
                                "maximum_degrees": _ANGLE,
                            },
                            "required": [],
                            "additionalProperties": False,
                        },
                    },
                    required=("joint_type",),
                    connector=_CONNECTOR,
                ),
            ),
        ),
        preserve_operation_branches=False,
    )


def assembly_relation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_RELATION_CAPABILITY_NAME,
        description=(
            "Create a distance, parallel, perpendicular, or angle relation "
            "between endpoints."
        ),
        primary_classification="mutation",
        variants=(
            _focused_variant(
                "Create a distance, parallel, perpendicular, or angle relation.",
                (
                    "Assembly_CreateJointDistance",
                    "Assembly_CreateJointParallel",
                    "Assembly_CreateJointPerpendicular",
                    "Assembly_CreateJointAngle",
                ),
                _joint_parameters(
                    properties={
                        "relation": {
                            "type": "string",
                            "enum": [
                                "distance",
                                "parallel",
                                "perpendicular",
                                "angle",
                            ],
                        },
                        "distance_mm": {
                            **_SIGNED_MM,
                            "description": "Signed distance for a distance relation.",
                        },
                        "angle_degrees": {
                            **_ANGLE,
                            "description": "Angle for an angle relation.",
                        },
                        "reverse": {"type": "boolean"},
                    },
                    required=("relation",),
                    connector=_CONNECTOR,
                ),
            ),
        ),
        preserve_operation_branches=False,
    )


def assembly_coupling_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_COUPLING_CAPABILITY_NAME,
        description=(
            "Create rack-pinion, screw, belt, or gear motion coupling from existing "
            "Slider or Revolute joints. Read joint and component names with "
            "assembly.component_joints."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "rack_pinion",
                "Couple a rack Slider joint to a pinion Revolute joint.",
                "Assembly_CreateJointRackPinion",
                _parameters(
                    {
                        "first_joint": _OBJECT_NAME,
                        "first_component": _OBJECT_NAME,
                        "second_joint": _OBJECT_NAME,
                        "second_component": _OBJECT_NAME,
                        "pitch_radius_mm": _SIGNED_NONZERO_MM,
                        "label": _LABEL,
                    },
                    (
                        "first_joint",
                        "first_component",
                        "second_joint",
                        "second_component",
                        "pitch_radius_mm",
                    ),
                ),
            ),
            _variant(
                "screw",
                "Couple a Slider joint to a coaxial Revolute joint.",
                "Assembly_CreateJointScrew",
                _parameters(
                    {
                        "first_joint": _OBJECT_NAME,
                        "first_component": _OBJECT_NAME,
                        "second_joint": _OBJECT_NAME,
                        "second_component": _OBJECT_NAME,
                        "thread_pitch_mm": _SIGNED_NONZERO_MM,
                        "label": _LABEL,
                    },
                    (
                        "first_joint",
                        "first_component",
                        "second_joint",
                        "second_component",
                        "thread_pitch_mm",
                    ),
                ),
            ),
            _variant(
                "belt",
                "Couple two Revolute pulley joints in the same direction.",
                "Assembly_CreateJointBelt",
                _parameters(
                    {
                        "first_joint": _OBJECT_NAME,
                        "first_component": _OBJECT_NAME,
                        "second_joint": _OBJECT_NAME,
                        "second_component": _OBJECT_NAME,
                        "radius1_mm": _POSITIVE_MM,
                        "radius2_mm": _POSITIVE_MM,
                        "label": _LABEL,
                    },
                    (
                        "first_joint",
                        "first_component",
                        "second_joint",
                        "second_component",
                        "radius1_mm",
                        "radius2_mm",
                    ),
                ),
            ),
            _variant(
                "gears",
                "Couple two Revolute gear joints in opposite directions.",
                "Assembly_CreateJointGears",
                _parameters(
                    {
                        "first_joint": _OBJECT_NAME,
                        "first_component": _OBJECT_NAME,
                        "second_joint": _OBJECT_NAME,
                        "second_component": _OBJECT_NAME,
                        "radius1_mm": _POSITIVE_MM,
                        "radius2_mm": _POSITIVE_MM,
                        "label": _LABEL,
                    },
                    (
                        "first_joint",
                        "first_component",
                        "second_joint",
                        "second_component",
                        "radius1_mm",
                        "radius2_mm",
                    ),
                ),
            ),
        ),
        preserve_operation_branches=True,
    )


def _dedicated_coupling_definition(
    *,
    name: str,
    operation: str,
    description: str,
    action_id: str,
    values: dict[str, Any],
    sides: dict[str, str] | None = None,
) -> NativeCapabilityDefinition:
    side_descriptions = sides or {
        "first_joint": "Prerequisite joint for first_component.",
        "first_component": "Moving side of first_joint.",
        "second_joint": "Prerequisite joint for second_component.",
        "second_component": "Moving side of second_joint.",
    }
    properties = {
        **{
            field: {**_OBJECT_REFERENCE, "description": field_description}
            for field, field_description in side_descriptions.items()
        },
        **values,
        "label": _LABEL,
    }
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification="mutation",
        variants=(
            _variant(
                operation,
                description,
                action_id,
                _parameters(
                    properties,
                    (*side_descriptions, *values),
                ),
            ),
        ),
    )


def assembly_rack_pinion_capability_definition() -> NativeCapabilityDefinition:
    return _dedicated_coupling_definition(
        name=ASSEMBLY_RACK_PINION_CAPABILITY_NAME,
        operation="rack_pinion",
        description=(
            "Couple a rack Slider and perpendicular pinion Revolute "
            "joint by pitch radius."
        ),
        action_id="Assembly_CreateJointRackPinion",
        sides={
            "slider_joint": "Rack Slider joint.",
            "rack_component": "Moving rack component.",
            "revolute_joint": "Pinion Revolute joint.",
            "pinion_component": "Moving pinion component.",
        },
        values={
            "pinion_pitch_radius_mm": {
                **_SIGNED_NONZERO_MM,
                "description": "Pinion pitch radius.",
            }
        },
    )


def assembly_screw_capability_definition() -> NativeCapabilityDefinition:
    return _dedicated_coupling_definition(
        name=ASSEMBLY_SCREW_CAPABILITY_NAME,
        operation="screw",
        description="Couple a Slider and coaxial Revolute joint by screw lead.",
        action_id="Assembly_CreateJointScrew",
        sides={
            "slider_joint": "Slider joint.",
            "slider_component": "Moving component of slider_joint.",
            "revolute_joint": "Coaxial Revolute joint.",
            "revolute_component": "Moving component of revolute_joint.",
        },
        values={
            "lead_mm": {
                **_SIGNED_NONZERO_MM,
                "description": "Axial travel per revolution.",
            }
        },
    )


def assembly_belt_capability_definition() -> NativeCapabilityDefinition:
    return _dedicated_coupling_definition(
        name=ASSEMBLY_BELT_CAPABILITY_NAME,
        operation="belt",
        description="Couple two pulley rotations in the same direction.",
        action_id="Assembly_CreateJointBelt",
        values={
            "first_pulley_radius_mm": {
                **_POSITIVE_MM,
                "description": "First pulley radius.",
            },
            "second_pulley_radius_mm": {
                **_POSITIVE_MM,
                "description": "Second pulley radius.",
            },
        },
    )


def assembly_gears_capability_definition() -> NativeCapabilityDefinition:
    return _dedicated_coupling_definition(
        name=ASSEMBLY_GEARS_CAPABILITY_NAME,
        operation="gears",
        description="Couple two gear rotations in opposite directions.",
        action_id="Assembly_CreateJointGears",
        values={
            "first_pitch_radius_mm": {
                **_POSITIVE_MM,
                "description": "First gear pitch radius.",
            },
            "second_pitch_radius_mm": {
                **_POSITIVE_MM,
                "description": "Second gear pitch radius.",
            },
        },
    )


def assembly_ground_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ASSEMBLY_GROUND_CAPABILITY_NAME,
        description="Ground or unground Assembly components.",
        primary_classification="mutation",
        variants=(
            _variant(
                "set_grounded",
                "Ground components.",
                "Assembly_ToggleGrounded",
                _parameters(
                    {
                        "components": {
                            "type": "array",
                            "items": _OBJECT_NAME,
                            "minItems": 1,
                            "maxItems": MAX_GROUNDING_TARGETS,
                            "uniqueItems": True,
                        },
                    },
                    ("components",),
                ),
            ),
            _variant(
                "set_movable",
                "Unground components.",
                "Assembly_ToggleGrounded",
                _parameters(
                    {
                        "components": {
                            "type": "array",
                            "items": _OBJECT_NAME,
                            "minItems": 1,
                            "maxItems": MAX_GROUNDING_TARGETS,
                            "uniqueItems": True,
                        },
                    },
                    ("components",),
                ),
                provider_supplemental=True,
            ),
        ),
    )


def register_assembly_joint_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(assembly_ground_capability_definition())
    registry.register_definition(assembly_joint_capability_definition())
    registry.register_definition(assembly_relation_capability_definition())
    registry.register_definition(assembly_coupling_capability_definition())
    registry.register_definition(assembly_rack_pinion_capability_definition())
    registry.register_definition(assembly_screw_capability_definition())
    registry.register_definition(assembly_belt_capability_definition())
    registry.register_definition(assembly_gears_capability_definition())
