# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped CAM Drag Knife dress-up."""

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


DRAG_KNIFE_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "corner_filter_angle_degrees": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 180.0,
            "description": (
                "Ignore direction changes smaller than this angle. Zero compensates "
                "every detected corner; 180 compensates only a complete reversal."
            ),
        },
        "blade_offset_mm": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 100.0,
            "description": (
                "Positive distance by which the blade tip trails the machine pivot."
            ),
        },
        "pivot_height_mm": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "description": (
                "Absolute Z height used while rotating the blade at compensated "
                "corners; it must be above every compensated cutting depth."
            ),
        },
    },
    (
        "label",
        "job",
        "base_operation",
        "corner_filter_angle_degrees",
        "blade_offset_mm",
        "pivot_height_mm",
    ),
)
