# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared human-facing setup operations for exact FEM studies."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from VibeCADNativeAnalyzeOwnership import (
    is_study as _is_study,
    studies_in_document,
)


def _is_analysis(value: Any, document: Any) -> bool:
    return _is_study(value, document)


def analyses_in_document(document: Any) -> tuple[Any, ...]:
    """Return every live FEM analysis in document order."""

    return studies_in_document(document)


def analysis_for_selection(document: Any, selection: Iterable[Any]) -> Any | None:
    """Resolve one exact selected analysis or unambiguous selected member owner."""

    selected = tuple(
        candidate
        for candidate in tuple(selection or ())
        if getattr(candidate, "Document", None) is document
    )
    direct = tuple(
        candidate for candidate in selected if _is_analysis(candidate, document)
    )
    if len(direct) == 1:
        return direct[0]
    if direct:
        return None
    ancestors = {id(value): value for value in selected}
    pending = list(selected)
    while pending and len(ancestors) <= 4096:
        current = pending.pop()
        parents = tuple(getattr(current, "InList", ()) or ())
        timeline_owner = getattr(current, "VibeCADTimelineOwner", None)
        if timeline_owner is not None:
            parents += (timeline_owner,)
        for parent in parents:
            if getattr(parent, "Document", None) is document and id(parent) not in ancestors:
                ancestors[id(parent)] = parent
                pending.append(parent)
    owners = tuple(
        analysis
        for analysis in analyses_in_document(document)
        if any(
            candidate in tuple(getattr(analysis, "Group", ()) or ())
            for candidate in ancestors.values()
        )
    )
    return owners[0] if len(owners) == 1 else None


def preferred_analysis(
    document: Any,
    selection: Iterable[Any],
    *,
    previous_name: str,
) -> Any | None:
    """Resolve UI focus from selection, then the dock's explicit prior choice."""

    selected = analysis_for_selection(document, selection)
    if selected is not None:
        return selected
    previous = str(previous_name or "")
    if not previous:
        return None
    candidate = document.getObject(previous)
    return candidate if _is_analysis(candidate, document) else None


def readiness_rows(inventory: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Present exact study inventory as six compact workflow rows."""

    conditions = sum(
        int(inventory.get(name, 0) or 0)
        for name in (
            "support_count",
            "connection_count",
            "load_count",
            "thermal_condition_count",
            "fluid_constraint_count",
            "electromagnetic_constraint_count",
        )
    )
    mesh_definitions = int(inventory.get("mesh_definition_count", 0) or 0)
    generated_meshes = int(inventory.get("generated_mesh_count", 0) or 0)
    mesh = (
        "generated"
        if generated_meshes
        else "defined" if mesh_definitions else "not set"
    )
    solver_kinds = tuple(
        str(value) for value in inventory.get("solver_kinds", ()) or ()
    )
    solver = ", ".join(value.title() for value in solver_kinds) or "not set"
    geometry_count = int(inventory.get("geometry_source_count", 0) or 0)
    geometry = f"{geometry_count} source" + ("" if geometry_count == 1 else "s")
    return (
        ("Geometry", geometry),
        ("Materials", str(int(inventory.get("material_count", 0) or 0))),
        ("Conditions", str(conditions)),
        ("Mesh", mesh),
        ("Solver", solver),
        ("Results", str(int(inventory.get("result_count", 0) or 0))),
    )


def apply_study(
    document: Any,
    *,
    analysis: Any | None,
    label: str,
    physics: Iterable[str],
    regime: str,
) -> tuple[Any, dict[str, Any]]:
    """Create or update one study through the same exact domain operations as AI."""

    from VibeCADNativeAnalyzeAnalysis import (
        create_analysis,
        prepare_analysis_create,
        verify_analysis_create,
    )
    from VibeCADNativeAnalyzeState import analysis_state
    from VibeCADNativeAnalyzeStudyEdit import (
        prepare_study_update,
        update_study_intent,
        verify_study_update,
    )
    from VibeCADNativeMutation import run_human_mutation

    study = {"physics": list(physics), "regime": str(regime)}
    if analysis is None:
        prepared = prepare_analysis_create(
            document,
            label=label,
            default_solver_policy="none",
            study=study,
        )
        result = run_human_mutation(
            document=document,
            transaction_name="Create FEM Study",
            mutate=lambda current: create_analysis(current, prepared),
            verify=verify_analysis_create,
        )
        object_name = str(result["created_analysis"]["object_name"])
        created = document.getObject(object_name)
        if not _is_analysis(created, document):
            raise RuntimeError("The created FEM study is no longer available.")
        return created, result

    if not _is_analysis(analysis, document):
        raise ValueError("analysis must be a live FEM analysis in the target document")
    state = analysis_state(analysis)
    prepared = prepare_study_update(
        document,
        str(document.Uid),
        target={
            "object_name": str(analysis.Name),
            "expected_state_sha256": str(state["state_sha256"]),
            "expected_member_count": int(state["member_count"]),
        },
        study=study,
    )
    result = run_human_mutation(
        document=document,
        transaction_name="Edit FEM Study",
        mutate=lambda current: update_study_intent(current, prepared),
        verify=verify_study_update,
    )
    return analysis, result
