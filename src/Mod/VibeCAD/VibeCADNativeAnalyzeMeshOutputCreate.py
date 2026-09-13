# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact FEM element filtering and surface conversion mutations."""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_left, bisect_right
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
)
from VibeCADNativeAnalyzeMeshOutputState import (
    PreparedFemMeshObjectTarget,
    fem_mesh_object_target_still_exact,
    mesh_filter_state,
    prepare_fem_mesh_object_target,
    primary_element_inventory,
)
from VibeCADNativeAnalyzeMeshDisplacement import (
    PreparedFemDisplacement,
    fem_displacement_data_still_exact,
    fem_displacement_still_exact,
    prepare_fem_displacement,
)
from VibeCADNativeAnalyzeMeshState import fem_mesh_object_state
from VibeCADNativeMeshState import mesh_geometry_sha256, mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_ERASE_ELEMENT_IDS = 256
MAX_ERASE_ELEMENT_RANGES = 256
MAX_ERASE_ELEMENTS_PER_CALL = 1_000_000


@dataclass(frozen=True, slots=True)
class PreparedEraseElements:
    target: PreparedFemMeshObjectTarget
    boundary: AnalyzeCreationBoundary
    label: str
    removed_ids: tuple[int, ...]
    remaining_ids: tuple[int, ...]
    type_marker: int


@dataclass(frozen=True, slots=True)
class PreparedFemSurfaceConversion:
    target: PreparedFemMeshObjectTarget
    boundary: AnalyzeCreationBoundary
    label: str
    exterior_face_count: int
    expected_facet_count: int
    displacement: PreparedFemDisplacement | None


def _label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError("label must contain 1 to 160 visible characters.")
    return label


def prepare_erase_elements(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    label: Any,
    element_ids: Any,
) -> PreparedEraseElements:
    prepared = prepare_fem_mesh_object_target(document, document_uid, target)
    if (
        not isinstance(element_ids, list)
        or not 1 <= len(element_ids) <= MAX_ERASE_ELEMENT_IDS
        or any(type(value) is not int or value <= 0 for value in element_ids)
    ):
        raise NativeAnalyzeError(
            f"element_ids must contain 1 to {MAX_ERASE_ELEMENT_IDS} positive integers."
        )
    removed = tuple(element_ids)
    if len(removed) != len(set(removed)):
        raise NativeAnalyzeError("element_ids must not contain duplicates.")
    return _prepare_erase_selection(document, prepared, label, removed)


def _prepare_erase_selection(
    document: Any,
    prepared: PreparedFemMeshObjectTarget,
    label: Any,
    removed: tuple[int, ...],
) -> PreparedEraseElements:
    available = set(prepared.element_ids)
    unavailable = tuple(value for value in removed if value not in available)
    if unavailable:
        shown = ", ".join(str(value) for value in unavailable[:8])
        suffix = " ..." if len(unavailable) > 8 else ""
        raise NativeAnalyzeError(
            f"element_ids contains IDs outside the mesh's primary {prepared.element_kind} "
            f"elements: {shown}{suffix}."
        )
    if len(removed) == len(prepared.element_ids):
        raise NativeAnalyzeError("Erase Elements must leave at least one primary element.")
    _kind, marker, _ids = primary_element_inventory(prepared.mesh.FemMesh)
    removed_set = set(removed)
    remaining = tuple(value for value in prepared.element_ids if value not in removed_set)
    return PreparedEraseElements(
        prepared,
        creation_boundary(document),
        _label(label),
        tuple(sorted(removed)),
        remaining,
        marker,
    )


def prepare_erase_element_ranges(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    label: Any,
    element_id_ranges: Any,
) -> PreparedEraseElements:
    prepared = prepare_fem_mesh_object_target(document, document_uid, target)
    if not isinstance(element_id_ranges, list) or not 1 <= len(
        element_id_ranges
    ) <= MAX_ERASE_ELEMENT_RANGES:
        raise NativeAnalyzeError(
            f"element_id_ranges must contain 1 to {MAX_ERASE_ELEMENT_RANGES} ranges."
        )
    ranges = []
    total = 0
    previous_last = 0
    for index, value in enumerate(element_id_ranges):
        if not isinstance(value, dict) or set(value) != {"first_id", "last_id"}:
            raise NativeAnalyzeError(
                f"element_id_ranges[{index}] must contain only first_id and last_id."
            )
        first = value["first_id"]
        last = value["last_id"]
        if type(first) is not int or type(last) is not int or first <= 0 or last < first:
            raise NativeAnalyzeError(
                f"element_id_ranges[{index}] must be one positive inclusive range."
            )
        if first <= previous_last:
            raise NativeAnalyzeError("element_id_ranges must be sorted and non-overlapping.")
        total += last - first + 1
        if total > MAX_ERASE_ELEMENTS_PER_CALL:
            raise NativeAnalyzeError(
                f"element_id_ranges may select at most {MAX_ERASE_ELEMENTS_PER_CALL} elements."
            )
        ranges.append((first, last))
        previous_last = last

    available = prepared.element_ids
    removed = []
    for first, last in ranges:
        begin = bisect_left(available, first)
        end = bisect_right(available, last)
        selected = available[begin:end]
        if len(selected) != last - first + 1:
            raise NativeAnalyzeError(
                f"Element range {first}..{last} includes IDs outside the mesh's primary "
                f"{prepared.element_kind} elements."
            )
        removed.extend(selected)
    return _prepare_erase_selection(document, prepared, label, tuple(removed))


