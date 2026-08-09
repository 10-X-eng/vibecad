# SPDX-License-Identifier: LGPL-2.1-or-later

"""List every TechDraw page in the active document with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "techdraw.list_pages",
    "description": (
        "List every TechDraw drawing page in the active document with its "
        "exact internal name, template, and the views it contains (with "
        "their source objects, positions, and scale). Use these page and view "
        "identities when reading or editing the owning Drawing VibeScript source."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "TechDrawWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.techdraw_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list TechDraw pages: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
