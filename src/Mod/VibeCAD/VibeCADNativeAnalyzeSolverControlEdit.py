# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact backend-specific edits for durable FEM solver settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeSolverControlValues import (
    PreparedSolverChanges,
    prepare_solver_changes,
)
from VibeCADNativeAnalyzeSolverState import (
    PreparedSolverTarget,
    prepare_solver_target,
    solver_state,
    solver_still_exact,
)
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSolverControlUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedSolverTarget
    state_before: Mapping[str, Any]
    settings_after: Mapping[str, Any]
    changes: PreparedSolverChanges


def _require_current_history(document: Any, solver: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        solver not in operations
        or str(getattr(solver, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(solver, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM solver is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )


def prepare_solver_control_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
) -> PreparedSolverControlUpdate:
    prepared_target = prepare_solver_target(document, document_uid, target)
    if prepared_target.kind != kind:
        raise NativeAnalyzeError(
            f"The exact target is {prepared_target.kind}; this operation requires {kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    solver = prepared_target.solver
    _require_current_history(document, solver)
    state = solver_state(solver)
    prepared_changes = prepare_solver_changes(kind, changes, state["settings"])
    settings_after = dict(state["settings"])
    settings_after.update(prepared_changes.native)
    return PreparedSolverControlUpdate(
        creation_boundary(document),
        prepared_target,
        state,
        settings_after,
        prepared_changes,
    )


def update_solver_control(
    document: Any,
    prepared: PreparedSolverControlUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSolverControlUpdate):
        raise TypeError("prepared must be a PreparedSolverControlUpdate")
    require_boundary(document, prepared.boundary)
    solver = prepared.target.solver
    if not solver_still_exact(solver, prepared.target.expected_state_sha256):
        raise NativeAnalyzeError(
            "The exact FEM solver changed after settings preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    for name, value in prepared.changes.native.items():
        try:
            setattr(solver, name, value)
        except Exception as exc:
            raise NativeAnalyzeError(
                f"The FEM solver rejected setting {name!r}: {exc}",
                error_code="NATIVE_ANALYZE_PROPERTY_REJECTED",
            ) from exc
    return NativeMutationDraft(
        value={"solver": solver, "prepared": prepared},
        recompute_targets=(solver,),
        changed=(object_identity(solver),),
    )


def verify_solver_control_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    solver = draft.value["solver"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = solver_state(solver)
    before = prepared.state_before
    stable_fields = (
        "object_name",
        "object_id",
        "label",
        "type_id",
        "solver_kind",
        "implementation",
        "analysis",
        "suppressed",
        "child_count",
        "timeline_role",
        "timeline_owner",
    )
    checks = {
        "live solver": is_live(document, solver),
        "settings": state["settings"] == prepared.settings_after,
        "state revision": state["state_sha256"] != before["state_sha256"],
        "stable identity and ownership": all(
            state.get(field) == before.get(field) for field in stable_fields
        ),
        "native validity": bool(solver.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM solver settings edit failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "updated_solver": state,
        "changed_settings": dict(prepared.changes.normalized),
    }
