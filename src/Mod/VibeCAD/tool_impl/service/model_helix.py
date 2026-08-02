# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical add/remove-material helical sweep tool."""

from copy import deepcopy
from typing import Any

from . import partdesign_additive_helix, partdesign_helix_feature


TOOL_SPEC = deepcopy(partdesign_additive_helix.TOOL_SPEC)
TOOL_SPEC["name"] = "model.helix"
TOOL_SPEC["description"] = (
    "Sweep a closed profile along a helix and explicitly add or remove material from its Body."
)
TOOL_SPEC["parameters"]["properties"]["operation"] = {
    "type": "string",
    "enum": ["add_material", "remove_material"],
    "description": "Whether the helical sweep adds or removes material.",
}
TOOL_SPEC["parameters"]["required"].insert(0, "operation")


def run(service: Any, operation: str, **arguments: Any) -> dict[str, Any]:
    choices = {
        "add_material": ("additive_helix", "PartDesign::AdditiveHelix"),
        "remove_material": ("subtractive_helix", "PartDesign::SubtractiveHelix"),
    }
    if operation not in choices:
        return _invalid("operation must be add_material or remove_material.")
    native_operation, type_id = choices[operation]
    result = partdesign_helix_feature.run(
        service,
        operation=native_operation,
        type_id=type_id,
        **arguments,
    )
    if isinstance(result, dict):
        result["operation"] = "helix"
        result["material_operation"] = operation
    return result


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False}
