# SPDX-License-Identifier: LGPL-2.1-or-later

"""List every FEM analysis in the active document with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime

MAX_ANALYSIS_PAGE = 80

TOOL_SPEC = {
    "name": "fem.list_analysis",
    "description": (
        "List every FEM analysis in the active document with its exact "
        "internal name and its members grouped by category (solver, "
        "material, constraint, mesh, result). Use these identities when "
        "reading or editing the owning FEM VibeScript source, and check the "
        "member categories before solving. A missing analysis_name returns a "
        "bounded catalog only; it never chooses a default study."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "FemWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "analysis_name": {
                "type": "string",
                "description": "Exact internal Fem::FemAnalysis.Name to inspect.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Zero-based page offset in the analysis catalog.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_ANALYSIS_PAGE,
                "description": "Maximum analyses to return in this page.",
            },
        },
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    analysis_name: str | None = None,
    offset: int = 0,
    limit: int = MAX_ANALYSIS_PAGE,
) -> dict[str, Any]:
    try:
        summary = domain_runtime.fem_summary(
            service,
            analysis_name=analysis_name,
            offset=offset,
            limit=limit,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list FEM analyses: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
