# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for live Geometrical Analysis Features actions."""

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


ANALYZE_GEOMETRICAL_CAPABILITY_NAME = "analyze.geometrical"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_plane_rotation": "ExactFemPlaneRotationAndFace",
    "update_section_print": "ExactFemSectionPrintAndFace",
    "update_transform": "ExactFemTransformAndEligibleFace",
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


_FACE = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "subelement": {
            "type": "string",
            "pattern": r"^Face[1-9][0-9]*$",
            "maxLength": 32,
        },
    },
    ("object_name", "expected_state_sha256", "subelement"),
)
_VECTOR = _closed({"x": _SIGNED, "y": _SIGNED, "z": _SIGNED}, ("x", "y", "z"))
_ROTATION = _closed(
    {
        "axis": _VECTOR,
        "angle_degrees": {
            "type": "number",
            "minimum": -360000.0,
            "maximum": 360000.0,
        },
    },
    ("axis", "angle_degrees"),
)
_COORDINATE_SYSTEM = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "rectangular"},
                "rotation": _ROTATION,
            },
            ("kind", "rotation"),
        ),
        _closed(
            {"kind": {"type": "string", "const": "cylindrical"}},
            ("kind",),
        ),
    ]
}
_VARIABLE = {
    "type": "string",
    "enum": ["section_force", "heat_flux", "drag_stress", "electric_flux"],
}
_CREATE_ACTIONS = {
    "plane_rotation": "FEM_ConstraintPlaneRotation",
    "section_print": "FEM_ConstraintSectionPrint",
    "transform": "FEM_ConstraintTransform",
}
_UPDATE_ACTIONS = {
    "plane_rotation": "VibeCAD_AnalyzeUpdatePlaneRotation",
    "section_print": "VibeCAD_AnalyzeUpdateSectionPrint",
    "transform": "VibeCAD_AnalyzeUpdateTransform",
}


def _kind_fields(kind: str) -> dict:
    if kind == "section_print":
        return {"variable": _VARIABLE}
    if kind == "transform":
        return {"coordinate_system": _COORDINATE_SYSTEM}
    return {}


def _create(kind: str) -> dict:
    fields = {
        "analysis": _ANALYSIS_TARGET,
        "label": _LABEL,
        "face": _FACE,
        **_kind_fields(kind),
    }
    return _closed(fields, tuple(fields))


def _update(kind: str) -> dict:
    fields = {
        "target": _TARGET,
        "label": _LABEL,
        "face": _FACE,
        **_kind_fields(kind),
    }
    schema = _closed(fields, ("target",))
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
            "ExactFemAnalysisGeometricalFeatureAndFace",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_geometrical_capability_definition() -> NativeCapabilityDefinition:
    descriptions = {
        "plane_rotation": "plane multi-point constraint on one planar face",
        "section_print": "section-print request on one face",
        "transform": "local coordinate system on one eligible loaded or constrained face",
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
                f"Edit one exact {descriptions[kind]} without creating a replacement.",
                action_id,
                _update(kind),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
        description=(
            "Create or precisely edit the three live Geometrical Analysis Features "
            "using one exact support face and explicit typed settings."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_geometrical_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_geometrical_capability_definition())
