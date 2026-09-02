# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of FEM mesh refinement History resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    finalize_new_operation_resource,
    require_boundary,
    stage_operation_resource_reconciliation,
    verify_new_operation_resource,
)
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshRefinementValues import (
    PreparedRefinementValues,
    apply_refinement_values,
    prepare_refinement_values,
)
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedFemMeshDefinitionTarget,
    fem_mesh_definition_target_still_exact,
    prepare_fem_mesh_definition_target,
    geometry_references_still_exact,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_REFERENCE_KINDS = {
    "region": frozenset({"Solid", "Face", "Edge", "Vertex"}),
    "group": frozenset({"Solid", "Face", "Edge", "Vertex"}),
    "distance": frozenset({"Face", "Edge", "Vertex"}),
    "boundary_layer": frozenset({"Edge"}),
    "transfinite_curve": frozenset({"Edge"}),
    "transfinite_surface": frozenset({"Face", "Vertex"}),
    "transfinite_volume": frozenset({"Solid"}),
}
_FACTORIES = {
    "region": "makeMeshRegion",
    "group": "makeMeshGroup",
    "distance": "makeMeshDistance",
    "boundary_layer": "makeMeshBoundaryLayer",
    "shape": "makeMeshShape",
    "transfinite_curve": "makeMeshTransfiniteCurve",
    "transfinite_surface": "makeMeshTransfiniteSurface",
    "transfinite_volume": "makeMeshTransfiniteVolume",
}


def validate_references_for_mesh(mesh: Any, references: tuple[Any, ...]) -> None:
    """Prove every selected subshape occurs exactly once in the meshed shape."""

    mesh_source = getattr(mesh, "Shape", None)
    mesh_shape = getattr(mesh_source, "Shape", None)
    if mesh_source is None or mesh_shape is None:
        raise NativeAnalyzeError("The FEM mesh definition has no geometry source.")
    mapped: set[str] = set()
    from femtools import geomtools

    for reference in references:
        same_shape = reference.source is mesh_source
        if not same_shape:
            try:
                same_shape = bool(mesh_shape.isSame(reference.source.Shape))
            except Exception:
                same_shape = False
        for name in reference.subelements:
            mapped_name = str(name) if same_shape else ""
            if not mapped_name:
                try:
                    subshape = reference.source.Shape.getElement(str(name))
                    mapped_name = str(
                        geomtools.find_element_in_shape(mesh_shape, subshape) or ""
                    )
                except Exception:
                    mapped_name = ""
            expected_kind = "".join(character for character in str(name) if character.isalpha())
            if not mapped_name or not mapped_name.startswith(expected_kind):
                raise NativeAnalyzeError(
                    f"{reference.source.Name}.{name} is not part of the exact shape "
                    f"meshed by {mesh.Name}."
                )
            if mapped_name in mapped:
                raise NativeAnalyzeError(
                    f"Mesh geometry references select {mapped_name} more than once."
                )
            mapped.add(mapped_name)


@dataclass(frozen=True, slots=True)
class PreparedMeshRefinementCreate:
    boundary: AnalyzeCreationBoundary
    mesh: PreparedFemMeshDefinitionTarget
    mode: str
    label: str
    references: tuple[Any, ...]
    values: PreparedRefinementValues


def _label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError("label must contain 1 to 160 visible characters.")
    return label


def _require_mesh_root(document: Any, mesh: Any) -> None:
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


def prepare_mesh_refinement_create(
    document: Any,
    document_uid: str,
    *,
    mode: str,
    mesh: Any,
    label: Any,
    references: Any = None,
    definition: Any,
) -> PreparedMeshRefinementCreate:
    expected_kind = None if mode == "region" else "gmsh"
    prepared_mesh = prepare_fem_mesh_definition_target(
        document,
        document_uid,
        mesh,
        expected_kind=expected_kind,
    )
    _require_mesh_root(document, prepared_mesh.mesh)
    if mode == "shape":
        if references is not None:
            raise NativeAnalyzeError("Shape refinement does not accept geometry references.")
        prepared_references = ()
    else:
        prepared_references = prepare_geometry_references(
            document,
            document_uid,
            references,
            allowed_kinds=_REFERENCE_KINDS[mode],
            allow_mixed_kinds=mode in {"region", "group", "transfinite_surface"},
        )
        if not prepared_references:
            raise NativeAnalyzeError(f"{mode} refinement requires exact geometry references.")
        validate_references_for_mesh(prepared_mesh.mesh, prepared_references)
    return PreparedMeshRefinementCreate(
        creation_boundary(document),
        prepared_mesh,
        mode,
        _label(label),
        prepared_references,
        prepare_refinement_values(mode, definition),
    )


def _factory(document: Any, mesh: Any, mode: str) -> Any:
    import ObjectsFem

    function = getattr(ObjectsFem, _FACTORIES[mode])
    return function(
        document,
        mesh,
        name=document.getUniqueObjectName("Mesh" + "".join(part.title() for part in mode.split("_"))),
    )


def create_mesh_refinement(
    document: Any,
    prepared: PreparedMeshRefinementCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshRefinementCreate):
        raise TypeError("prepared must be PreparedMeshRefinementCreate")
    require_boundary(document, prepared.boundary)
    if not fem_mesh_definition_target_still_exact(prepared.mesh):
        raise NativeAnalyzeError(
            "The exact FEM mesh definition changed after refinement preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.references and not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Mesh refinement geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    mesh = prepared.mesh.mesh
    old_resources = stage_operation_resource_reconciliation(
        document,
        prepared.boundary,
        mesh,
    )
    try:
        refinement = _factory(document, mesh, prepared.mode)
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM mesh-refinement factory failed: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    prepared = assign_prepared_label(refinement, prepared)
    if prepared.references:
        refinement.References = reference_value(prepared.references)
    apply_refinement_values(refinement, prepared.values)
    import Fem

    mesh.FemMesh = Fem.FemMesh()
    finalize_new_operation_resource(
        document,
        prepared.boundary,
        mesh,
        old_resources,
        refinement,
    )
    return NativeMutationDraft(
        value={
            "refinement": refinement,
            "old_resources": old_resources,
            "prepared": prepared,
        },
        recompute_targets=(refinement, mesh),
        created=(object_identity(refinement),),
        changed=(object_identity(mesh),),
    )


def verify_mesh_refinement_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    refinement = draft.value["refinement"]
    old_resources = draft.value["old_resources"]
    prepared = draft.value["prepared"]
    mesh = prepared.mesh.mesh
    verify_new_operation_resource(
        document,
        prepared.boundary,
        mesh,
        old_resources,
        refinement,
    )
    state = mesh_refinement_state(refinement)
    checks = {
        "live object": is_live(document, refinement),
        "mode": state["refinement_mode"] == prepared.mode,
        "label": str(refinement.Label) == prepared.label,
        "definition": state["definition"] == prepared.values.normalized(),
        "references": (
            state["references"]
            == [
                {
                    "object_name": str(reference.source.Name),
                    "subelements": list(reference.subelements),
                }
                for reference in prepared.references
            ]
        ),
        "geometry state": geometry_references_still_exact(prepared.references),
        "mesh invalidated": not fem_mesh_definition_state(mesh)["generated"],
        "native validity": bool(refinement.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM mesh refinement failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_mesh_refinement": state,
        "mesh_definition": fem_mesh_definition_state(mesh),
    }
