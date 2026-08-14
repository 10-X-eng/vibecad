# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for FEM thermal conditions."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import (
    _ANALYSIS_TARGET,
    _LABEL,
    _OBJECT_NAME,
    _STATE_SHA256,
)
from VibeCADNativeAnalyzeThermalValues import MODES, thermal_family_for_mode
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_THERMAL_CAPABILITY_NAME = "analyze.thermal"

_EXACT_TARGET_BY_OPERATION = {
    "create_convection": "ExactFemSurfaceConvectionAndGeometry",
    "create_radiation": "ExactFemSurfaceRadiationAndGeometry",
    "create_concentrated_heat_input": (
        "ExactFemConcentratedHeatInputAndGeometry"
    ),
    "create_total_body_power": "ExactFemTotalBodyPowerAndGeometry",
    "update_initial_temperature": "ExactFemInitialTemperature",
    "update_surface_heat_flux": "ExactFemSurfaceHeatFluxAndGeometry",
    "update_convection": "ExactFemSurfaceConvectionAndGeometry",
    "update_radiation": "ExactFemSurfaceRadiationAndGeometry",
    "update_boundary_temperature": "ExactFemBoundaryTemperatureAndGeometry",
    "update_concentrated_heat_input": (
        "ExactFemConcentratedHeatInputAndGeometry"
    ),
    "update_mass_heat_generation": "ExactFemMassHeatGenerationAndGeometry",
    "update_total_body_power": "ExactFemTotalBodyPowerAndGeometry",
}
_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_TEMPERATURE = {"type": "number", "minimum": 0.0, "maximum": 1.0e30}
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e30}
_NONZERO = {
    "anyOf": [
        {"type": "number", "minimum": -1.0e30, "exclusiveMaximum": 0.0},
        {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e30},
    ]
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _references(kinds: tuple[str, ...]) -> dict:
    pattern = "|".join(kinds)
    return {
        "type": "array",
        "items": _closed(
            {
                "object_name": _OBJECT_NAME,
                "expected_state_sha256": _STATE_SHA256,
                "subelements": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "pattern": rf"^(?:{pattern})[1-9][0-9]*$",
                        "maxLength": 32,
                    },
                    "minItems": 1,
                    "maxItems": 256,
                    "uniqueItems": True,
                },
            },
            ("object_name", "expected_state_sha256", "subelements"),
        ),
        "minItems": 1,
        "maxItems": 64,
        "description": "Exact current geometry using one common subelement kind.",
    }


def _value_schemas(mode: str) -> dict[str, dict]:
    return {
        "initial_temperature": {"temperature_k": _TEMPERATURE},
        "surface_heat_flux": {"heat_flux_w_m2": _NONZERO},
        "convection": {
            "ambient_temperature_k": _TEMPERATURE,
            "film_coefficient_w_m2_k": _POSITIVE,
        },
        "radiation": {
            "ambient_temperature_k": _TEMPERATURE,
            "emissivity": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1.0,
            },
        },
        "boundary_temperature": {"temperature_k": _TEMPERATURE},
        "concentrated_heat_input": {"power_w": _NONZERO},
        "mass_heat_generation": {"dissipation_rate_w_kg": _NONZERO},
        "total_body_power": {"total_power_w": _NONZERO},
    }[mode]


def _reference_schema(mode: str) -> dict | None:
    family = thermal_family_for_mode(mode)
    if family == "initial_temperature":
        return None
    if family == "surface_condition":
        return _references(("Edge", "Face"))
    if family == "nodal_condition":
        return _references(("Vertex", "Edge", "Face"))
    return _references(("Solid", "Face"))


def _create(mode: str) -> dict:
    fields = {
        "analysis": _ANALYSIS_TARGET,
        "label": _LABEL,
        **_value_schemas(mode),
    }
    references = _reference_schema(mode)
    if references is not None:
        fields["references"] = references
    return _closed(fields, tuple(fields))


def _update(mode: str) -> dict:
    fields = {"target": _TARGET, "label": _LABEL, **_value_schemas(mode)}
    references = _reference_schema(mode)
    if references is not None:
        fields["references"] = references
    schema = _closed(fields, ("target",))
    schema["minProperties"] = 3
    return schema


_CREATE_ACTIONS = {
    "initial_temperature": "FEM_ConstraintInitialTemperature",
    "surface_heat_flux": "FEM_ConstraintHeatflux",
    "convection": "VibeCAD_AnalyzeCreateConvection",
    "radiation": "VibeCAD_AnalyzeCreateRadiation",
    "boundary_temperature": "FEM_ConstraintTemperature",
    "concentrated_heat_input": "VibeCAD_AnalyzeCreateConcentratedHeatInput",
    "mass_heat_generation": "FEM_ConstraintBodyHeatSource",
    "total_body_power": "VibeCAD_AnalyzeCreateTotalBodyPower",
}
_UPDATE_ACTIONS = {
    mode: "VibeCAD_AnalyzeUpdate" + "".join(part.title() for part in mode.split("_"))
    for mode in MODES
}


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type=_EXACT_TARGET_BY_OPERATION.get(
            operation,
            "ExactFemThermalConditionAndGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_thermal_capability_definition() -> NativeCapabilityDefinition:
    descriptions = {
        "initial_temperature": "global initial temperature",
        "surface_heat_flux": "distributed surface heat flux",
        "convection": "surface convection boundary",
        "radiation": "surface radiation boundary",
        "boundary_temperature": "prescribed geometry temperature",
        "concentrated_heat_input": "concentrated nodal heat input",
        "mass_heat_generation": "body heat generation per unit mass",
        "total_body_power": "total power dissipated in a body",
    }
    variants = []
    for mode in MODES:
        variants.append(
            _variant(
                f"create_{mode}",
                f"Create one {descriptions[mode]} in an exact FEM analysis.",
                _CREATE_ACTIONS[mode],
                _create(mode),
            )
        )
        variants.append(
            _variant(
                f"update_{mode}",
                f"Edit one exact {descriptions[mode]} in place.",
                _UPDATE_ACTIONS[mode],
                _update(mode),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_THERMAL_CAPABILITY_NAME,
        description=(
            "Create or edit exact solver-backed thermal initial, boundary, nodal, "
            "and body conditions using explicit SI values and current geometry."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_thermal_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_thermal_capability_definition())
