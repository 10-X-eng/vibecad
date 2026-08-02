# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical general BREP boolean tool."""

from copy import deepcopy
from typing import Any

from . import part_boolean


TOOL_SPEC = deepcopy(part_boolean.TOOL_SPEC)
TOOL_SPEC["name"] = "model.boolean"
TOOL_SPEC["description"] = (
    "Create one parametric union, subtraction, or intersection from exact modeled objects. "
    "This is the single boolean entry point for the consolidated modeling workbench."
)


def run(service: Any, **arguments: Any) -> dict[str, Any]:
    result = part_boolean.run(service, **arguments)
    if isinstance(result, dict):
        result["operation"] = "boolean"
        result["boolean_operation"] = arguments.get("operation")
    return result
