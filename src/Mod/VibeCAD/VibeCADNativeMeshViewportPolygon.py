# SPDX-License-Identifier: LGPL-2.1-or-later

"""Retained results for human viewport-projected Mesh polygon edits."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_geometry_sha256, mesh_object_state
from VibeCADNativeMeshTargets import (
    PreparedMeshTarget,
    is_live,
    mesh_target_still_exact,
    prepare_mesh_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, object_reference


_OPERATIONS = {"viewport_cut": "Polygon cut", "viewport_trim": "Polygon trim"}


@dataclass(frozen=True, slots=True)
class PreparedMeshViewportPolygon:
    operation: str
    targets: tuple[PreparedMeshTarget, ...]
    polygon: tuple[tuple[float, float], ...]
    projection_matrix: tuple[float, ...]
    regions: tuple[str, ...]
    result_targets: tuple[int, ...]
    labels: tuple[str, ...]
    expected_result_sha256: tuple[str, ...] = ()
    accepted_meshes: tuple[Any, ...] = ()


def _finite(value: Any, field: str) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be finite.")
    result = float(value)
    if not math.isfinite(result):
        raise NativeMeshError(f"{field} must be finite.")
    return result


def _polygon(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or len(value) < 3:
        raise NativeMeshError("The viewport polygon needs at least three points.")
    points = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise NativeMeshError(f"viewport_polygon[{index}] must contain x and y.")
        points.append(
            (
                _finite(point[0], f"viewport_polygon[{index}].x"),
                _finite(point[1], f"viewport_polygon[{index}].y"),
            )
        )
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or len(set(points)) < 3:
        raise NativeMeshError("The viewport polygon needs three distinct points.")
    return tuple(points)


def _projection(value: Any) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != 16:
        raise NativeMeshError("The viewport projection must contain 16 matrix values.")
    return tuple(_finite(component, "projection_matrix") for component in value)


def prepare_mesh_viewport_polygon(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedMeshViewportPolygon:
    if operation not in _OPERATIONS:
        raise NativeMeshError("The viewport Mesh polygon operation is unavailable.")
    references = values.get("targets")
    if not isinstance(references, list) or not references:
        raise NativeMeshError("Select at least one Mesh for the viewport polygon edit.")
    targets = tuple(
        prepare_mesh_target(document, document_uid, reference, require_label=False)
        for reference in references
    )
    if len({target.source.Name for target in targets}) != len(targets):
        raise NativeMeshError("Each viewport Mesh target must be unique.")
    mode = str(values.get("mode") or "")
    if mode == "remove_inside":
        regions = ("inside",)
        suffixes = ("",)
    elif mode == "remove_outside":
        regions = ("outside",)
        suffixes = ("",)
    elif mode == "split":
        regions = ("outside", "inside")
        suffixes = (" Inside", " Outside")
    else:
        raise NativeMeshError("The viewport result mode is invalid.")
    result_targets = tuple(
        target_index
        for target_index in range(len(targets))
        for _region in regions
    )
    labels = tuple(
        f"{targets[target_index].source.Label} {_OPERATIONS[operation]}{suffix}"
        for target_index in range(len(targets))
        for suffix in suffixes
    )
    return PreparedMeshViewportPolygon(
        operation=operation,
        targets=targets,
        polygon=_polygon(values.get("polygon")),
        projection_matrix=_projection(values.get("projection_matrix")),
        regions=regions * len(targets),
        result_targets=result_targets,
        labels=labels,
    )


def create_mesh_viewport_polygon(
    document: Any,
    prepared: PreparedMeshViewportPolygon,
) -> NativeMutationDraft:
    if not all(mesh_target_still_exact(document, target) for target in prepared.targets):
        raise NativeMeshError(
            "A viewport Mesh target changed while the edit was running.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    if (
        len(prepared.accepted_meshes) != len(prepared.result_targets)
        or len(prepared.expected_result_sha256) != len(prepared.result_targets)
    ):
        raise NativeMeshError("The accepted viewport polygon results are incomplete.")
    import Mesh  # noqa: F401 - registers Mesh::StoredEdit
    import MeshGui

    results = []
    sources = []
    for target_index, label, accepted in zip(
        prepared.result_targets,
        prepared.labels,
        prepared.accepted_meshes,
        strict=True,
    ):
        source = prepared.targets[target_index].source
        result = document.addObject(
            "Mesh::StoredEdit",
            document.getUniqueObjectName(
                "PolygonCut" if prepared.operation == "viewport_cut" else "PolygonTrim"
            ),
        )
        if result is None or str(getattr(result, "TypeId", "")) != "Mesh::StoredEdit":
            raise NativeMeshError("The retained viewport Mesh edit could not be created.")
        result.Label = label
        result.Source = source
        result.AcceptedSource = source.Mesh
        result.AcceptedResult = accepted
        result.EditKind = _OPERATIONS[prepared.operation]
        result.Mesh = accepted
        sources.append(source)
        results.append(result)
    group = MeshGui.publishReplacingOutputs(
        str(document.Name),
        sources,
        results,
        "ViewportPolygonResults",
        _OPERATIONS[prepared.operation].title(),
        _OPERATIONS[prepared.operation],
    )
    created = [*results, *([group] if group is not None else [])]
    return NativeMutationDraft(
        value={"prepared": prepared, "results": tuple(results), "group": group},
        recompute_targets=tuple(created),
        created=tuple(object_identity(obj) for obj in created),
        replaced=tuple(object_identity(target.source) for target in prepared.targets),
    )


def verify_mesh_viewport_polygon(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared")
    results = tuple(draft.value.get("results") or ())
    group = draft.value.get("group")
    if not isinstance(prepared, PreparedMeshViewportPolygon) or len(results) != len(
        prepared.result_targets
    ):
        raise NativeMeshError("The viewport Mesh edit lost its prepared state.")
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", ()) or ()) if timeline else []
    if group is None:
        history_valid = len(results) == 1 and operations.count(results[0]) == 1
    else:
        history_valid = (
            is_live(document, group)
            and operations.count(group) == 1
            and tuple(getattr(group, "Group", ()) or ()) == results
            and all(getattr(result, "VibeCADTimelineOwner", None) is group for result in results)
        )
    if not history_valid:
        raise NativeMeshError("The viewport Mesh edit failed its History postcondition.")
    summaries = []
    for result, target_index, label, expected in zip(
        results,
        prepared.result_targets,
        prepared.labels,
        prepared.expected_result_sha256,
        strict=True,
    ):
        target = prepared.targets[target_index]
        if (
            not is_live(document, result)
            or str(getattr(result, "TypeId", "")) != "Mesh::StoredEdit"
            or result.Source is not target.source
            or str(result.Label) != label
            or mesh_geometry_sha256(result.AcceptedSource) != target.source_geometry_sha256
            or mesh_geometry_sha256(result.AcceptedResult) != expected
            or mesh_geometry_sha256(result.Mesh) != expected
            or int(result.Mesh.CountFacets) < 1
            or not bool(result.isValid())
            or not bool(result.Visibility)
        ):
            raise NativeMeshError("A viewport Mesh result failed its exact postcondition.")
        summaries.append(mesh_object_state(result))
    if not all(mesh_target_still_exact(document, target) for target in prepared.targets):
        raise NativeMeshError("A viewport Mesh source changed during publication.")
    return {
        "operation": prepared.operation,
        "sources": [object_reference(target.source) for target in prepared.targets],
        "outputs": summaries,
        **(
            {"operation_controller": object_reference(group)}
            if group is not None
            else {}
        ),
    }
