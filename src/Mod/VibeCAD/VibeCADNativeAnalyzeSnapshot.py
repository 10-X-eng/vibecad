# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Analyze ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_ANALYSES = 16


def _category(obj: Any) -> str:
    type_id = str(getattr(obj, "TypeId", "") or "")
    for fragment, category in (
        ("Solver", "solver"),
        ("FemMesh", "mesh"),
        ("Mesh", "mesh"),
        ("Material", "material"),
        ("Constraint", "constraint"),
        ("Equation", "equation"),
        ("Result", "result"),
        ("Post", "post"),
    ):
        if fragment in type_id:
            return category
    return "member"


def _analysis_summary(analysis: Any) -> dict[str, Any]:
    result = concise_object(analysis)
    members = list(getattr(analysis, "Group", []) or [])
    counts: dict[str, int] = {}
    summarized = []
    for member in members:
        category = _category(member)
        counts[category] = counts.get(category, 0) + 1
        if len(summarized) < 48:
            summarized.append({**concise_object(member), "category": category})
    result["member_counts"] = counts
    result["members"] = summarized
    return result


def build_analyze_snapshot(document: Any) -> dict[str, Any]:
    analyses = objects_of_type(document, "Fem::FemAnalysis")
    return {
        "kind": "analyze",
        "analysis_count": len(analyses),
        "analyses": [
            _analysis_summary(value) for value in analyses[:MAX_ANALYSES]
        ],
    }
