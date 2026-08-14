# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped CAM Array dress-up."""

from __future__ import annotations

from VibeCADNativeManufactureContract import (
    PATH_OPERATION_LABEL_SCHEMA as LABEL_SCHEMA,
)


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
_DISTANCE_MM = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
}
_NONNEGATIVE_DISTANCE_MM = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1_000_000.0,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_EXACT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_VECTOR_MM = _closed(
    {
        "x_mm": _DISTANCE_MM,
        "y_mm": _DISTANCE_MM,
        "z_mm": _DISTANCE_MM,
    },
    ("x_mm", "y_mm", "z_mm"),
)
_NONNEGATIVE_VECTOR_MM = _closed(
    {
        "x_mm": _NONNEGATIVE_DISTANCE_MM,
        "y_mm": _NONNEGATIVE_DISTANCE_MM,
        "z_mm": _NONNEGATIVE_DISTANCE_MM,
    },
    ("x_mm", "y_mm", "z_mm"),
)
ARRAY_DRESSUP_PATTERN_SCHEMA = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "linear_1d"},
                "copies": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 99999,
                    "description": "Additional copies after the original toolpath.",
                },
                "offset_mm": _VECTOR_MM,
            },
            ("kind", "copies", "offset_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "linear_2d"},
                "copies_x": {"type": "integer", "minimum": 0, "maximum": 99999},
                "copies_y": {"type": "integer", "minimum": 0, "maximum": 99999},
                "offset_mm": _VECTOR_MM,
                "first_direction": {"type": "string", "enum": ["x", "y"]},
            },
            ("kind", "copies_x", "copies_y", "offset_mm", "first_direction"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "polar"},
                "copies": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 99999,
                    "description": "Additional rotated copies after the original toolpath.",
                },
                "total_angle_degrees": {
                    "type": "number",
                    "minimum": -360_000.0,
                    "maximum": 360_000.0,
                },
                "centre_mm": _VECTOR_MM,
            },
            ("kind", "copies", "total_angle_degrees", "centre_mm"),
        ),
    ]
}
ARRAY_DRESSUP_JITTER_SCHEMA = {
    "oneOf": [
        _closed(
            {"enabled": {"type": "boolean", "const": False}},
            ("enabled",),
        ),
        _closed(
            {
                "enabled": {"type": "boolean", "const": True},
                "percentage": {"type": "integer", "minimum": 1, "maximum": 100},
                "seed": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2_147_483_647,
                },
                "maximum_offset_mm": _NONNEGATIVE_VECTOR_MM,
            },
            ("enabled", "percentage", "seed", "maximum_offset_mm"),
        ),
    ]
}
ARRAY_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "pattern": ARRAY_DRESSUP_PATTERN_SCHEMA,
        "jitter": ARRAY_DRESSUP_JITTER_SCHEMA,
    },
    ("label", "job", "base_operation", "pattern", "jitter"),
)
