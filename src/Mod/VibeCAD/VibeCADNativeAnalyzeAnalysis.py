# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of FEM analysis containers and optional default solvers."""

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
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeStudy import (
    configure_study_intent,
    normalize_study_intent,
    study_intent_state,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSnapshot import concise_object
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedAnalysisCreate:
    boundary: AnalyzeCreationBoundary
    label: str
    solver_name: str | None
    study: tuple[tuple[str, ...], str] | None


def _label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError("label must contain 1 to 160 visible characters.")
    return label


def prepare_analysis_create(
    document: Any,
    *,
    label: Any,
    default_solver_policy: Any,
    study: Any | None = None,
) -> PreparedAnalysisCreate:
    policy = str(default_solver_policy or "")
    if policy not in {"user_preference", "none"}:
        raise NativeAnalyzeError(
            "default_solver_policy must be user_preference or none."
        )
    solver_name = None
    if policy == "user_preference":
        from femsolver.settings import get_default_solver

        solver_name = get_default_solver()
        if solver_name is not None:
            solver_name = str(solver_name)
    return PreparedAnalysisCreate(
        creation_boundary(document),
        _label(label),
        solver_name,
        normalize_study_intent(study) if study is not None else None,
    )


def create_analysis(
    document: Any,
    prepared: PreparedAnalysisCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedAnalysisCreate):
        raise TypeError("prepared must be a PreparedAnalysisCreate")
    require_boundary(document, prepared.boundary)
    import ObjectsFem

    analysis = ObjectsFem.makeAnalysis(
        document,
        document.getUniqueObjectName("Analysis"),
    )
    if analysis is None or not analysis.isDerivedFrom("Fem::FemAnalysis"):
        raise NativeAnalyzeError("The FEM analysis factory returned the wrong object type.")
    analysis.Label = prepared.label
    if prepared.study is not None:
        configure_study_intent(
            analysis,
            {"physics": list(prepared.study[0]), "regime": prepared.study[1]},
        )
    solver = None
    if prepared.solver_name is not None:
        from femcommands.commands import createDefaultSolverFeature

        solver = createDefaultSolverFeature(document, prepared.solver_name)
        analysis.addObject(solver)
        if solver not in tuple(analysis.Group or ()):
            raise NativeAnalyzeError("The default solver was not added to its analysis.")
    resources = (solver,) if solver is not None else ()
    publish_operation(document, prepared.boundary, analysis, resources)
    created = (object_identity(analysis),)
    recompute = (analysis,)
    if solver is not None:
        created += (object_identity(solver),)
        recompute += (solver,)
    return NativeMutationDraft(
        value={"analysis": analysis, "solver": solver, "prepared": prepared},
        recompute_targets=recompute,
        created=created,
    )


def verify_analysis_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    analysis = draft.value["analysis"]
    solver = draft.value["solver"]
    prepared = draft.value["prepared"]
    resources = (solver,) if solver is not None else ()
    verify_operation_block(document, prepared.boundary, analysis, resources)
    members = tuple(getattr(analysis, "Group", ()) or ())
    if (
        not is_live(document, analysis)
        or not analysis.isDerivedFrom("Fem::FemAnalysis")
        or str(analysis.Label) != prepared.label
        or members != resources
        or not bool(analysis.isValid())
    ):
        raise NativeAnalyzeError("The new FEM analysis failed its exact postcondition.")
    intent = study_intent_state(analysis)
    if prepared.study is None:
        if intent.get("declared") is not False:
            raise NativeAnalyzeError("The new FEM analysis gained unexpected study intent.")
    elif (
        tuple(intent.get("physics") or ()) != prepared.study[0]
        or intent.get("regime") != prepared.study[1]
    ):
        raise NativeAnalyzeError("The new FEM analysis lost its study intent.")
    if solver is not None and (
        not is_live(document, solver)
        or not solver.isDerivedFrom("Fem::FemSolverObjectPython")
        or not bool(solver.isValid())
    ):
        raise NativeAnalyzeError("The default FEM solver failed its exact postcondition.")
    current_analysis = analysis_state(analysis)
    result = {
        "created_analysis": current_analysis,
        "analysis_target": {
            "object_name": current_analysis["object_name"],
            "expected_state_sha256": current_analysis["state_sha256"],
            "expected_member_count": current_analysis["member_count"],
        },
    }
    if solver is not None:
        result["created_solver"] = concise_object(solver)
    else:
        result["created_solver"] = None
    return result
