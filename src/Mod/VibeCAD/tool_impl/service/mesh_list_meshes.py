# SPDX-License-Identifier: LGPL-2.1-or-later

"""List every mesh object in the active document with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "mesh.list_meshes",
    "description": (
        "List every mesh object in the active document with its exact "
        "internal name, label, and triangle counts. Use the returned "
        "internal names to target mesh.analyze or mesh.repair."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "MeshWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.mesh_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list mesh objects: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
