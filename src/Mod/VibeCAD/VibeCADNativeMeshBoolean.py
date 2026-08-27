# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation, creation, and proof for retained Mesh booleans."""

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


_NATIVE_OPERATIONS = {
    "union": "Union",
    "intersection": "Intersection",
    "difference": "Difference",
}


@dataclass(frozen=True, slots=True)
class PreparedMeshBoolean:
    operation: str
    first: PreparedMeshTarget
    second: PreparedMeshTarget
    result_label: str


def capture_mesh_boolean(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
    *,
    linear_deflection_mm: float = 0.1,
    angular_deflection_radians: float = 0.5,
    relative: bool = False,
) -> Any:
    """Capture exact world-space Mesh snapshots without performing BREP work."""

    if operation not in _NATIVE_OPERATIONS:
        raise NativeMeshError("The requested Mesh boolean is unavailable.")
    first = prepare_mesh_target(
        document, document_uid, values["first"], require_label=False
    )
    second = prepare_mesh_target(
        document, document_uid, values["second"], require_label=False
    )
    if first.source is second.source:
        raise NativeMeshError("first and second must identify two different Meshes.")
    if (
        not math.isfinite(float(linear_deflection_mm))
        or float(linear_deflection_mm) <= 0.0
        or not math.isfinite(float(angular_deflection_radians))
        or not 0.0 < float(angular_deflection_radians) <= math.pi
        or type(relative) is not bool
    ):
        raise NativeMeshError("Mesh boolean tessellation settings are invalid.")
    from VibeCADMeshBooleanJob import make_request

    first_state = mesh_object_state(first.source)
    second_state = mesh_object_state(second.source)
    return make_request(
        operation=operation,
        first=first,
        second=second,
        first_mesh=first.source_mesh,
        second_mesh=second.source_mesh,
        first_placement=dict(first_state.get("placement") or {}),
        second_placement=dict(second_state.get("placement") or {}),
        result_label=_label(values["result_label"]),
        linear_deflection_mm=float(linear_deflection_mm),
        angular_deflection_radians=float(angular_deflection_radians),
        relative=relative,
    )


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError("result_label must contain 1 to 160 visible characters.")
    return result


def _closed_solid(target: PreparedMeshTarget, role: str) -> None:
    try:
        closed = bool(target.source.Mesh.isSolid())
    except Exception as exc:
        raise NativeMeshError(f"The {role} Mesh could not be tested for solidity.") from exc
    if not closed:
        raise NativeMeshError(
            f"The {role} Mesh is not a closed solid; repair or close it before a solid boolean.",
            error_code="NATIVE_MESH_BOOLEAN_SOURCE_NOT_SOLID",
        )


