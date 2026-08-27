# SPDX-License-Identifier: LGPL-2.1-or-later

"""Creation and exact proof for retained Mesh segment operations."""

from __future__ import annotations

from typing import Any

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshSegments import PreparedMeshSegment, PreparedSegmentOutput
from VibeCADNativeMeshState import mesh_geometry_sha256, mesh_object_state
from VibeCADNativeMeshTargets import is_live, mesh_target_still_exact
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, object_reference


_OPERATION_LABELS = {
    "split_components": "Split connected components",
    "mesh_segmentation": "Curvature segmentation",
    "segmentation_best_fit": "Best-fit segmentation",
    "reverse_segmentation": "Planar surface segmentation",
    "segmentation_manual": "Manual facet segmentation",
    "segmentation_from_components": "Segment connected components",
}


def _create_subset(
    document: Any,
    output: PreparedSegmentOutput,
    accepted_mesh: Any | None = None,
) -> Any:
    result = document.addObject(
        "Mesh::FacetSubset",
        document.getUniqueObjectName("MeshSegment"),
    )
    if result is None or str(getattr(result, "TypeId", "")) != "Mesh::FacetSubset":
        raise NativeMeshError("A retained Mesh facet subset could not be created.")
    result.Label = output.label
    result.Source = output.target.source
    result.FacetIndices = list(output.facet_indices)
    result.AcceptedTopology = output.target.source.Mesh
    result.SelectionKind = output.kind
    if accepted_mesh is not None:
        result.AcceptedResult = accepted_mesh
        result.AcceptedSourceRevision = str(output.target.source_geometry_revision)
        result.AcceptedSourceStale = False
        result.Mesh = accepted_mesh
        result.UpdateFromSource = False
    return result


def _create_boundary(
    document: Any,
    target: Any,
    label: str,
    *,
    facet_indices: tuple[int, ...] = (),
    make_faces: bool,
    accepted_shape: Any | None = None,
) -> Any:
    result = document.addObject(
        "MeshPart::Boundary",
        document.getUniqueObjectName("MeshBoundary"),
    )
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::Boundary":
        raise NativeMeshError("A retained Mesh boundary could not be created.")
    result.Label = label
    result.Source = target.source
    result.FacetIndices = list(facet_indices)
    if facet_indices:
        result.AcceptedTopology = target.source.Mesh
    result.MakeFaces = make_faces
    if accepted_shape is not None:
        result.Shape = accepted_shape
        result.UpdateFromSource = False
    return result


def _publish_outputs(
    document: Any,
    prepared: PreparedMeshSegment,
    outputs: list[Any],
    paired_targets: list[Any],
    *,
    source_preserving: bool,
) -> Any | None:
    import MeshGui

    sources = [target.source for target in paired_targets]
    object_name = "MeshSegmentResults"
    label = _OPERATION_LABELS.get(prepared.operation, "Mesh Segments").title()
    operation_kind = _OPERATION_LABELS.get(prepared.operation, prepared.operation)
    if source_preserving:
        return MeshGui.publishSourcePreservingOutputs(
            str(document.Name),
            sources,
            outputs,
            object_name,
            label,
            operation_kind,
        )
    return MeshGui.publishReplacingOutputs(
        str(document.Name),
        sources,
        outputs,
        object_name,
        label,
        operation_kind,
    )


