# SPDX-License-Identifier: LGPL-2.1-or-later

"""Reusable bounded schema fragments for current Design operations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


LABEL_SCHEMA = {"type": "string", "maxLength": 160}
OBJECT_NAME_SCHEMA = {
    "type": "string",
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
POSITIVE_MM_SCHEMA = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1_000_000.0,
}
NONNEGATIVE_MM_SCHEMA = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1_000_000.0,
}
SIGNED_MM_SCHEMA = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
}


def parameters_schema(
    properties: dict[str, Any],
    required: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": deepcopy(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def object_reference_schema() -> dict[str, Any]:
    return parameters_schema(
        {"object_name": OBJECT_NAME_SCHEMA},
        ("object_name",),
    )


def design_result_schema() -> dict[str, Any]:
    return parameters_schema(
        {
            "mode": {
                "type": "string",
                "enum": ["new_body", "join", "cut", "intersect"],
            },
            "targets": {
                "type": "array",
                "items": object_reference_schema(),
                "minItems": 0,
                "maxItems": 16,
                "uniqueItems": True,
            },
            "destination_component": {
                "oneOf": [object_reference_schema(), {"type": "null"}],
            },
        },
        ("mode", "targets", "destination_component"),
    )


def vector_schema(*, minimum: float, maximum: float) -> dict[str, Any]:
    number = {"type": "number", "minimum": minimum, "maximum": maximum}
    return parameters_schema(
        {"x": number, "y": number, "z": number},
        ("x", "y", "z"),
    )


def placement_schema() -> dict[str, Any]:
    rotation = parameters_schema(
        {
            "axis": vector_schema(minimum=-1.0, maximum=1.0),
            "angle_degrees": {
                "type": "number",
                "minimum": -360.0,
                "maximum": 360.0,
            },
        },
        ("axis", "angle_degrees"),
    )
    return parameters_schema(
        {
            "origin_mm": vector_schema(
                minimum=-1_000_000.0,
                maximum=1_000_000.0,
            ),
            "rotation": rotation,
        },
        ("origin_mm", "rotation"),
    )


def design_link_schema(
    field: str,
    pattern: str,
    *,
    minimum: int,
    maximum: int,
) -> dict[str, Any]:
    return parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            field: {
                "type": "array",
                "items": {
                    "type": "string",
                    "maxLength": 64,
                    "pattern": pattern,
                },
                "minItems": minimum,
                "maxItems": maximum,
                "uniqueItems": True,
            },
        },
        ("object_name", field),
    )
