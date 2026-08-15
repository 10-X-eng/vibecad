# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact current-History targets shared by Native Mesh operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_geometry_sha256, mesh_object_state
from VibeCADNativeTargets import NativeObjectRef, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedMeshTarget:
    source: Any
    label: str
    expected_state_sha256: str
    source_geometry_sha256: str
    topology: Mapping[str, int]
    source_visible: bool
    point_indices: tuple[int, ...] = ()
    facet_indices: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedPlaneTarget:
    plane: Any
    expected_state_sha256: str
    source_visible: bool


def is_live(document: Any, obj: Any) -> bool:
    return (
        getattr(obj, "Document", None) is document
        and document.getObject(str(getattr(obj, "Name", "") or "")) is obj
    )


def is_active_mesh_input(obj: Any) -> bool:
    import MeshGui

    return bool(MeshGui.isNativeMeshInputActive(obj))


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError("label must contain 1 to 160 visible characters.")
    return result


def prepare_mesh_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    require_label: bool = True,
) -> PreparedMeshTarget:
    required = {"object_name", "expected_state_sha256"}
    if require_label:
        required.add("label")
    if not isinstance(value, Mapping) or set(value) != required:
        fields = ", ".join(sorted(required))
        raise NativeMeshError(f"The exact Mesh target must contain only {fields}.")
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    source = resolve_object(document, reference, expected_types=("Mesh::Feature",))
    if not is_active_mesh_input(source):
        raise NativeMeshError(
            "The exact Mesh is not active at the current History position.",
            error_code="NATIVE_MESH_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(source)
    expected = str(value["expected_state_sha256"])
    if state.get("state_sha256") != expected:
        raise NativeMeshError(
            "The exact Mesh changed after the provider read its state.",
            error_code="NATIVE_MESH_STATE_STALE",
            repair={
                "target": {"object_name": reference.object_name},
                "current_state_sha256": state.get("state_sha256"),
                "current_topology": state.get("topology"),
            },
        )
    topology = dict(state.get("topology") or {})
    if int(topology.get("points", 0) or 0) < 3 or int(topology.get("facets", 0) or 0) < 1:
        raise NativeMeshError("The exact Mesh target must contain facets.")
    return PreparedMeshTarget(
        source=source,
        label=_label(value["label"]) if require_label else "",
        expected_state_sha256=expected,
        source_geometry_sha256=mesh_geometry_sha256(source.Mesh),
        topology=topology,
        source_visible=bool(source.Visibility),
    )


def prepare_mesh_targets(
    document: Any,
    document_uid: str,
    values: Any,
    *,
    extra_keys: tuple[str, ...] = (),
) -> tuple[PreparedMeshTarget, ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= 32:
        raise NativeMeshError("targets must contain 1 to 32 exact Mesh targets.")
    required = {"object_name", "expected_state_sha256", "label"}
    targets = []
    for value in values:
        if not isinstance(value, Mapping) or set(value) != required | set(extra_keys):
            raise NativeMeshError(
                "Every target must contain exactly its published target fields."
            )
        targets.append(
            prepare_mesh_target(
                document,
                document_uid,
                {name: value[name] for name in required},
            )
        )
    result = tuple(targets)
    names = [str(target.source.Name) for target in result]
    if len(names) != len(set(names)):
        raise NativeMeshError("targets must not repeat a Mesh object.")
    return result


def replace_mesh_target(
    target: PreparedMeshTarget,
    *,
    point_indices: tuple[int, ...] = (),
    facet_indices: tuple[int, ...] = (),
) -> PreparedMeshTarget:
    return replace(
        target,
        point_indices=point_indices,
        facet_indices=facet_indices,
    )


def mesh_target_still_exact(document: Any, target: PreparedMeshTarget) -> bool:
    if not is_live(document, target.source) or not is_active_mesh_input(target.source):
        return False
    state = mesh_object_state(target.source)
    return (
        state.get("state_sha256") == target.expected_state_sha256
        and mesh_geometry_sha256(target.source.Mesh) == target.source_geometry_sha256
    )


def prepare_plane_target(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedPlaneTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeMeshError(
            "plane must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    plane = resolve_object(document, reference, expected_types=("Part::Plane",))
    if not is_active_mesh_input(plane):
        raise NativeMeshError(
            "The exact datum plane is not active at the current History position.",
            error_code="NATIVE_MESH_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(plane)
    expected = str(value["expected_state_sha256"])
    if state.get("state_sha256") != expected:
        raise NativeMeshError(
            "The datum plane changed after the provider read its state.",
            error_code="NATIVE_MESH_STATE_STALE",
            repair={
                "target": {"object_name": reference.object_name},
                "current_state_sha256": state.get("state_sha256"),
            },
        )
    return PreparedPlaneTarget(
        plane=plane,
        expected_state_sha256=expected,
        source_visible=bool(plane.Visibility),
    )


def plane_target_still_exact(document: Any, target: PreparedPlaneTarget) -> bool:
    return (
        is_live(document, target.plane)
        and is_active_mesh_input(target.plane)
        and mesh_object_state(target.plane).get("state_sha256")
        == target.expected_state_sha256
    )
