# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed in-place edits of exact FEM electromagnetic constraints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeConstraintCreate import (
    constraint_label,
    require_unambiguous_global_assignment,
)
from VibeCADNativeAnalyzeConstraintState import electromagnetic_constraint_state
from VibeCADNativeAnalyzeConstraintValues import (
    PreparedConstraintValues,
    apply_constraint_values,
    prepare_constraint_values,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeGeometryCreate import references_match
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedElectromagneticConstraintTarget,
    PreparedGeometryReference,
    electromagnetic_constraint_target_still_exact,
    geometry_references_still_exact,
    prepare_electromagnetic_constraint_target,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedConstraintUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedElectromagneticConstraintTarget
    analysis: Any
    analysis_state_sha256: str
    label: str
    references: tuple[PreparedGeometryReference, ...]
    values: PreparedConstraintValues
    values_changed: bool
    expected_definition: dict[str, Any]


def _owner_analysis(document: Any, constraint: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and constraint in tuple(
                obj.Group or ()
            ):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError(
            "The FEM electromagnetic constraint must belong to exactly one analysis."
        )
    return owners[0]


def _require_current_history(document: Any, constraint: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        constraint not in operations
        or str(getattr(constraint, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(constraint, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM electromagnetic constraint is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(constraint))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM electromagnetic constraint is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def _current_reference_payload(constraint: Any) -> list[dict[str, Any]]:
    grouped: dict[Any, list[str]] = {}
    for raw in tuple(getattr(constraint, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise NativeAnalyzeError(
                "The exact FEM electromagnetic constraint has malformed geometry references."
            )
        source, names = raw
        names = (names,) if isinstance(names, str) else tuple(names or ())
        grouped.setdefault(source, []).extend(str(name) for name in names)
    return [
        {
            "object_name": str(source.Name),
            "expected_state_sha256": mesh_object_state(source)["state_sha256"],
            "subelements": names,
        }
        for source, names in grouped.items()
    ]


def _effective_references(
    document: Any,
    document_uid: str,
    constraint: Any,
    raw: Any | None,
    values: PreparedConstraintValues,
) -> tuple[PreparedGeometryReference, ...]:
    payload = _current_reference_payload(constraint) if raw is None else raw
    references = prepare_geometry_references(
        document,
        document_uid,
        payload,
        allowed_kinds=values.allowed_reference_kinds,
        allow_mixed_kinds=values.allow_mixed_reference_kinds,
    )
    if not references and not values.allow_empty_references:
        expected = " or ".join(sorted(values.allowed_reference_kinds))
        raise NativeAnalyzeError(
            f"This {values.kind.replace('_', ' ')} requires at least one exact {expected} reference."
        )
    return references


def prepare_constraint_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
) -> PreparedConstraintUpdate:
    prepared_target = prepare_electromagnetic_constraint_target(
        document,
        document_uid,
        target,
        expected_kind=kind,
    )
    constraint = prepared_target.constraint
    _require_current_history(document, constraint)
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError(
            "changes must be one non-empty electromagnetic constraint edit object."
        )
    allowed = {"label", "references", "constraint"}
    if not set(changes) <= allowed:
        raise NativeAnalyzeError(
            "changes accepts only label, references, and constraint."
        )
    current_state = electromagnetic_constraint_state(constraint)
    values_changed = "constraint" in changes
    values = prepare_constraint_values(
        kind,
        changes["constraint"] if values_changed else current_state["definition"],
    )
    label = (
        constraint_label(changes["label"], field="changes.label")
        if "label" in changes
        else str(constraint.Label)
    )
    references = _effective_references(
        document,
        document_uid,
        constraint,
        changes.get("references") if "references" in changes else None,
        values,
    )
    analysis = _owner_analysis(document, constraint)
    require_unambiguous_global_assignment(
        analysis,
        kind,
        references,
        ignore=constraint,
    )
    expected_definition = values.normalized()
    references_changed = not references_match(constraint, references)
    if (
        label == str(constraint.Label)
        and not references_changed
        and expected_definition == current_state["definition"]
    ):
        raise NativeAnalyzeError(
            "The requested FEM electromagnetic constraint edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    return PreparedConstraintUpdate(
        creation_boundary(document),
        prepared_target,
        analysis,
        analysis_state(analysis)["state_sha256"],
        label,
        references,
        values,
        values_changed,
        expected_definition,
    )


def update_constraint(
    document: Any,
    prepared: PreparedConstraintUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedConstraintUpdate):
        raise TypeError("prepared must be a PreparedConstraintUpdate")
    require_boundary(document, prepared.boundary)
    if not electromagnetic_constraint_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM electromagnetic constraint changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if (
        analysis_state(prepared.analysis)["state_sha256"]
        != prepared.analysis_state_sha256
    ):
        raise NativeAnalyzeError(
            "The owning FEM analysis changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Electromagnetic reference geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    require_unambiguous_global_assignment(
        prepared.analysis,
        prepared.target.kind,
        prepared.references,
        ignore=prepared.target.constraint,
    )
    constraint = prepared.target.constraint
    prepared = assign_prepared_label(constraint, prepared)
    constraint.References = reference_value(prepared.references)
    if prepared.values_changed:
        apply_constraint_values(constraint, prepared.values)
    return NativeMutationDraft(
        value={"constraint": constraint, "prepared": prepared},
        recompute_targets=(constraint,),
        changed=(object_identity(constraint),),
    )


def verify_constraint_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    constraint = draft.value["constraint"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = electromagnetic_constraint_state(constraint)
    if (
        not is_live(document, constraint)
        or str(constraint.Label) != prepared.label
        or state["constraint_kind"] != prepared.target.kind
        or state["definition"] != prepared.expected_definition
        or not references_match(constraint, prepared.references)
        or constraint not in tuple(prepared.analysis.Group or ())
        or analysis_state(prepared.analysis)["state_sha256"]
        != prepared.analysis_state_sha256
        or not geometry_references_still_exact(prepared.references)
        or not bool(constraint.isValid())
    ):
        raise NativeAnalyzeError(
            "The FEM electromagnetic constraint edit failed its exact postcondition."
        )
    return {"updated_constraint": state}