def create_mesh_segment(document: Any, prepared: PreparedMeshSegment) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshSegment):
        raise TypeError("prepared must be a PreparedMeshSegment")
    if any(not mesh_target_still_exact(document, target) for target in prepared.targets):
        raise NativeMeshError(
            "An exact Mesh changed after segmentation preflight.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    import Mesh  # noqa: F401 - registers retained Mesh operations
    import MeshGui
    import MeshPart  # noqa: F401 - registers retained MeshPart boundaries

    if prepared.operation == "split_components" and not prepared.outputs:
        return NativeMutationDraft(
            value={
                "prepared": prepared,
                "results": (),
                "result_labels": (),
                "group": None,
                "boundaries": (),
                "boundary_labels": (),
            },
        )

    if prepared.operation == "merge":
        if len(prepared.accepted_meshes) not in {0, 1}:
            raise NativeMeshError("The accepted Mesh merge artifact is incomplete.")
        result = document.addObject(
            "Mesh::Merge",
            document.getUniqueObjectName("MeshMerge"),
        )
        if result is None or str(getattr(result, "TypeId", "")) != "Mesh::Merge":
            raise NativeMeshError("The retained Mesh merge could not be created.")
        result.Label = prepared.settings["result_label"]
        result.Sources = [target.source for target in prepared.targets]
        if prepared.accepted_meshes:
            result.AcceptedResult = prepared.accepted_meshes[0]
            result.AcceptedSourceRevisions = [
                str(target.source_geometry_revision) for target in prepared.targets
            ]
            result.AcceptedSourcePlacements = [
                target.source.Placement for target in prepared.targets
            ]
            result.AcceptedSourcesStale = False
            result.UpdateFromSource = False
        MeshGui.publishReplacingOperation(
            str(document.Name),
            [target.source for target in prepared.targets],
            result,
        )
        return NativeMutationDraft(
            value={
                "prepared": prepared,
                "results": (result,),
                "result_labels": (str(result.Label),),
                "group": None,
                "boundaries": (),
                "boundary_labels": (),
            },
            recompute_targets=(result,),
            created=(object_identity(result),),
            replaced=tuple(object_identity(target.source) for target in prepared.targets),
        )

    if prepared.operation == "mesh_boundary":
        if prepared.accepted_shapes and len(prepared.accepted_shapes) != len(prepared.targets):
            raise NativeMeshError("The accepted Mesh boundary artifacts are incomplete.")
        results = [
            _create_boundary(
                document,
                target,
                target.label,
                make_faces=bool(prepared.settings["make_faces"]),
                accepted_shape=(
                    prepared.accepted_shapes[index]
                    if prepared.accepted_shapes
                    else None
                ),
            )
            for index, target in enumerate(prepared.targets)
        ]
        group = _publish_outputs(
            document,
            prepared,
            results,
            list(prepared.targets),
            source_preserving=True,
        )
        created_objects = [*results, *([group] if group is not None else [])]
        return NativeMutationDraft(
            value={
                "prepared": prepared,
                "results": tuple(results),
                "result_labels": tuple(str(result.Label) for result in results),
                "group": group,
                "boundaries": (),
                "boundary_labels": (),
            },
            recompute_targets=tuple(created_objects),
            created=tuple(object_identity(result) for result in created_objects),
        )

    if prepared.accepted_meshes and len(prepared.accepted_meshes) != len(prepared.outputs):
        raise NativeMeshError("The accepted Mesh segment artifacts are incomplete.")
    results = [
        _create_subset(
            document,
            output,
            prepared.accepted_meshes[index] if prepared.accepted_meshes else None,
        )
        for index, output in enumerate(prepared.outputs)
    ]
    paired_targets = [output.target for output in prepared.outputs]
    boundaries = []
    if prepared.operation == "reverse_segmentation" and prepared.settings[
        "create_boundary_faces"
    ]:
        expected_shapes = sum(
            1 for output in prepared.outputs if output.kind != "Unused facets"
        )
        if prepared.accepted_shapes and len(prepared.accepted_shapes) != expected_shapes:
            raise NativeMeshError("The accepted planar boundary artifacts are incomplete.")
        shape_index = 0
        for output in prepared.outputs:
            if output.kind == "Unused facets":
                continue
            boundaries.append(
                _create_boundary(
                    document,
                    output.target,
                    f"{output.label} Boundary",
                    facet_indices=output.facet_indices,
                    make_faces=True,
                    accepted_shape=(
                        prepared.accepted_shapes[shape_index]
                        if prepared.accepted_shapes
                        else None
                    ),
                )
            )
            shape_index += 1
            paired_targets.append(output.target)

    all_outputs = [*results, *boundaries]
    source_preserving = (
        prepared.operation == "segmentation_manual"
        and prepared.settings["mode"] == "extract"
    )
    group = _publish_outputs(
        document,
        prepared,
        all_outputs,
        paired_targets,
        source_preserving=source_preserving,
    )
    created_objects = [*all_outputs, *([group] if group is not None else [])]
    replaced_targets = () if source_preserving else prepared.targets
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "results": tuple(results),
            "result_labels": tuple(str(result.Label) for result in results),
            "group": group,
            "boundaries": tuple(boundaries),
            "boundary_labels": tuple(str(boundary.Label) for boundary in boundaries),
        },
        recompute_targets=tuple(created_objects),
        created=tuple(object_identity(result) for result in created_objects),
        replaced=tuple(object_identity(target.source) for target in replaced_targets),
    )


