# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of live FEM fluid constraints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeFluidState import fluid_constraint_kind, fluid_constraint_state
from VibeCADNativeAnalyzeFluidValues import (
    PreparedFluidValues,
    apply_fluid_values,
    prepare_fluid_values,
)
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
from VibeCADNativeLabel import matches_preferred_document_label
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedFluidCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    references: tuple[PreparedGeometryReference, ...]
    kind: str
    label: str
    values: PreparedFluidValues


def fluid_label(value: Any, *, field: str = "label") -> str:
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
            if fluid_constraint_kind(member) == kind:
                result.append(member)
        except NativeAnalyzeError:
            continue
    return result


def require_unambiguous_initial_assignment(
    analysis: Any,
    kind: str,
    references: tuple[PreparedGeometryReference, ...],
    *,
    ignore: Any = None,
) -> None:
    if kind not in {"initial_flow_velocity", "initial_pressure"}:
        return
    peers = _same_kind_members(analysis, kind, ignore=ignore)
    name = kind.replace("_", " ")
    if not references and peers:
        raise NativeAnalyzeError(
            f"A global {name} is valid only when it is the sole constraint of that type in the analysis."
        )
    if references and any(
        not tuple(getattr(peer, "References", ()) or ()) for peer in peers
    ):
        raise NativeAnalyzeError(
            f"The analysis already contains a global {name}; add exact references to that constraint before creating another."
        )


def require_unassigned_boundary_faces(
    analysis: Any,
    references: tuple[PreparedGeometryReference, ...],
    *,
    ignore: Any = None,
) -> None:
    requested: dict[Any, set[str]] = {}
    for reference in references:
        requested.setdefault(reference.source, set()).update(reference.subelements)
    for peer in _same_kind_members(analysis, "fluid_boundary", ignore=ignore):
        for raw in tuple(getattr(peer, "References", ()) or ()):
            if not isinstance(raw, tuple) or len(raw) != 2:
                continue
            source, names = raw
            selected = requested.get(source)
            if not selected:
                continue
            values = (names,) if isinstance(names, str) else tuple(names or ())
            overlap = next((str(name) for name in values if str(name) in selected), "")
            if overlap:
                raise NativeAnalyzeError(
                    f"{source.Name}.{overlap} already belongs to fluid boundary "
                    f"{peer.Label}; use an unassigned face or edit that boundary."
                )


def prepare_fluid_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
    references: Any,
    constraint: Any,
) -> PreparedFluidCreate:
    values = prepare_fluid_values(kind, constraint)
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
    require_unambiguous_initial_assignment(target.analysis, kind, prepared_references)
    if kind == "fluid_boundary":
        require_unassigned_boundary_faces(target.analysis, prepared_references)
    return PreparedFluidCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepared_references,
        kind,
        fluid_label(label),
        values,
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    factories = {
        "initial_flow_velocity": (
            "InitialFlowVelocity",
            ObjectsFem.makeConstraintInitialFlowVelocity,
        ),
        "initial_pressure": (
            "InitialPressure",
            ObjectsFem.makeConstraintInitialPressure,
        ),
        "flow_velocity": ("FlowVelocity", ObjectsFem.makeConstraintFlowVelocity),
        "fluid_boundary": (
            "FluidBoundary",
            ObjectsFem.makeConstraintFluidBoundary,
        ),
    }
    try:
        stem, factory = factories[kind]
    except KeyError as exc:
        raise NativeAnalyzeError(
            "The requested FEM fluid constraint kind is unavailable."
        ) from exc
    return factory(document, document.getUniqueObjectName(stem))


def create_fluid_constraint(
    document: Any,
    prepared: PreparedFluidCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedFluidCreate):
        raise TypeError("prepared must be a PreparedFluidCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after fluid-constraint preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Fluid-constraint reference geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    require_unambiguous_initial_assignment(
        prepared.analysis.analysis, prepared.kind, prepared.references
    )
    if prepared.kind == "fluid_boundary":
        require_unassigned_boundary_faces(
            prepared.analysis.analysis,
            prepared.references,
        )
    constraint = _factory(document, prepared.kind)
    if constraint is None or fluid_constraint_kind(constraint) != prepared.kind:
        raise NativeAnalyzeError(
            "The FEM fluid factory returned the wrong object type."
        )
    prepared = assign_prepared_label(constraint, prepared)
    apply_fluid_values(constraint, prepared.values)
    constraint.References = reference_value(prepared.references)
    prepared.analysis.analysis.addObject(constraint)
    if constraint not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError(
            "The FEM fluid constraint was not added to its analysis."
        )
    publish_operation(document, prepared.boundary, constraint)
    return NativeMutationDraft(
        value={"constraint": constraint, "prepared": prepared},
        recompute_targets=(constraint, prepared.analysis.analysis),
        created=(object_identity(constraint),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def verify_fluid_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    constraint = draft.value["constraint"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, constraint)
    state = fluid_constraint_state(constraint)
    checks = {
        "object is live": is_live(document, constraint),
        "constraint kind": fluid_constraint_kind(constraint) == prepared.kind,
        "label": matches_preferred_document_label(
            str(constraint.Label), prepared.label
        ),
        "definition": state["definition"] == prepared.values.normalized(),
        "references": references_match(constraint, prepared.references),
        "analysis membership": tuple(analysis.Group or ())
        == (*prepared.members_before, constraint),
        "reference geometry": geometry_references_still_exact(prepared.references),
        "object validity": bool(constraint.isValid()),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise NativeAnalyzeError(
            "The new FEM fluid constraint failed: " + ", ".join(failed) + "."
        )
    owner_state = analysis_state(analysis)
    if owner_state["state_sha256"] == prepared.analysis.expected_state_sha256:
        raise NativeAnalyzeError(
            "The FEM analysis did not record its new fluid constraint."
        )
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_constraint": state,
    }
