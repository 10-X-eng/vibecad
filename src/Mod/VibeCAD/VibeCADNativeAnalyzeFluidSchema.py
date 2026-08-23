# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for live FEM Fluid ribbon actions."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import (
    _ANALYSIS_TARGET,
    _LABEL,
    _OBJECT_NAME,
    _STATE_SHA256,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_FLUID_CAPABILITY_NAME = "analyze.fluid"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_initial_flow_velocity": "ExactFemInitialFlowVelocityAndGeometry",
    "update_initial_pressure": "ExactFemInitialPressureAndGeometry",
    "update_flow_velocity": "ExactFemFlowVelocityAndGeometry",
    "update_fluid_boundary": "ExactFemFluidBoundaryAndGeometry",
}
_SIGNED = {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30}
_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _references(
    kinds: tuple[str, ...],
    *,
    allow_empty: bool,
    description: str,
) -> dict:
    pattern = "|".join(kinds)
    result = {
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
        "maxItems": 64,
        "description": description,
    }
    if not allow_empty:
        result["minItems"] = 1
    return result


_VELOCITY_COMPONENT = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "value"},
                "value_m_s": _SIGNED,
            },
            ("kind", "value_m_s"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "formula"},
                "expression": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "pattern": r"^[^\r\n\u0000]+$",
                    "description": "One-line Elmer velocity expression, including any required Variable/MATC syntax.",
                },
            },
            ("kind", "expression"),
        ),
    ]
}
_COMPONENTS = {
    "type": "object",
    "properties": {
        "x": _VELOCITY_COMPONENT,
        "y": _VELOCITY_COMPONENT,
        "z": _VELOCITY_COMPONENT,
    },
    "minProperties": 1,
    "additionalProperties": False,
    "description": "Explicit constrained axes.",
}
_CONSTRAINTS = {
    "initial_flow_velocity": _closed({"components": _COMPONENTS}, ("components",)),
    "initial_pressure": _closed({"pressure_pa": _SIGNED}, ("pressure_pa",)),
    "flow_velocity": _closed(
        {
            "components": _COMPONENTS,
            "normal_to_boundary": {"type": "boolean"},
        },
        ("components", "normal_to_boundary"),
    ),
}


def _typed(kind: str, **properties: dict) -> dict:
    return _closed(
        {"kind": {"type": "string", "const": kind}, **properties},
        ("kind", *properties),
    )


