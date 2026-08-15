# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise, bounded live state for the Mesh ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeMeshState import mesh_inventory_digest, mesh_object_state


MAX_MESH_OBJECTS = 32


def build_mesh_snapshot(document: Any) -> dict[str, Any]:
    values = []
    for obj in list(getattr(document, "Objects", []) or []):
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id.startswith(("Mesh::", "MeshPart::", "Points::", "ReverseEngineering::")):
            values.append(obj)
            continue
        try:
            if bool(obj.isDerivedFrom("Part::Plane")):
                values.append(obj)
        except Exception:
            continue
    counts = {
        "mesh": 0,
        "mesh_part": 0,
        "points": 0,
        "reverse_engineering": 0,
        "datum_plane": 0,
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
            category = "datum_plane"
        counts[category] += 1
    objects = [mesh_object_state(value) for value in values[:MAX_MESH_OBJECTS]]
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
