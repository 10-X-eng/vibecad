# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped parametric CAM Array operation."""

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
_POINT_SUBELEMENTS = {
    "type": "array",
    "items": {
        "type": "string",
        "pattern": r"^(?:Vertex|Edge|Face)[1-9][0-9]*$",
        "maxLength": 32,
    },
    "minItems": 0,
    "maxItems": 64,
    "uniqueItems": True,
    "description": (
        "Exact point-bearing subelements. An empty list deliberately uses the "
        "whole source shape according to the shipped CAM Array rules."
    ),
}
_POINT_SOURCE = _closed(
    {
        "model": _EXACT_TARGET,
        "subelements": _POINT_SUBELEMENTS,
    },
    ("model", "subelements"),
)
_POINT_ORIGIN = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "global"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "whole_source"},
                "model": _EXACT_TARGET,
            },
            ("kind", "model"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "subelement"},
                "model": _EXACT_TARGET,
                "subelement": {
                    "type": "string",
                    "pattern": r"^(?:Vertex|Edge|Face)[1-9][0-9]*$",
                    "maxLength": 32,
                },
            },
            ("kind", "model", "subelement"),
        ),
    ]
}
ARRAY_PATTERN_SCHEMA = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "linear_1d"},
                "copies": {"type": "integer", "minimum": 1, "maximum": 99999},
                "offset_mm": _VECTOR_MM,
            },
            ("kind", "copies", "offset_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "linear_2d"},
                "copies_x": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 99999,
                },
                "copies_y": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 99999,
                },
                "offset_mm": _VECTOR_MM,
                "first_direction": {"type": "string", "enum": ["x", "y"]},
            },
            (
                "kind",
                "copies_x",
                "copies_y",
                "offset_mm",
                "first_direction",
            ),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "polar"},
                "copies": {"type": "integer", "minimum": 1, "maximum": 99999},
                "total_angle_degrees": {
                    "type": "number",
                    "minimum": -360_000.0,
                    "maximum": 360_000.0,
                },
                "centre_mm": _VECTOR_MM,
            },
            ("kind", "copies", "total_angle_degrees", "centre_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "points"},
                "sources": {
                    "type": "array",
                    "items": _POINT_SOURCE,
                    "minItems": 1,
                    "maxItems": 32,
                },
                "origin": _POINT_ORIGIN,
                "sorting": {"type": "string", "enum": ["automatic", "manual"]},
            },
            ("kind", "sources", "origin", "sorting"),
        ),
    ]
}
ARRAY_JITTER_SCHEMA = {
    "oneOf": [
        _closed(
            {"enabled": {"type": "boolean", "const": False}},
            ("enabled",),
        ),
        _closed(
            {
                "enabled": {"type": "boolean", "const": True},
                "seed": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 2_147_483_647,
                },
                "maximum_offset_mm": _NONNEGATIVE_VECTOR_MM,
                "maximum_rotation_degrees": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 360.0,
                },
            },
            (
                "enabled",
                "seed",
                "maximum_offset_mm",
                "maximum_rotation_degrees",
            ),
        ),
    ]
}
ARRAY_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operations": {
            "type": "array",
            "items": _EXACT_TARGET,
            "minItems": 1,
            "maxItems": 64,
            "uniqueItems": True,
            "description": (
                "Ordered exact Job operation outputs whose placed toolpaths are repeated."
            ),
        },
        "pattern": ARRAY_PATTERN_SCHEMA,
        "reverse_direction": {"type": "boolean"},
        "jitter": ARRAY_JITTER_SCHEMA,
    },
    (
        "label",
        "job",
        "base_operations",
        "pattern",
        "reverse_direction",
        "jitter",
    ),
)
