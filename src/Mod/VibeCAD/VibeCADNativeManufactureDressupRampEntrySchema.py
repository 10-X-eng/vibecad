# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped CAM Ramp Entry dress-up."""

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


def _closed(properties: dict, required: tuple[str, ...], description: str = "") -> dict:
    result = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
    if description:
        result["description"] = description
    return result


_EXACT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_METHOD = {
    "type": "string",
    "enum": [
        "forward_then_return",
        "reverse_into_cut",
        "zigzag",
        "contour_helix",
    ],
    "description": (
        "forward_then_return ramps forward and returns level; reverse_into_cut "
        "positions forward then ramps backward into the cut; zigzag descends on "
        "forward and reverse passes; contour_helix follows a closed contour repeatedly."
    ),
}
_ANGLE = {
    "type": "number",
    "minimum": 0.1,
    "maximum": 89.9,
    "description": (
        "Ramp angle measured from vertical: small values are steep and values near "
        "90 degrees are shallow."
    ),
}
_ALL_PLUNGES = _closed(
    {"kind": {"type": "string", "const": "all_plunges"}},
    ("kind",),
    "Generate entry motion for every eligible descending plunge.",
)
_BELOW_DEPTH = _closed(
    {
        "kind": {"type": "string", "const": "below_absolute_z"},
        "z_mm": {
            "type": "number",
            "minimum": -1_000_000.0,
            "maximum": 1_000_000.0,
            "description": (
                "Absolute Job-coordinate Z threshold. Motion above it is retained "
                "unchanged; crossing plunges are split exactly at this depth."
            ),
        },
    },
    ("kind", "z_mm"),
)


RAMP_ENTRY_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "method": _METHOD,
        "angle_from_vertical_degrees": _ANGLE,
        "activation": {"oneOf": [_ALL_PLUNGES, _BELOW_DEPTH]},
    },
    (
        "label",
        "job",
        "base_operation",
        "method",
        "angle_from_vertical_degrees",
        "activation",
    ),
)
