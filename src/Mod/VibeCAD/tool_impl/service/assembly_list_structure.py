# SPDX-License-Identifier: LGPL-2.1-or-later

"""List every assembly with its components, joints, and grounding state."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "assembly.list_structure",
    "description": (
        "List assemblies with exact names, components, joints, grounding, and groups."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.assembly_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list assembly structure: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
