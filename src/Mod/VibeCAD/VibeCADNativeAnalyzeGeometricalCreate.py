# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of live FEM geometrical analysis features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeGeometricalState import (
    geometrical_feature_kind,
    geometrical_feature_state,
)
from VibeCADNativeAnalyzeGeometricalValues import (
    PreparedGeometricalValues,
    apply_geometrical_values,
    prepare_feature_face,
    prepare_geometrical_values,
    validate_feature_face,
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
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedGeometricalCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    face: PreparedGeometryReference
    kind: str
    label: str
    values: PreparedGeometricalValues
    transform_conditions: tuple[Any, ...]


def feature_label(value: Any, *, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError(f"{field} must contain 1 to 160 visible characters.")
    return label


def prepare_geometrical_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
    face: Any,
    settings: Any = None,
) -> PreparedGeometricalCreate:
    values = prepare_geometrical_values(kind, settings)
    target = prepare_analysis_target(document, document_uid, analysis)
    prepared_face = prepare_feature_face(document, document_uid, face)
    conditions = validate_feature_face(
        target.analysis,
        kind,
        prepared_face,
        values,
    )
    return PreparedGeometricalCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepared_face,
        kind,
        feature_label(label),
        values,
        conditions,
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    factories = {
        "plane_rotation": (
            "PlaneRotation",
            ObjectsFem.makeConstraintPlaneRotation,
        ),
        "section_print": (
            "SectionPrint",
            ObjectsFem.makeConstraintSectionPrint,
        ),
        "transform": ("Transform", ObjectsFem.makeConstraintTransform),
    }
    try:
        stem, factory = factories[kind]
    except KeyError as exc:
        raise NativeAnalyzeError(
            "The requested FEM geometrical feature kind is unavailable."
        ) from exc
    return factory(document, document.getUniqueObjectName(stem))


def create_geometrical_feature(
    document: Any,
    prepared: PreparedGeometricalCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedGeometricalCreate):
        raise TypeError("prepared must be a PreparedGeometricalCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after geometrical-feature preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact((prepared.face,)):
        raise NativeAnalyzeError(
            "The geometrical-feature support face changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    conditions = validate_feature_face(
        prepared.analysis.analysis,
        prepared.kind,
        prepared.face,
        prepared.values,
    )
    if conditions != prepared.transform_conditions:
        raise NativeAnalyzeError(
            "The transformable support-face conditions changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    feature = _factory(document, prepared.kind)
    if feature is None or geometrical_feature_kind(feature) != prepared.kind:
        raise NativeAnalyzeError(
            "The FEM geometrical-feature factory returned the wrong object type."
        )
    prepared = assign_prepared_label(feature, prepared)
    apply_geometrical_values(feature, prepared.values)
    feature.References = reference_value((prepared.face,))
    prepared.analysis.analysis.addObject(feature)
    if feature not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError(
            "The FEM geometrical feature was not added to its analysis."
        )
    publish_operation(document, prepared.boundary, feature)
    return NativeMutationDraft(
        value={"feature": feature, "prepared": prepared},
        recompute_targets=(feature, prepared.analysis.analysis),
        created=(object_identity(feature),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def verify_geometrical_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    feature = draft.value["feature"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, feature)
    state = geometrical_feature_state(feature)
    if (
        not is_live(document, feature)
        or geometrical_feature_kind(feature) != prepared.kind
        or str(feature.Label) != prepared.label
        or state["definition"] != prepared.values.normalized()
        or not references_match(feature, (prepared.face,))
        or tuple(analysis.Group or ()) != (*prepared.members_before, feature)
        or not geometry_references_still_exact((prepared.face,))
        or validate_feature_face(
            analysis,
            prepared.kind,
            prepared.face,
            prepared.values,
        )
        != prepared.transform_conditions
        or not bool(feature.isValid())
    ):
        raise NativeAnalyzeError(
            "The new FEM geometrical feature failed its exact postcondition."
        )
    owner_state = analysis_state(analysis)
    if owner_state["state_sha256"] == prepared.analysis.expected_state_sha256:
        raise NativeAnalyzeError(
            "The FEM analysis did not record its new geometrical feature."
        )
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_feature": state,
    }
