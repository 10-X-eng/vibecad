# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state and targets for FEM solver definitions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeAnalyzePropertyState import bounded_fem_properties
from VibeCADNativeSnapshot import concise_object
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_KINDS = {
    "Fem::SolverCalculiX": "calculix",
    "Fem::SolverCcxTools": "calculix",
    "Fem::SolverElmer": "elmer",
    "Fem::SolverMystran": "mystran",
    "Fem::SolverZ88": "z88",
}
_SETTING_GROUPS = frozenset(
    {"Solver", "AnalysisType", "TimeIncrement", "Timestepping", "ElementModel", "Fem"}
)


@dataclass(frozen=True, slots=True)
class PreparedSolverTarget:
    solver: Any
    kind: str
    expected_state_sha256: str


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def solver_kind(obj: Any) -> str:
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    kind = _KINDS.get(proxy_type)
    if kind is None:
        raise NativeAnalyzeError(
            "The exact target is not a supported FEM solver.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    return kind


def _settings(obj: Any) -> dict[str, Any]:
    return bounded_fem_properties(obj, included_groups=_SETTING_GROUPS)


def _owner_analysis(document: Any, solver: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and solver in tuple(obj.Group or ()):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError("The FEM solver must belong to exactly one analysis.")
    return owners[0]


def solver_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM solver is no longer live.")
    kind = solver_kind(obj)
    analysis = _owner_analysis(document, obj)
    settings = _settings(obj)
    proxy_type = str(getattr(obj.Proxy, "Type", "") or "")
    role = str(getattr(obj, "VibeCADTimelineRole", "") or "")
    owner = getattr(obj, "VibeCADTimelineOwner", None)
    owner_identity = (
        [str(owner.Name), int(owner.ID)] if is_live(document, owner) else None
    )
    children = tuple(getattr(obj, "Group", ()) or ()) if kind == "elmer" else ()
    exact_children = [
        [str(value.Name), int(value.ID)] for value in children if is_live(document, value)
    ]
    result_roots = tuple(getattr(obj, "Results", ()) or ())
    exact_results = [
        [str(value.Name), int(value.ID), str(value.TypeId)]
        for value in result_roots
        if is_live(document, value)
    ]
    result = {
        **concise_object(obj),
        "solver_kind": kind,
        "implementation": (
            "pipeline"
            if proxy_type == "Fem::SolverCalculiX"
            else "ccx_tools"
            if proxy_type == "Fem::SolverCcxTools"
            else kind
        ),
        "analysis": str(analysis.Name),
        "suppressed": bool(getattr(obj, "Suppressed", False)),
        "settings": settings,
        "child_count": len(children),
        "result_count": len(exact_results),
        "results": [concise_object(value) for value in result_roots if is_live(document, value)],
    }
    if role:
        result["timeline_role"] = role
    if owner_identity is not None:
        result["timeline_owner"] = owner_identity[0]
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "proxy_type": proxy_type,
            "analysis": [str(analysis.Name), int(analysis.ID)],
            "suppressed": bool(getattr(obj, "Suppressed", False)),
            "settings": settings,
            "children": exact_children,
            "results": exact_results,
            "timeline_role": role,
            "timeline_owner": owner_identity,
        }
    )
    return result


def solver_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return solver_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False


def prepare_solver_target(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedSolverTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "solver target must contain only object_name and expected_state_sha256."
        )
    solver = resolve_object(
        document,
        NativeObjectRef(document_uid, str(value["object_name"])),
    )
    state = solver_state(solver)
    expected = str(value["expected_state_sha256"] or "")
    if state["state_sha256"] != expected:
        raise NativeAnalyzeError(
            "The exact FEM solver changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "solver": {"object_name": str(solver.Name)},
                "current_state_sha256": state["state_sha256"],
            },
        )
    return PreparedSolverTarget(solver, state["solver_kind"], expected)
