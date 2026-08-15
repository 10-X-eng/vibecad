# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation, creation, and proof for retained Mesh booleans."""

from __future__ import annotations

from dataclasses import dataclass
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
