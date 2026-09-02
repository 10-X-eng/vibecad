# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of Elmer equations as owned solver History resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeEquationState import equation_kind, equation_state
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    finalize_new_operation_resource,
    require_boundary,
    stage_operation_resource_reconciliation,
    verify_new_operation_resource,
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


_FACTORIES = {
    "elasticity": "makeEquationElasticity",
    "deformation": "makeEquationDeformation",
    "electrostatic": "makeEquationElectrostatic",
    "electric_force": "makeEquationElectricforce",
    "magnetodynamic": "makeEquationMagnetodynamic",
    "magnetodynamic_2d": "makeEquationMagnetodynamic2D",
    "static_current": "makeEquationStaticCurrent",
    "flow": "makeEquationFlow",
    "flux": "makeEquationFlux",
    "heat": "makeEquationHeat",
}


@dataclass(frozen=True, slots=True)
class PreparedEquationCreate:
    boundary: AnalyzeCreationBoundary
    solver: PreparedSolverTarget
    children_before: tuple[Any, ...]
    kind: str
    label: str


def _label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError("label must contain 1 to 160 visible characters.")
    return label


def _require_solver_root(document: Any, solver: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        solver not in operations
        or str(getattr(solver, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(solver, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The Elmer solver is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )


def prepare_equation_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    solver: Any,
    label: Any,
) -> PreparedEquationCreate:
    if kind not in _FACTORIES:
        raise NativeAnalyzeError("The requested Elmer equation kind is unavailable.")
    prepared_solver = prepare_solver_target(document, document_uid, solver)
    if prepared_solver.kind != "elmer":
        raise NativeAnalyzeError(
            "Elmer equations require an exact Elmer solver target.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    _require_solver_root(document, prepared_solver.solver)
    return PreparedEquationCreate(
        creation_boundary(document),
        prepared_solver,
        tuple(getattr(prepared_solver.solver, "Group", ()) or ()),
        kind,
        _label(label),
    )


def create_equation(
    document: Any,
    prepared: PreparedEquationCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedEquationCreate):
        raise TypeError("prepared must be a PreparedEquationCreate")
    require_boundary(document, prepared.boundary)
    if (
        not solver_still_exact(
            prepared.solver.solver,
            prepared.solver.expected_state_sha256,
        )
        or tuple(getattr(prepared.solver.solver, "Group", ()) or ())
        != prepared.children_before
    ):
        raise NativeAnalyzeError(
            "The exact Elmer solver changed after equation preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    solver = prepared.solver.solver
    old_resources = stage_operation_resource_reconciliation(
        document,
        prepared.boundary,
        solver,
    )
    try:
        import ObjectsFem

        factory = getattr(ObjectsFem, _FACTORIES[prepared.kind])
        equation = factory(
            document,
            solver,
            document.getUniqueObjectName("Equation" + prepared.kind.title().replace("_", "")),
        )
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The {prepared.kind.replace('_', ' ')} equation factory failed: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    if equation_kind(equation) != prepared.kind:
        raise NativeAnalyzeError("The Elmer equation factory returned the wrong kind.")
    prepared = assign_prepared_label(equation, prepared)
    if tuple(getattr(solver, "Group", ()) or ()) != (*prepared.children_before, equation):
        raise NativeAnalyzeError("The Elmer equation was not appended to the exact solver.")
    finalize_new_operation_resource(
        document,
        prepared.boundary,
        solver,
        old_resources,
        equation,
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "equation": equation,
            "old_resources": old_resources,
        },
        recompute_targets=(equation, solver),
        created=(object_identity(equation),),
        changed=(object_identity(solver),),
    )


def verify_equation_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    equation = draft.value["equation"]
    old_resources = draft.value["old_resources"]
    solver = prepared.solver.solver
    verify_new_operation_resource(
        document,
        prepared.boundary,
        solver,
        old_resources,
        equation,
    )
    state = equation_state(equation)
    current_solver = solver_state(solver)
    checks = {
        "live equation": is_live(document, equation),
        "equation kind": state["equation_kind"] == prepared.kind,
        "equation label": str(equation.Label) == prepared.label,
        "solver membership": tuple(getattr(solver, "Group", ()) or ())
        == (*prepared.children_before, equation),
        "priority": state["priority"] == 255 - len(prepared.children_before),
        "history resource": state.get("timeline_role") == "resource"
        and state.get("timeline_owner") == str(solver.Name),
        "native validity": bool(equation.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The Elmer equation failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {"created_equation": state, "solver": current_solver}
