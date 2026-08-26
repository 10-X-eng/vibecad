# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact FEM mesh targets, element pages, and filtered-output state."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshState import (
    fem_mesh_object_context_state,
    fem_mesh_object_state,
    fem_mesh_object_still_exact,
)
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeSnapshot import concise_object
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_ELEMENT_TYPES = (
    ("volume", "Volume", -6),
    ("face", "Face", -5),
    ("edge", "Edge", -4),
    ("zero_d", "0DElement", -2),
    ("ball", "Ball", -3),
)
_ELEMENT_TYPE_BY_NAME = {name: (native, marker) for name, native, marker in _ELEMENT_TYPES}


@dataclass(frozen=True, slots=True)
class PreparedFemMeshObjectTarget:
    mesh: Any
    expected_state_sha256: str
    element_kind: str
    element_ids: tuple[int, ...]
    source_visible: bool


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _semantic_root(document: Any, obj: Any) -> Any | None:
    current = obj
    visited: set[int] = set()
    while is_live(document, current):
        identity = int(current.ID)
        if identity in visited:
            return None
        visited.add(identity)
        if str(getattr(current, "VibeCADTimelineRole", "") or "") != "resource":
            return current
        current = getattr(current, "VibeCADTimelineOwner", None)
    return None


def fem_mesh_is_active(document: Any, obj: Any) -> bool:
    root = _semantic_root(document, obj)
    if root is None:
        return False
    try:
        import PartGui

        return bool(PartGui.isModelingObjectActive(root))
    except Exception:
        return False


def element_ids_for_kind(fem_mesh: Any, kind: str) -> tuple[int, ...]:
    if kind not in _ELEMENT_TYPE_BY_NAME:
        raise NativeAnalyzeError("element_kind is not supported.")
    native_name, _marker = _ELEMENT_TYPE_BY_NAME[kind]
    try:
        values = tuple(int(value) for value in fem_mesh.getIdByElementType(native_name))
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM mesh could not enumerate its {kind} elements."
        ) from exc
    if len(values) != len(set(values)) or any(value <= 0 for value in values):
        raise NativeAnalyzeError("The FEM mesh contains invalid element identities.")
    return tuple(sorted(values))


def primary_element_inventory(fem_mesh: Any) -> tuple[str, int, tuple[int, ...]]:
    for kind, _native, marker in _ELEMENT_TYPES:
        ids = element_ids_for_kind(fem_mesh, kind)
        if ids:
            return kind, marker, ids
    raise NativeAnalyzeError(
        "The FEM mesh contains no erasable volume, face, edge, 0D, or ball elements."
    )


