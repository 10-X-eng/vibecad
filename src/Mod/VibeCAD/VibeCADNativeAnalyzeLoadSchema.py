# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for FEM mechanical loads."""

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


ANALYZE_LOAD_CAPABILITY_NAME = "analyze.load"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_force": "ExactFemForceLoadAndGeometry",
    "update_pressure": "ExactFemPressureLoadAndGeometry",
    "update_centrifugal": "ExactFemCentrifugalLoadAxisAndScope",
    "update_gravity": "ExactFemGlobalGravityLoad",
}
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e30}
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
        "description": "Exact current geometry using one common subelement kind.",
    }


_DIRECTION = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "normal"},
                "reversed": {"type": "boolean"},
            },
            ("kind", "reversed"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "reference"},
                "object_name": _OBJECT_NAME,
                "expected_state_sha256": _STATE_SHA256,
                "subelement": {
                    "type": "string",
                    "pattern": r"^(?:(?:Edge|Face)[1-9][0-9]*)?$",
                    "maxLength": 32,
                    "description": "One linear edge, planar face, datum line, or datum plane.",
                },
                "reversed": {"type": "boolean"},
            },
            (
                "kind",
                "object_name",
                "expected_state_sha256",
                "subelement",
                "reversed",
            ),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "vector"},
                "x": {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30},
                "y": {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30},
                "z": {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30},
            },
            ("kind", "x", "y", "z"),
        ),
    ]
}
_AXIS = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "subelement": {
            "type": "string",
            "pattern": r"^Edge[1-9][0-9]*$",
            "maxLength": 32,
            "description": "One exact current linear edge.",
        },
    },
    ("object_name", "expected_state_sha256", "subelement"),
)
_CENTRIFUGAL_SCOPE = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "all_bodies"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "selected_geometry"},
                "references": _references(("Solid", "Face")),
            },
            ("kind", "references"),
        ),
    ]
}
_VECTOR = _closed(
    {
        "x": {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30},
        "y": {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30},
        "z": {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30},
    },
    ("x", "y", "z"),
)
_CREATE_ACTIONS = {
    "force": "FEM_ConstraintForce",
    "pressure": "FEM_ConstraintPressure",
    "centrifugal": "FEM_ConstraintCentrif",
    "gravity": "FEM_ConstraintSelfWeight",
}
_UPDATE_ACTIONS = {
    "force": "VibeCAD_AnalyzeUpdateForce",
    "pressure": "VibeCAD_AnalyzeUpdatePressure",
    "centrifugal": "VibeCAD_AnalyzeUpdateCentrifugal",
    "gravity": "VibeCAD_AnalyzeUpdateGravity",
}


def _create(kind: str) -> dict:
    fields = {"analysis": _ANALYSIS_TARGET, "label": _LABEL}
    if kind == "force":
        fields.update(
            references=_references(("Vertex", "Edge", "Face")),
            force_n=_POSITIVE,
            direction=_DIRECTION,
        )
    elif kind == "pressure":
        fields.update(
            references=_references(("Edge", "Face")),
            pressure_pa=_POSITIVE,
            reversed={"type": "boolean"},
        )
    elif kind == "centrifugal":
        fields.update(
            rotation_frequency_hz=_POSITIVE,
            axis=_AXIS,
            scope=_CENTRIFUGAL_SCOPE,
        )
    else:
        fields.update(acceleration_m_s2=_POSITIVE, direction=_VECTOR)
    return _closed(fields, tuple(fields))


def _change_fields(kind: str) -> dict:
    fields = {"label": _LABEL}
    if kind == "force":
        fields.update(
            references=_references(("Vertex", "Edge", "Face")),
            force_n=_POSITIVE,
            direction=_DIRECTION,
        )
    elif kind == "pressure":
        fields.update(
            references=_references(("Edge", "Face")),
            pressure_pa=_POSITIVE,
            reversed={"type": "boolean"},
        )
    elif kind == "centrifugal":
        fields.update(
            rotation_frequency_hz=_POSITIVE,
            axis=_AXIS,
            scope=_CENTRIFUGAL_SCOPE,
        )
    else:
        fields.update(acceleration_m_s2=_POSITIVE, direction=_VECTOR)
    return fields


def _update(kind: str) -> dict:
    schema = _closed(
        {"target": _TARGET, **_change_fields(kind)},
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
            "ExactFemMechanicalLoadAndGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_load_capability_definition() -> NativeCapabilityDefinition:
    names = {
        "force": "concentrated or distributed force",
        "pressure": "surface pressure",
        "centrifugal": "centrifugal body load",
        "gravity": "global gravity load",
    }
    variants = []
    for kind, action_id in _CREATE_ACTIONS.items():
        variants.append(
            _variant(
                f"create_{kind}",
                f"Create one {names[kind]} in an exact FEM analysis.",
                action_id,
                _create(kind),
            )
        )
    for kind, action_id in _UPDATE_ACTIONS.items():
        variants.append(
            _variant(
                f"update_{kind}",
                f"Edit one exact {names[kind]} in place.",
                action_id,
                _update(kind),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_LOAD_CAPABILITY_NAME,
        description=(
            "Create or edit exact solver-backed mechanical loads with explicit "
            "geometry, direction, axis, scope, and SI values."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_load_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_load_capability_definition())
