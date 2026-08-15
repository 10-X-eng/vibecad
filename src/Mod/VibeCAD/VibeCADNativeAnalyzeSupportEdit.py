# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed in-place edits of exact FEM mechanical support conditions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import references_match
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeSupportCreate import support_label
from VibeCADNativeAnalyzeSupportState import support_condition_state
from VibeCADNativeAnalyzeSupportValues import (
    PreparedSupportValues,
    apply_support_values,
    prepare_support_values,
)
from VibeCADNativeAnalyzeTargets import (
    PreparedGeometryReference,
    PreparedSupportConditionTarget,
    geometry_references_still_exact,
    prepare_geometry_references,
    prepare_support_condition_target,
    reference_value,
    support_condition_target_still_exact,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSupportUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedSupportConditionTarget
    analysis: Any
    analysis_state_sha256: str
    label: str
    references: tuple[PreparedGeometryReference, ...]
    values: PreparedSupportValues
    values_changed: bool


def _owner_analysis(document: Any, condition: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and condition in tuple(
                obj.Group or ()
            ):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError(
            "The FEM support condition must belong to exactly one analysis."
        )
    return owners[0]


def _require_current_history(document: Any, condition: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        condition not in operations
        or str(getattr(condition, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(condition, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM support condition is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(condition))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM support condition is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def _current_reference_payload(condition: Any) -> list[dict[str, Any]]:
    grouped: dict[Any, list[str]] = {}
    for raw in tuple(getattr(condition, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise NativeAnalyzeError(
                "The exact FEM support condition has malformed geometry references."
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


def prepare_support_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
) -> PreparedSupportUpdate:
    prepared_target = prepare_support_condition_target(
        document,
        document_uid,
        target,
        expected_kind=kind,
    )
    condition = prepared_target.condition
    _require_current_history(document, condition)
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError(
            "changes must be one non-empty FEM support-condition edit object."
        )
    allowed = {"label", "references"} if kind == "fixed" else {"label", "references", "condition"}
    if not set(changes) <= allowed:
        raise NativeAnalyzeError(f"changes accepts only {', '.join(sorted(allowed))}.")
    current_state = support_condition_state(condition)
    values_changed = "condition" in changes
    values = prepare_support_values(
        kind,
        changes["condition"] if values_changed else current_state["definition"],
    )
    label = (
        support_label(changes["label"], field="changes.label")
        if "label" in changes
        else str(condition.Label)
    )
    references = prepare_geometry_references(
        document,
        document_uid,
        changes.get("references", _current_reference_payload(condition)),
        allowed_kinds=values.allowed_reference_kinds,
    )
    if not references:
        raise NativeAnalyzeError(
            f"This {kind.replace('_', ' ')} condition requires at least one exact geometry reference."
        )
    analysis = _owner_analysis(document, condition)
    if (
        label == str(condition.Label)
        and references_match(condition, references)
        and values.normalized() == current_state["definition"]
    ):
        raise NativeAnalyzeError(
            "The requested FEM support-condition edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    return PreparedSupportUpdate(
        creation_boundary(document),
        prepared_target,
        analysis,
        analysis_state(analysis)["state_sha256"],
        label,
        references,
        values,
        values_changed,
    )


def update_support_condition(
    document: Any,
    prepared: PreparedSupportUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSupportUpdate):
        raise TypeError("prepared must be a PreparedSupportUpdate")
    require_boundary(document, prepared.boundary)
    if not support_condition_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM support condition changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if analysis_state(prepared.analysis)["state_sha256"] != prepared.analysis_state_sha256:
        raise NativeAnalyzeError(
            "The owning FEM analysis changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Support-condition reference geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    condition = prepared.target.condition
    condition.Label = prepared.label
    if prepared.values_changed:
        apply_support_values(condition, prepared.values)
    condition.References = reference_value(prepared.references)
    return NativeMutationDraft(
        value={"condition": condition, "prepared": prepared},
        recompute_targets=(condition,),
        changed=(object_identity(condition),),
    )


def verify_support_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    condition = draft.value["condition"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = support_condition_state(condition)
    if (
        not is_live(document, condition)
        or str(condition.Label) != prepared.label
        or state["condition_kind"] != prepared.target.kind
        or state["definition"] != prepared.values.normalized()
        or not references_match(condition, prepared.references)
        or condition not in tuple(prepared.analysis.Group or ())
        or analysis_state(prepared.analysis)["state_sha256"]
        != prepared.analysis_state_sha256
        or not geometry_references_still_exact(prepared.references)
        or not bool(condition.isValid())
    ):
        raise NativeAnalyzeError(
            "The FEM support-condition edit failed its exact postcondition."
        )
    return {"updated_condition": state}