def _history_postcondition(
    document: Any,
    prepared: PreparedMeshSegment,
    resources: tuple[Any, ...],
    group: Any | None,
    *,
    source_preserving: bool,
) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return False
    operations = list(getattr(timeline, "Operations", ()) or ())
    unique_target_values = []
    seen_sources = set()
    for output in prepared.outputs:
        source_name = str(output.target.source.Name)
        if source_name not in seen_sources:
            seen_sources.add(source_name)
            unique_target_values.append(output.target)
    unique_targets = tuple(unique_target_values)
    if prepared.operation == "mesh_boundary":
        unique_targets = prepared.targets
    expected_sources = tuple(target.source for target in unique_targets)
    if group is None:
        expected_replaced = ()
        if not source_preserving:
            expected_replaced = tuple(
                target.source for target in unique_targets if target.source_visible
            )
        return (
            len(resources) == 1
            and operations.count(resources[0]) == 1
            and str(getattr(resources[0], "VibeCADTimelineRole", "") or "") == "operation"
            and getattr(resources[0], "VibeCADTimelineOwner", None) is None
            and tuple(getattr(resources[0], "VibeCADTimelineReplacedInputs", ()) or ())
            == expected_replaced
        )
    expected_replaced = () if source_preserving else tuple(
        target.source for target in unique_targets if target.source_visible
    )
    return (
        is_live(document, group)
        and str(getattr(group, "TypeId", "")) == "Mesh::OutputGroup"
        and operations.count(group) == 1
        and str(getattr(group, "VibeCADTimelineRole", "") or "") == "operation"
        and getattr(group, "VibeCADTimelineOwner", None) is None
        and tuple(getattr(group, "Sources", ()) or ()) == expected_sources
        and tuple(getattr(group, "Group", ()) or ()) == resources
        and str(getattr(group, "InputMode", "") or "")
        == ("Source preserving" if source_preserving else "Replacement")
        and tuple(getattr(group, "VibeCADTimelineReplacedInputs", ()) or ())
        == expected_replaced
        and all(
            str(getattr(resource, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(resource, "VibeCADTimelineOwner", None) is group
            for resource in resources
        )
    )


def _verify_merge(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["results"][0]
    result_label = draft.value["result_labels"][0]
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", ()) or ()) if timeline else []
    mesh = getattr(result, "Mesh", None)
    expected_replaced = tuple(
        target.source for target in prepared.targets if target.source_visible
    )
    detached = bool(prepared.accepted_meshes)
    if (
        not is_live(document, result)
        or str(getattr(result, "TypeId", "")) != "Mesh::Merge"
        or tuple(getattr(result, "Sources", ()) or ())
        != tuple(target.source for target in prepared.targets)
        or str(getattr(result, "Label", "")) != result_label
        or bool(result.UpdateFromSource) is detached
        or not bool(result.isValid())
        or int(getattr(mesh, "CountFacets", 0) or 0) < 1
        or timeline is None
        or operations.count(result) != 1
        or str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != expected_replaced
        or any(not mesh_target_still_exact(document, target) for target in prepared.targets)
        or any(bool(target.source.Visibility) for target in prepared.targets)
    ):
        raise NativeMeshError("The retained Mesh merge failed its exact postcondition.")
    return {
        "operation": "merge",
        "source_count": len(prepared.targets),
        "result": mesh_object_state(result),
        "result_geometry_sha256": mesh_geometry_sha256(mesh),
        "updates_from_source": not detached,
    }


def verify_mesh_segment(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    if not isinstance(prepared, PreparedMeshSegment):
        raise NativeMeshError("The Mesh segment operation lost its prepared state.")
    if prepared.operation == "merge":
        return _verify_merge(document, draft)
    if prepared.operation == "split_components" and not prepared.outputs:
        if any(not mesh_target_still_exact(document, target) for target in prepared.targets):
            raise NativeMeshError("An exact Mesh changed while verifying segmentation.")
        return {
            "operation": prepared.operation,
            "changed": False,
            "source_count": len(prepared.targets),
            "result_count": 0,
            "results": [],
            "sources": [object_reference(target.source) for target in prepared.targets],
            "unchanged": [mesh_object_state(target.source) for target in prepared.targets],
        }
    results = draft.value["results"]
    result_labels = draft.value["result_labels"]
    boundaries = draft.value["boundaries"]
    boundary_labels = draft.value["boundary_labels"]
    group = draft.value["group"]
    resources = (*results, *boundaries)
    source_preserving = prepared.operation == "mesh_boundary" or (
        prepared.operation == "segmentation_manual"
        and prepared.settings["mode"] == "extract"
    )
    detached_meshes = bool(prepared.accepted_meshes)
    detached_shapes = bool(prepared.accepted_shapes)
    if len(results) != (len(prepared.targets) if prepared.operation == "mesh_boundary" else len(prepared.outputs)):
        raise NativeMeshError("The Mesh segment operation returned the wrong result count.")
    if not _history_postcondition(
        document,
        prepared,
        resources,
        group,
        source_preserving=source_preserving,
    ):
        raise NativeMeshError("The Mesh segment operation failed its exact History postcondition.")

    summaries = []
    if prepared.operation == "mesh_boundary":
        boundary_pairs = zip(prepared.targets, results, result_labels)
    else:
        boundary_pairs = ()
        for output, result, result_label in zip(
            prepared.outputs,
            results,
            result_labels,
        ):
            mesh = getattr(result, "Mesh", None)
            status = str(getattr(result, "getStatusString", lambda: "")() or "").strip()
            if not is_live(document, result):
                raise NativeMeshError("A retained Mesh segment is no longer in the document.")
            if str(getattr(result, "TypeId", "")) != "Mesh::FacetSubset":
                raise NativeMeshError("A retained Mesh segment has the wrong object type.")
            if getattr(result, "Source", None) is not output.target.source:
                raise NativeMeshError("A retained Mesh segment lost its exact source.")
            if tuple(int(value) for value in result.FacetIndices) != output.facet_indices:
                raise NativeMeshError("A retained Mesh segment changed its accepted facets.")
            if str(result.SelectionKind) != output.kind:
                raise NativeMeshError("A retained Mesh segment changed its surface kind.")
            if str(result.Label) != result_label:
                raise NativeMeshError("A retained Mesh segment changed its label.")
            if (
                mesh_geometry_sha256(result.AcceptedTopology)
                != output.target.source_geometry_sha256
            ):
                raise NativeMeshError("A retained Mesh segment lost its accepted topology.")
            if not bool(result.isValid()):
                raise NativeMeshError(status or "A retained Mesh segment is invalid.")
            if bool(result.UpdateFromSource) is detached_meshes:
                raise NativeMeshError("A retained Mesh segment changed its update mode.")
            if int(getattr(mesh, "CountFacets", 0) or 0) != len(output.facet_indices):
                raise NativeMeshError("A retained Mesh segment has the wrong facet count.")
            summaries.append(mesh_object_state(result))

    for target, boundary, boundary_label in boundary_pairs:
        shape = getattr(boundary, "Shape", None)
        if (
            not is_live(document, boundary)
            or str(getattr(boundary, "TypeId", "")) != "MeshPart::Boundary"
            or getattr(boundary, "Source", None) is not target.source
            or tuple(int(value) for value in boundary.FacetIndices)
            or bool(boundary.MakeFaces) is not bool(prepared.settings["make_faces"])
            or str(boundary.Label) != boundary_label
            or bool(boundary.UpdateFromSource) is detached_shapes
            or not bool(boundary.isValid())
            or shape is None
            or bool(shape.isNull())
            or not bool(shape.isValid())
        ):
            raise NativeMeshError("A retained Mesh boundary failed its exact postcondition.")
        summaries.append(mesh_object_state(boundary))

    expected_boundary_outputs = (
        sum(1 for output in prepared.outputs if output.kind != "Unused facets")
        if prepared.operation == "reverse_segmentation"
        and prepared.settings["create_boundary_faces"]
        else 0
    )
    if len(boundaries) != expected_boundary_outputs:
        raise NativeMeshError("The planar boundary output count is incorrect.")
    boundary_index = 0
    for output in prepared.outputs:
        if output.kind == "Unused facets" or expected_boundary_outputs == 0:
            continue
        boundary = boundaries[boundary_index]
        boundary_label = boundary_labels[boundary_index]
        boundary_index += 1
        shape = getattr(boundary, "Shape", None)
        if (
            not is_live(document, boundary)
            or str(getattr(boundary, "TypeId", "")) != "MeshPart::Boundary"
            or getattr(boundary, "Source", None) is not output.target.source
            or tuple(int(value) for value in boundary.FacetIndices) != output.facet_indices
            or mesh_geometry_sha256(boundary.AcceptedTopology)
            != output.target.source_geometry_sha256
            or not bool(boundary.MakeFaces)
            or str(boundary.Label) != boundary_label
            or bool(boundary.UpdateFromSource) is detached_shapes
            or not bool(boundary.isValid())
            or shape is None
            or bool(shape.isNull())
            or not bool(shape.isValid())
        ):
            raise NativeMeshError("A planar segment boundary failed its exact postcondition.")
        summaries.append(mesh_object_state(boundary))

    if any(not mesh_target_still_exact(document, target) for target in prepared.targets):
        raise NativeMeshError("An exact Mesh changed while verifying segmentation.")
    if source_preserving:
        if any(bool(target.source.Visibility) is not target.source_visible for target in prepared.targets):
            raise NativeMeshError("Source-preserving segmentation changed source visibility.")
    elif any(bool(target.source.Visibility) for target in prepared.targets):
        raise NativeMeshError("Replacement segmentation did not hide every source Mesh.")
    response = {
        "operation": prepared.operation,
        "changed": True,
        "source_count": len(prepared.targets),
        "result_count": len(resources),
        "results": summaries,
        "sources": [object_reference(target.source) for target in prepared.targets],
        "updates_from_source": not (detached_meshes or detached_shapes),
    }
    if group is not None:
        response["operation_controller"] = mesh_object_state(group)
    return response
