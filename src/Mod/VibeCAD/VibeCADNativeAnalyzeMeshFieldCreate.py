# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of Gmsh refinement-field History resources."""

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
from VibeCADNativeAnalyzeMeshFieldTarget import (
    PreparedMeshFieldDependencies,
    mesh_field_dependencies_still_exact,
    prepare_mesh_field_dependencies,
)
from VibeCADNativeAnalyzeMeshFieldResult import (
    PreparedMeshFieldResult,
    mesh_field_result_still_exact,
    prepare_mesh_field_result,
)
from VibeCADNativeAnalyzeMeshFieldValues import (
    ADVANCED_KINDS,
    MANIPULATION_KINDS,
    PreparedMeshFieldValues,
    apply_mesh_field_values,
    prepare_mesh_field_values,
)
from VibeCADNativeAnalyzeMeshRefinementCreate import (
    _require_mesh_root,
    validate_references_for_mesh,
)
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedFemMeshDefinitionTarget,
    fem_mesh_definition_target_still_exact,
    geometry_references_still_exact,
    prepare_fem_mesh_definition_target,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_REFERENCE_KINDS = {
    "restrict": frozenset({"Solid", "Face", "Edge", "Vertex"}),
    "attractor_aniso_curve": frozenset({"Edge"}),
    "distance": frozenset({"Face", "Edge", "Vertex"}),
}


@dataclass(frozen=True, slots=True)
class PreparedMeshFieldCreate:
    boundary: AnalyzeCreationBoundary
    mesh: PreparedFemMeshDefinitionTarget
    kind: str
    label: str
    dependencies: PreparedMeshFieldDependencies
    references: tuple[Any, ...]
    values: PreparedMeshFieldValues
    result: PreparedMeshFieldResult | None


def _label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError("label must contain 1 to 160 visible characters.")
    return label


def _dependencies(
    document: Any,
    document_uid: str,
    mesh: Any,
    kind: str,
    input_refinement: Any,
    input_refinements: Any,
) -> PreparedMeshFieldDependencies:
    if kind in MANIPULATION_KINDS:
        if input_refinement is None or input_refinements is not None:
            raise NativeAnalyzeError(
                f"{kind} requires exactly one input_refinement target."
            )
        return prepare_mesh_field_dependencies(
            document,
            document_uid,
            mesh,
            [input_refinement],
            minimum=1,
            maximum=1,
        )
    if kind in {"math_eval", "math_eval_aniso"}:
        if input_refinement is not None or input_refinements is None:
            raise NativeAnalyzeError(
                f"{kind} requires the ordered input_refinements array, which may be empty."
            )
        return prepare_mesh_field_dependencies(
            document,
            document_uid,
            mesh,
            input_refinements,
            minimum=0,
            maximum=8,
        )
    if input_refinement is not None or input_refinements is not None:
        raise NativeAnalyzeError(f"{kind} does not accept refinement-field inputs.")
    return prepare_mesh_field_dependencies(
        document,
        document_uid,
        mesh,
        [],
        minimum=0,
        maximum=0,
    )


def _references(
    document: Any,
    document_uid: str,
    kind: str,
    value: Any,
) -> tuple[Any, ...]:
    allowed = _REFERENCE_KINDS.get(kind)
    if allowed is None:
        if value is not None:
            raise NativeAnalyzeError(f"{kind} does not accept geometry references.")
        return ()
    prepared = prepare_geometry_references(
        document,
        document_uid,
        value,
        allowed_kinds=allowed,
        allow_mixed_kinds=kind in {"restrict", "distance"},
    )
    if not prepared:
        raise NativeAnalyzeError(f"{kind} requires exact geometry references.")
    return prepared


def prepare_mesh_field_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    mesh: Any,
    label: Any,
    definition: Any,
    input_refinement: Any = None,
    input_refinements: Any = None,
    references: Any = None,
    result: Any = None,
) -> PreparedMeshFieldCreate:
    if kind not in {*MANIPULATION_KINDS, *ADVANCED_KINDS}:
        raise NativeAnalyzeError("The requested Gmsh refinement field is unavailable.")
    prepared_mesh = prepare_fem_mesh_definition_target(
        document,
        document_uid,
        mesh,
        expected_kind="gmsh",
    )
    _require_mesh_root(document, prepared_mesh.mesh)
    dependencies = _dependencies(
        document,
        document_uid,
        prepared_mesh.mesh,
        kind,
        input_refinement,
        input_refinements,
    )
    prepared_references = _references(document, document_uid, kind, references)
    validate_references_for_mesh(prepared_mesh.mesh, prepared_references)
    values = prepare_mesh_field_values(
        kind,
        definition,
        input_count=len(dependencies.direct),
    )
    if kind == "result":
        if result is None:
            raise NativeAnalyzeError(
                "result requires one exact post-processing result target."
            )
        prepared_result = prepare_mesh_field_result(
            document,
            document_uid,
            result,
            values.values["field"],
        )
    else:
        if result is not None:
            raise NativeAnalyzeError(f"{kind} does not accept a result target.")
        prepared_result = None
    return PreparedMeshFieldCreate(
        creation_boundary(document),
        prepared_mesh,
        kind,
        _label(label),
        dependencies,
        prepared_references,
        values,
        prepared_result,
    )


