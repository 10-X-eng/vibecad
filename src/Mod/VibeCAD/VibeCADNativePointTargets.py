# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact current-History point-cloud targets for the Mesh ribbon."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import is_active_mesh_input, is_live
from VibeCADNativeTargets import NativeObjectRef, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedPointTarget:
    source: Any
    object_name: str
    label: str
    expected_state_sha256: str
    point_count: int
    source_visible: bool
    points: Any
    placement: Any
    width: int
    height: int
    intensities: tuple[float, ...]
    colors: tuple[tuple[float, ...], ...]
    normals: tuple[tuple[float, ...], ...]


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError("label must contain 1 to 160 visible characters.")
    return result


def _attribute_values(source: Any, name: str, point_count: int) -> tuple[Any, ...]:
    if name not in set(getattr(source, "PropertiesList", ()) or ()):
        return ()
    values = tuple(getattr(source, name))
    if values and len(values) != point_count:
        raise NativeMeshError(
            f"Point-cloud property {name} does not match the exact point count.",
            error_code="NATIVE_POINT_CLOUD_INVALID",
        )
    return values


def prepare_point_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    require_label: bool = True,
) -> PreparedPointTarget:
    required = {"object_name", "expected_state_sha256", "expected_point_count"}
    if require_label:
        required.add("label")
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeMeshError(
            "The exact point-cloud target must contain only its published fields."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    source = resolve_object(document, reference, expected_types=("Points::Feature",))
    if not is_active_mesh_input(source):
        raise NativeMeshError(
            "The exact point cloud is not active at the current History position.",
            error_code="NATIVE_POINT_CLOUD_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(source)
    expected = str(value["expected_state_sha256"])
    point_count = int((state.get("topology") or {}).get("points", 0) or 0)
    supplied_count = value["expected_point_count"]
    if type(supplied_count) is not int or supplied_count < 0:
        raise NativeMeshError("expected_point_count must be one non-negative integer.")
    if state.get("state_sha256") != expected or point_count != supplied_count:
        raise NativeMeshError(
            "The exact point cloud changed after the provider read its state.",
            error_code="NATIVE_POINT_CLOUD_STATE_STALE",
            repair={
                "target": {"object_name": reference.object_name},
                "current_state_sha256": state.get("state_sha256"),
                "current_point_count": point_count,
            },
        )
    if point_count < 1:
        raise NativeMeshError("The exact point cloud is empty.")
    width = int(getattr(source, "Width", 0) or 0)
    height = int(getattr(source, "Height", 0) or 0)
    if (width or height) and (width < 2 or height < 2 or width * height != point_count):
        raise NativeMeshError(
            "The exact structured point cloud has invalid grid dimensions.",
            error_code="NATIVE_POINT_CLOUD_INVALID",
        )
    return PreparedPointTarget(
        source=source,
        object_name=str(source.Name),
        label=_label(value["label"]) if require_label else "",
        expected_state_sha256=expected,
        point_count=point_count,
        source_visible=bool(source.Visibility),
        points=source.Points,
        placement=source.getGlobalPlacement(),
        width=width,
        height=height,
        intensities=tuple(
            float(item)
            for item in _attribute_values(source, "Intensity", point_count)
        ),
        colors=tuple(
            tuple(float(channel) for channel in item)
            for item in _attribute_values(source, "Color", point_count)
        ),
        normals=tuple(
            tuple(float(channel) for channel in item)
            for item in _attribute_values(source, "Normal", point_count)
        ),
    )


def point_target_still_exact(document: Any, target: PreparedPointTarget) -> bool:
    return (
        is_live(document, target.source)
        and is_active_mesh_input(target.source)
        and mesh_object_state(target.source).get("state_sha256")
        == target.expected_state_sha256
        and int(target.source.Points.CountPoints) == target.point_count
    )
