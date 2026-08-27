# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for every shipped CAM Lead In/Out style."""

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
_POSITIVE_DISTANCE = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1_000_000.0,
}
_OFFSET = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
    "description": (
        "Signed distance along the source profile: positive adds overtravel and "
        "negative trims the profile end."
    ),
}
_EXTENSION = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1_000_000.0,
    "description": "Additional straight extension beyond the selected lead geometry.",
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


def _style(name: str) -> dict:
    return {"type": "string", "const": name}


def _shared_styles(schema: dict, *styles: str) -> dict:
    """Publish one branch for styles with the exact same fields."""

    result = dict(schema)
    result.pop("description", None)
    result["properties"] = {
        **schema["properties"],
        "style": {"type": "string", "enum": list(styles)},
    }
    return result


def _angled_radius(name: str, *, maximum: float = 180.0, invert: bool = True) -> dict:
    properties = {
        "style": _style(name),
        "angle_degrees": {"type": "number", "minimum": 0.1, "maximum": maximum},
        "radius_mm": _POSITIVE_DISTANCE,
    }
    required = ["style", "angle_degrees", "radius_mm"]
    if invert:
        properties["invert"] = {
            "type": "boolean",
            "description": "Place the lead on the opposite side of source-path travel.",
        }
        required.append("invert")
    properties["offset_mm"] = _OFFSET
    required.append("offset_mm")
    return _closed(properties, tuple(required))


def _angled_length(
    name: str,
    *,
    minimum: float = 0.0,
    maximum: float = 180.0,
    invert: bool = True,
    extend: bool = False,
) -> dict:
    properties = {
        "style": _style(name),
        "angle_degrees": {"type": "number", "minimum": minimum, "maximum": maximum},
        "length_mm": _POSITIVE_DISTANCE,
    }
    required = ["style", "angle_degrees", "length_mm"]
    if invert:
        properties["invert"] = {
            "type": "boolean",
            "description": "Place the lead on the opposite side of source-path travel.",
        }
        required.append("invert")
    properties["offset_mm"] = _OFFSET
    required.append("offset_mm")
    if extend:
        properties["extend_mm"] = _EXTENSION
        required.append("extend_mm")
    return _closed(properties, tuple(required))


_DISABLED = _closed(
    {"style": _style("disabled")},
    ("style",),
    "No lead motion on this side.",
)
_ARC = _angled_radius("arc")
_ARC["properties"]["extend_mm"] = _EXTENSION
_ARC["required"].append("extend_mm")
_ARC["description"] = "Planar tangent arc followed by an optional straight extension."
_LINE = _angled_length("line", extend=True)
_LINE["description"] = (
    "Planar straight lead with explicit angle, length, and extension."
)
_PERPENDICULAR = _closed(
    {
        "style": _style("perpendicular"),
        "length_mm": _POSITIVE_DISTANCE,
        "offset_mm": _OFFSET,
        "extend_mm": _EXTENSION,
    },
    ("style", "length_mm", "offset_mm", "extend_mm"),
    "Planar line fixed perpendicular to source-path travel.",
)
_TANGENT = _closed(
    {
        "style": _style("tangent"),
        "length_mm": _POSITIVE_DISTANCE,
        "offset_mm": _OFFSET,
        "extend_mm": _EXTENSION,
    },
    ("style", "length_mm", "offset_mm", "extend_mm"),
    "Planar line fixed tangent to source-path travel.",
)
_ARC_3D = _angled_radius("arc_3d")
_ARC_3D["description"] = (
    "Planar arc whose free endpoint inherits the prior depth transition."
)
_ARC_Z = _angled_radius("arc_z", maximum=179.0, invert=False)
_ARC_Z["description"] = (
    "Segmented vertical-plane arc without following the source profile."
)
_ARC_Z_FOLLOW = _angled_radius("arc_z_follow", maximum=179.0, invert=False)
_ARC_Z_FOLLOW["description"] = "Segmented vertical arc that follows the source profile."
_HELIX = _angled_radius("helix")
_HELIX["description"] = "Arc lead whose Z motion is blended between adjacent depths."
_LINE_3D = _angled_length("line_3d")
_LINE_3D["description"] = (
    "Straight lead whose free endpoint inherits the prior depth transition."
)
_LINE_Z = _angled_length("line_z", minimum=0.1, invert=False)
_LINE_Z["description"] = (
    "Inclined straight lead in Z without following the source profile."
)
_LINE_Z_FOLLOW = _angled_length(
    "line_z_follow",
    minimum=0.1,
    maximum=89.0,
    invert=False,
)
_LINE_Z_FOLLOW["description"] = "Inclined Z lead distributed along the source profile."
_NO_RETRACT = _closed(
    {"style": _style("no_retract")},
    ("style",),
    "Join profiles without adding a clearance retract or independent lead geometry.",
)
_VERTICAL = _closed(
    {"style": _style("vertical"), "offset_mm": _OFFSET},
    ("style", "offset_mm"),
    "Use only the vertical entry or exit travel, with optional profile trim/overtravel.",
)

LEAD_DEFINITION_SCHEMA = {
    "oneOf": [
        _shared_styles(_DISABLED, "disabled", "no_retract"),
        _ARC,
        _LINE,
        _shared_styles(_PERPENDICULAR, "perpendicular", "tangent"),
        _shared_styles(_ARC_3D, "arc_3d", "helix"),
        _shared_styles(_ARC_Z, "arc_z", "arc_z_follow"),
        _LINE_3D,
        _LINE_Z,
        _LINE_Z_FOLLOW,
        _VERTICAL,
    ]
}


LEAD_IN_OUT_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "lead_in": LEAD_DEFINITION_SCHEMA,
        "lead_out": LEAD_DEFINITION_SCHEMA,
        "retract_threshold_mm": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1_000_000.0,
            "description": (
                "Retract between profile endpoints farther apart than this XY distance; "
                "shorter transfers remain down."
            ),
        },
        "rapid_plunge": {
            "type": "boolean",
            "description": "True selects G0; false selects vertical feed for the final plunge.",
        },
    },
    (
        "label",
        "job",
        "base_operation",
        "lead_in",
        "lead_out",
        "retract_threshold_mm",
        "rapid_plunge",
    ),
)