def create_erased_elements(
    document: Any,
    prepared: PreparedEraseElements,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedEraseElements):
        raise TypeError("prepared must be PreparedEraseElements")
    require_boundary(document, prepared.boundary)
    if not fem_mesh_object_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM mesh changed after element-filter preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    source = prepared.target.mesh
    try:
        filtered = source.FemMesh.copy()
        filtered.removeElements(list(prepared.removed_ids), True)
        kind, marker, remaining = primary_element_inventory(filtered)
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError("The FEM mesh could not erase the requested elements.") from exc
    if (
        kind != prepared.target.element_kind
        or marker != prepared.type_marker
        or remaining != prepared.remaining_ids
    ):
        raise NativeAnalyzeError("The filtered FEM mesh did not retain the exact expected elements.")

    operation = document.addObject(
        "Fem::FemSetElementNodesObject",
        document.getUniqueObjectName("ElementsSet"),
    )
    result_mesh = document.addObject(
        "Fem::FemMeshObject",
        document.getUniqueObjectName("FilteredMesh"),
    )
    if operation is None or result_mesh is None:
        raise NativeAnalyzeError("The FEM element-filter objects could not be created.")
    prepared = assign_prepared_label(operation, prepared)
    result_mesh.Label = f"{source.Label} (filtered)"
    result_mesh.FemMesh = filtered
    operation.FemMesh = result_mesh
    operation.Elements = [prepared.type_marker, *prepared.remaining_ids]
    replaced = (source,) if prepared.target.source_visible else ()
    publish_operation(
        document,
        prepared.boundary,
        operation,
        (result_mesh,),
        replaced,
    )
    source.ViewObject.Visibility = False
    result_mesh.ViewObject.Visibility = True
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "operation": operation,
            "result_mesh": result_mesh,
        },
        recompute_targets=(operation, result_mesh),
        created=(object_identity(result_mesh), object_identity(operation)),
        replaced=(object_identity(source),) if replaced else (),
    )


def verify_erased_elements(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    operation = draft.value["operation"]
    result_mesh = draft.value["result_mesh"]
    state = mesh_filter_state(operation)
    mesh_state = fem_mesh_object_state(result_mesh)
    checks = {
        "operation label": str(operation.Label) == prepared.label,
        "linked result": getattr(operation, "FemMesh", None) is result_mesh,
        "primary marker": state["primary_type_marker"] == prepared.type_marker,
        "remaining count": state["remaining_element_count"] == len(prepared.remaining_ids),
        "result topology": mesh_state["generated"],
        "source unchanged": fem_mesh_object_state(prepared.target.mesh)["state_sha256"]
        == prepared.target.expected_state_sha256,
        "presentation": not bool(prepared.target.mesh.ViewObject.Visibility)
        and bool(result_mesh.ViewObject.Visibility),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "Erase Elements failed its exact postcondition: " + ", ".join(failures) + "."
        )
    return {
        "created_filter": state,
        "removed": {
            "element_kind": prepared.target.element_kind,
            "count": len(prepared.removed_ids),
        },
        "result_mesh": mesh_state,
    }


def prepare_fem_surface_conversion(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    label: Any,
    result: Any = None,
) -> PreparedFemSurfaceConversion:
    prepared = prepare_fem_mesh_object_target(document, document_uid, target)
    topology = fem_mesh_object_state(prepared.mesh)["topology"]
    if int(topology["volumes"]) < 1 and int(topology["faces"]) < 1:
        raise NativeAnalyzeError(
            "FEM surface conversion requires at least one volume or face element."
        )
    try:
        from femmesh.femmesh2mesh import femmesh_exterior_faces

        exterior_faces = femmesh_exterior_faces(prepared.mesh.FemMesh)
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM exterior could not be resolved: {exc}"
        ) from exc
    surface_node_ids = tuple(
        sorted({node_id for face in exterior_faces for node_id in face})
    )
    displacement = (
        prepare_fem_displacement(
            document,
            document_uid,
            result,
            prepared.mesh,
            surface_node_ids,
        )
        if result is not None
        else None
    )
    return PreparedFemSurfaceConversion(
        prepared,
        creation_boundary(document),
        _label(label),
        len(exterior_faces),
        sum(2 if len(face) == 4 else 1 for face in exterior_faces),
        displacement,
    )


