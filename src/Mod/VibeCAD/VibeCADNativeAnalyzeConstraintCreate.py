# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of live FEM electromagnetic constraints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeConstraintState import (
    electromagnetic_constraint_kind,
    electromagnetic_constraint_state,
)
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
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
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
class PreparedConstraintCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    references: tuple[PreparedGeometryReference, ...]
    kind: str
    label: str
    values: PreparedConstraintValues


def constraint_label(value: Any, *, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError(f"{field} must contain 1 to 160 visible characters.")
    return label


def _same_kind_members(analysis: Any, kind: str, *, ignore: Any = None) -> list[Any]:
    result = []
    for member in tuple(analysis.Group or ()):
        if member is ignore:
            continue
        try:
            if electromagnetic_constraint_kind(member) == kind:
                result.append(member)
        except NativeAnalyzeError:
            continue
    return result


def require_unambiguous_global_assignment(
    analysis: Any,
    kind: str,
    references: tuple[PreparedGeometryReference, ...],
    *,
    ignore: Any = None,
) -> None:
    if kind not in {"current_density", "magnetization"}:
        return
    peers = _same_kind_members(analysis, kind, ignore=ignore)
    if not references and peers:
        raise NativeAnalyzeError(
            f"A global {kind.replace('_', ' ')} is valid only when it is the sole constraint of that type in the analysis."
        )
    if references and any(
        not tuple(getattr(peer, "References", ()) or ()) for peer in peers
    ):
        raise NativeAnalyzeError(
            f"The analysis already contains a global {kind.replace('_', ' ')}; add exact references to that constraint before creating another."
        )


def prepare_constraint_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
    references: Any,
    constraint: Any,
) -> PreparedConstraintCreate:
    values = prepare_constraint_values(kind, constraint)
    target = prepare_analysis_target(document, document_uid, analysis)
    prepared_references = prepare_geometry_references(
        document,
        document_uid,
        references,
        allowed_kinds=values.allowed_reference_kinds,
        allow_mixed_kinds=values.allow_mixed_reference_kinds,
    )
    if not prepared_references and not values.allow_empty_references:
        expected = " or ".join(sorted(values.allowed_reference_kinds))
        raise NativeAnalyzeError(
            f"This {kind.replace('_', ' ')} requires at least one exact {expected} reference."
        )
    require_unambiguous_global_assignment(target.analysis, kind, prepared_references)
    return PreparedConstraintCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepared_references,
        kind,
        constraint_label(label),
        values,
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    names = {
        "electromagnetic": (
            "Electromagnetic",
            ObjectsFem.makeConstraintElectromagnetic,
        ),
        "current_density": ("CurrentDensity", ObjectsFem.makeConstraintCurrentDensity),
        "magnetization": ("Magnetization", ObjectsFem.makeConstraintMagnetization),
        "electric_charge_density": (
            "ElectricCharge",
            ObjectsFem.makeConstraintElectricChargeDensity,
        ),
    }
    try:
        stem, factory = names[kind]
    except KeyError as exc:
        raise NativeAnalyzeError(
            "The requested electromagnetic constraint kind is unavailable."
        ) from exc
    return factory(document, document.getUniqueObjectName(stem))


def create_constraint(
    document: Any,
    prepared: PreparedConstraintCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedConstraintCreate):
        raise TypeError("prepared must be a PreparedConstraintCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after electromagnetic preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Electromagnetic reference geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    require_unambiguous_global_assignment(
        prepared.analysis.analysis, prepared.kind, prepared.references
    )
    constraint = _factory(document, prepared.kind)
    if (
        constraint is None
        or electromagnetic_constraint_kind(constraint) != prepared.kind
    ):
        raise NativeAnalyzeError(
            "The FEM constraint factory returned the wrong object type."
        )
    prepared = assign_prepared_label(constraint, prepared)
    apply_constraint_values(constraint, prepared.values)
    constraint.References = reference_value(prepared.references)
    prepared.analysis.analysis.addObject(constraint)
    if constraint not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError("The FEM constraint was not added to its analysis.")
    publish_operation(document, prepared.boundary, constraint)
    return NativeMutationDraft(
        value={"constraint": constraint, "prepared": prepared},
        recompute_targets=(constraint, prepared.analysis.analysis),
        created=(object_identity(constraint),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def verify_constraint_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    constraint = draft.value["constraint"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, constraint)
    state = electromagnetic_constraint_state(constraint)
    if (
        not is_live(document, constraint)
        or electromagnetic_constraint_kind(constraint) != prepared.kind
        or str(constraint.Label) != prepared.label
        or state["definition"] != prepared.values.normalized()
        or not references_match(constraint, prepared.references)
        or tuple(analysis.Group or ()) != (*prepared.members_before, constraint)
        or not geometry_references_still_exact(prepared.references)
        or not bool(constraint.isValid())
    ):
        raise NativeAnalyzeError(
            "The new FEM electromagnetic constraint failed its exact postcondition."
        )
    owner_state = analysis_state(analysis)
    if owner_state["state_sha256"] == prepared.analysis.expected_state_sha256:
        raise NativeAnalyzeError("The FEM analysis did not record its new constraint.")
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_constraint": state,
    }
