# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for creating FEM fluid conditions."""

from __future__ import annotations

from VibeCADNativeAnalyzeFluidSchema import (
    _BOUNDARY_CONDITION,
    _COMPONENTS,
    _LABEL,
    _OBJECT_NAME,
    _SIGNED,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_INITIAL_VELOCITY = "analyze.initial_velocity"
ANALYZE_INITIAL_PRESSURE = "analyze.initial_pressure"
ANALYZE_BOUNDARY_VELOCITY = "analyze.boundary_velocity"
ANALYZE_FLUID_BOUNDARY = "analyze.fluid_boundary"
ANALYZE_EDIT_FLUID_BOUNDARY = "analyze.edit_fluid_boundary"

_INLET_TURBULENCE = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "const": "intensity_length_scale"},
        "intensity_ratio": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1.0,
        },
        "length_scale_m": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1.0e30,
        },
    },
    "required": ["kind", "intensity_ratio", "length_scale_m"],
    "additionalProperties": False,
    "description": "Inlet turbulence intensity and length scale.",
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _definition(
    name: str,
    description: str,
    action_id: str,
    parameters: dict,
    *,
    operation: str = "create",
    exact_target_type: str = "ExactFemAnalysisFluidConstraintAndGeometry",
) -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation=operation,
                description=description,
                action_ids=frozenset({action_id}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type=exact_target_type,
                transaction_behavior="document",
                background_required=False,
                parameters=parameters,
                provider_supplemental=True,
            ),
        ),
    )


def analyze_fluid_create_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    label = {**_LABEL}
    analysis_name = {**_OBJECT_NAME, "description": "Analysis object name."}
    source_name = {**_OBJECT_NAME, "description": "Geometry object name."}
    return (
        _definition(
            ANALYZE_INITIAL_VELOCITY,
            "Create an initial fluid velocity.",
            "VibeCAD_AnalyzeCreateInitialVelocity",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "components": _COMPONENTS,
                    "label": {**label, "default": "Initial flow velocity"},
                },
                ("analysis_name", "components"),
            ),
        ),
        _definition(
            ANALYZE_INITIAL_PRESSURE,
            "Create an initial fluid pressure.",
            "VibeCAD_AnalyzeCreateInitialPressure",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "pressure_pa": _SIGNED,
                    "label": {**label, "default": "Initial pressure"},
                },
                ("analysis_name", "pressure_pa"),
            ),
        ),
        _definition(
            ANALYZE_BOUNDARY_VELOCITY,
            "Create a velocity condition on exact geometry.",
            "VibeCAD_AnalyzeCreateBoundaryVelocity",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "source_name": source_name,
                    "geometry_names": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "pattern": r"^(?:Face|Edge)[1-9][0-9]*$",
                            "maxLength": 32,
                        },
                        "minItems": 1,
                        "maxItems": 256,
                        "uniqueItems": True,
                    },
                    "components": _COMPONENTS,
                    "normal_to_boundary": {"type": "boolean", "default": False},
                    "label": {**label, "default": "Boundary velocity"},
                },
                ("analysis_name", "source_name", "geometry_names", "components"),
            ),
        ),
        _definition(
            ANALYZE_FLUID_BOUNDARY,
            "Create an adiabatic CFD condition on exact faces.",
            "VibeCAD_AnalyzeCreateFluidBoundary",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "source_name": source_name,
                    "face_names": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "pattern": r"^Face[1-9][0-9]*$",
                            "maxLength": 32,
                        },
                        "minItems": 1,
                        "maxItems": 256,
                        "uniqueItems": True,
                    },
                    "condition": _BOUNDARY_CONDITION,
                    "turbulence": _INLET_TURBULENCE,
                    "label": {**label, "default": "Fluid boundary"},
                },
                ("analysis_name", "source_name", "face_names", "condition"),
            ),
        ),
        _definition(
            ANALYZE_EDIT_FLUID_BOUNDARY,
            "Edit one current CFD boundary.",
            "VibeCAD_AnalyzeUpdateFluidBoundary",
            _closed(
                {
                    "boundary_name": {
                        **_OBJECT_NAME,
                        "description": "Fluid boundary object name.",
                    },
                    "changes": {
                        "type": "object",
                        "properties": {
                            "label": label,
                            "geometry": _closed(
                                {
                                    "source_name": source_name,
                                    "face_names": {
                                        "type": "array",
                                        "items": {
                                            "type": "string",
                                            "pattern": r"^Face[1-9][0-9]*$",
                                            "maxLength": 32,
                                        },
                                        "minItems": 1,
                                        "maxItems": 256,
                                        "uniqueItems": True,
                                    },
                                },
                                ("source_name", "face_names"),
                            ),
                            "condition": _BOUNDARY_CONDITION,
                            "turbulence": _INLET_TURBULENCE,
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("boundary_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemFluidBoundary",
        ),
    )


def register_analyze_fluid_create_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in analyze_fluid_create_capability_definitions():
        registry.register_definition(definition)
