# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical active geometry at the public modeling-object boundary."""

from __future__ import annotations

from typing import Any


def _is_body(obj: Any) -> bool:
    check = getattr(obj, "isDerivedFrom", None)
    if callable(check):
        try:
            return bool(check("PartDesign::Body"))
        except Exception:
            return False
    return str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body"


def _parent_geo_feature_group(obj: Any) -> Any | None:
    parent = getattr(obj, "getParentGeoFeatureGroup", None)
    if not callable(parent):
        return None
    try:
        return parent()
    except (AttributeError, ReferenceError, RuntimeError):
        return None


def _is_body_member(obj: Any) -> bool:
    parent = _parent_geo_feature_group(obj)
    return parent is not obj and _is_body(parent)


def _is_internal_resource(obj: Any) -> bool:
    role = str(getattr(obj, "VibeCADTimelineRole", "") or "").strip()
    return role in {"internal", "resource"} and not _is_body(obj)


def active_public_geometry_sources(document: Any) -> tuple[Any, ...]:
    """Return each active usable shape once at its public Body boundary."""

    try:
        import PartGui
    except ImportError:
        return ()
    result = []
    seen: set[tuple[str, int]] = set()
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        shape = getattr(obj, "Shape", None)
        if shape is None or _is_body_member(obj) or _is_internal_resource(obj):
            continue
        try:
            if (
                shape.isNull()
                or not shape.isValid()
                or not PartGui.isModelingObjectActive(obj)
                or not any((len(shape.Solids), len(shape.Faces), len(shape.Edges)))
            ):
                continue
            identity = (str(document.Uid), int(obj.ID))
        except Exception:
            continue
        if identity in seen:
            continue
        seen.add(identity)
        result.append(obj)
    return tuple(result)


def active_design_geometry_sources(document: Any) -> tuple[Any, ...]:
    """Return design geometry without downstream analysis-domain artifacts."""

    return tuple(
        obj
        for obj in active_public_geometry_sources(document)
        if not bool(getattr(obj, "VibeCADAnalysisDomain", False))
    )


__all__ = ["active_design_geometry_sources", "active_public_geometry_sources"]
