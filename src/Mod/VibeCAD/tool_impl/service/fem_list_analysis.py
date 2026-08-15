# SPDX-License-Identifier: LGPL-2.1-or-later

"""List every FEM analysis in the active document with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "fem.list_analysis",
    "description": (
        "List every FEM analysis in the active document with its exact "
        "internal name and its members grouped by category (solver, "
        "material, constraint, mesh, result). Use these identities when "
        "reading or editing the owning FEM VibeScript source, and check the "
        "member categories before solving."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "FemWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.fem_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list FEM analyses: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