def _add_conversion_provenance(result: Any, prepared: PreparedFemSurfaceConversion) -> None:
    properties = set(result.PropertiesList)
    definitions = (
        (
            "App::PropertyLink",
            "FemSource",
            "Source FEM mesh whose exterior was converted",
        ),
        (
            "App::PropertyLink",
            "FemResultSource",
            "Mechanical result used to deform the converted exterior",
        ),
        (
            "App::PropertyString",
            "ConversionMode",
            "Whether the exterior is undeformed or result-deformed",
        ),
        (
            "App::PropertyFloat",
            "DisplacementScale",
            "Scale applied to the mechanical-result displacement vectors",
        ),
    )
    for property_type, name, description in definitions:
        if name not in properties:
            result.addProperty(property_type, name, "FEM Conversion", description)
    result.FemSource = prepared.target.mesh
    result.FemResultSource = (
        prepared.displacement.result if prepared.displacement is not None else None
    )
    result.ConversionMode = (
        "result_deformed" if prepared.displacement is not None else "undeformed"
    )
    result.DisplacementScale = 1.0 if prepared.displacement is not None else 0.0
    for _property_type, name, _description in definitions:
        result.setEditorMode(name, 1)


def create_fem_surface_conversion(
    document: Any,
    prepared: PreparedFemSurfaceConversion,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedFemSurfaceConversion):
        raise TypeError("prepared must be PreparedFemSurfaceConversion")
    require_boundary(document, prepared.boundary)
    if not fem_mesh_object_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM mesh changed after surface-conversion preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if (
        prepared.displacement is not None
        and not fem_displacement_still_exact(prepared.displacement)
    ):
        raise NativeAnalyzeError(
            "The exact mechanical displacement result changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    try:
        import Mesh
        from femmesh.femmesh2mesh import femmesh_surface_triangles

        triangles = femmesh_surface_triangles(
            prepared.target.mesh.FemMesh,
            prepared.displacement.result if prepared.displacement is not None else None,
            1.0,
        )
        converted_mesh = Mesh.Mesh(triangles)
        geometry_sha256 = mesh_geometry_sha256(converted_mesh)
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM exterior surface could not be converted: {exc}"
        ) from exc
    if int(converted_mesh.CountFacets) != prepared.expected_facet_count:
        raise NativeAnalyzeError(
            "FEM surface conversion produced an unexpected number of Mesh facets."
        )
    result = document.addObject("Mesh::Feature", document.getUniqueObjectName("Mesh"))
    if result is None:
        raise NativeAnalyzeError("The converted Mesh feature could not be created.")
    prepared = assign_prepared_label(result, prepared)
    result.Mesh = converted_mesh
    _add_conversion_provenance(result, prepared)
    replaced = (prepared.target.mesh,) if prepared.target.source_visible else ()
    publish_operation(document, prepared.boundary, result, (), replaced)
    prepared.target.mesh.ViewObject.Visibility = False
    result.ViewObject.Visibility = True
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "result": result,
            "geometry_sha256": geometry_sha256,
        },
        recompute_targets=(result,),
        created=(object_identity(result),),
        replaced=(object_identity(prepared.target.mesh),) if replaced else (),
    )


def verify_fem_surface_conversion(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["result"]
    state = mesh_object_state(result)
    checks = {
        "type": str(result.TypeId) == "Mesh::Feature",
        "label": str(result.Label) == prepared.label,
        "facets": int(state.get("topology", {}).get("facets", 0))
        == prepared.expected_facet_count,
        "geometry": mesh_geometry_sha256(result.Mesh) == draft.value["geometry_sha256"],
        "source unchanged": fem_mesh_object_state(prepared.target.mesh)["state_sha256"]
        == prepared.target.expected_state_sha256,
        "presentation": not bool(prepared.target.mesh.ViewObject.Visibility)
        and bool(result.ViewObject.Visibility),
        "history role": str(getattr(result, "VibeCADTimelineRole", "") or "")
        == "operation",
        "history root": getattr(result, "VibeCADTimelineOwner", None) is None,
        "mesh provenance": getattr(result, "FemSource", None) is prepared.target.mesh,
        "result provenance": getattr(result, "FemResultSource", None)
        is (
            prepared.displacement.result
            if prepared.displacement is not None
            else None
        ),
        "conversion mode": str(getattr(result, "ConversionMode", ""))
        == ("result_deformed" if prepared.displacement is not None else "undeformed"),
        "displacement scale": float(getattr(result, "DisplacementScale", -1.0))
        == (1.0 if prepared.displacement is not None else 0.0),
        "displacement state": prepared.displacement is None
        or fem_displacement_data_still_exact(prepared.displacement),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "FEM surface conversion failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    response = {
        "created_mesh": state,
        "conversion": {
            "mode": "result_deformed"
            if prepared.displacement is not None
            else "undeformed",
            "exterior_face_count": prepared.exterior_face_count,
            "mesh_facet_count": prepared.expected_facet_count,
        },
        "source_fem_mesh": {
            "object_name": str(prepared.target.mesh.Name),
            "state_sha256": prepared.target.expected_state_sha256,
        },
    }
    if prepared.displacement is not None:
        response["source_fem_result"] = prepared.displacement.response()
    return response
