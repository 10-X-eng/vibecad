# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed edits of FEM mesh refinement History resources."""

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
from VibeCADNativeAnalyzeMeshRefinementCreate import (
    _REFERENCE_KINDS,
    validate_references_for_mesh,
)
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshRefinementTarget import (
    PreparedMeshRefinementTarget,
    mesh_refinement_target_still_exact,
    prepare_mesh_refinement_target,
)
from VibeCADNativeAnalyzeMeshRefinementValues import (
    PreparedRefinementValues,
    apply_refinement_values,
    prepare_refinement_values,
)
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeTargets import (
    geometry_references_still_exact,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedMeshRefinementUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedMeshRefinementTarget
    mesh: Any
    mesh_state_sha256: str
    label: str
    references: tuple[Any, ...]
    changed_references: tuple[Any, ...] | None
    values: PreparedRefinementValues
    invalidates_mesh: bool


def _owner_mesh(document: Any, refinement: Any) -> Any:
    owner = getattr(refinement, "VibeCADTimelineOwner", None)
    if owner is None or owner.Document is not document:
        raise NativeAnalyzeError("The FEM mesh refinement has no durable History owner.")
    linked = refinement in tuple(getattr(owner, "MeshRefinementList", ()) or ()) or (
        refinement in tuple(getattr(owner, "MeshGroupList", ()) or ())
    )
    if not linked:
        raise NativeAnalyzeError("The FEM mesh refinement is not linked to its History owner.")
    return owner


def _current_reference_payload(refinement: Any) -> list[dict[str, Any]]:
    from VibeCADNativeMeshState import mesh_object_state

    result = []
    for source, raw_names in tuple(getattr(refinement, "References", ()) or ()):
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        result.append(
            {
                "object_name": str(source.Name),
                "expected_state_sha256": mesh_object_state(source)["state_sha256"],
                "subelements": [str(name) for name in names],
            }
        )
    return result


def _label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError("changes.label must contain 1 to 160 visible characters.")
    return label


def prepare_mesh_refinement_update(
    document: Any,
    document_uid: str,
    *,
    mode: str,
    target: Any,
    changes: Any,
) -> PreparedMeshRefinementUpdate:
    prepared_target = prepare_mesh_refinement_target(
        document,
        document_uid,
        target,
        expected_mode=mode,
    )
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError("changes must be one non-empty mesh-refinement edit.")
    allowed = {"label", "definition"} if mode == "shape" else {
        "label",
        "references",
        "definition",
    }
    if not set(changes) <= allowed:
        raise NativeAnalyzeError(
            f"changes accepts only {', '.join(sorted(allowed))} for {mode}."
        )
    refinement = prepared_target.refinement
    state = mesh_refinement_state(refinement)
    label = _label(changes["label"]) if "label" in changes else str(refinement.Label)
    values = prepare_refinement_values(
        mode,
        changes.get("definition", state["definition"]),
    )
    changed_references = None
    if mode == "shape":
        references = ()
    else:
        payload = changes.get("references", _current_reference_payload(refinement))
        references = prepare_geometry_references(
            document,
            document_uid,
            payload,
            allowed_kinds=_REFERENCE_KINDS[mode],
            allow_mixed_kinds=mode in {"region", "group", "transfinite_surface"},
        )
        if not references:
            raise NativeAnalyzeError(f"{mode} refinement requires exact geometry references.")
        if "references" in changes:
            changed_references = references
    final_references = [
        {"object_name": ref.source.Name, "subelements": list(ref.subelements)}
        for ref in references
    ]
    definition_changed = values.normalized() != state["definition"]
    references_changed = final_references != state["references"]
    if label == str(refinement.Label) and not definition_changed and not references_changed:
        raise NativeAnalyzeError(
            "The requested mesh-refinement edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    mesh = _owner_mesh(document, refinement)
    validate_references_for_mesh(mesh, references)
    return PreparedMeshRefinementUpdate(
        creation_boundary(document),
        prepared_target,
        mesh,
        fem_mesh_definition_state(mesh)["state_sha256"],
        label,
        references,
        changed_references,
        values,
        definition_changed or references_changed,
    )


def update_mesh_refinement(
    document: Any,
    prepared: PreparedMeshRefinementUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshRefinementUpdate):
        raise TypeError("prepared must be PreparedMeshRefinementUpdate")
    require_boundary(document, prepared.boundary)
    if not mesh_refinement_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM mesh refinement changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if fem_mesh_definition_state(prepared.mesh)["state_sha256"] != prepared.mesh_state_sha256:
        raise NativeAnalyzeError(
            "The owning FEM mesh definition changed after refinement preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.changed_references is not None and not geometry_references_still_exact(
        prepared.changed_references
    ):
        raise NativeAnalyzeError(
            "Mesh refinement geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    refinement = prepared.target.refinement
    prepared = assign_prepared_label(refinement, prepared)
    if prepared.target.mode != "shape":
        refinement.References = reference_value(prepared.references)
    apply_refinement_values(refinement, prepared.values)
    if prepared.invalidates_mesh:
        import Fem

        prepared.mesh.FemMesh = Fem.FemMesh()
    return NativeMutationDraft(
        value={"refinement": refinement, "prepared": prepared},
        recompute_targets=(refinement, prepared.mesh),
        changed=(
            (object_identity(refinement), object_identity(prepared.mesh))
            if prepared.invalidates_mesh
            else (object_identity(refinement),)
        ),
    )


def verify_mesh_refinement_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    refinement = draft.value["refinement"]
    prepared = draft.value["prepared"]
    state = mesh_refinement_state(refinement)
    expected_references = [
        {"object_name": ref.source.Name, "subelements": list(ref.subelements)}
        for ref in prepared.references
    ]
    checks = {
        "mode": state["refinement_mode"] == prepared.target.mode,
        "label": str(refinement.Label) == prepared.label,
        "definition": state["definition"] == prepared.values.normalized(),
        "references": state["references"] == expected_references,
        "geometry state": geometry_references_still_exact(prepared.references),
        "same mesh owner": getattr(refinement, "VibeCADTimelineOwner", None) is prepared.mesh,
        "mesh invalidated": not prepared.invalidates_mesh
        or not fem_mesh_definition_state(prepared.mesh)["generated"],
        "native validity": bool(refinement.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM mesh-refinement edit failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "updated_mesh_refinement": state,
        "mesh_definition": fem_mesh_definition_state(prepared.mesh),
    }
