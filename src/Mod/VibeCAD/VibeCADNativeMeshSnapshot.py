# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise, bounded live state for the Mesh ribbon."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeGeometrySources import active_design_geometry_sources
from VibeCADNativeMeshState import mesh_inventory_digest, mesh_object_state


MAX_MESH_OBJECTS = 32


def mesh_object_is_context_active(obj: Any) -> bool:
    """Return whether an object is usable at the current History position."""

    try:
        import MeshGui
    except ImportError:
        return True
    try:
        return bool(MeshGui.isNativeMeshInputActive(obj))
    except Exception:
        return False


def _selected_names(selection: Mapping[str, Any] | None) -> tuple[str, ...]:
    names = []
    for item in list((selection or {}).get("items") or []):
        if not isinstance(item, Mapping):
            continue
        reference = item.get("object")
        if not isinstance(reference, Mapping):
            continue
        name = str(reference.get("object_name") or "")
        if name and name not in names:
            names.append(name)
    return tuple(names)


def build_mesh_snapshot(
    document: Any,
    *,
    selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    values = []
    for obj in list(getattr(document, "Objects", []) or []):
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id.startswith(("Mesh::", "MeshPart::", "Points::", "ReverseEngineering::")):
            if mesh_object_is_context_active(obj):
                values.append(obj)
            continue
        try:
            if (
                bool(obj.isDerivedFrom("Part::Plane"))
                and mesh_object_is_context_active(obj)
            ):
                values.append(obj)
        except Exception:
            continue
    present = {id(obj) for obj in values}
    for obj in active_design_geometry_sources(document):
        if id(obj) not in present:
            values.append(obj)
            present.add(id(obj))
    counts = {
        "mesh": 0,
        "mesh_part": 0,
        "points": 0,
        "reverse_engineering": 0,
        "datum_plane": 0,
        "shape": 0,
    }
    for obj in values:
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id.startswith("Mesh::"):
            category = "mesh"
        elif type_id.startswith("MeshPart::"):
            category = "mesh_part"
        elif type_id.startswith("Points::"):
            category = "points"
        elif type_id.startswith("ReverseEngineering::"):
            category = "reverse_engineering"
        else:
            try:
                category = (
                    "datum_plane"
                    if bool(obj.isDerivedFrom("Part::Plane"))
                    else "shape"
                )
            except Exception:
                category = "shape"
        counts[category] += 1
    by_name = {
        str(getattr(value, "Name", "") or ""): value
        for value in values
    }
    prioritized = [
        by_name[name]
        for name in _selected_names(selection)
        if name in by_name
    ]
    selected = {id(value) for value in prioritized}
    prioritized.extend(value for value in values if id(value) not in selected)
    objects = [
        mesh_object_state(value)
        for value in prioritized[:MAX_MESH_OBJECTS]
    ]
    result = {
        "kind": "mesh",
        "counts": counts,
        "objects": objects,
        "inventory_sha256": mesh_inventory_digest(objects),
    }
    if len(values) > len(objects):
        result["truncated"] = True
        result["total_objects"] = len(values)
    return result
