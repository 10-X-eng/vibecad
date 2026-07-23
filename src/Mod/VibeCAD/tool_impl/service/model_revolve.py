# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical revolve tool across solid, surface, add, and remove semantics."""

from typing import Any

from . import domain_runtime, part_revolve, partdesign_rotational_feature


_GLOBAL_AXIS = {
    "type": "object",
    "properties": {
        "source": {"const": "global"},
        "point": domain_runtime.vector_schema("A global point on the axis in mm."),
        "direction": domain_runtime.vector_schema(
            "Global axis direction; only direction matters.", units=None
        ),
    },
    "required": ["source", "point", "direction"],
    "additionalProperties": False,
}


TOOL_SPEC = {
    "name": "model.revolve",
    "description": (
        "Revolve one exact profile. operation explicitly creates a new solid or surface, adds "
        "material to a Body, or removes material from a Body."
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
            "axis": {
                "description": "Global axis for new geometry or a Body/profile reference axis.",
                "oneOf": [
                    _GLOBAL_AXIS,
                    *partdesign_rotational_feature.AXIS_SCHEMA["oneOf"],
                ],
            },
            "extent": partdesign_rotational_feature.extent_schema(
                [
                    "angle",
                    "two_angles",
                    "through_all",
                    "up_to_last",
                    "up_to_first",
                    "up_to_face",
                ]
            ),
            "midplane": {
                "type": "boolean",
                "description": "Center the revolved angle symmetrically around its profile.",
            },
            "reversed": {
                "type": "boolean",
                "description": "Reverse the resolved revolve direction.",
            },
            "label": {"type": "string", "description": "Visible result label."},
        },
        "required": [
            "profile_name",
            "operation",
            "axis",
            "extent",
            "midplane",
            "reversed",
            "label",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    profile_name: str,
    operation: str,
    axis: dict[str, Any],
    extent: dict[str, Any],
    midplane: bool,
    reversed: bool,
    label: str,
) -> dict[str, Any]:
    if operation in {"add_material", "remove_material"}:
        if axis.get("source") == "global":
            return _invalid(
                "Body material operations require a Body, profile, or object-edge axis."
            )
        native_operation, type_id = (
            ("revolution", "PartDesign::Revolution")
            if operation == "add_material"
            else ("groove", "PartDesign::Groove")
        )
        result = partdesign_rotational_feature.run(
            service,
            operation=native_operation,
            type_id=type_id,
            profile_name=profile_name,
            label=label,
            axis=axis,
            extent=extent,
            midplane=midplane,
            reversed=reversed,
        )
    elif operation in {"new_solid", "new_surface"}:
        if axis.get("source") != "global":
            return _invalid("New standalone geometry requires axis.source='global'.")
        if extent.get("type") != "angle":
            return _invalid("New standalone geometry requires an angle extent.")
        direction = dict(axis["direction"])
        angle = float(extent["angle_degrees"])
        if reversed:
            direction = {name: -float(direction[name]) for name in ("x", "y", "z")}
        result = part_revolve.run(
            service,
            profile_object_name=profile_name,
            axis_point=axis["point"],
            axis_direction=direction,
            angle_degrees=angle,
            solid=operation == "new_solid",
            label=label,
            symmetric=midplane,
        )
    else:
        return _invalid("Unknown revolve operation.")
    if isinstance(result, dict):
        result["operation"] = "revolve"
        result["material_operation"] = operation
    return result


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False}
