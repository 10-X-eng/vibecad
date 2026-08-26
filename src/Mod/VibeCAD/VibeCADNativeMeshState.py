# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cheap, bounded identity and topology state for Native Mesh tools."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import math
import struct
from typing import Any, Mapping

from VibeCADNativeSnapshot import concise_object


MAX_MESH_STATE_SOURCES = 16
MAX_MESH_STATE_RESOURCES = 32


_MESH_OBJECT_STATE_CACHE: ContextVar[dict[int, dict[str, Any]] | None] = ContextVar(
    "vibecad_mesh_object_state_cache",
    default=None,
)


@contextmanager
def mesh_object_state_cache():
    """Reuse detached mesh/object state during one immutable capture pass."""

    token = _MESH_OBJECT_STATE_CACHE.set({})
    try:
        yield
    finally:
        _MESH_OBJECT_STATE_CACHE.reset(token)


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return round(result, 9) if math.isfinite(result) else None


def _vector(value: Any) -> list[float] | None:
    result = [_finite(getattr(value, axis, None)) for axis in ("x", "y", "z")]
    return None if any(item is None for item in result) else result  # type: ignore[return-value]


def _bounds(value: Any) -> dict[str, list[float]] | None:
    box = getattr(value, "BoundBox", None)
    if box is None or not bool(getattr(box, "isValid", lambda: False)()):
        return None
    minimum = [_finite(getattr(box, name, None)) for name in ("XMin", "YMin", "ZMin")]
    maximum = [_finite(getattr(box, name, None)) for name in ("XMax", "YMax", "ZMax")]
    size = [_finite(getattr(box, name, None)) for name in ("XLength", "YLength", "ZLength")]
    if any(item is None for item in (*minimum, *maximum, *size)):
        return None
    return {
        "minimum_mm": minimum,  # type: ignore[dict-item]
        "maximum_mm": maximum,  # type: ignore[dict-item]
        "size_mm": size,  # type: ignore[dict-item]
    }


def _linked_names(obj: Any, property_name: str, limit: int) -> list[str]:
    value = getattr(obj, property_name, None)
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else (value,)
    return [
        name
        for name in (
            str(getattr(item, "Name", "") or "").strip()
            for item in values[:limit]
        )
        if name
    ]


def _placement(obj: Any) -> dict[str, Any] | None:
    placement = getattr(obj, "Placement", None)
    if placement is None:
        return None
    base = _vector(getattr(placement, "Base", None))
    rotation = getattr(placement, "Rotation", None)
    quaternion = None
    if rotation is not None:
        try:
            quaternion = [_finite(value) for value in rotation.Q]
        except Exception:
            quaternion = None
    if base is None or quaternion is None or any(value is None for value in quaternion):
        return None
    return {
        "base_mm": base,
        "quaternion": quaternion,
    }


