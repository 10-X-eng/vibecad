# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for live FEM electromagnetic ribbon actions."""

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


ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME = "analyze.electromagnetic"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_electromagnetic": "ExactFemElectromagneticConstraintAndGeometry",
    "update_current_density": "ExactFemCurrentDensityConstraintAndGeometry",
    "update_magnetization": "ExactFemMagnetizationConstraintAndGeometry",
    "update_electric_charge_density": (
        "ExactFemElectricChargeDensityConstraintAndGeometry"
    ),
}
_SIGNED = {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30}
_BOOLEAN = {"type": "boolean"}
_CONSTRAINT_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}


def _closed(
    properties: dict, required: tuple[str, ...], *, min_properties: int | None = None
) -> dict:
    result = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
    if min_properties is not None:
        result["minProperties"] = min_properties
    return result


def _reference_item(kinds: tuple[str, ...]) -> dict:
    kind_pattern = "|".join(kinds)
    return _closed(
        {
            "object_name": _OBJECT_NAME,
            "expected_state_sha256": _STATE_SHA256,
            "subelements": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": rf"^(?:{kind_pattern})[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
        },
        ("object_name", "expected_state_sha256", "subelements"),
    )


def _references(
    kinds: tuple[str, ...],
    *,
    allow_empty: bool,
    description: str,
) -> dict:
    result = {
        "type": "array",
        "items": _reference_item(kinds),
        "maxItems": 64,
        "description": description,
    }
    if not allow_empty:
        result["minItems"] = 1
    return result


_COMPLEX_V = _closed(
    {"real_v": _SIGNED, "imaginary_v": _SIGNED},
    ("real_v", "imaginary_v"),
)
_COMPLEX_WB_M = _closed(
    {"real_wb_m": _SIGNED, "imaginary_wb_m": _SIGNED},
    ("real_wb_m", "imaginary_wb_m"),
)
_COMPLEX_WB_M2 = _closed(
    {"real_wb_m2": _SIGNED, "imaginary_wb_m2": _SIGNED},
    ("real_wb_m2", "imaginary_wb_m2"),
)
_COMPLEX_A_M2 = _closed(
    {"real_a_m2": _SIGNED, "imaginary_a_m2": _SIGNED},
    ("real_a_m2", "imaginary_a_m2"),
)
_COMPLEX_A_M = _closed(
    {"real_a_m": _SIGNED, "imaginary_a_m": _SIGNED},
    ("real_a_m", "imaginary_a_m"),
)


def _components(value: dict, description: str) -> dict:
    return {
        "type": "object",
        "properties": {"x": value, "y": value, "z": value},
        "minProperties": 1,
        "additionalProperties": False,
        "description": description,
    }


_CAPACITANCE_BODY = {
    "type": "integer",
    "minimum": 1,
    "maximum": 1_000_000,
    "description": "Optional one-based capacitance-body counter.",
}
_ELECTROMAGNETIC = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "dirichlet"},
                "electric_potential_v": _SIGNED,
                "scalar_potential": _COMPLEX_V,
                "vector_potential": _components(
                    _COMPLEX_WB_M,
                    "Enabled magnetic-vector-potential components in webers per metre.",
                ),
                "potential_constant": _BOOLEAN,
                "far_field": _BOOLEAN,
                "capacitance_body": _CAPACITANCE_BODY,
            },
            ("kind", "potential_constant", "far_field"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "neumann"},
                "electric_flux_density_c_m2": _SIGNED,
                "magnetic_flux_density": _components(
                    _COMPLEX_WB_M2,
                    "Enabled magnetic-flux-density components in webers per square metre.",
                ),
                "capacitance_body": _CAPACITANCE_BODY,
            },
            ("kind", "electric_flux_density_c_m2"),
        ),
    ]
}
_CURRENT_DENSITY = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "cartesian"},
                "components": _components(
                    _COMPLEX_A_M2,
                    "One or more enabled Cartesian current-density components.",
                ),
            },
            ("kind", "components"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "normal"},
                "real_a_m2": _SIGNED,
                "imaginary_a_m2": _SIGNED,
            },
            ("kind", "real_a_m2", "imaginary_a_m2"),
        ),
    ]
}
_MAGNETIZATION = _closed(
    {
        "components": _components(
            _COMPLEX_A_M,
            "One or more enabled Cartesian magnetization components.",
        )
    },
    ("components",),
)
_ELECTRIC_CHARGE_DENSITY = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "interface"},
                "surface_charge_density_c_m2": _SIGNED,
            },
            ("kind", "surface_charge_density_c_m2"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "source"},
                "volume_charge_density_c_m3": _SIGNED,
            },
            ("kind", "volume_charge_density_c_m3"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "total_interface"},
                "total_charge_c": _SIGNED,
            },
            ("kind", "total_charge_c"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "total_source"},
                "total_charge_c": _SIGNED,
                "concentrated": _BOOLEAN,
            },
            ("kind", "total_charge_c", "concentrated"),
        ),
    ]
}


