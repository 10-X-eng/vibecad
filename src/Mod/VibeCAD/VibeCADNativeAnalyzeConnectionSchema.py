# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for paired FEM mechanical connections."""

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


ANALYZE_CONNECTION_CAPABILITY_NAME = "analyze.connection"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_contact": "ExactFemContactAndSlaveMasterGeometry",
    "update_tie": "ExactFemTieAndSlaveMasterGeometry",
}
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e30}
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


_ENDPOINT = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "subelement": {
            "type": "string",
            "pattern": r"^(?:Face|Edge)[1-9][0-9]*$",
            "maxLength": 32,
            "description": "One face, or one edge when analyzing a 2D model.",
        },
    },
    ("object_name", "expected_state_sha256", "subelement"),
)
_SLAVE_ENDPOINT = {
    **_ENDPOINT,
    "description": "Dependent mating face; choose the smaller or finer surface.",
}
_MASTER_ENDPOINT = {
    **_ENDPOINT,
    "description": "Independent mating face; choose the larger or coarser surface.",
}
_FRICTION = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "frictionless"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "coulomb"},
                "coefficient": _POSITIVE,
                "stick_stiffness_gpa_per_m": _POSITIVE,
            },
            ("kind", "coefficient", "stick_stiffness_gpa_per_m"),
        ),
    ]
}
_CONTACT = _closed(
    {
        "contact_stiffness_gpa_per_m": _POSITIVE,
        "clearance_adjustment_mm": _NONNEGATIVE,
        "friction": _FRICTION,
    },
    (
        "contact_stiffness_gpa_per_m",
        "clearance_adjustment_mm",
        "friction",
    ),
)
_TIE = _closed(
    {
        "tolerance_mm": _NONNEGATIVE,
        "adjust": {"type": "boolean"},
    },
    ("tolerance_mm", "adjust"),
)
_DEFINITIONS = {"contact": _CONTACT, "tie": _TIE}
_CREATE_ACTIONS = {
    "contact": "FEM_ConstraintContact",
    "tie": "FEM_ConstraintTie",
}
_UPDATE_ACTIONS = {
    "contact": "VibeCAD_AnalyzeUpdateContact",
    "tie": "VibeCAD_AnalyzeUpdateTie",
}


def _create(kind: str) -> dict:
    return _closed(
        {
            "analysis": _ANALYSIS_TARGET,
            "label": _LABEL,
            "slave": _SLAVE_ENDPOINT,
            "master": _MASTER_ENDPOINT,
            "connection": _DEFINITIONS[kind],
        },
        ("analysis", "label", "slave", "master", "connection"),
    )


def _update(kind: str) -> dict:
    schema = _closed(
        {
            "target": _TARGET,
            "label": _LABEL,
            "slave": _SLAVE_ENDPOINT,
            "master": _MASTER_ENDPOINT,
            "connection": _DEFINITIONS[kind],
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
            "ExactFemConnectionAndSlaveMasterGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_connection_capability_definition() -> NativeCapabilityDefinition:
    variants = []
    descriptions = {
        "contact": "contact pair with explicit slave and master",
        "tie": "bonded tie pair with explicit slave and master",
    }
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
                f"Edit one exact {descriptions[kind]} in place.",
                action_id,
                _update(kind),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_CONNECTION_CAPABILITY_NAME,
        description=(
            "Create or edit exact paired mechanical connections with explicit "
            "slave/master roles and solver-backed values."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_connection_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_connection_capability_definition())
