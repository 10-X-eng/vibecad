# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for the shipped CAM holding-tag dress-up."""

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
_TAG_SHAPE = _closed(
    {
        "material_width_mm": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1_000_000.0,
            "description": "Uncut material width left by each holding tag.",
        },
        "material_height_mm": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1_000_000.0,
            "description": "Requested vertical material height retained at each tag.",
        },
        "side_angle_from_horizontal_degrees": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 90.0,
            "description": (
                "Ascent/descent side angle from horizontal. 90 degrees makes a "
                "vertical-sided tag; lower values taper the tag and may limit its height."
            ),
        },
        "top_fillet_radius_mm": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1_000_000.0,
            "description": (
                "Requested top fillet radius. Geometry safely clips it to the largest "
                "radius the selected tag shape can contain."
            ),
        },
    },
    (
        "material_width_mm",
        "material_height_mm",
        "side_angle_from_horizontal_degrees",
        "top_fillet_radius_mm",
    ),
)
_TAG_LOCATION = _closed(
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
        "enabled": {
            "type": "boolean",
            "description": "Explicit durable tag state.",
        },
    },
    ("x_mm", "y_mm", "enabled"),
)
_EXPLICIT = _closed(
    {
        "kind": {"type": "string", "const": "explicit_locations"},
        "shape": _TAG_SHAPE,
        "tags": {
            "type": "array",
            "items": _TAG_LOCATION,
            "minItems": 1,
            "maxItems": 256,
            "description": "Ordered enabled or disabled XY tag locations.",
        },
    },
    ("kind", "shape", "tags"),
    "Use exact editable tag locations and explicit enablement.",
)
_AUTOMATIC = _closed(
    {
        "kind": {"type": "string", "const": "automatic_distribution"},
        "shape": _TAG_SHAPE,
        "minimum_per_wire": {
            "type": "integer",
            "minimum": 1,
            "maximum": 64,
        },
        "maximum_for_longest_wire": {
            "type": "integer",
            "minimum": 1,
            "maximum": 64,
            "description": "Maximum tag count for the longest bottom wire.",
        },
    },
    ("kind", "shape", "minimum_per_wire", "maximum_for_longest_wire"),
    "Distribute tags deterministically over every bottom cutting wire.",
)
_COPY = _closed(
    {
        "kind": {"type": "string", "const": "copy_enabled_from_dressup"},
        "source_tag_dressup": _EXACT_TARGET,
    },
    ("kind", "source_tag_dressup"),
    (
        "Inherit the source tag shape and map each enabled source position to the "
        "closest bottom wire of the target operation."
    ),
)


TAG_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "placement": {"oneOf": [_EXPLICIT, _AUTOMATIC, _COPY]},
    },
    ("label", "job", "base_operation", "placement"),
)
