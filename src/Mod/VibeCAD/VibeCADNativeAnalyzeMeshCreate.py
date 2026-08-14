# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of durable FEM mesh definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeMeshState import (
    fem_mesh_definition_state,
    fem_mesher_kind,
)
from VibeCADNativeAnalyzeMeshValues import (
    PreparedMesherValues,
    apply_mesher_values,
    prepare_mesher_values,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    analysis_target_still_exact,
    prepare_analysis_target,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef, object_identity, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedMeshSource:
    source: Any
    expected_state_sha256: str
    shape_type: str


@dataclass(frozen=True, slots=True)
class PreparedMeshDefinitionCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    source: PreparedMeshSource
    kind: str
    label: str
    values: PreparedMesherValues


def mesh_label(value: Any, *, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError(f"{field} must contain 1 to 160 visible characters.")
    return label


def prepare_mesh_source(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    kind: str,
) -> PreparedMeshSource:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "source must contain only object_name and expected_state_sha256."
        )
    source = resolve_object(
        document,
        NativeObjectRef(document_uid, str(value["object_name"])),
    )
    shape = getattr(source, "Shape", None)
    try:
        usable = shape is not None and not shape.isNull() and shape.isValid()
    except Exception:
        usable = False
    if not usable:
        raise NativeAnalyzeError("The FEM mesh source has no valid shape.")
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(source))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM mesh source is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(source)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The FEM mesh source changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "source": {"object_name": str(source.Name)},
                "current_state_sha256": state["state_sha256"],
                "current_topology": state.get("topology"),
            },
        )
    shape_type = str(shape.ShapeType)
    allowed = (
        {"Edge", "Wire", "Face", "Shell", "Solid", "CompSolid", "Compound"}
        if kind == "gmsh"
        else {"Face", "Shell", "Solid", "CompSolid"}
    )
    if shape_type not in allowed:
        raise NativeAnalyzeError(
            f"{kind.title()} cannot mesh a {shape_type}; accepted shape types are "
            + ", ".join(sorted(allowed))
            + "."
        )
    return PreparedMeshSource(source, expected_sha, shape_type)


def mesh_source_still_exact(source: PreparedMeshSource) -> bool:
    try:
        import PartGui

        return bool(PartGui.isModelingObjectActive(source.source)) and (
            mesh_object_state(source.source)["state_sha256"]
            == source.expected_state_sha256
        )
    except Exception:
        return False


def _analysis_has_mesh(analysis: Any, *, excluding: Any = None) -> bool:
    for member in tuple(analysis.Group or ()):
        if member is excluding:
            continue
        try:
            fem_mesher_kind(member)
            return True
        except NativeAnalyzeError:
            continue
    return False


def prepare_mesh_definition_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    source: Any,
    label: Any,
    settings: Any,
) -> PreparedMeshDefinitionCreate:
    target = prepare_analysis_target(document, document_uid, analysis)
    if _analysis_has_mesh(target.analysis):
        raise NativeAnalyzeError(
            "This FEM analysis already contains a mesh definition; update or generate it "
            "instead of creating an ambiguous second solver mesh."
        )
    return PreparedMeshDefinitionCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepare_mesh_source(document, document_uid, source, kind=kind),
        kind,
        mesh_label(label),
        prepare_mesher_values(kind, settings),
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    if kind == "gmsh":
        return ObjectsFem.makeMeshGmsh(
            document,
            document.getUniqueObjectName("FEMMeshGmsh"),
        )
    if kind == "netgen":
        return ObjectsFem.makeMeshNetgen(
            document,
            document.getUniqueObjectName("FEMMeshNetgen"),
        )
    raise NativeAnalyzeError("The requested FEM mesher is unavailable.")


def create_mesh_definition(
    document: Any,
    prepared: PreparedMeshDefinitionCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshDefinitionCreate):
        raise TypeError("prepared must be PreparedMeshDefinitionCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after mesh-definition preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not mesh_source_still_exact(prepared.source):
        raise NativeAnalyzeError(
            "The FEM mesh source changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if _analysis_has_mesh(prepared.analysis.analysis):
        raise NativeAnalyzeError("The FEM analysis acquired another mesh after preflight.")
    mesh = _factory(document, prepared.kind)
    if mesh is None or fem_mesher_kind(mesh) != prepared.kind:
        raise NativeAnalyzeError("The FEM mesher factory returned the wrong object type.")
    mesh.Label = prepared.label
    mesh.Shape = prepared.source.source
    apply_mesher_values(mesh, prepared.values)
    prepared.analysis.analysis.addObject(mesh)
    if mesh not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError("The FEM mesh definition was not added to its analysis.")
    publish_operation(document, prepared.boundary, mesh)
    return NativeMutationDraft(
        value={"mesh": mesh, "prepared": prepared},
        recompute_targets=(mesh, prepared.analysis.analysis),
        created=(object_identity(mesh),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def verify_mesh_definition_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    mesh = draft.value["mesh"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, mesh)
    state = fem_mesh_definition_state(mesh)
    checks = {
        "live object": is_live(document, mesh),
        "mesher": state["mesher"] == prepared.kind,
        "label": str(mesh.Label) == prepared.label,
        "source identity": mesh.Shape is prepared.source.source,
        "source state": mesh_source_still_exact(prepared.source),
        "settings": state["settings"] == prepared.values.normalized(),
        "empty definition": not state["generated"],
        "analysis append order": tuple(analysis.Group or ())
        == (*prepared.members_before, mesh),
        "native validity": bool(mesh.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM mesh definition failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    owner_state = analysis_state(analysis)
    if owner_state["state_sha256"] == prepared.analysis.expected_state_sha256:
        raise NativeAnalyzeError("The FEM analysis did not record its mesh definition.")
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_mesh_definition": state,
        "next": {
            "tool": "analyze.mesh",
            "operation": f"generate_{prepared.kind}",
            "target": {
                "object_name": state["object_name"],
                "expected_state_sha256": state["state_sha256"],
            },
        },
    }
