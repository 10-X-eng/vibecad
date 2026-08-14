# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for Sketch setup and in-edit ribbons."""

from __future__ import annotations

from typing import Any

from VibeCADNativeSketchRevision import sketch_revision
from VibeCADNativeSketchState import serialize_sketch_state
from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_SKETCHES = 24


def _active_edit_sketch(document: Any) -> Any | None:
    try:
        from VibeCADEditState import active_edit_object

        value = active_edit_object()
    except Exception:
        return None
    return value if getattr(value, "Document", None) is document else None


def _support_summary(sketch: Any) -> list[dict[str, Any]]:
    result = []
    raw_support = getattr(sketch, "AttachmentSupport", None)
    if not raw_support:
        raw_support = getattr(sketch, "Support", None)
    if (
        isinstance(raw_support, tuple)
        and raw_support
        and hasattr(raw_support[0], "Name")
    ):
        values = [raw_support]
    elif isinstance(raw_support, list):
        values = raw_support
    else:
        values = [raw_support]
    for value in values:
        obj = value[0] if isinstance(value, tuple) and value else value
        if getattr(obj, "Document", None) is getattr(sketch, "Document", None):
            result.append(concise_object(obj))
    return result[:8]


def _construction_count(sketch: Any, geometry_count: int) -> int:
    get_construction = getattr(sketch, "getConstruction", None)
    if not callable(get_construction):
        return 0
    count = 0
    for index in range(geometry_count):
        try:
            count += int(bool(get_construction(index)))
        except Exception:
            continue
    return count


def _external_reference_count(sketch: Any) -> int:
    count = 0
    for raw in list(getattr(sketch, "ExternalGeometry", []) or []):
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        names = raw[1]
        count += 1 if isinstance(names, str) else len(list(names or []))
    return count


def _summary(sketch: Any) -> dict[str, Any]:
    result = concise_object(sketch)
    try:
        result["geometry_count"] = int(sketch.GeometryCount)
    except Exception:
        result["geometry_count"] = len(list(getattr(sketch, "Geometry", []) or []))
    result["constraint_count"] = len(list(getattr(sketch, "Constraints", []) or []))
    result["construction_geometry_count"] = _construction_count(
        sketch,
        result["geometry_count"],
    )
    result["external_reference_count"] = _external_reference_count(sketch)
    result["external_geometry_count"] = max(
        0,
        len(list(getattr(sketch, "ExternalGeo", []) or [])) - 2,
    )
    result["map_mode"] = str(getattr(sketch, "MapMode", "") or "Deactivated")
    supports = _support_summary(sketch)
    if supports:
        result["supports"] = supports
    if hasattr(sketch, "FullyConstrained"):
        result["fully_constrained"] = bool(sketch.FullyConstrained)
    return result


def _detailed_state(sketch: Any) -> dict[str, Any]:
    return {**_summary(sketch), **serialize_sketch_state(sketch)}


def build_sketch_snapshot(document: Any, surface_id: str) -> dict[str, Any]:
    sketches = objects_of_type(document, "Sketcher::SketchObject")
    active = _active_edit_sketch(document)
    result: dict[str, Any] = {
        "kind": "sketch",
        "context": "edit" if surface_id == "sketch.edit" else "setup",
        "sketch_count": len(sketches),
    }
    if active is not None:
        result["revision"] = sketch_revision(active)
        result["active_sketch"] = _detailed_state(active)
        result["source_sketches"] = [
            _summary(value) for value in sketches if value is not active
        ][:MAX_SKETCHES]
    if surface_id == "sketch.setup":
        result["sketches"] = [_summary(value) for value in sketches[:MAX_SKETCHES]]
    return result