_REFERENCE_SCHEMAS = {
    "electromagnetic": _references(
        ("Solid", "Face", "Edge", "Vertex"),
        allow_empty=False,
        description="One or more exact current geometry assignments; mixed supported subelement kinds are allowed.",
    ),
    "current_density": _references(
        ("Solid", "Face"),
        allow_empty=True,
        description="Exact solid/face assignments. An empty list is global only for Cartesian body current density.",
    ),
    "magnetization": _references(
        ("Solid", "Face"),
        allow_empty=True,
        description="Exact solid/face assignments; an empty list deliberately applies the sole magnetization globally.",
    ),
    "electric_charge_density": _references(
        ("Solid", "Face", "Edge", "Vertex"),
        allow_empty=False,
        description="Exact assignments. Runtime enforces mode-appropriate source or interface subelements.",
    ),
}
_CONSTRAINT_SCHEMAS = {
    "electromagnetic": _ELECTROMAGNETIC,
    "current_density": _CURRENT_DENSITY,
    "magnetization": _MAGNETIZATION,
    "electric_charge_density": _ELECTRIC_CHARGE_DENSITY,
}
_CREATE_ACTIONS = {
    "electromagnetic": "FEM_ConstraintElectromagnetic",
    "current_density": "FEM_ConstraintCurrentDensity",
    "magnetization": "FEM_ConstraintMagnetization",
    "electric_charge_density": "FEM_ConstraintElectricChargeDensity",
}
_UPDATE_ACTIONS = {
    "electromagnetic": "VibeCAD_AnalyzeUpdateElectromagnetic",
    "current_density": "VibeCAD_AnalyzeUpdateCurrentDensity",
    "magnetization": "VibeCAD_AnalyzeUpdateMagnetization",
    "electric_charge_density": "VibeCAD_AnalyzeUpdateElectricChargeDensity",
}


def _create(kind: str) -> dict:
    return _closed(
        {
            "analysis": _ANALYSIS_TARGET,
            "label": _LABEL,
            "references": _REFERENCE_SCHEMAS[kind],
            "constraint": _CONSTRAINT_SCHEMAS[kind],
        },
        ("analysis", "label", "references", "constraint"),
    )


def _update(kind: str) -> dict:
    schema = _closed(
        {
            "target": _CONSTRAINT_TARGET,
            "label": _LABEL,
            "references": _REFERENCE_SCHEMAS[kind],
            "constraint": _CONSTRAINT_SCHEMAS[kind],
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
            "ExactFemAnalysisElectromagneticConstraintAndGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_electromagnetic_capability_definition() -> NativeCapabilityDefinition:
    descriptions = {
        "electromagnetic": "electromagnetic Dirichlet or Neumann boundary constraint",
        "current_density": "normal or enabled Cartesian current-density constraint",
        "magnetization": "enabled complex Cartesian magnetization constraint",
        "electric_charge_density": "interface, source, or total electric-charge constraint",
    }
    variants = []
    for kind, action_id in _CREATE_ACTIONS.items():
        variants.append(
            _variant(
                f"constraint_{kind}",
                f"Create one strongly typed {descriptions[kind]} in an exact FEM analysis.",
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
        name=ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
        description=(
            "Create or precisely edit the four live FEM electromagnetic constraint types "
            "using explicit SI values and exact current geometry."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_electromagnetic_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_electromagnetic_capability_definition())
