# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed edits of exact Gmsh refinement-field resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeMeshFieldCreate import _REFERENCE_KINDS
from VibeCADNativeAnalyzeMeshFieldResult import (
    PreparedMeshFieldResult,
    current_mesh_field_result_target,
    mesh_field_result_still_exact,
    prepare_mesh_field_result,
)
from VibeCADNativeAnalyzeMeshRefinementCreate import validate_references_for_mesh
from VibeCADNativeAnalyzeMeshFieldTarget import (
    PreparedMeshFieldDependencies,
    dependency_payload,
    mesh_field_dependencies_still_exact,
    mesh_resource_owner,
    prepare_mesh_field_dependencies,
)
from VibeCADNativeAnalyzeMeshFieldValues import (
    ADVANCED_KINDS,
    MANIPULATION_KINDS,
    PreparedMeshFieldValues,
    apply_mesh_field_values,
    prepare_mesh_field_values,
)
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshRefinementTarget import (
    PreparedMeshRefinementTarget,
    mesh_refinement_target_still_exact,
    prepare_mesh_refinement_target,
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
class PreparedMeshFieldUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedMeshRefinementTarget
    mesh: Any
    mesh_state_sha256: str
    kind: str
    label: str
    dependencies: PreparedMeshFieldDependencies
    references: tuple[Any, ...]
    values: PreparedMeshFieldValues
    result: PreparedMeshFieldResult | None
    invalidates_mesh: bool


def _label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError("changes.label must contain 1 to 160 visible characters.")
    return label


def _current_reference_payload(field: Any) -> list[dict[str, Any]]:
    from VibeCADNativeMeshState import mesh_object_state

    result = []
    for source, raw_names in tuple(getattr(field, "References", ()) or ()):
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        result.append(
            {
                "object_name": str(source.Name),
                "expected_state_sha256": mesh_object_state(source)["state_sha256"],
                "subelements": [str(name) for name in names],
            }
        )
    return result


def _dependency_payload(field: Any, kind: str, changes: Mapping[str, Any]) -> list[Any]:
    if kind in MANIPULATION_KINDS:
        current = getattr(field, "Refinement", None)
        payload = changes.get(
            "input_refinement",
            dependency_payload(current) if current is not None else None,
        )
        if payload is None:
            raise NativeAnalyzeError(f"{kind} requires exactly one input_refinement.")
        return [payload]
    if kind in {"math_eval", "math_eval_aniso"}:
        return changes.get(
            "input_refinements",
            [dependency_payload(obj) for obj in tuple(field.Refinements or ())],
        )
    return []


def _allowed_changes(kind: str) -> set[str]:
    result = {"label", "definition"}
    if kind in MANIPULATION_KINDS:
        result.add("input_refinement")
    elif kind in {"math_eval", "math_eval_aniso"}:
        result.add("input_refinements")
    if kind in _REFERENCE_KINDS:
        result.add("references")
    if kind == "result":
        result.add("result")
    return result


def _current_values(state: Mapping[str, Any]) -> dict[str, Any]:
    if state["definition"].get("kind") == "result":
        return {"field": state["definition"]["result"]["field"]}
    return {
        name: value
        for name, value in state["definition"].items()
        if name not in {"kind", "input_refinement", "input_refinements"}
    }


def _expected_definition(prepared: PreparedMeshFieldUpdate) -> dict[str, Any]:
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


def prepare_mesh_field_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
) -> PreparedMeshFieldUpdate:
    if kind not in {*MANIPULATION_KINDS, *ADVANCED_KINDS}:
        raise NativeAnalyzeError("The requested Gmsh refinement field is unavailable.")
    family = "manipulate" if kind in MANIPULATION_KINDS else "advanced"
    prepared_target = prepare_mesh_refinement_target(
        document,
        document_uid,
        target,
        expected_mode=family,
    )
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError("changes must be one non-empty refinement-field edit.")
    allowed = _allowed_changes(kind)
    if not set(changes) <= allowed:
        raise NativeAnalyzeError(
            f"changes accepts only {', '.join(sorted(allowed))} for {kind}."
        )
    field = prepared_target.refinement
    state = mesh_refinement_state(field)
    if state["definition"].get("kind") != kind:
        raise NativeAnalyzeError(
            f"The exact target is {state['definition'].get('kind')}; this operation "
            f"requires {kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    mesh = mesh_resource_owner(document, field)
    raw_dependencies = _dependency_payload(field, kind, changes)
    dependencies = prepare_mesh_field_dependencies(
        document,
        document_uid,
        mesh,
        raw_dependencies,
        minimum=1 if family == "manipulate" else 0,
        maximum=1 if family == "manipulate" else (8 if kind.startswith("math_eval") else 0),
        forbidden=field,
    )
    allowed_reference_kinds = _REFERENCE_KINDS.get(kind)
    if allowed_reference_kinds is None:
        references = ()
    else:
        references = prepare_geometry_references(
            document,
            document_uid,
            changes.get("references", _current_reference_payload(field)),
            allowed_kinds=allowed_reference_kinds,
            allow_mixed_kinds=kind in {"restrict", "distance"},
        )
        if not references:
            raise NativeAnalyzeError(f"{kind} requires exact geometry references.")
    values = prepare_mesh_field_values(
        kind,
        changes.get("definition", _current_values(state)),
        input_count=len(dependencies.direct),
    )
    if kind == "result":
        current_result = getattr(field, "ResultObject", None)
        if current_result is None:
            raise NativeAnalyzeError(
                "The Result-backed Gmsh field has no post-processing source."
            )
        prepared_result = prepare_mesh_field_result(
            document,
            document_uid,
            changes.get("result", current_mesh_field_result_target(current_result)),
            values.values["field"],
        )
    else:
        prepared_result = None
    label = _label(changes["label"]) if "label" in changes else str(field.Label)
    validate_references_for_mesh(mesh, references)
    expected_dependencies = [
        str(target.refinement.Name) for target in dependencies.direct
    ]
    current_dependencies = (
        [state["definition"]["input_refinement"]["object_name"]]
        if family == "manipulate"
        else [
            item["object_name"]
            for item in state["definition"].get("input_refinements", [])
        ]
    )
    expected_references = [
        {
            "object_name": str(reference.source.Name),
            "subelements": list(reference.subelements),
        }
        for reference in references
    ]
    semantic_change = (
        expected_dependencies != current_dependencies
        or expected_references != state["references"]
        or values.normalized() != _current_values(state)
        or (
            prepared_result is not None
            and prepared_result.result is not getattr(field, "ResultObject", None)
        )
    )
    if not semantic_change and label == str(field.Label):
        raise NativeAnalyzeError(
            "The requested refinement-field edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    return PreparedMeshFieldUpdate(
        creation_boundary(document),
        prepared_target,
        mesh,
        fem_mesh_definition_state(mesh)["state_sha256"],
        kind,
        label,
        dependencies,
        references,
        values,
        prepared_result,
        semantic_change,
    )


def update_mesh_field(document: Any, prepared: PreparedMeshFieldUpdate) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshFieldUpdate):
        raise TypeError("prepared must be PreparedMeshFieldUpdate")
    require_boundary(document, prepared.boundary)
    if not mesh_refinement_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact refinement field changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if fem_mesh_definition_state(prepared.mesh)["state_sha256"] != prepared.mesh_state_sha256:
        raise NativeAnalyzeError(
            "The owning Gmsh definition changed after field preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not mesh_field_dependencies_still_exact(prepared.dependencies):
        raise NativeAnalyzeError(
            "A refinement-field input changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.references and not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Refinement-field geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.result is not None and not mesh_field_result_still_exact(prepared.result):
        raise NativeAnalyzeError(
            "The exact post-processing result changed after field preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    field = prepared.target.refinement
    field.Label = prepared.label
    if prepared.values.family == "manipulate":
        field.Refinement = prepared.dependencies.objects[0]
    else:
        field.Refinements = list(prepared.dependencies.objects)
    if prepared.result is not None:
        field.ResultObject = prepared.result.result
    field.References = reference_value(prepared.references) if prepared.references else []
    apply_mesh_field_values(field, prepared.values)
    if prepared.invalidates_mesh:
        import Fem

        prepared.mesh.FemMesh = Fem.FemMesh()
    changed = (object_identity(field),)
    if prepared.invalidates_mesh:
        changed = (*changed, object_identity(prepared.mesh))
    return NativeMutationDraft(
        value={"field": field, "prepared": prepared},
        recompute_targets=(field, prepared.mesh),
        changed=changed,
    )


def verify_mesh_field_update(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    field = draft.value["field"]
    prepared = draft.value["prepared"]
    state = mesh_refinement_state(field)
    expected_references = [
        {
            "object_name": str(reference.source.Name),
            "subelements": list(reference.subelements),
        }
        for reference in prepared.references
    ]
    checks = {
        "family": state["refinement_mode"] == prepared.values.family,
        "definition": state["definition"] == _expected_definition(prepared),
        "label": str(field.Label) == prepared.label,
        "references": state["references"] == expected_references,
        "same mesh owner": mesh_resource_owner(document, field) is prepared.mesh,
        "dependency state": mesh_field_dependencies_still_exact(prepared.dependencies),
        "geometry state": geometry_references_still_exact(prepared.references),
        "result state": prepared.result is None
        or mesh_field_result_still_exact(prepared.result),
        "mesh invalidated": not prepared.invalidates_mesh
        or not fem_mesh_definition_state(prepared.mesh)["generated"],
        "native validity": bool(field.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The refinement-field edit failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    result = {
        "updated_mesh_field": state,
        "mesh_definition": fem_mesh_definition_state(prepared.mesh),
    }
    if prepared.result is not None:
        result["result_field"] = prepared.result.response()
    return result
