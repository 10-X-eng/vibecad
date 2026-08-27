# SPDX-License-Identifier: LGPL-2.1-or-later

"""Retained, camera-independent Mesh polygon cuts and trims."""

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


_ACTION = {"poly_cut": "Cut Facets", "poly_trim": "Trim Facets"}
_OPERATION_KIND = {"poly_cut": "Polygon cut", "poly_trim": "Polygon trim"}


@dataclass(frozen=True, slots=True)
class PreparedMeshPolygon:
    operation: str
    target: PreparedMeshTarget
    polygon: tuple[tuple[float, float, float], ...]
    regions: tuple[str, ...]
    labels: tuple[str, ...]
    expected_result_sha256: tuple[str, ...]
    result_mode: str
    accepted_meshes: tuple[Any, ...] = ()


def _label(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError(f"{field} must contain 1 to 160 visible characters.")
    return result


def _finite(value: Any, field: str) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise NativeMeshError(f"{field} must be one finite number.")
    return result


def _polygon(value: Any) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(value, list) or not 3 <= len(value) <= 256:
        raise NativeMeshError("polygon must contain 3 to 256 ordered model-space vertices.")
    points = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {"x_mm", "y_mm", "z_mm"}:
            raise NativeMeshError(
                f"polygon[{index}] must contain only x_mm, y_mm, and z_mm."
            )
        points.append(
            (
                _finite(item["x_mm"], f"polygon[{index}].x_mm"),
                _finite(item["y_mm"], f"polygon[{index}].y_mm"),
                _finite(item["z_mm"], f"polygon[{index}].z_mm"),
            )
        )
    if points[0] == points[-1]:
        raise NativeMeshError(
            "Do not repeat the first polygon vertex at the end; closure is implicit."
        )
    if len(points) != len(set(points)):
        raise NativeMeshError("polygon vertices must be distinct.")

    # Newell's method gives a stable normal for arbitrary ordered planar polygons.
    nx = ny = nz = 0.0
    for first, second in zip(points, points[1:] + points[:1]):
        nx += (first[1] - second[1]) * (first[2] + second[2])
        ny += (first[2] - second[2]) * (first[0] + second[0])
        nz += (first[0] - second[0]) * (first[1] + second[1])
    normal_length = math.sqrt(nx * nx + ny * ny + nz * nz)
    extent = max(
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    )
    if normal_length <= max(1.0e-12, extent * extent * 1.0e-12):
        raise NativeMeshError("polygon vertices are collinear or have negligible area.")
    normal = (nx / normal_length, ny / normal_length, nz / normal_length)
    origin = points[0]
    tolerance = max(1.0e-7, extent * 1.0e-8)
    if any(
        abs(
            (point[0] - origin[0]) * normal[0]
            + (point[1] - origin[1]) * normal[1]
            + (point[2] - origin[2]) * normal[2]
        )
        > tolerance
        for point in points[1:]
    ):
        raise NativeMeshError(
            "polygon vertices must be coplanar in document coordinates."
        )
    return tuple(points)


def _result_spec(value: Any) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise NativeMeshError("result must identify one published polygon result mode.")
    mode = str(value.get("mode") or "")
    if mode in {"remove_inside", "remove_outside"}:
        if set(value) != {"mode", "result_label"}:
            raise NativeMeshError(f"{mode} requires only mode and result_label.")
        region = "Inside" if mode == "remove_inside" else "Outside"
        return mode, (region,), (_label(value["result_label"], "result_label"),)
    if mode == "split":
        if set(value) != {"mode", "inside_result_label", "outside_result_label"}:
            raise NativeMeshError(
                "split requires mode, inside_result_label, and outside_result_label."
            )
        # Keeping inside removes outside; keeping outside removes inside.
        return (
            mode,
            ("Outside", "Inside"),
            (
                _label(value["inside_result_label"], "inside_result_label"),
                _label(value["outside_result_label"], "outside_result_label"),
            ),
        )
    raise NativeMeshError("result.mode must be remove_inside, remove_outside, or split.")


def prepare_mesh_polygon(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedMeshPolygon:
    if operation not in _ACTION:
        raise NativeMeshError("The requested Mesh polygon operation is unavailable.")
    target = prepare_mesh_target(
        document, document_uid, values["target"], require_label=False
    )
    polygon = _polygon(values["polygon"])
    result_mode, regions, labels = _result_spec(values["result"])
    return PreparedMeshPolygon(
        operation,
        target,
        polygon,
        regions,
        labels,
        (),
        result_mode,
    )


def create_mesh_polygon(
    document: Any,
    prepared: PreparedMeshPolygon,
) -> NativeMutationDraft:
    if not mesh_target_still_exact(document, prepared.target):
        raise NativeMeshError(
            "The exact Mesh changed after polygon preflight.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    import FreeCAD as App
    import Mesh  # noqa: F401 - registers Mesh::PolygonEdit
    import MeshGui

    if (
        len(prepared.accepted_meshes) != len(prepared.regions)
        or len(prepared.expected_result_sha256) != len(prepared.regions)
    ):
        raise NativeMeshError("The accepted polygon results are incomplete.")

    results = []
    for region, label, accepted in zip(
        prepared.regions,
        prepared.labels,
        prepared.accepted_meshes,
        strict=True,
    ):
        result = document.addObject(
            "Mesh::PolygonEdit",
            document.getUniqueObjectName(
                "PolygonCut" if prepared.operation == "poly_cut" else "PolygonTrim"
            ),
        )
        if result is None or str(getattr(result, "TypeId", "")) != "Mesh::PolygonEdit":
            raise NativeMeshError("The retained Mesh polygon operation could not be created.")
        result.Label = label
        result.Source = prepared.target.source
        result.Polygon = [App.Vector(*point) for point in prepared.polygon]
        result.Action = _ACTION[prepared.operation]
        result.Region = region
        result.UpdateFromSource = False
        result.Mesh = accepted
        results.append(result)
    group = MeshGui.publishReplacingOutputs(
        str(document.Name),
        [prepared.target.source] * len(results),
        results,
        "PolygonResults",
        _OPERATION_KIND[prepared.operation].title(),
        _OPERATION_KIND[prepared.operation],
    )
    created = [*results, *([group] if group is not None else [])]
    return NativeMutationDraft(
        value={"prepared": prepared, "results": tuple(results), "group": group},
        recompute_targets=tuple(created),
        created=tuple(object_identity(obj) for obj in created),
        replaced=(object_identity(prepared.target.source),),
    )


def _history_exact(
    document: Any,
    prepared: PreparedMeshPolygon,
    results: tuple[Any, ...],
    group: Any | None,
) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return False
    operations = list(getattr(timeline, "Operations", ()) or ())
    replaced = (prepared.target.source,) if prepared.target.source_visible else ()
    if group is None:
        return (
            len(results) == 1
            and operations.count(results[0]) == 1
            and str(getattr(results[0], "VibeCADTimelineRole", "") or "") == "operation"
            and tuple(getattr(results[0], "VibeCADTimelineReplacedInputs", ()) or ())
            == replaced
        )
    return (
        is_live(document, group)
        and str(getattr(group, "TypeId", "")) == "Mesh::OutputGroup"
        and operations.count(group) == 1
        and str(getattr(group, "VibeCADTimelineRole", "") or "") == "operation"
        and tuple(getattr(group, "Sources", ()) or ()) == (prepared.target.source,)
        and tuple(getattr(group, "Group", ()) or ()) == results
        and str(getattr(group, "InputMode", "") or "") == "Replacement"
        and str(getattr(group, "OperationKind", "") or "")
        == _OPERATION_KIND[prepared.operation]
        and tuple(getattr(group, "VibeCADTimelineReplacedInputs", ()) or ()) == replaced
        and all(
            str(getattr(result, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(result, "VibeCADTimelineOwner", None) is group
            for result in results
        )
    )


def verify_mesh_polygon(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    results = draft.value["results"]
    group = draft.value["group"]
    if not isinstance(prepared, PreparedMeshPolygon) or len(results) != len(prepared.regions):
        raise NativeMeshError("The Mesh polygon operation lost its prepared state.")
    if not _history_exact(document, prepared, results, group):
        raise NativeMeshError("The Mesh polygon operation failed its History postcondition.")
    summaries = []
    for index, (result, region, label, expected) in enumerate(
        zip(results, prepared.regions, prepared.labels, prepared.expected_result_sha256)
    ):
        status = str(getattr(result, "getStatusString", lambda: "")() or "").strip()
        actual_polygon = tuple(
            (float(point.x), float(point.y), float(point.z)) for point in result.Polygon
        )
        if (
            not is_live(document, result)
            or str(getattr(result, "TypeId", "")) != "Mesh::PolygonEdit"
            or result.Source is not prepared.target.source
            or str(result.Action) != _ACTION[prepared.operation]
            or str(result.Region) != region
            or str(result.Label) != label
            or bool(result.UpdateFromSource)
            or actual_polygon != prepared.polygon
            or not bool(result.isValid())
            or int(result.Mesh.CountFacets) < 1
            or mesh_geometry_sha256(result.Mesh) != expected
            or not bool(result.Visibility)
        ):
            raise NativeMeshError(
                status
                if not bool(result.isValid())
                else f"Polygon result {index} failed its exact postcondition."
            )
        summaries.append(mesh_object_state(result))
    if not mesh_target_still_exact(document, prepared.target) or bool(
        prepared.target.source.Visibility
    ):
        raise NativeMeshError("The polygon operation changed or exposed its source Mesh.")
    response = {
        "operation": prepared.operation,
        "result_mode": prepared.result_mode,
        "source": object_reference(prepared.target.source),
        "outputs": summaries,
    }
    if group is not None:
        response["operation_controller"] = object_reference(group)
    return response