def _factory(document: Any, mesh: Any, family: str) -> Any:
    import ObjectsFem

    function = (
        ObjectsFem.makeMeshManipulate
        if family == "manipulate"
        else ObjectsFem.makeMeshAdvanced
    )
    prefix = "MeshManipulate" if family == "manipulate" else "MeshAdvanced"
    return function(document, mesh, name=document.getUniqueObjectName(prefix))


def _expected_definition(prepared: PreparedMeshFieldCreate) -> dict[str, Any]:
    names = [str(target.refinement.Name) for target in prepared.dependencies.direct]
    result: dict[str, Any] = {"kind": prepared.kind}
    if prepared.kind == "result":
        assert prepared.result is not None
        result["input_refinements"] = []
        result["result"] = {
            "object_name": str(prepared.result.result.Name),
            "field": prepared.result.field,
        }
    elif prepared.values.family == "manipulate":
        result["input_refinement"] = {"object_name": names[0]}
    else:
        result["input_refinements"] = [
            {"object_name": name} for name in names
        ]
    if prepared.kind != "result":
        result.update(prepared.values.normalized())
    return result


def create_mesh_field(document: Any, prepared: PreparedMeshFieldCreate) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshFieldCreate):
        raise TypeError("prepared must be PreparedMeshFieldCreate")
    require_boundary(document, prepared.boundary)
    if not fem_mesh_definition_target_still_exact(prepared.mesh):
        raise NativeAnalyzeError(
            "The exact Gmsh definition changed after field preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not mesh_field_dependencies_still_exact(prepared.dependencies):
        raise NativeAnalyzeError(
            "A refinement-field input changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.references and not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Refinement-field geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.result is not None and not mesh_field_result_still_exact(prepared.result):
        raise NativeAnalyzeError(
            "The exact post-processing result changed after field preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    mesh = prepared.mesh.mesh
    old_resources = stage_operation_resource_reconciliation(
        document,
        prepared.boundary,
        mesh,
    )
    try:
        field = _factory(document, mesh, prepared.values.family)
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The refinement-field factory failed: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    prepared = assign_prepared_label(field, prepared)
    if prepared.values.family == "manipulate":
        field.Refinement = prepared.dependencies.objects[0]
    else:
        field.Refinements = list(prepared.dependencies.objects)
    if prepared.result is not None:
        field.ResultObject = prepared.result.result
    field.References = reference_value(prepared.references) if prepared.references else []
    apply_mesh_field_values(field, prepared.values)
    import Fem

    mesh.FemMesh = Fem.FemMesh()
    finalize_new_operation_resource(
        document,
        prepared.boundary,
        mesh,
        old_resources,
        field,
    )
    return NativeMutationDraft(
        value={
            "field": field,
            "old_resources": old_resources,
            "prepared": prepared,
        },
        recompute_targets=(field, mesh),
        created=(object_identity(field),),
        changed=(object_identity(mesh),),
    )


def verify_mesh_field_create(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    field = draft.value["field"]
    old_resources = draft.value["old_resources"]
    prepared = draft.value["prepared"]
    mesh = prepared.mesh.mesh
    verify_new_operation_resource(
        document,
        prepared.boundary,
        mesh,
        old_resources,
        field,
    )
    state = mesh_refinement_state(field)
    expected_references = [
        {
            "object_name": str(reference.source.Name),
            "subelements": list(reference.subelements),
        }
        for reference in prepared.references
    ]
    checks = {
        "live object": is_live(document, field),
        "family": state["refinement_mode"] == prepared.values.family,
        "definition": state["definition"] == _expected_definition(prepared),
        "references": state["references"] == expected_references,
        "dependency state": mesh_field_dependencies_still_exact(prepared.dependencies),
        "geometry state": geometry_references_still_exact(prepared.references),
        "result state": prepared.result is None
        or mesh_field_result_still_exact(prepared.result),
        "mesh invalidated": not fem_mesh_definition_state(mesh)["generated"],
        "native validity": bool(field.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The refinement field failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    result = {
        "created_mesh_field": state,
        "mesh_definition": fem_mesh_definition_state(mesh),
    }
    if prepared.result is not None:
        result["result_field"] = prepared.result.response()
    return result
