# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider contract for exact CAM program-control operations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeManufactureContract import PATH_OPERATION_LABEL_SCHEMA


MANUFACTURE_PROGRAM_CAPABILITY_NAME = "manufacture.program"

_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}
_LABEL = PATH_OPERATION_LABEL_SCHEMA


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_EXACT_JOB = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_CUSTOM_PARAMETER_WORDS = [
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "H",
    "I",
    "J",
    "K",
    "L",
    "P",
    "Q",
    "R",
    "S",
    "T",
    "U",
    "V",
    "W",
    "X",
    "Y",
    "Z",
]
_CUSTOM_PARAMETER = _closed(
    {
        "word": {
            "type": "string",
            "enum": _CUSTOM_PARAMETER_WORDS,
            "description": "One explicit uppercase CNC parameter word.",
        },
        "value": {
            "type": "number",
            "minimum": -1_000_000_000.0,
            "maximum": 1_000_000_000.0,
            "description": "Finite numeric value for this parameter word.",
        },
    },
    ("word", "value"),
)
_CUSTOM_BLOCK = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "command"},
                "code": {
                    "type": "string",
                    "pattern": r"^[GM][0-9]{1,4}(?:\.[0-9]{1,3})?$",
                    "maxLength": 9,
                    "description": (
                        "One uppercase G or M code, for example G4, G38.2, or M62."
                    ),
                },
                "parameters": {
                    "type": "array",
                    "items": _CUSTOM_PARAMETER,
                    "minItems": 0,
                    "maxItems": 16,
                    "description": "Explicit parameters with unique words.",
                },
            },
            ("kind", "code", "parameters"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "comment"},
                "comment": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "pattern": r"^[\x20-\x27\x2A-\x7E]+$",
                    "description": (
                        "Printable ASCII comment without parentheses, line breaks, or "
                        "control characters."
                    ),
                },
            },
            ("kind", "comment"),
        ),
    ]
}


def manufacture_program_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_PROGRAM_CAPABILITY_NAME,
        description=(
            "Add bounded program-control or explicitly structured Custom operations "
            "to one exact CAM Job."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="comment",
                description="Append one printable parenthetical comment at the History marker.",
                action_ids=frozenset({"CAM_Comment"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobAndProgramComment",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": _LABEL,
                        "job": _EXACT_JOB,
                        "comment": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1024,
                            "pattern": r"^[\x20-\x27\x2A-\x7E]+$",
                            "description": (
                                "One printable ASCII comment with no parentheses, line "
                                "breaks, or control characters."
                            ),
                        },
                    },
                    ("label", "job", "comment"),
                ),
            ),
            NativeCapabilityVariant(
                operation="stop",
                description=(
                    "Add one source-preserving program stop at the exact document "
                    "History marker. Optional emits M1; mandatory emits M0."
                ),
                action_ids=frozenset({"CAM_Stop"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobAndProgramStop",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": _LABEL,
                        "job": _EXACT_JOB,
                        "stop_mode": {
                            "type": "string",
                            "enum": ["optional", "mandatory"],
                            "description": (
                                "optional emits M1 and depends on the machine's optional-"
                                "stop control; mandatory emits M0 and always requests a stop."
                            ),
                        },
                    },
                    ("label", "job", "stop_mode"),
                ),
            ),
            NativeCapabilityVariant(
                operation="custom",
                description="Create one Custom operation from structured G/M codes and comments.",
                action_ids=frozenset({"CAM_Custom"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobControllerAndStructuredCustomProgram"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": _LABEL,
                        "job": _EXACT_JOB,
                        "tool_controller": _EXACT_JOB,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                            "description": (
                                "Coolant policy for cutting-motion blocks. Flood or mist "
                                "adds the matching on/off commands around feed motion."
                            ),
                        },
                        "blocks": {
                            "type": "array",
                            "items": _CUSTOM_BLOCK,
                            "minItems": 1,
                            "maxItems": 64,
                            "description": "Ordered command or comment program blocks.",
                        },
                    },
                    ("label", "job", "tool_controller", "coolant", "blocks"),
                ),
            ),
        ),
    )


def register_manufacture_program_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_program_capability_definition())
