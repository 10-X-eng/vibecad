# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped CAM Mirror dress-up."""

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
_VECTOR = _closed(
    {
        f"{axis}_mm": {
            "type": "number",
            "minimum": -1_000_000.0,
            "maximum": 1_000_000.0,
        }
        for axis in ("x", "y", "z")
    },
    ("x_mm", "y_mm", "z_mm"),
)
_COMMON = {
    "axis": {
        "type": "string",
        "enum": ["x", "y", "xy"],
        "description": (
            "x reflects Y about an X-parallel line; y reflects X about a Y-parallel "
            "line; xy reflects both coordinates."
        ),
    },
    "offset_mm": _VECTOR,
    "keep_base_path": {
        "type": "boolean",
        "description": "Prepend one unchanged placed copy of the source toolpath.",
    },
}
_GLOBAL_AXIS = _closed(
    {
        "kind": {"type": "string", "const": "axis_at_origin"},
        **_COMMON,
    },
    ("kind", "axis", "offset_mm", "keep_base_path"),
    "Reflect about the selected global axis or axes, then apply offset_mm.",
)
_MODEL_CENTER = _closed(
    {
        "kind": {"type": "string", "const": "axis_at_model_center"},
        **_COMMON,
        "model": _EXACT_TARGET,
    },
    ("kind", "axis", "model", "offset_mm", "keep_base_path"),
    (
        "Reflect about the global-bounds center of one exact current Job model, then "
        "apply offset_mm."
    ),
)
_REFERENCE_TARGET = _closed(
    {
        **_EXACT_TARGET["properties"],
        "subelement": {
            "type": "string",
            "pattern": r"^(?:Edge|Face)[1-9][0-9]*$",
            "maxLength": 80,
            "description": (
                "Axis-aligned EdgeN or FaceN with exactly one fixed global X or Y coordinate."
            ),
        },
    },
    ("object_name", "expected_state_sha256", "subelement"),
)
_REFERENCE = _closed(
    {
        "kind": {"type": "string", "const": "axis_aligned_reference"},
        "reference": _REFERENCE_TARGET,
        "offset_mm": _VECTOR,
        "keep_base_path": _COMMON["keep_base_path"],
    },
    ("kind", "reference", "offset_mm", "keep_base_path"),
    (
        "Reflect about one exact axis-aligned Edge or Face, then apply offset_mm. "
        "The reference determines whether X or Y is reflected."
    ),
)


MIRROR_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "mirror": {"oneOf": [_GLOBAL_AXIS, _MODEL_CENTER, _REFERENCE]},
    },
    ("label", "job", "base_operation", "mirror"),
)
