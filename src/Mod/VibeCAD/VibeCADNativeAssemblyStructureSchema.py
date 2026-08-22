# SPDX-License-Identifier: LGPL-2.1-or-later

"""Small provider contract for native Assembly structure mutations."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import placement_schema


_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
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
        "document_name": _OBJECT_NAME,
        "object_name": _OBJECT_NAME,
    },
    "required": ["document_name", "object_name"],
    "additionalProperties": False,
}
_VIEW_PLACEMENT = placement_schema()
_VIEW_MOVE = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "transform"},
                "component": _OBJECT_REF,
                "translation_mm": _VIEW_PLACEMENT["properties"]["origin_mm"],
                "rotation": _VIEW_PLACEMENT["properties"]["rotation"],
            },
            "required": ["kind", "component", "translation_mm"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "radial"},
                "component": _OBJECT_REF,
                "radial_distance_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 1_000_000.0,
                },
            },
            "required": ["kind", "component", "radial_distance_mm"],
            "additionalProperties": False,
        },
    ]
}
_SIMULATION_FORMULA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 512,
    "description": (
        "Assembly expression using time in seconds and initialValue; angular "
        "values are radians and linear values are millimeters."
    ),
}


def _simulation_motion(
    motion_type: str,
    value_name: str,
    value_schema: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "joint": _OBJECT_REF,
            "motion_type": {"type": "string", "const": motion_type},
            value_name: value_schema,
        },
        "required": ["joint", "motion_type", value_name],
        "additionalProperties": False,
    }


_SIMULATION_MOTION = {
    "oneOf": [
        _simulation_motion(
            "angular",
            "angular_speed_degrees_per_second",
            {
                "type": "number",
                "minimum": -1_000_000.0,
                "maximum": 1_000_000.0,
            },
        ),
        _simulation_motion(
            "linear",
            "linear_speed_mm_per_second",
            {
                "type": "number",
                "minimum": -1_000_000.0,
                "maximum": 1_000_000.0,
            },
        ),
        _simulation_motion("angular", "formula", _SIMULATION_FORMULA),
        _simulation_motion("linear", "formula", _SIMULATION_FORMULA),
    ]
}


def _parameters(
    properties: dict[str, Any],
    required: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _initial_placement_schema() -> dict[str, Any]:
    schema = placement_schema()
    schema["required"] = []
    schema["description"] = "Initial placement; omitted fields use identity."
    return schema


def _variant(
    operation: str,
    description: str,
    action_id: str,
    exact_target_type: str,
    parameters: dict[str, Any],
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"assemble"}),
        exact_target_type=exact_target_type,
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def assembly_structure_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="assembly.structure",
        description="Create components, solve, views, and simulations in the active assembly.",
        primary_classification="mutation",
        variants=(
            _variant(
                "create_assembly",
                "Create a root assembly or a child of parent_assembly.",
                "Assembly_CreateAssembly",
                "HumanActiveAssemblyAndExpectedCount",
                _parameters(
                    {"label": _LABEL, "parent_assembly": _OBJECT_REF},
                    ("label",),
                ),
            ),
            _variant(
                "insert_component",
                "Insert an available component source.",
                "Assembly_InsertLink",
                "HumanActiveAssemblyExactSourceAndExpectedCount",
                _parameters(
                    {
                        "assembly": _OBJECT_REF,
                        "source": _SOURCE_REF,
                        "label": _LABEL,
                        "placement": _initial_placement_schema(),
                    },
                    ("assembly", "source"),
                ),
            ),
            _variant(
                "create_part",
                "Create an editable Part and Body occurrence.",
                "Assembly_InsertNewPart",
                "HumanActiveAssemblyAndExpectedCount",
                _parameters(
                    {
                        "assembly": _OBJECT_REF,
                        "label": _LABEL,
                        "placement": _initial_placement_schema(),
                    },
                    ("assembly", "label"),
                ),
            ),
            _variant(
                "make_flexible",
                "Allow an AssemblyLink's internal joints to move.",
                "AssemblyContextMakeFlexible",
                "HumanActiveAssemblyExactAssemblyLinkAndFrozenAssemblyState",
                _parameters(
                    {"assembly": _OBJECT_REF, "link": _OBJECT_REF},
                    ("assembly", "link"),
                ),
            ),
            _variant(
                "make_rigid",
                "Treat an AssemblyLink as one rigid component.",
                "AssemblyContextMakeRigid",
                "HumanActiveAssemblyExactAssemblyLinkAndFrozenAssemblyState",
                _parameters(
                    {"assembly": _OBJECT_REF, "link": _OBJECT_REF},
                    ("assembly", "link"),
                ),
            ),
            _variant(
                "solve_assembly",
                "Solve the active assembly.",
                "Assembly_SolveAssembly",
                "HumanActiveAssemblyAndExactSolverState",
                _parameters({"assembly": _OBJECT_REF}, ("assembly",)),
            ),
            _variant(
                "create_view",
                "Create an exploded view from ordered component moves.",
                "Assembly_CreateView",
                "HumanActiveAssemblyExactViewStateAndMovableTargets",
                _parameters(
                    {
                        "assembly": _OBJECT_REF,
                        "label": _LABEL,
                        "parts_as_single_solid": {
                            "type": "boolean",
                            "default": False,
                        },
                        "moves": {
                            "type": "array",
                            "items": _VIEW_MOVE,
                            "minItems": 1,
                            "maxItems": 256,
                        },
                    },
                    ("assembly", "label", "moves"),
                ),
            ),
            _variant(
                "create_simulation",
                "Create a joint-motion simulation.",
                "Assembly_CreateSimulation",
                "HumanActiveAssemblyExactSimulationStateAndDriveableJoints",
                _parameters(
                    {
                        "label": {**_LABEL, "default": "Simulation"},
                        "time_start_seconds": {
                            "type": "number",
                            "minimum": -1_000_000.0,
                            "maximum": 1_000_000.0,
                            "default": 0.0,
                        },
                        "time_end_seconds": {
                            "type": "number",
                            "minimum": -1_000_000.0,
                            "maximum": 1_000_000.0,
                            "default": 10.0,
                        },
                        "output_time_step_seconds": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 1_000_000.0,
                            "default": 0.05,
                        },
                        "global_error_tolerance": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 1.0,
                            "default": 1.0e-6,
                        },
                        "frames_per_second": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 240,
                            "default": 30,
                        },
                        "motions": {
                            "type": "array",
                            "items": _SIMULATION_MOTION,
                            "minItems": 1,
                            "maxItems": 256,
                        },
                    },
                    ("motions",),
                ),
            ),
        ),
    )


def _focused_structure_definition(
    name: str,
    description: str,
    *operations: str,
) -> NativeCapabilityDefinition:
    by_operation = {
        variant.operation: variant
        for variant in assembly_structure_capability_definition().variants
    }
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification="mutation",
        variants=tuple(by_operation[operation] for operation in operations),
    )


def assembly_create_capability_definition() -> NativeCapabilityDefinition:
    return _focused_structure_definition(
        "assembly.create",
        "Create an empty Assembly.",
        "create_assembly",
    )


def assembly_insert_capability_definition() -> NativeCapabilityDefinition:
    return _focused_structure_definition(
        "assembly.insert",
        "Insert an available component source into the active Assembly.",
        "insert_component",
    )


def assembly_new_part_capability_definition() -> NativeCapabilityDefinition:
    return _focused_structure_definition(
        "assembly.new_part",
        "Create an editable Part in the active Assembly.",
        "create_part",
    )


def assembly_rigidity_capability_definition() -> NativeCapabilityDefinition:
    return _focused_structure_definition(
        "assembly.rigidity",
        "Set a nested AssemblyLink rigid or flexible.",
        "make_flexible",
        "make_rigid",
    )


def assembly_solve_capability_definition() -> NativeCapabilityDefinition:
    return _focused_structure_definition(
        "assembly.solve",
        "Solve the active Assembly.",
        "solve_assembly",
    )


def assembly_exploded_view_capability_definition() -> NativeCapabilityDefinition:
    return _focused_structure_definition(
        "assembly.exploded_view",
        "Create an exploded view.",
        "create_view",
    )


def assembly_motion_study_capability_definition() -> NativeCapabilityDefinition:
    return _focused_structure_definition(
        "assembly.motion_study",
        "Create a joint-motion simulation.",
        "create_simulation",
    )


def register_assembly_structure_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in (
        assembly_create_capability_definition(),
        assembly_insert_capability_definition(),
        assembly_new_part_capability_definition(),
        assembly_rigidity_capability_definition(),
        assembly_solve_capability_definition(),
        assembly_exploded_view_capability_definition(),
        assembly_motion_study_capability_definition(),
    ):
        registry.register_definition(definition)