def _mesh_counts(mesh: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for name, attribute in (
        ("points", "CountPoints"),
        ("edges", "CountEdges"),
        ("facets", "CountFacets"),
        ("segments", "CountSegments"),
    ):
        try:
            value = int(getattr(mesh, attribute))
        except Exception:
            continue
        if value >= 0:
            result[name] = value
    return result


def _point_counts(points: Any) -> dict[str, int]:
    try:
        value = int(getattr(points, "CountPoints"))
    except Exception:
        return {}
    return {"points": value} if value >= 0 else {}


def _shape_counts(shape: Any) -> dict[str, int]:
    result = {}
    for name, attribute in (
        ("vertices", "Vertexes"),
        ("edges", "Edges"),
        ("wires", "Wires"),
        ("faces", "Faces"),
        ("shells", "Shells"),
        ("solids", "Solids"),
    ):
        try:
            result[name] = len(getattr(shape, attribute))
        except Exception:
            continue
    return result


def _state_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def mesh_object_state(obj: Any) -> dict[str, Any]:
    """Return enough cheap state to target one mesh-domain object exactly.

    Expensive defect, component, area, and volume walks deliberately belong to
    ``mesh.inspect``. Building provider context must stay responsive even for a
    multi-million-facet mesh.
    """

    cache = _MESH_OBJECT_STATE_CACHE.get()
    cache_key = id(obj)
    if cache is not None and cache_key in cache:
        return deepcopy(cache[cache_key])

    result = concise_object(obj)
    mesh = getattr(obj, "Mesh", None)
    points = getattr(obj, "Points", None)
    shape = getattr(obj, "Shape", None)
    type_id = str(getattr(obj, "TypeId", "") or "")
    curvature_source = (
        getattr(obj, "Source", None)
        if type_id == "Mesh::Curvature"
        else None
    )
    curvature_mesh = getattr(curvature_source, "Mesh", None)
    geometry = mesh if mesh is not None else points if points is not None else shape
    if mesh is not None:
        result["topology"] = _mesh_counts(mesh)
    elif points is not None:
        result["topology"] = _point_counts(points)
    elif shape is not None:
        result["topology"] = _shape_counts(shape)
    elif curvature_mesh is not None:
        try:
            sample_count = int(getattr(obj, "SampleCount", 0))
        except Exception:
            sample_count = 0
        result["topology"] = {"curvature_samples": max(0, sample_count)}
        result["source_state_sha256"] = mesh_object_state(curvature_source).get(
            "state_sha256"
        )
        geometry = curvature_mesh
    if geometry is not None:
        bounds = _bounds(geometry)
        if bounds is not None:
            result["bounds"] = bounds
    placement = _placement(obj)
    if placement is not None:
        result["placement"] = placement

    properties = set(getattr(obj, "PropertiesList", ()) or ())
    if "Source" in properties:
        sources = _linked_names(obj, "Source", 1)
        if sources:
            result["sources"] = sources
    elif "Sources" in properties:
        sources = _linked_names(obj, "Sources", MAX_MESH_STATE_SOURCES)
        if sources:
            result["sources"] = sources
    if "Group" in properties:
        resources = _linked_names(obj, "Group", MAX_MESH_STATE_RESOURCES)
        if resources:
            result["resources"] = resources
    for property_name, output_name in (
        ("OperationKind", "operation_kind"),
        ("InputMode", "input_mode"),
        ("VibeCADTimelineRole", "timeline_role"),
    ):
        if property_name not in properties:
            continue
        value = str(getattr(obj, property_name, "") or "").strip()
        if value:
            result[output_name] = value[:160]
    owner = _linked_names(obj, "VibeCADTimelineOwner", 1)
    if owner:
        result["timeline_owner"] = owner[0]

    digest_source = {
        name: value
        for name, value in result.items()
        if name not in {"label", "state"}
    }
    result["state_sha256"] = _state_digest(digest_source)
    if cache is not None:
        cache[cache_key] = deepcopy(result)
    return result


def mesh_inventory_digest(objects: list[Mapping[str, Any]]) -> str:
    return _state_digest(
        {
            "objects": [
                {
                    "object_name": value.get("object_name"),
                    "type_id": value.get("type_id"),
                    "state_sha256": value.get("state_sha256"),
                }
                for value in objects
            ]
        }
    )


def mesh_geometry_sha256(mesh: Any) -> str:
    """Hash complete topology, coordinates, and segment membership on demand."""

    try:
        points, facets = mesh.Topology
        digest = hashlib.sha256()
        digest.update(struct.pack("!QQ", len(points), len(facets)))
        for point in points:
            digest.update(
                struct.pack(
                    "!ddd",
                    float(point.x),
                    float(point.y),
                    float(point.z),
                )
            )
        for facet in facets:
            digest.update(struct.pack("!QQQ", *(int(index) for index in facet)))
        segment_count = int(mesh.countSegments())
        digest.update(struct.pack("!Q", segment_count))
        for index in range(segment_count):
            segment = tuple(int(value) for value in mesh.getSegment(index))
            digest.update(struct.pack("!Q", len(segment)))
            for facet_index in segment:
                digest.update(struct.pack("!Q", facet_index))
        return digest.hexdigest()
    except Exception as exc:
        raise RuntimeError("The exact Mesh geometry could not be fingerprinted.") from exc
