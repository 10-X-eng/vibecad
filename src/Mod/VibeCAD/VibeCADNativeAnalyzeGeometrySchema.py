# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for FEM element definitions."""

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


ANALYZE_GEOMETRY_CAPABILITY_NAME = "analyze.geometry"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_beam_section": "ExactFemBeamSectionAndGeometry",
    "update_beam_rotation": "ExactFemBeamRotationAndGeometry",
    "update_shell_thickness": "ExactFemShellThicknessAndGeometry",
    "update_fluid_section": "ExactFemFluidSectionAndGeometry",
}
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e12}
_NONNEGATIVE = {"type": "number", "minimum": 0.0, "maximum": 1.0e12}
_SIGNED = {"type": "number", "minimum": -1.0e12, "maximum": 1.0e12}
_ELEMENT_TARGET = {
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


def _reference(kind: str) -> dict:
    return _closed(
        {
            "object_name": _OBJECT_NAME,
            "expected_state_sha256": _STATE_SHA256,
            "subelements": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": rf"^{kind}[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
        },
        ("object_name", "expected_state_sha256", "subelements"),
    )


def _references(kind: str) -> dict:
    return {
        "type": "array",
        "items": _reference(kind),
        "maxItems": 64,
        "description": f"{kind} scope; empty is global.",
    }


def _section(kind: str, properties: dict, required: tuple[str, ...]) -> dict:
    return _closed(
        {"kind": {"type": "string", "const": kind}, **properties},
        ("kind", *required),
    )


_BEAM_SECTION = {
    "oneOf": [
        _section(
            "rectangular",
            {"width_mm": _POSITIVE, "height_mm": _POSITIVE},
            ("width_mm", "height_mm"),
        ),
        _section("circular", {"diameter_mm": _POSITIVE}, ("diameter_mm",)),
        _section(
            "pipe",
            {
                "outer_diameter_mm": _POSITIVE,
                "wall_thickness_mm": _POSITIVE,
            },
            ("outer_diameter_mm", "wall_thickness_mm"),
        ),
        _section(
            "elliptical",
            {"axis_1_mm": _POSITIVE, "axis_2_mm": _POSITIVE},
            ("axis_1_mm", "axis_2_mm"),
        ),
        _section(
            "box",
            {
                "width_mm": _POSITIVE,
                "height_mm": _POSITIVE,
                "t1_mm": _POSITIVE,
                "t2_mm": _POSITIVE,
                "t3_mm": _POSITIVE,
                "t4_mm": _POSITIVE,
            },
            ("width_mm", "height_mm", "t1_mm", "t2_mm", "t3_mm", "t4_mm"),
        ),
    ]
}


_FLUID_SECTION = {
    "oneOf": [
        _section(
            "pipe_manning",
            {
                "area_mm2": _POSITIVE,
                "hydraulic_radius_mm": _POSITIVE,
                "manning_coefficient": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            ("area_mm2", "hydraulic_radius_mm", "manning_coefficient"),
        ),
        _section(
            "pipe_enlargement",
            {"initial_area_mm2": _POSITIVE, "enlarged_area_mm2": _POSITIVE},
            ("initial_area_mm2", "enlarged_area_mm2"),
        ),
        _section(
            "pipe_contraction",
            {"initial_area_mm2": _POSITIVE, "contracted_area_mm2": _POSITIVE},
            ("initial_area_mm2", "contracted_area_mm2"),
        ),
        _section(
            "pipe_inlet",
            {
                "pressure_mpa": _SIGNED,
                "mass_flow_rate_kg_s": _SIGNED,
                "pressure_active": {"type": "boolean"},
                "mass_flow_rate_active": {"type": "boolean"},
            },
            (
                "pressure_mpa",
                "mass_flow_rate_kg_s",
                "pressure_active",
                "mass_flow_rate_active",
            ),
        ),
        _section(
            "pipe_outlet",
            {
                "pressure_mpa": _SIGNED,
                "mass_flow_rate_kg_s": _SIGNED,
                "pressure_active": {"type": "boolean"},
                "mass_flow_rate_active": {"type": "boolean"},
            },
            (
                "pressure_mpa",
                "mass_flow_rate_kg_s",
                "pressure_active",
                "mass_flow_rate_active",
            ),
        ),
        _section(
            "pipe_entrance",
            {"pipe_area_mm2": _POSITIVE, "entrance_area_mm2": _POSITIVE},
            ("pipe_area_mm2", "entrance_area_mm2"),
        ),
        _section(
            "pipe_diaphragm",
            {"pipe_area_mm2": _POSITIVE, "aperture_area_mm2": _POSITIVE},
            ("pipe_area_mm2", "aperture_area_mm2"),
        ),
        _section(
            "pipe_bend",
            {
                "pipe_area_mm2": _POSITIVE,
                "bend_radius_to_diameter": {
                    "type": "number",
                    "minimum": 1.0,
                    "maximum": 9_999_999.0,
                },
                "angle_degrees": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 360.0,
                },
                "loss_coefficient": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            (
                "pipe_area_mm2",
                "bend_radius_to_diameter",
                "angle_degrees",
                "loss_coefficient",
            ),
        ),
        _section(
            "pipe_gate_valve",
            {
                "pipe_area_mm2": _POSITIVE,
                "closing_coefficient": {
                    "type": "number",
                    "minimum": 0.125,
                    "maximum": 1.0,
                },
            },
            ("pipe_area_mm2", "closing_coefficient"),
        ),
        _section(
            "liquid_pump",
            {
                "curve": {
                    "type": "array",
                    "items": _closed(
                        {
                            "flow_rate_mm3_s": _NONNEGATIVE,
                            "head_loss_mm": _NONNEGATIVE,
                        },
                        ("flow_rate_mm3_s", "head_loss_mm"),
                    ),
                    "minItems": 2,
                    "maxItems": 128,
                }
            },
            ("curve",),
        ),
        _section(
            "pipe_white_colebrook",
            {
                "pipe_area_mm2": _POSITIVE,
                "hydraulic_radius_mm": _POSITIVE,
                "grain_diameter_mm": _NONNEGATIVE,
                "form_factor": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            (
                "pipe_area_mm2",
                "hydraulic_radius_mm",
                "grain_diameter_mm",
                "form_factor",
            ),
        ),
    ]
}


def _create(section_name: str, section_schema: dict, reference_kind: str) -> dict:
    return _closed(
        {
            "analysis": _ANALYSIS_TARGET,
            "label": _LABEL,
            "references": _references(reference_kind),
            section_name: section_schema,
        },
        ("analysis", "label", "references", section_name),
    )


def _update(properties: dict) -> dict:
    schema = _closed(
        {"target": _ELEMENT_TARGET, "label": _LABEL, **properties},
        ("target",),
    )
    schema["minProperties"] = 3
    return schema


def _variant(
    operation: str, description: str, action_id: str, parameters: dict
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type=_UPDATE_EXACT_TARGET_BY_OPERATION.get(
            operation,
            "ExactFemAnalysisElementDefinitionAndGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_geometry_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_GEOMETRY_CAPABILITY_NAME,
        description="Create or edit element definitions.",
        primary_classification="mutation",
        variants=(
            _variant(
                "create_beam_section",
                "Create a beam section.",
                "FEM_ElementGeometry1D",
                _create("section", _BEAM_SECTION, "Edge"),
            ),
            _variant(
                "create_beam_rotation",
                "Create a beam rotation.",
                "FEM_ElementRotation1D",
                _create("rotation_degrees", _SIGNED, "Edge"),
            ),
            _variant(
                "create_shell_thickness",
                "Create a shell thickness.",
                "FEM_ElementGeometry2D",
                _create("thickness_mm", _POSITIVE, "Face"),
            ),
            _variant(
                "create_fluid_section",
                "Create a CalculiX fluid section.",
                "FEM_ElementFluid1D",
                _create("section", _FLUID_SECTION, "Edge"),
            ),
            _variant(
                "update_beam_section",
                "Edit a beam section.",
                "VibeCAD_AnalyzeUpdateBeamSection",
                _update({"references": _references("Edge"), "section": _BEAM_SECTION}),
            ),
            _variant(
                "update_beam_rotation",
                "Edit a beam rotation.",
                "VibeCAD_AnalyzeUpdateBeamRotation",
                _update(
                    {"references": _references("Edge"), "rotation_degrees": _SIGNED}
                ),
            ),
            _variant(
                "update_shell_thickness",
                "Edit a shell thickness.",
                "VibeCAD_AnalyzeUpdateShellThickness",
                _update({"references": _references("Face"), "thickness_mm": _POSITIVE}),
            ),
            _variant(
                "update_fluid_section",
                "Edit a fluid section.",
                "VibeCAD_AnalyzeUpdateFluidSection",
                _update({"references": _references("Edge"), "section": _FLUID_SECTION}),
            ),
        ),
    )


def register_analyze_geometry_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_geometry_capability_definition())
