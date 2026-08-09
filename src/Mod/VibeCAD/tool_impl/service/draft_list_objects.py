# SPDX-License-Identifier: LGPL-2.1-or-later

"""List every Draft object in the active document with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "draft.list_objects",
    "description": (
        "List every Draft object in the active document with its exact internal "
        "name, label, type, and geometry summary. Use these identities when "
        "reading or editing the owning Draft VibeScript source."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "DraftWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.draft_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list Draft objects: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
