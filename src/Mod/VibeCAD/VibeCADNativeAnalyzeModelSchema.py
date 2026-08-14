# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for FEM analyses and materials."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_MODEL_CAPABILITY_NAME = "analyze.model"
_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_UUID = {
    "type": "string",
    "minLength": 36,
    "maxLength": 36,
    "pattern": r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$",
}
_ANALYSIS_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "expected_member_count": {"type": "integer", "minimum": 0, "maximum": 100_000},
    },
    "required": ["object_name", "expected_state_sha256", "expected_member_count"],
    "additionalProperties": False,
}
_MATERIAL_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_GEOMETRY_REFERENCE = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "subelements": {
            "type": "array",
            "items": {
                "type": "string",
                "pattern": r"^(Solid|Face|Edge)[1-9][0-9]*$",
                "maxLength": 32,
            },
            "minItems": 1,
            "maxItems": 256,
            "uniqueItems": True,
        },
    },
    "required": ["object_name", "expected_state_sha256", "subelements"],
    "additionalProperties": False,
}
_REFERENCES = {
    "type": "array",
    "items": _GEOMETRY_REFERENCE,
    "maxItems": 64,
}
_COMMON_PROPERTIES = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 160},
        "density_kg_m3": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e9},
        "young_modulus_mpa": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e12},
        "poisson_ratio": {"type": "number", "exclusiveMinimum": -1.0, "exclusiveMaximum": 0.5},
        "thermal_conductivity_w_m_k": {"type": "number", "minimum": 0.0, "maximum": 1.0e9},
        "thermal_expansion_per_k": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "reference_temperature_k": {"type": "number", "minimum": 0.0, "maximum": 100_000.0},
        "specific_heat_j_kg_k": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e12},
        "kinematic_viscosity_m2_s": {"type": "number", "minimum": 0.0, "maximum": 1.0e6},
    },
    "additionalProperties": False,
}
_CLEAR_PROPERTIES = {
    "type": "array",
    "items": {
        "type": "string",
        "enum": [
            "name",
            "density_kg_m3",
            "young_modulus_mpa",
            "poisson_ratio",
            "thermal_conductivity_w_m_k",
            "thermal_expansion_per_k",
            "reference_temperature_k",
            "specific_heat_j_kg_k",
            "kinematic_viscosity_m2_s",
        ],
    },
    "maxItems": 9,
    "uniqueItems": True,
}
_YIELD_POINTS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "stress_mpa": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1.0e12,
            },
            "plastic_strain": {"type": "number", "minimum": 0.0, "maximum": 10.0},
        },
        "required": ["stress_mpa", "plastic_strain"],
        "additionalProperties": False,
    },
    "minItems": 1,
    "maxItems": 128,
}


def _variant(operation: str, description: str, action_id: str, parameters: dict) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type="ExactFemAnalysisOrMaterial",
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def _material_create_parameters(*, reinforced: bool = False) -> dict:
    properties = {
        "analysis": _ANALYSIS_TARGET,
        "label": _LABEL,
        "references": _REFERENCES,
        "material_uuid": {
            **_UUID,
            "description": "Installed card UUID; typed property overrides clear card identity.",
        },
        "properties": {
            **_COMMON_PROPERTIES,
            "description": "Typed custom values; any override clears material_uuid.",
        },
    }
    if reinforced:
        properties.update(
            {
                "reinforcement_uuid": {
                    **_UUID,
                    "description": "Installed solid card UUID for reinforcement.",
                },
                "reinforcement_properties": {
                    **_COMMON_PROPERTIES,
                    "description": "Typed overrides; any override clears reinforcement_uuid.",
                },
            }
        )
    return {
        "type": "object",
        "properties": properties,
        "required": ["analysis", "label", "references"],
        "additionalProperties": False,
    }


def analyze_model_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_MODEL_CAPABILITY_NAME,
        description=(
            "Create exact FEM analyses and typed materials, or edit one exact material "
            "without modal UI or ambient selection."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "create_analysis",
                "Create one analysis and optionally its human-preference default solver.",
                "FEM_Analysis",
                {
                    "type": "object",
                    "properties": {
                        "label": _LABEL,
                        "default_solver_policy": {
                            "type": "string",
                            "enum": ["user_preference", "none"],
                        },
                    },
                    "required": ["label", "default_solver_policy"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "create_solid_material",
                "Create a solid material in one exact analysis from an optional card plus typed overrides.",
                "FEM_MaterialSolid",
                _material_create_parameters(),
            ),
            _variant(
                "create_fluid_material",
                "Create a fluid material in one exact analysis from an optional card plus typed overrides.",
                "FEM_MaterialFluid",
                _material_create_parameters(),
            ),
            _variant(
                "create_nonlinear_material",
                "Attach typed isotropic or kinematic hardening data to one exact solid material.",
                "FEM_MaterialMechanicalNonlinear",
                {
                    "type": "object",
                    "properties": {
                        "base_material": _MATERIAL_TARGET,
                        "label": _LABEL,
                        "model": {
                            "type": "string",
                            "enum": ["isotropic_hardening", "kinematic_hardening"],
                        },
                        "yield_points": _YIELD_POINTS,
                    },
                    "required": ["base_material", "label", "model", "yield_points"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "create_reinforced_material",
                "Create a reinforced solid material with explicit matrix and reinforcement cards or overrides.",
                "FEM_MaterialReinforced",
                _material_create_parameters(reinforced=True),
            ),
            _variant(
                "update_material",
                "Edit one exact common, reinforced, or nonlinear FEM material with typed changes.",
                "FEM_MaterialEditor",
                {
                    "type": "object",
                    "properties": {
                        "target": _MATERIAL_TARGET,
                        "label": _LABEL,
                        "references": _REFERENCES,
                        "material_uuid": _UUID,
                        "clear_material_uuid": {
                            "type": "boolean",
                            "enum": [True],
                        },
                        "properties": _COMMON_PROPERTIES,
                        "clear_properties": _CLEAR_PROPERTIES,
                        "reinforcement_uuid": _UUID,
                        "clear_reinforcement_uuid": {
                            "type": "boolean",
                            "enum": [True],
                        },
                        "reinforcement_properties": _COMMON_PROPERTIES,
                        "clear_reinforcement_properties": _CLEAR_PROPERTIES,
                        "model": {
                            "type": "string",
                            "enum": ["isotropic_hardening", "kinematic_hardening"],
                        },
                        "yield_points": _YIELD_POINTS,
                    },
                    "required": ["target"],
                    "minProperties": 3,
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_analyze_model_capability_definition(registry: NativeCapabilityRegistry) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_model_capability_definition())