def prepare_mesh_boolean(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedMeshBoolean:
    if operation not in _NATIVE_OPERATIONS:
        raise NativeMeshError("The requested Mesh boolean is unavailable.")
    first = prepare_mesh_target(
        document, document_uid, values["first"], require_label=False
    )
    second = prepare_mesh_target(
        document, document_uid, values["second"], require_label=False
    )
    if first.source is second.source:
        raise NativeMeshError("first and second must identify two different Meshes.")
    _closed_solid(first, "first")
    _closed_solid(second, "second")
    return PreparedMeshBoolean(operation, first, second, _label(values["result_label"]))


def create_mesh_boolean(
    document: Any,
    prepared: PreparedMeshBoolean,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshBoolean):
        raise TypeError("prepared must be a PreparedMeshBoolean")
    if not mesh_target_still_exact(
        document, prepared.first
    ) or not mesh_target_still_exact(document, prepared.second):
        raise NativeMeshError(
            "An exact Mesh changed after boolean preflight.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    import MeshPart  # noqa: F401 - registers MeshPart::Boolean
    import MeshGui

    result = document.addObject(
        "MeshPart::Boolean",
        document.getUniqueObjectName(_NATIVE_OPERATIONS[prepared.operation]),
    )
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::Boolean":
        raise NativeMeshError("The retained Mesh boolean could not be created.")
    result.Label = prepared.result_label
    result.Source1 = prepared.first.source
    result.Source2 = prepared.second.source
    result.Operation = _NATIVE_OPERATIONS[prepared.operation]
    MeshGui.publishReplacingOperation(
        str(document.Name),
        [prepared.first.source, prepared.second.source],
        result,
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
        replaced=(
            object_identity(prepared.first.source),
            object_identity(prepared.second.source),
        ),
    )


def verify_mesh_boolean(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["result"]
    if not isinstance(prepared, PreparedMeshBoolean):
        raise NativeMeshError("The Mesh boolean lost its prepared state.")
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", ()) or ()) if timeline else []
    expected_replaced = tuple(
        target.source
        for target in (prepared.first, prepared.second)
        if target.source_visible
    )
    status = str(getattr(result, "getStatusString", lambda: "")() or "").strip()
    mesh = getattr(result, "Mesh", None)
    try:
        solid = bool(mesh.isSolid())
    except Exception:
        solid = False
    if (
        not is_live(document, result)
        or str(getattr(result, "TypeId", "")) != "MeshPart::Boolean"
        or result.Source1 is not prepared.first.source
        or result.Source2 is not prepared.second.source
        or str(result.Operation) != _NATIVE_OPERATIONS[prepared.operation]
        or str(result.Label) != prepared.result_label
        or not bool(result.isValid())
        or int(getattr(mesh, "CountFacets", 0) or 0) < 1
        or not solid
        or timeline is None
        or operations.count(result) != 1
        or str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != expected_replaced
        or not mesh_target_still_exact(document, prepared.first)
        or not mesh_target_still_exact(document, prepared.second)
        or bool(prepared.first.source.Visibility)
        or bool(prepared.second.source.Visibility)
    ):
        raise NativeMeshError(
            status if not bool(result.isValid()) else "The Mesh boolean failed its exact postcondition."
        )
    return {
        "operation": prepared.operation,
        "first": object_reference(prepared.first.source),
        "second": object_reference(prepared.second.source),
        "result": mesh_object_state(result),
        "result_geometry_sha256": mesh_geometry_sha256(mesh),
        "closed_solid": True,
    }


def commit_prepared_mesh_boolean(document: Any, prepared: Any) -> NativeMutationDraft:
    """Publish one worker-verified Mesh result without repeating its BREP boolean."""

    from VibeCADMeshBooleanJob import PreparedMeshBooleanResult

    if not isinstance(prepared, PreparedMeshBooleanResult):
        raise TypeError("prepared must be a PreparedMeshBooleanResult")
    request = prepared.request
    if not mesh_target_still_exact(
        document, request.first
    ) or not mesh_target_still_exact(document, request.second):
        raise NativeMeshError(
            "An exact Mesh changed while its boolean was being prepared; no stale result was applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    try:
        import Mesh

        output = Mesh.Mesh(prepared.artifact_path)
    except Exception as exc:
        raise NativeMeshError(
            "The verified Mesh boolean artifact could not be imported.",
            error_code="NATIVE_MESH_BOOLEAN_ARTIFACT_INVALID",
        ) from exc
    if (
        int(getattr(output, "CountPoints", 0) or 0) != prepared.points
        or int(getattr(output, "CountFacets", 0) or 0) != prepared.facets
    ):
        raise NativeMeshError(
            "The verified Mesh boolean artifact does not match its authenticated metadata.",
            error_code="NATIVE_MESH_BOOLEAN_ARTIFACT_INVALID",
        )

    import MeshGui
    import MeshPart  # noqa: F401 - registers MeshPart::Boolean

    result = document.addObject(
        "MeshPart::Boolean",
        document.getUniqueObjectName(_NATIVE_OPERATIONS[request.operation]),
    )
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::Boolean":
        raise NativeMeshError("The retained Mesh boolean could not be published.")
    view = getattr(result, "ViewObject", None)
    if view is not None:
        view.Visibility = False
    result.Label = request.result_label
    result.UpdateFromSource = False
    result.Source1 = request.first.source
    result.Source2 = request.second.source
    result.Operation = _NATIVE_OPERATIONS[request.operation]
    result.LinearDeflection = request.linear_deflection_mm
    result.AngularDeflection = request.angular_deflection_radians
    result.Relative = request.relative
    result.Mesh = output
    MeshGui.publishReplacingOperation(
        str(document.Name),
        [request.first.source, request.second.source],
        result,
    )
    if view is not None:
        view.Visibility = True
    return NativeMutationDraft(
        value={"prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
        replaced=(
            object_identity(request.first.source),
            object_identity(request.second.source),
        ),
    )


def verify_prepared_mesh_boolean(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    from VibeCADMeshBooleanJob import PreparedMeshBooleanResult

    prepared = draft.value["prepared"]
    result = draft.value["result"]
    if not isinstance(prepared, PreparedMeshBooleanResult):
        raise NativeMeshError("The Mesh boolean lost its prepared result.")
    request = prepared.request
    mesh = getattr(result, "Mesh", None)
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", ()) or ()) if timeline else []
    expected_replaced = tuple(
        target.source for target in (request.first, request.second) if target.source_visible
    )
    if (
        not is_live(document, result)
        or str(getattr(result, "TypeId", "")) != "MeshPart::Boolean"
        or result.Source1 is not request.first.source
        or result.Source2 is not request.second.source
        or str(result.Operation) != _NATIVE_OPERATIONS[request.operation]
        or str(result.Label) != request.result_label
        or bool(result.UpdateFromSource) is not False
        or int(getattr(mesh, "CountPoints", 0) or 0) != prepared.points
        or int(getattr(mesh, "CountFacets", 0) or 0) != prepared.facets
        or timeline is None
        or operations.count(result) != 1
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != expected_replaced
        or not mesh_target_still_exact(document, request.first)
        or not mesh_target_still_exact(document, request.second)
        or bool(request.first.source.Visibility)
        or bool(request.second.source.Visibility)
        or not bool(result.Visibility)
    ):
        raise NativeMeshError("The background Mesh boolean failed its exact postcondition.")
    return {
        "operation": request.operation,
        "first": object_reference(request.first.source),
        "second": object_reference(request.second.source),
        "result": mesh_object_state(result),
        "closed_solid": True,
        "conversion": {
            "background": True,
            "cache_hit": prepared.cache_hit,
            "artifact_sha256": prepared.artifact_sha256,
        },
    }
