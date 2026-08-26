# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for mechanical support conditions."""

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


ANALYZE_SUPPORT_CAPABILITY_NAME = "analyze.support"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_fixed": "ExactFemFixedConditionAndGeometry",
    "update_rigid_body": "ExactFemRigidBodyConditionAndGeometry",
    "update_displacement": "ExactFemDisplacementConditionAndGeometry",
    "update_spring": "ExactFemSpringConditionAndFace",
}
_SIGNED = {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30}
_NONNEGATIVE = {"type": "number", "minimum": 0.0, "maximum": 1.0e30}
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
        "description": "Exact current geometry with one common subelement kind.",
    }


_VECTOR = _closed({"x": _SIGNED, "y": _SIGNED, "z": _SIGNED}, ("x", "y", "z"))
_RIGID_TRANSLATION_AXIS = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "free"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "prescribed"},
                "displacement_mm": _SIGNED,
            },
            ("kind", "displacement_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "load"},
                "force_n": _SIGNED,
            },
            ("kind", "force_n"),
        ),
    ]
}
_RIGID_ROTATION_AXIS = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "free"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "prescribed"},
                "rotation_degrees": _SIGNED,
            },
            ("kind", "rotation_degrees"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "load"},
                "moment_n_mm": _SIGNED,
            },
            ("kind", "moment_n_mm"),
        ),
    ]
}


def _axes(item: dict) -> dict:
    return _closed({"x": item, "y": item, "z": item}, ("x", "y", "z"))


_RIGID_BODY = _closed(
    {
        "reference_node_mm": _VECTOR,
        "translation": _axes(_RIGID_TRANSLATION_AXIS),
        "rotation": _axes(_RIGID_ROTATION_AXIS),
    },
    ("reference_node_mm", "translation", "rotation"),
)
_FORMULA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 512,
    "pattern": r"^[^\r\n\u0000]+$",
}
_DISPLACEMENT_AXIS = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "free"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "value"},
                "displacement_mm": _SIGNED,
            },
            ("kind", "displacement_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "formula"},
                "expression": _FORMULA,
            },
            ("kind", "expression"),
        ),
    ]
}
_ROTATION_AXIS = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "free"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "value"},
                "rotation_degrees": _SIGNED,
            },
            ("kind", "rotation_degrees"),
        ),
    ]
}
_DISPLACEMENT = _closed(
    {
        "translation": _axes(_DISPLACEMENT_AXIS),
        "rotation": _axes(_ROTATION_AXIS),
        "flow_surface_force": {"type": "boolean"},
    },
    ("translation", "rotation", "flow_surface_force"),
)
_SPRING = _closed(
    {
        "normal_stiffness_n_m": _NONNEGATIVE,
        "tangential_stiffness_n_m": _NONNEGATIVE,
        "elmer_component": {"type": "string", "enum": ["normal", "tangential"]},
    },
    ("normal_stiffness_n_m", "tangential_stiffness_n_m", "elmer_component"),
)
_CONDITIONS = {
    "rigid_body": _RIGID_BODY,
    "displacement": _DISPLACEMENT,
    "spring": _SPRING,
}
_REFERENCES = {
    "fixed": _references(("Vertex", "Edge", "Face")),
    "rigid_body": _references(("Vertex", "Edge", "Face")),
    "displacement": _references(("Vertex", "Edge", "Face")),
    "spring": _references(("Face",)),
}
_CREATE_ACTIONS = {
    "fixed": "FEM_ConstraintFixed",
    "rigid_body": "FEM_ConstraintRigidBody",
    "displacement": "FEM_ConstraintDisplacement",
    "spring": "FEM_ConstraintSpring",
}
_UPDATE_ACTIONS = {
    "fixed": "VibeCAD_AnalyzeUpdateFixed",
    "rigid_body": "VibeCAD_AnalyzeUpdateRigidBody",
    "displacement": "VibeCAD_AnalyzeUpdateDisplacement",
    "spring": "VibeCAD_AnalyzeUpdateSpring",
}


def _create(kind: str) -> dict:
    fields = {
        "analysis": _ANALYSIS_TARGET,
        "label": _LABEL,
        "references": _REFERENCES[kind],
    }
    if kind != "fixed":
        fields["condition"] = _CONDITIONS[kind]
    return _closed(fields, tuple(fields))


def _update(kind: str) -> dict:
    changes = {"label": _LABEL, "references": _REFERENCES[kind]}
    if kind != "fixed":
        changes["condition"] = _CONDITIONS[kind]
    schema = _closed({"target": _TARGET, **changes}, ("target",))
    schema["minProperties"] = 3
    return schema


def _variant(operation: str, description: str, action_id: str, parameters: dict) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type=_UPDATE_EXACT_TARGET_BY_OPERATION.get(
            operation,
            "ExactFemAnalysisSupportConditionAndGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_support_capability_definition() -> NativeCapabilityDefinition:
    descriptions = {
        "fixed": "fully fixed support",
        "rigid_body": "rigid coupling through a reference node",
        "displacement": "displacement support",
        "spring": "spring support",
    }
    variants = []
    for kind, action_id in _CREATE_ACTIONS.items():
        variants.append(
            _variant(
                f"create_{kind}",
                f"Create {descriptions[kind]}.",
                action_id,
                _create(kind),
            )
        )
    for kind, action_id in _UPDATE_ACTIONS.items():
        variants.append(
            _variant(
                f"update_{kind}",
                f"Edit {descriptions[kind]}.",
                action_id,
                _update(kind),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_SUPPORT_CAPABILITY_NAME,
        description=(
            "Create rigid couplings, prescribed displacements, and springs; "
            "edit existing support conditions."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_support_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_support_capability_definition())
