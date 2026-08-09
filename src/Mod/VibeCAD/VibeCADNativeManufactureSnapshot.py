# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Manufacture ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeSnapshot import concise_object


MAX_JOBS = 12


def _is_job(obj: Any) -> bool:
    proxy = getattr(obj, "Proxy", None)
    return bool(
        str(getattr(obj, "TypeId", "") or "") == "Path::FeaturePython"
        and proxy is not None
        and proxy.__class__.__name__ == "ObjectJob"
        and proxy.__class__.__module__ == "Path.Main.Job"
    )


def _group(job: Any, property_name: str) -> list[Any]:
    group = getattr(job, property_name, None)
    return list(getattr(group, "Group", []) or []) if group is not None else []


def _job_summary(job: Any) -> dict[str, Any]:
    result = concise_object(job)
    operations = _group(job, "Operations")
    tools = _group(job, "Tools")
    models = _group(job, "Model")
    result["counts"] = {
        "models": len(models),
        "tools": len(tools),
        "operations": len(operations),
        "active_operations": sum(
            1 for value in operations if bool(getattr(value, "Active", True))
        ),
    }
    result["operations"] = [concise_object(value) for value in operations[:40]]
    postprocessor = str(getattr(job, "PostProcessor", "") or "").strip()
    if postprocessor:
        result["postprocessor"] = postprocessor[:160]
    return result


def build_manufacture_snapshot(document: Any) -> dict[str, Any]:
    jobs = [obj for obj in list(getattr(document, "Objects", []) or []) if _is_job(obj)]
    return {
        "kind": "manufacture",
        "job_count": len(jobs),
        "jobs": [_job_summary(value) for value in jobs[:MAX_JOBS]],
    }
