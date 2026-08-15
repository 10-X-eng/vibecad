# SPDX-License-Identifier: LGPL-2.1-or-later

"""List every CAM job in the active document with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "cam.list_jobs",
    "description": (
        "List every CAM job in the active document with its exact internal "
        "name and its model, stock, tool, and operation members. Use these "
        "identities when reading or editing the owning CAM VibeScript source; "
        "a job with no tool controller cannot machine anything."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "CAMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.cam_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list CAM jobs: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
