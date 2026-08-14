# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact result-graph state and atomic purge for Native Analyze."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeResultState import result_kind
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    prepare_analysis_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSnapshot import concise_object
from VibeCADNativeTargets import object_identity


MAX_PURGE_SUMMARIES = 32


@dataclass(frozen=True, slots=True)
class PreparedResultPurge:
    analysis_target: PreparedAnalysisTarget
    expected_graph_sha256: str
    expected_object_count: int
    plan: Any


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _proxy_type(obj: Any) -> str:
    return str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")


def _result_member(obj: Any) -> bool:
    try:
        result_kind(obj)
        return True
    except NativeAnalyzeError:
        pass
    return _proxy_type(obj) == "Fem::MeshResult" or str(
        getattr(obj, "TypeId", "") or ""
    ) == "App::TextDocument"


def _timeline_role(obj: Any) -> str:
    return str(getattr(obj, "VibeCADTimelineRole", "") or "")


def _timeline_owner(obj: Any) -> Any | None:
    return getattr(obj, "VibeCADTimelineOwner", None)


def _build_result_graph_plan(analysis: Any) -> Any:
    try:
        from femresult.resulttools import plan_result_graph_purge

        return plan_result_graph_purge(analysis)
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(str(exc)) from exc


def _timeline_root(obj: Any, document: Any) -> Any | None:
    current = obj
    visited: set[int] = set()
    while is_live(document, current) and _timeline_role(current) == "resource":
        identity = int(current.ID)
        if identity in visited:
            return None
        visited.add(identity)
        current = _timeline_owner(current)
    return current if is_live(document, current) else None


def result_purge_state(analysis: Any) -> dict[str, Any]:
    plan = _build_result_graph_plan(analysis)
    solver_root_set = {
        root for _solver, roots in plan.solver_roots for root in roots
    }
    summaries = []
    exact = []
    for target in plan.targets:
        category = (
            "solver_result"
            if target in solver_root_set
            else "post_processing"
            if _result_member(target)
            else "result_resource"
        )
        exact.append(
            {
                "object_name": str(target.Name),
                "object_id": int(target.ID),
                "type_id": str(target.TypeId),
                "timeline_role": _timeline_role(target),
                "timeline_owner": (
                    [str(_timeline_owner(target).Name), int(_timeline_owner(target).ID)]
                    if is_live(target.Document, _timeline_owner(target))
                    else None
                ),
                "category": category,
            }
        )
        if len(summaries) < MAX_PURGE_SUMMARIES:
            summaries.append({**concise_object(target), "category": category})
    solver_links = [
        {
            "solver": [str(solver.Name), int(solver.ID)],
            "roots": [[str(root.Name), int(root.ID)] for root in roots],
        }
        for solver, roots in plan.solver_roots
    ]
    graph_sha = _digest(
        {
            "analysis": [str(analysis.Name), int(analysis.ID)],
            "objects": exact,
            "solver_links": solver_links,
            "blockers": list(plan.blockers),
        }
    )
    return {
        "object_count": len(plan.targets),
        "objects": summaries,
        "objects_truncated": len(plan.targets) > len(summaries),
        "solver_result_root_count": sum(
            len(roots) for _solver, roots in plan.solver_roots
        ),
        "ordinary_operation_count": len(plan.ordinary_operations),
        "purge_ready": bool(plan.targets) and not plan.blockers,
        "blockers": list(plan.blockers)[:8],
        "graph_sha256": graph_sha,
    }


def prepare_result_purge(
    document: Any,
    document_uid: str,
    *,
    analysis: Any,
    expected_result_graph_sha256: Any,
    expected_result_object_count: Any,
) -> PreparedResultPurge:
    analysis_target = prepare_analysis_target(document, document_uid, analysis)
    expected_sha = str(expected_result_graph_sha256 or "")
    expected_count = expected_result_object_count
    if type(expected_count) is not int or expected_count < 1:
        raise NativeAnalyzeError(
            "expected_result_object_count must be a positive integer."
        )
    state = result_purge_state(analysis_target.analysis)
    if (
        state["graph_sha256"] != expected_sha
        or state["object_count"] != expected_count
    ):
        raise NativeAnalyzeError(
            "The exact FEM result graph changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "analysis": {"object_name": str(analysis_target.analysis.Name)},
                "current_result_graph_sha256": state["graph_sha256"],
                "current_result_object_count": state["object_count"],
            },
        )
    if not state["purge_ready"]:
        message = (
            state["blockers"][0]
            if state["blockers"]
            else "The exact analysis has no results to purge."
        )
        raise NativeAnalyzeError(message)
    return PreparedResultPurge(
        analysis_target,
        expected_sha,
        expected_count,
        _build_result_graph_plan(analysis_target.analysis),
    )


def create_result_purge(
    document: Any,
    prepared: PreparedResultPurge,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedResultPurge):
        raise TypeError("prepared must be PreparedResultPurge")
    analysis = prepared.analysis_target.analysis
    current = result_purge_state(analysis)
    if (
        current["graph_sha256"] != prepared.expected_graph_sha256
        or current["object_count"] != prepared.expected_object_count
    ):
        raise NativeAnalyzeError(
            "The exact FEM result graph changed after purge preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    plan = _build_result_graph_plan(analysis)
    if plan.blockers:
        raise NativeAnalyzeError(plan.blockers[0])
    deleted_identities = tuple(object_identity(value) for value in plan.targets)
    changed = [object_identity(analysis)]
    for solver, _roots in plan.solver_roots:
        changed.append(object_identity(solver))

    try:
        from femresult.resulttools import apply_result_graph_purge

        apply_result_graph_purge(plan)
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(str(exc)) from exc
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "deleted_names": tuple(value.object_name for value in deleted_identities),
            "solvers": tuple(solver for solver, _roots in plan.solver_roots),
        },
        recompute_targets=(analysis, *tuple(solver for solver, _ in plan.solver_roots)),
        changed=tuple(changed),
        deleted=deleted_identities,
    )


def verify_result_purge(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    analysis = prepared.analysis_target.analysis
    survivors = [
        name for name in draft.value["deleted_names"] if document.getObject(name) is not None
    ]
    state = result_purge_state(analysis)
    if survivors or state["object_count"] != 0:
        details = ", ".join(survivors[:8]) or "result graph remains nonempty"
        raise NativeAnalyzeError(f"Result purge failed its postcondition: {details}.")
    solvers = [solver_state(solver) for solver in draft.value["solvers"]]
    return {
        "purged": {
            "object_count": prepared.expected_object_count,
            "result_graph_sha256": prepared.expected_graph_sha256,
        },
        "analysis": analysis_state(analysis),
        "solvers": solvers,
        "result_graph": state,
    }
