# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped CAM Dogbone dress-up."""

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
_BONE_LOCATION = _closed(
    {
        "x_mm": {
            "type": "number",
            "minimum": -1_000_000.0,
            "maximum": 1_000_000.0,
        },
        "y_mm": {
            "type": "number",
            "minimum": -1_000_000.0,
            "maximum": 1_000_000.0,
        },
    },
    ("x_mm", "y_mm"),
)
_ADAPTIVE = _closed(
    {
        "kind": {"type": "string", "enum": ["adaptive"]},
        "maximum_length_mm": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1_000_000.0,
            "description": "Zero means no cap; a positive value limits adaptive incision length.",
        },
    },
    ("kind", "maximum_length_mm"),
)
_ADAPTIVE["description"] = (
    "Fit each relief to its corner geometry, optionally capped by maximum_length_mm."
)
_FIXED = _closed(
    {"kind": {"type": "string", "enum": ["fixed"]}},
    ("kind",),
)
_FIXED["description"] = "Use the inherited cutter radius as every incision length."
_CUSTOM = _closed(
    {
        "kind": {"type": "string", "enum": ["custom"]},
        "length_mm": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1_000_000.0,
        },
    },
    ("kind", "length_mm"),
)
_CUSTOM["description"] = "Use the explicit positive length_mm for every incision."


DOGBONE_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "style": {
            "oneOf": [
                {
                    "type": "string",
                    "const": "dogbone",
                    "description": "Relief follows the corner-angle bisector.",
                },
                {
                    "type": "string",
                    "const": "t_bone_horizontal",
                    "description": "T-bone relief is constrained to the global X direction.",
                },
                {
                    "type": "string",
                    "const": "t_bone_vertical",
                    "description": "T-bone relief is constrained to the global Y direction.",
                },
                {
                    "type": "string",
                    "const": "t_bone_long_edge",
                    "description": "T-bone relief is normal to the longer adjacent edge.",
                },
                {
                    "type": "string",
                    "const": "t_bone_short_edge",
                    "description": "T-bone relief is normal to the shorter adjacent edge.",
                },
            ],
        },
        "side": {
            "type": "string",
            "enum": ["left", "right"],
            "description": "Side of base-path travel on which eligible concave corners lie.",
        },
        "incision": {"oneOf": [_ADAPTIVE, _FIXED, _CUSTOM]},
        "only_closed_profiles": {
            "type": "boolean",
            "description": "Restrict reliefs to outer closed cutting profiles.",
        },
        "disabled_bone_locations_mm": {
            "type": "array",
            "items": _BONE_LOCATION,
            "minItems": 0,
            "maxItems": 256,
            "uniqueItems": True,
            "description": (
                "Exact placed-path XY corner locations to leave unmodified, obtainable "
                "from manufacture.inspect_toolpath. Coordinates are matched to 0.0001 mm; "
                "one location disables that corner across every cutting depth, matching "
                "the human checklist."
            ),
        },
    },
    (
        "label",
        "job",
        "base_operation",
        "style",
        "side",
        "incision",
        "only_closed_profiles",
        "disabled_bone_locations_mm",
    ),
)