_NONNEGATIVE = {"type": "number", "minimum": 0.0, "maximum": 1.0e30}
_POSITIVE = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1.0e30,
}
_RATIO = {"type": "number", "minimum": 0.0, "maximum": 1.0}
_BOUNDARY_CONDITION = {
    "oneOf": [
        _typed("wall_no_slip"),
        _typed("wall_slip"),
        _typed("wall_partial_slip", slip_ratio=_RATIO),
        _typed("wall_moving", speed_m_s=_NONNEGATIVE),
        _typed("inlet_total_pressure", pressure_pa=_SIGNED),
        _typed("inlet_velocity", velocity_m_s=_NONNEGATIVE),
        _typed("inlet_volumetric_flow", flow_m3_s=_NONNEGATIVE),
        _typed("inlet_mass_flow", flow_kg_s=_NONNEGATIVE),
        _typed("outlet_total_pressure", pressure_pa=_SIGNED),
        _typed("outlet_static_pressure", pressure_pa=_SIGNED),
        _typed("outlet_velocity", velocity_m_s=_NONNEGATIVE),
        _typed("outlet_outflow"),
        _typed("symmetry"),
        _typed("wedge"),
        _typed("cyclic"),
        _typed("empty"),
        _typed("freestream"),
    ]
}
_BOUNDARY_TURBULENCE = {
    "oneOf": [
        _typed("none"),
        _typed(
            "intensity_dissipation_rate",
            intensity_ratio=_RATIO,
            dissipation_rate_m2_s3=_POSITIVE,
        ),
        _typed(
            "intensity_length_scale",
            intensity_ratio=_RATIO,
            length_scale_m=_POSITIVE,
        ),
        _typed(
            "intensity_viscosity_ratio",
            intensity_ratio=_RATIO,
            viscosity_ratio=_POSITIVE,
        ),
        _typed(
            "intensity_hydraulic_diameter",
            intensity_ratio=_RATIO,
            hydraulic_diameter_m=_POSITIVE,
        ),
    ]
}
_BOUNDARY_THERMAL = {
    "oneOf": [
        _typed("adiabatic"),
        _typed("fixed_temperature", temperature_k=_NONNEGATIVE),
        _typed("fixed_gradient", gradient_k_m=_SIGNED),
        _typed("mixed", temperature_k=_NONNEGATIVE, gradient_k_m=_SIGNED),
        _typed("heat_flux", heat_flux_w_m2=_SIGNED),
        _typed(
            "heat_transfer_coefficient",
            coefficient_w_m2_k=_NONNEGATIVE,
            ambient_temperature_k=_NONNEGATIVE,
        ),
        _typed("coupled"),
    ]
}
_CONSTRAINTS["fluid_boundary"] = _closed(
    {
        "condition": _BOUNDARY_CONDITION,
        "turbulence": _BOUNDARY_TURBULENCE,
        "thermal": _BOUNDARY_THERMAL,
    },
    ("condition", "turbulence", "thermal"),
)
_REFERENCES = {
    "initial_flow_velocity": _references(
        ("Solid", "Face"),
        allow_empty=True,
        description="Exact fluid-body assignments; an empty list deliberately applies the sole initial velocity globally.",
    ),
    "initial_pressure": _references(
        ("Solid", "Face"),
        allow_empty=True,
        description="Exact fluid-body assignments; an empty list deliberately applies the sole initial pressure globally.",
    ),
    "flow_velocity": _references(
        ("Solid", "Face", "Edge", "Vertex"),
        allow_empty=False,
        description="One or more exact current boundary assignments; mixed supported subelement kinds are allowed.",
    ),
    "fluid_boundary": _references(
        ("Face",),
        allow_empty=False,
        description="Exact boundary faces.",
    ),
}
_CREATE_ACTIONS = {
    "initial_flow_velocity": "FEM_ConstraintInitialFlowVelocity",
    "initial_pressure": "FEM_ConstraintInitialPressure",
    "flow_velocity": "FEM_ConstraintFlowVelocity",
    "fluid_boundary": "FEM_ConstraintFluidBoundary",
}
_UPDATE_ACTIONS = {
    "initial_flow_velocity": "VibeCAD_AnalyzeUpdateInitialFlowVelocity",
    "initial_pressure": "VibeCAD_AnalyzeUpdateInitialPressure",
    "flow_velocity": "VibeCAD_AnalyzeUpdateFlowVelocity",
    "fluid_boundary": "VibeCAD_AnalyzeUpdateFluidBoundary",
}


def _create(kind: str) -> dict:
    return _closed(
        {
            "analysis": _ANALYSIS_TARGET,
            "label": _LABEL,
            "references": _REFERENCES[kind],
            "constraint": _CONSTRAINTS[kind],
        },
        ("analysis", "label", "references", "constraint"),
    )


def _update(kind: str) -> dict:
    schema = _closed(
        {
            "target": _TARGET,
            "label": _LABEL,
            "references": _REFERENCES[kind],
            "constraint": _CONSTRAINTS[kind],
        },
        ("target",),
    )
    schema["minProperties"] = 3
    return schema


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
        exact_target_type=_UPDATE_EXACT_TARGET_BY_OPERATION.get(
            operation,
            "ExactFemAnalysisFluidConstraintAndGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_fluid_capability_definition() -> NativeCapabilityDefinition:
    descriptions = {
        "initial_flow_velocity": "initial fluid velocity with explicit value/formula axes",
        "initial_pressure": "initial fluid pressure in pascals",
        "flow_velocity": "fluid boundary velocity with explicit value/formula axes",
        "fluid_boundary": "CFD face boundary with explicit condition, turbulence, and thermal modes",
    }
    variants = []
    for kind, action_id in _CREATE_ACTIONS.items():
        variants.append(
            _variant(
                f"create_{kind}",
                f"Create one {descriptions[kind]} in an exact FEM analysis.",
                action_id,
                _create(kind),
            )
        )
    for kind, action_id in _UPDATE_ACTIONS.items():
        variants.append(
            _variant(
                f"update_{kind}",
                f"Edit one exact {descriptions[kind]} without creating a replacement operation.",
                action_id,
                _update(kind),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_FLUID_CAPABILITY_NAME,
        description=(
            "Create or precisely edit the three live FEM Fluid ribbon constraints "
            "using explicit units, formula/value discriminators, and exact geometry."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_fluid_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_fluid_capability_definition())
