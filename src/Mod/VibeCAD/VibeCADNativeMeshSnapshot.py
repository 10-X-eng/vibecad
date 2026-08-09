# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Mesh ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeSnapshot import concise_object


MAX_MESH_OBJECTS = 32


def _mesh_summary(obj: Any) -> dict[str, Any]:
    result = concise_object(obj)
    mesh = getattr(obj, "Mesh", None)
    points = getattr(obj, "Points", None)
    for key, source, attribute in (
        ("points", mesh, "CountPoints"),
        ("edges", mesh, "CountEdges"),
        ("facets", mesh, "CountFacets"),
        ("points", points, "CountPoints"),
    ):
        if source is None or key in result:
            continue
        try:
            result[key] = int(getattr(source, attribute))
        except Exception:
            continue
    return result


def build_mesh_snapshot(document: Any) -> dict[str, Any]:
    values = [
        obj
        for obj in list(getattr(document, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "").startswith(
            ("Mesh::", "Points::", "ReverseEngineering::")
        )
    ]
    counts = {"mesh": 0, "points": 0, "reverse_engineering": 0}
    for obj in values:
        type_id = str(getattr(obj, "TypeId", "") or "")
        category = (
            "mesh"
            if type_id.startswith("Mesh::")
            else "points"
            if type_id.startswith("Points::")
            else "reverse_engineering"
        )
        counts[category] += 1
    return {
        "kind": "mesh",
        "counts": counts,
        "objects": [_mesh_summary(value) for value in values[:MAX_MESH_OBJECTS]],
    }
