# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact FEM solver creation using the human ribbon factories and preferences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeSolverState import solver_kind, solver_state
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    analysis_target_still_exact,
    prepare_analysis_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_FACTORY_NAMES = {
    "calculix": "CalculiX",
    "elmer": "Elmer",
    "openfoam": "OpenFOAM",
    "mystran": "Mystran",
    "z88": "Z88",
}


@dataclass(frozen=True, slots=True)
class PreparedSolverCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    kind: str
    label: str


def _label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError("label must contain 1 to 160 visible characters.")
    return label


def prepare_solver_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
) -> PreparedSolverCreate:
    if kind not in _FACTORY_NAMES:
        raise NativeAnalyzeError("The requested FEM solver kind is unavailable.")
    prepared_analysis = prepare_analysis_target(document, document_uid, analysis)
    members = tuple(getattr(prepared_analysis.analysis, "Group", ()) or ())
    if len(members) != prepared_analysis.expected_member_count:
        raise NativeAnalyzeError("The exact FEM analysis membership changed during preflight.")
    return PreparedSolverCreate(
        creation_boundary(document),
        prepared_analysis,
        members,
        kind,
        _label(label),
    )


def create_solver(
    document: Any,
    prepared: PreparedSolverCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSolverCreate):
        raise TypeError("prepared must be a PreparedSolverCreate")
    require_boundary(document, prepared.boundary)
    if (
        not analysis_target_still_exact(prepared.analysis)
        or tuple(getattr(prepared.analysis.analysis, "Group", ()) or ())
        != prepared.members_before
    ):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after solver preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )

    try:
        from femcommands.commands import createDefaultSolverFeature

        solver = createDefaultSolverFeature(document, _FACTORY_NAMES[prepared.kind])
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The {_FACTORY_NAMES[prepared.kind]} solver factory failed: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    if solver_kind(solver) != prepared.kind:
        raise NativeAnalyzeError("The FEM solver factory returned the wrong solver kind.")

    solver.Label = prepared.label
    analysis = prepared.analysis.analysis
    analysis.addObject(solver)
    if tuple(getattr(analysis, "Group", ()) or ()) != (*prepared.members_before, solver):
        raise NativeAnalyzeError("The FEM solver was not appended to the exact analysis.")
    publish_operation(document, prepared.boundary, solver)
    return NativeMutationDraft(
        value={"prepared": prepared, "solver": solver},
        recompute_targets=(solver, analysis),
        created=(object_identity(solver),),
        changed=(object_identity(analysis),),
    )


def verify_solver_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    solver = draft.value["solver"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, solver)
    state = solver_state(solver)
    current_analysis = analysis_state(analysis)
    checks = {
        "live solver": is_live(document, solver),
        "solver kind": state["solver_kind"] == prepared.kind,
        "solver label": str(solver.Label) == prepared.label,
        "analysis membership": tuple(getattr(analysis, "Group", ()) or ())
        == (*prepared.members_before, solver),
        "analysis count": current_analysis["member_count"]
        == prepared.analysis.expected_member_count + 1,
        "history root": state.get("timeline_role") == "operation"
        and "timeline_owner" not in state,
        "native validity": bool(solver.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM solver failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {"created_solver": state, "analysis": current_analysis}
