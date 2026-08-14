# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed in-place edits of exact FEM geometrical analysis features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometricalCreate import feature_label
from VibeCADNativeAnalyzeGeometricalState import geometrical_feature_state
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
    require_boundary,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedGeometricalFeatureTarget,
    PreparedGeometryReference,
    geometrical_feature_target_still_exact,
    geometry_references_still_exact,
    prepare_geometrical_feature_target,
    reference_value,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedGeometricalUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedGeometricalFeatureTarget
    analysis: Any
    analysis_state_sha256: str
    label: str
    face: PreparedGeometryReference
    values: PreparedGeometricalValues
    values_changed: bool
    transform_conditions: tuple[Any, ...]


def _owner_analysis(document: Any, feature: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and feature in tuple(
                obj.Group or ()
            ):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError(
            "The FEM geometrical feature must belong to exactly one analysis."
        )
    return owners[0]


def _require_current_history(document: Any, feature: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        feature not in operations
        or str(getattr(feature, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(feature, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM geometrical feature is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(feature))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM geometrical feature is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def _current_face_payload(feature: Any) -> dict[str, Any]:
    values = tuple(getattr(feature, "References", ()) or ())
    if len(values) != 1 or not isinstance(values[0], tuple) or len(values[0]) != 2:
        raise NativeAnalyzeError(
            "The exact FEM geometrical feature has a malformed support face."
        )
    source, names = values[0]
    names = (names,) if isinstance(names, str) else tuple(names or ())
    if len(names) != 1:
        raise NativeAnalyzeError(
            "The exact FEM geometrical feature has a malformed support face."
        )
    return {
        "object_name": str(source.Name),
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "subelement": str(names[0]),
    }


def _allowed_changes(kind: str) -> set[str]:
    common = {"label", "face"}
    if kind == "section_print":
        return common | {"variable"}
    if kind == "transform":
        return common | {"coordinate_system"}
    return common


def _effective_settings(
    kind: str,
    changes: Mapping[str, Any],
    current_definition: Mapping[str, Any],
) -> tuple[Any, bool]:
    if kind == "plane_rotation":
        return None, False
    key = "variable" if kind == "section_print" else "coordinate_system"
    if key in changes:
        return {key: changes[key]}, True
    return dict(current_definition), False


def prepare_geometrical_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
) -> PreparedGeometricalUpdate:
    prepared_target = prepare_geometrical_feature_target(
        document,
        document_uid,
        target,
        expected_kind=kind,
    )
    feature = prepared_target.feature
    _require_current_history(document, feature)
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError(
            "changes must be one non-empty FEM geometrical-feature edit object."
        )
    allowed = _allowed_changes(kind)
    if not set(changes) <= allowed:
        raise NativeAnalyzeError(
            f"changes accepts only {', '.join(sorted(allowed))}."
        )
    current_state = geometrical_feature_state(feature)
    settings, values_changed = _effective_settings(
        kind,
        changes,
        current_state["definition"],
    )
    values = prepare_geometrical_values(kind, settings)
    label = (
        feature_label(changes["label"], field="changes.label")
        if "label" in changes
        else str(feature.Label)
    )
    face = prepare_feature_face(
        document,
        document_uid,
        changes.get("face", _current_face_payload(feature)),
    )
    analysis = _owner_analysis(document, feature)
    conditions = validate_feature_face(analysis, kind, face, values)
    if (
        label == str(feature.Label)
        and references_match(feature, (face,))
        and values.normalized() == current_state["definition"]
    ):
        raise NativeAnalyzeError(
            "The requested FEM geometrical-feature edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    return PreparedGeometricalUpdate(
        creation_boundary(document),
        prepared_target,
        analysis,
        analysis_state(analysis)["state_sha256"],
        label,
        face,
        values,
        values_changed,
        conditions,
    )


def update_geometrical_feature(
    document: Any,
    prepared: PreparedGeometricalUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedGeometricalUpdate):
        raise TypeError("prepared must be a PreparedGeometricalUpdate")
    require_boundary(document, prepared.boundary)
    if not geometrical_feature_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM geometrical feature changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if analysis_state(prepared.analysis)["state_sha256"] != prepared.analysis_state_sha256:
        raise NativeAnalyzeError(
            "The owning FEM analysis changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact((prepared.face,)):
        raise NativeAnalyzeError(
            "The geometrical-feature support face changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    conditions = validate_feature_face(
        prepared.analysis,
        prepared.target.kind,
        prepared.face,
        prepared.values,
    )
    if conditions != prepared.transform_conditions:
        raise NativeAnalyzeError(
            "The transformable support-face conditions changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    feature = prepared.target.feature
    feature.Label = prepared.label
    if prepared.values_changed:
        apply_geometrical_values(feature, prepared.values)
    feature.References = reference_value((prepared.face,))
    return NativeMutationDraft(
        value={"feature": feature, "prepared": prepared},
        recompute_targets=(feature,),
        changed=(object_identity(feature),),
    )


def verify_geometrical_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    feature = draft.value["feature"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = geometrical_feature_state(feature)
    if (
        not is_live(document, feature)
        or str(feature.Label) != prepared.label
        or state["feature_kind"] != prepared.target.kind
        or state["definition"] != prepared.values.normalized()
        or not references_match(feature, (prepared.face,))
        or feature not in tuple(prepared.analysis.Group or ())
        or analysis_state(prepared.analysis)["state_sha256"]
        != prepared.analysis_state_sha256
        or not geometry_references_still_exact((prepared.face,))
        or validate_feature_face(
            prepared.analysis,
            prepared.target.kind,
            prepared.face,
            prepared.values,
        )
        != prepared.transform_conditions
        or not bool(feature.isValid())
    ):
        raise NativeAnalyzeError(
            "The FEM geometrical-feature edit failed its exact postcondition."
        )
    return {"updated_feature": state}
