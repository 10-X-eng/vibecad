# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped CAM Axis Map dress-up."""

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
AXIS_MAP_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "axis_mapping": {
            "type": "string",
            "enum": [
                "x_to_a",
                "y_to_a",
                "x_to_b",
                "y_to_b",
                "x_to_c",
                "y_to_c",
            ],
            "description": "Linear input axis and rotary output axis.",
        },
        "radius_mm": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1_000_000.0,
            "description": "Positive wrap radius used for distance-to-angle conversion.",
        },
        "reverse": {
            "type": "boolean",
            "description": "Reverse the generated rotary-axis direction explicitly.",
        },
    },
    ("label", "job", "base_operation", "axis_mapping", "radius_mm", "reverse"),
)
