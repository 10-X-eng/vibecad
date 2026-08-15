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
    "description": "Only specified axes are constrained; omitted axes remain unspecified.",
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
}
_CREATE_ACTIONS = {
    "initial_flow_velocity": "FEM_ConstraintInitialFlowVelocity",
    "initial_pressure": "FEM_ConstraintInitialPressure",
    "flow_velocity": "FEM_ConstraintFlowVelocity",
}
_UPDATE_ACTIONS = {
    "initial_flow_velocity": "VibeCAD_AnalyzeUpdateInitialFlowVelocity",
    "initial_pressure": "VibeCAD_AnalyzeUpdateInitialPressure",
    "flow_velocity": "VibeCAD_AnalyzeUpdateFlowVelocity",
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