def prepare_fem_mesh_object_target(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedFemMeshObjectTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "FEM mesh target must contain only object_name and expected_state_sha256."
        )
    mesh = resolve_object(
        document,
        NativeObjectRef(document_uid, str(value["object_name"])),
    )
    state = fem_mesh_object_context_state(mesh)
    expected_sha = str(value["expected_state_sha256"] or "")
    if not fem_mesh_object_still_exact(mesh, expected_sha):
        raise NativeAnalyzeError(
            "The exact FEM mesh changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "fem_mesh": {"object_name": str(mesh.Name)},
                "current_state_sha256": state["state_sha256"],
            },
        )
    if not state["generated"]:
        raise NativeAnalyzeError("The exact FEM mesh contains no generated mesh data.")
    if not fem_mesh_is_active(document, mesh):
        raise NativeAnalyzeError(
            "The exact FEM mesh is not active at the current History position.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )
    kind, _marker, ids = primary_element_inventory(mesh.FemMesh)
    return PreparedFemMeshObjectTarget(
        mesh,
        expected_sha,
        kind,
        ids,
        bool(mesh.ViewObject.Visibility),
    )


def fem_mesh_object_target_still_exact(target: PreparedFemMeshObjectTarget) -> bool:
    document = getattr(target.mesh, "Document", None)
    return (
        is_live(document, target.mesh)
        and fem_mesh_is_active(document, target.mesh)
        and fem_mesh_object_still_exact(target.mesh, target.expected_state_sha256)
        and bool(target.mesh.ViewObject.Visibility) is target.source_visible
    )


def mesh_filter_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM mesh-filter operation is no longer live.")
    try:
        valid_type = bool(obj.isDerivedFrom("Fem::FemSetElementNodesObject"))
    except Exception:
        valid_type = False
    if not valid_type:
        raise NativeAnalyzeError(
            "The exact target is not a FEM element-filter operation.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    result_mesh = getattr(obj, "FemMesh", None)
    if result_mesh is None:
        raise NativeAnalyzeError("The FEM element filter has no result mesh.")
    result_state = fem_mesh_object_state(result_mesh)
    values = tuple(int(value) for value in tuple(getattr(obj, "Elements", ()) or ()))
    markers = tuple(value for value in values if value < 0)
    remaining = tuple(value for value in values if value > 0)
    replaced = tuple(getattr(obj, "VibeCADTimelineReplacedInputs", ()) or ())
    result = {
        **concise_object(obj),
        "filter_kind": "erase_elements",
        "result_mesh": {
            "object_name": result_state["object_name"],
            "state_sha256": result_state["state_sha256"],
            "topology": result_state["topology"],
        },
        "primary_type_marker": markers[0] if len(markers) == 1 else None,
        "remaining_element_count": len(remaining),
        "remaining_element_ids_sha256": _digest({"element_ids": remaining}),
        "replaced_inputs": [str(value.Name) for value in replaced if is_live(document, value)],
    }
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "result_mesh": [str(result_mesh.Name), int(result_mesh.ID), result_state["state_sha256"]],
            "markers": markers,
            "remaining": remaining,
            "replaced_inputs": [
                [str(value.Name), int(value.ID)]
                for value in replaced
                if is_live(document, value)
            ],
        }
    )
    return result


def inspect_fem_mesh_elements(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    element_kind: Any,
    offset: Any,
    page_size: Any,
) -> dict[str, Any]:
    prepared = prepare_fem_mesh_object_target(document, document_uid, target)
    kind = str(element_kind or "")
    if kind == "primary":
        kind = prepared.element_kind
        ids = prepared.element_ids
    else:
        ids = element_ids_for_kind(prepared.mesh.FemMesh, kind)
    if type(offset) is not int or offset < 0:
        raise NativeAnalyzeError("offset must be a non-negative integer.")
    if type(page_size) is not int or not 1 <= page_size <= 64:
        raise NativeAnalyzeError("page_size must be an integer from 1 to 64.")
    selected = ids[offset : offset + page_size]
    nodes = prepared.mesh.FemMesh.Nodes
    elements = []
    for element_id in selected:
        try:
            node_ids = tuple(
                int(value) for value in prepared.mesh.FemMesh.getElementNodes(element_id)
            )
            points = [nodes[node_id] for node_id in node_ids]
        except Exception as exc:
            raise NativeAnalyzeError(
                f"FEM element {element_id} has unreadable node connectivity."
            ) from exc
        if not points:
            raise NativeAnalyzeError(f"FEM element {element_id} has no nodes.")
        coordinates = [
            [float(point.x), float(point.y), float(point.z)] for point in points
        ]
        if any(not math.isfinite(value) for point in coordinates for value in point):
            raise NativeAnalyzeError(f"FEM element {element_id} has non-finite coordinates.")
        elements.append(
            {
                "element_id": element_id,
                "node_ids": list(node_ids),
                "centroid_mm": [
                    float(format(sum(point[index] for point in coordinates) / len(points), ".12g"))
                    for index in range(3)
                ],
                "bounds_mm": {
                    "minimum": [min(point[index] for point in coordinates) for index in range(3)],
                    "maximum": [max(point[index] for point in coordinates) for index in range(3)],
                },
            }
        )
    next_offset = offset + len(selected)
    return {
        "fem_mesh": fem_mesh_object_state(prepared.mesh),
        "element_kind": kind,
        "total": len(ids),
        "offset": offset,
        "elements": elements,
        "next_offset": next_offset if next_offset < len(ids) else None,
    }
