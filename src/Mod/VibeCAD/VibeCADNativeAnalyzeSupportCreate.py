# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of live FEM mechanical support conditions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import references_match
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeSupportState import (
    support_condition_kind,
    support_condition_state,
)
from VibeCADNativeAnalyzeSupportValues import (
    PreparedSupportValues,
    apply_support_values,
    prepare_support_values,
)
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    PreparedGeometryReference,
    analysis_target_still_exact,
    geometry_references_still_exact,
    prepare_analysis_target,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSupportCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    references: tuple[PreparedGeometryReference, ...]
    kind: str
    label: str
    values: PreparedSupportValues


def support_label(value: Any, *, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError(f"{field} must contain 1 to 160 visible characters.")
    return label


def prepare_support_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
    references: Any,
    condition: Any = None,
) -> PreparedSupportCreate:
    values = prepare_support_values(kind, condition)
    target = prepare_analysis_target(document, document_uid, analysis)
    prepared_references = prepare_geometry_references(
        document,
        document_uid,
        references,
        allowed_kinds=values.allowed_reference_kinds,
    )
    if not prepared_references:
        expected = ", ".join(sorted(values.allowed_reference_kinds))
        raise NativeAnalyzeError(
            f"This {kind.replace('_', ' ')} condition requires at least one exact {expected} reference."
        )
    return PreparedSupportCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepared_references,
        kind,
        support_label(label),
        values,
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    factories = {
        "fixed": ("Fixed", ObjectsFem.makeConstraintFixed),
        "rigid_body": ("RigidBody", ObjectsFem.makeConstraintRigidBody),
        "displacement": ("Displacement", ObjectsFem.makeConstraintDisplacement),
        "spring": ("Spring", ObjectsFem.makeConstraintSpring),
    }
    try:
        stem, factory = factories[kind]
    except KeyError as exc:
        raise NativeAnalyzeError(
            "The requested FEM support-condition kind is unavailable."
        ) from exc
    return factory(document, document.getUniqueObjectName(stem))


def create_support_condition(
    document: Any,
    prepared: PreparedSupportCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSupportCreate):
        raise TypeError("prepared must be a PreparedSupportCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after support-condition preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Support-condition reference geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    condition = _factory(document, prepared.kind)
    if condition is None or support_condition_kind(condition) != prepared.kind:
        raise NativeAnalyzeError(
            "The FEM support-condition factory returned the wrong object type."
        )
    prepared = assign_prepared_label(condition, prepared)
    apply_support_values(condition, prepared.values)
    condition.References = reference_value(prepared.references)
    prepared.analysis.analysis.addObject(condition)
    if condition not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError(
            "The FEM support condition was not added to its analysis."
        )
    publish_operation(document, prepared.boundary, condition)
    return NativeMutationDraft(
        value={"condition": condition, "prepared": prepared},
        recompute_targets=(condition, prepared.analysis.analysis),
        created=(object_identity(condition),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def verify_support_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    condition = draft.value["condition"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, condition)
    state = support_condition_state(condition)
    if (
        not is_live(document, condition)
        or support_condition_kind(condition) != prepared.kind
        or str(condition.Label) != prepared.label
        or state["definition"] != prepared.values.normalized()
        or not references_match(condition, prepared.references)
        or tuple(analysis.Group or ()) != (*prepared.members_before, condition)
        or not geometry_references_still_exact(prepared.references)
        or not bool(condition.isValid())
    ):
        raise NativeAnalyzeError(
            "The new FEM support condition failed its exact postcondition."
        )
    owner_state = analysis_state(analysis)
    if owner_state["state_sha256"] == prepared.analysis.expected_state_sha256:
        raise NativeAnalyzeError(
            "The FEM analysis did not record its new support condition."
        )
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_condition": state,
    }
