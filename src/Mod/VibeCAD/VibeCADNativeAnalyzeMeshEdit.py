# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed edits of durable FEM mesh definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeMeshCreate import (
    PreparedMeshSource,
    mesh_label,
    mesh_source_still_exact,
    prepare_mesh_source,
)
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeMeshValues import (
    PreparedMesherValues,
    apply_mesher_values,
    prepare_mesher_values,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedFemMeshDefinitionTarget,
    fem_mesh_definition_target_still_exact,
    prepare_fem_mesh_definition_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedMeshDefinitionUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedFemMeshDefinitionTarget
    analysis: Any
    analysis_state_sha256: str
    source: PreparedMeshSource
    kind: str
    label: str
    values: PreparedMesherValues
    invalidates_mesh: bool


def _owner_analysis(document: Any, mesh: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and mesh in tuple(obj.Group or ()):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError("The FEM mesh definition must belong to exactly one analysis.")
    return owners[0]


def _require_current_history(document: Any, mesh: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        mesh not in operations
        or str(getattr(mesh, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(mesh, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM mesh definition is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(mesh))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM mesh definition is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def _source_payload(mesh: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "object_name": str(mesh.Shape.Name),
        "expected_state_sha256": str(state["source"]["state_sha256"]),
    }


def prepare_mesh_definition_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
) -> PreparedMeshDefinitionUpdate:
    prepared_target = prepare_fem_mesh_definition_target(
        document,
        document_uid,
        target,
        expected_kind=kind,
    )
    mesh = prepared_target.mesh
    _require_current_history(document, mesh)
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError("changes must be one non-empty FEM mesh-definition edit.")
    allowed = {"label", "source", "settings"}
    if not set(changes) <= allowed:
        raise NativeAnalyzeError("changes accepts only label, source, and settings.")
    state = fem_mesh_definition_state(mesh)
    label = (
        mesh_label(changes["label"], field="changes.label")
        if "label" in changes
        else str(mesh.Label)
    )
    source = prepare_mesh_source(
        document,
        document_uid,
        changes.get("source", _source_payload(mesh, state)),
        kind=kind,
    )
    values = prepare_mesher_values(kind, changes.get("settings", state["settings"]))
    source_changed = source.source is not mesh.Shape
    settings_changed = values.normalized() != state["settings"]
    if label == str(mesh.Label) and not source_changed and not settings_changed:
        raise NativeAnalyzeError(
            "The requested FEM mesh-definition edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    analysis = _owner_analysis(document, mesh)
    return PreparedMeshDefinitionUpdate(
        creation_boundary(document),
        prepared_target,
        analysis,
        analysis_state(analysis)["state_sha256"],
        source,
        kind,
        label,
        values,
        source_changed or settings_changed,
    )


def update_mesh_definition(
    document: Any,
    prepared: PreparedMeshDefinitionUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshDefinitionUpdate):
        raise TypeError("prepared must be PreparedMeshDefinitionUpdate")
    require_boundary(document, prepared.boundary)
    if not fem_mesh_definition_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM mesh definition changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if analysis_state(prepared.analysis)["state_sha256"] != prepared.analysis_state_sha256:
        raise NativeAnalyzeError(
            "The owning FEM analysis changed after mesh edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not mesh_source_still_exact(prepared.source):
        raise NativeAnalyzeError(
            "The FEM mesh source changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    mesh = prepared.target.mesh
    prepared = assign_prepared_label(mesh, prepared)
    mesh.Shape = prepared.source.source
    apply_mesher_values(mesh, prepared.values)
    if prepared.invalidates_mesh:
        import Fem

        mesh.FemMesh = Fem.FemMesh()
    return NativeMutationDraft(
        value={"mesh": mesh, "prepared": prepared},
        recompute_targets=(mesh,),
        changed=(object_identity(mesh),),
    )


def verify_mesh_definition_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    mesh = draft.value["mesh"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = fem_mesh_definition_state(mesh)
    checks = {
        "live object": is_live(document, mesh),
        "mesher": state["mesher"] == prepared.kind,
        "label": str(mesh.Label) == prepared.label,
        "source identity": mesh.Shape is prepared.source.source,
        "source state": mesh_source_still_exact(prepared.source),
        "settings": state["settings"] == prepared.values.normalized(),
        "stale generated mesh cleared": not prepared.invalidates_mesh or not state["generated"],
        "analysis membership": mesh in tuple(prepared.analysis.Group or ()),
        "stable analysis membership": analysis_state(prepared.analysis)["state_sha256"]
        == prepared.analysis_state_sha256,
        "native validity": bool(mesh.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM mesh-definition edit failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {"updated_mesh_definition": state}
