# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical extrusion tool across solid, surface, add, and remove semantics."""

from typing import Any

from . import part_extrude, partdesign_linear_feature


_VECTOR_OR_NULL = {
    "description": "Custom global direction, or null to follow the sketch normal.",
    "oneOf": [partdesign_linear_feature.VECTOR_SCHEMA, {"type": "null"}],
}

_EXTENT_SCHEMA = {
    "description": "Distance or geometric termination for the extrusion.",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "type": {"const": "distance"},
                "distance_mm": {"type": "number", "exclusiveMinimum": 0},
                "second_distance_mm": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["type", "distance_mm"],
            "additionalProperties": False,
        },
        *partdesign_linear_feature.extent_schema(
            ["through_all", "up_to_last", "up_to_first", "up_to_face", "up_to_shape"]
        )["oneOf"],
    ],
}


TOOL_SPEC = {
    "name": "model.extrude",
    "description": (
        "Extrude one exact profile. operation states the design intent explicitly: create a new "
        "solid or surface, add material to a Body, or remove material from a Body."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "profile_name": {
                "type": "string",
                "description": "Exact internal name of the sketch, wire, or planar face.",
            },
            "operation": {
                "type": "string",
                "enum": ["new_solid", "new_surface", "add_material", "remove_material"],
                "description": (
                    "Create standalone geometry or explicitly add/remove Body material."
                ),
            },
            "extent": _EXTENT_SCHEMA,
            "side": {
                "type": "string",
                "enum": ["one_side", "two_sides", "symmetric"],
                "description": "How the extrusion distance is distributed around the profile.",
            },
            "direction": _VECTOR_OR_NULL,
            "reversed": {
                "type": "boolean",
                "description": "Reverse the resolved extrusion direction.",
            },
            "taper_angle_degrees": {
                "type": "number",
                "exclusiveMinimum": -89,
                "exclusiveMaximum": 89,
                "description": "Draft angle for the primary extrusion side in degrees.",
            },
            "second_taper_angle_degrees": {
                "type": "number",
                "exclusiveMinimum": -89,
                "exclusiveMaximum": 89,
                "description": "Draft angle for the second extrusion side in degrees.",
            },
            "refine": {
                "type": "boolean",
                "description": "Remove redundant edges from a Body material result.",
            },
            "label": {"type": "string", "description": "Visible result label."},
        },
        "required": [
            "profile_name",
            "operation",
            "extent",
            "side",
            "direction",
            "reversed",
            "taper_angle_degrees",
            "second_taper_angle_degrees",
            "refine",
            "label",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    profile_name: str,
    operation: str,
    extent: dict[str, Any],
    side: str,
    direction: dict[str, float] | None,
    reversed: bool,
    taper_angle_degrees: float,
    second_taper_angle_degrees: float,
    refine: bool,
    label: str,
) -> dict[str, Any]:
    if operation in {"add_material", "remove_material"}:
        native_operation, type_id = (
            ("pad", "PartDesign::Pad")
            if operation == "add_material"
            else ("pocket", "PartDesign::Pocket")
        )
        mapped_extent = _body_extent(extent)
        if not mapped_extent.get("ok"):
            return mapped_extent
        result = partdesign_linear_feature.run(
            service,
            operation=native_operation,
            type_id=type_id,
            profile_name=profile_name,
            label=label,
            extent=mapped_extent["extent"],
            side=side,
            reversed=reversed,
            taper_angle_degrees=taper_angle_degrees,
            second_taper_angle_degrees=second_taper_angle_degrees,
            direction=direction,
            refine=refine,
        )
    elif operation in {"new_solid", "new_surface"}:
        if refine:
            return _invalid(
                "Standalone extrusion does not have a native refine option; set refine=false."
            )
        if direction is None:
            return _invalid("new_solid and new_surface require an explicit direction.")
        mapped_extent = _standalone_extent(extent, side)
        if not mapped_extent.get("ok"):
            return mapped_extent
        actual_direction = dict(direction)
        if reversed:
            actual_direction = {
                axis: -float(actual_direction[axis]) for axis in ("x", "y", "z")
            }
        result = part_extrude.run(
            service,
            profile_object_name=profile_name,
            direction=actual_direction,
            extent=mapped_extent["extent"],
            solid=operation == "new_solid",
            taper_angle_degrees=taper_angle_degrees,
            second_taper_angle_degrees=second_taper_angle_degrees,
            label=label,
        )
    else:
        return _invalid("Unknown extrusion operation.")
    if isinstance(result, dict):
        result["operation"] = "extrude"
        result["material_operation"] = operation
    return result


def _body_extent(extent: dict[str, Any]) -> dict[str, Any]:
    mapped = dict(extent or {})
    if mapped.get("type") == "distance":
        mapped = {
            "type": "length",
            "length": mapped.get("distance_mm"),
            **(
                {"second_length": mapped["second_distance_mm"]}
                if "second_distance_mm" in mapped
                else {}
            ),
        }
    return {"ok": True, "extent": mapped}


def _standalone_extent(extent: dict[str, Any], side: str) -> dict[str, Any]:
    if (extent or {}).get("type") != "distance":
        return _invalid(
            "Standalone solid and surface extrusion require a distance extent."
        )
    distance = extent.get("distance_mm")
    if side == "one_side":
        mapped = {"type": "one_direction", "length_mm": distance}
    elif side == "two_sides":
        if "second_distance_mm" not in extent:
            return _invalid("two_sides requires second_distance_mm.")
        mapped = {
            "type": "two_directions",
            "forward_mm": distance,
            "reverse_mm": extent["second_distance_mm"],
        }
    elif side == "symmetric":
        mapped = {"type": "symmetric", "total_length_mm": distance}
    else:
        return _invalid("side must be one_side, two_sides, or symmetric.")
    return {"ok": True, "extent": mapped}


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False}
