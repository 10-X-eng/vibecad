# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical current-History geometry shared by Analyze UI and AI."""

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


def active_analyze_geometry_sources(
    document: Any,
    *,
    filter_analysis_sources: bool = True,
    validate_brep: bool = True,
) -> tuple[Any, ...]:
    """Return each usable engineering source once at its public Body boundary.

    BREP validity remains the default usable-source contract. Responsive
    provider discovery may defer that unbounded check; exact operations still
    validate their chosen geometry before execution. The optional filtering
    switch only lets bounded capture defer cross-batch domain/source
    de-duplication until every batch has been read.
    """

    if type(filter_analysis_sources) is not bool:
        raise TypeError("filter_analysis_sources must be a boolean")
    if type(validate_brep) is not bool:
        raise TypeError("validate_brep must be a boolean")

    try:
        import PartGui
    except ImportError:
        return ()
    candidates = []
    seen: set[tuple[str, int]] = set()
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        shape = getattr(obj, "Shape", None)
        if shape is None or _is_body_member(obj) or _is_internal_resource(obj):
            continue
        try:
            if (
                shape.isNull()
                or (validate_brep and not shape.isValid())
                or not PartGui.isModelingObjectActive(obj)
                or (
                    validate_brep
                    and not any((len(shape.Solids), len(shape.Faces), len(shape.Edges)))
                )
            ):
                continue
            identity = (str(document.Uid), int(obj.ID))
        except Exception:
            continue
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(obj)
    if not filter_analysis_sources:
        return tuple(candidates)
    domain_sources = {
        source
        for domain in candidates
        if bool(getattr(domain, "VibeCADAnalysisDomain", False))
        for source in tuple(getattr(domain, "AnalysisSources", ()) or ())
    }
    return tuple(
        obj
        for obj in candidates
        if bool(getattr(obj, "VibeCADAnalysisDomain", False))
        or obj not in domain_sources
    )
