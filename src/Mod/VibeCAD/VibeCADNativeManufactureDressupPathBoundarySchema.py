# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped CAM Path Boundary dress-up."""

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
_LENGTH = {"type": "number", "minimum": 0.0, "maximum": 1_000_000.0}
_POSITIVE_LENGTH = {
    "type": "number",
    "minimum": 0.001,
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
_POINT = _closed(
    {
        "x": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
        "y": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
        "z": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
    },
    ("x", "y", "z"),
)
_DIRECTION = _closed(
    {
        "x": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "y": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "z": {"type": "number", "minimum": -1.0, "maximum": 1.0},
    },
    ("x", "y", "z"),
)
_PLACEMENT = _closed(
    {
        "origin_mm": _POINT,
        "rotation_axis": _DIRECTION,
        "rotation_degrees": {
            "type": "number",
            "minimum": -360_000.0,
            "maximum": 360_000.0,
        },
    },
    ("origin_mm", "rotation_axis", "rotation_degrees"),
)
_MODEL_BOUNDS = _closed(
    {
        "kind": {"type": "string", "enum": ["model_bounds"]},
        "x_negative_mm": _LENGTH,
        "x_positive_mm": _LENGTH,
        "y_negative_mm": _LENGTH,
        "y_positive_mm": _LENGTH,
        "z_negative_mm": _LENGTH,
        "z_positive_mm": _LENGTH,
    },
    (
        "kind",
        "x_negative_mm",
        "x_positive_mm",
        "y_negative_mm",
        "y_positive_mm",
        "z_negative_mm",
        "z_positive_mm",
    ),
)
_BOX = _closed(
    {
        "kind": {"type": "string", "enum": ["box"]},
        "length_mm": _POSITIVE_LENGTH,
        "width_mm": _POSITIVE_LENGTH,
        "height_mm": _POSITIVE_LENGTH,
        "placement": _PLACEMENT,
    },
    ("kind", "length_mm", "width_mm", "height_mm", "placement"),
)
_CYLINDER = _closed(
    {
        "kind": {"type": "string", "enum": ["cylinder"]},
        "radius_mm": _POSITIVE_LENGTH,
        "height_mm": _POSITIVE_LENGTH,
        "placement": _PLACEMENT,
    },
    ("kind", "radius_mm", "height_mm", "placement"),
)
_EXISTING_SOLID = _closed(
    {
        "kind": {"type": "string", "enum": ["existing_solid"]},
        "source": _EXACT_TARGET,
    },
    ("kind", "source"),
)


PATH_BOUNDARY_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "boundary": {
            "oneOf": [_MODEL_BOUNDS, _BOX, _CYLINDER, _EXISTING_SOLID],
            "description": (
                "Exact clipping solid: Job-model bounds, an explicit box or cylinder, "
                "or a durable clone of one current public solid."
            ),
        },
        "inside": {
            "type": "boolean",
            "description": "Keep motion inside the boundary when true; outside when false.",
        },
        "offset_mm": {
            **_LENGTH,
            "description": (
                "Boundary offset magnitude. Inside clipping shrinks the boundary; "
                "outside clipping expands it."
            ),
        },
        "retract_threshold_mm": {
            **_LENGTH,
            "description": (
                "Maximum excluded gap that may be linked at feed instead of retracting."
            ),
        },
        "rest_machining_pass": {
            "type": "boolean",
            "description": "Mark the result for downstream Rest Machining handling.",
        },
    },
    (
        "label",
        "job",
        "base_operation",
        "boundary",
        "inside",
        "offset_mm",
        "retract_threshold_mm",
        "rest_machining_pass",
    ),
)
