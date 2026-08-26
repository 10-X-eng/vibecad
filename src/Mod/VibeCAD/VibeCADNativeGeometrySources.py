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


def _is_active_public_geometry_source(
    document: Any,
    obj: Any,
    *,
    part_gui: Any,
    validate_brep: bool,
) -> bool:
    shape = getattr(obj, "Shape", None)
    if shape is None or _is_body_member(obj) or _is_internal_resource(obj):
        return False
    try:
        str(document.Uid)
        int(obj.ID)
        return not (
            shape.isNull()
            or (validate_brep and not shape.isValid())
            or not part_gui.isModelingObjectActive(obj)
            or (
                validate_brep
                and not any((len(shape.Solids), len(shape.Faces), len(shape.Edges)))
            )
        )
    except Exception:
        return False


def is_active_public_geometry_source(
    document: Any,
    obj: Any,
    *,
    validate_brep: bool = True,
) -> bool:
    """Return whether one object is an active public geometry boundary."""

    if type(validate_brep) is not bool:
        raise TypeError("validate_brep must be a boolean")
    try:
        import PartGui
    except ImportError:
        return False
    return _is_active_public_geometry_source(
        document,
        obj,
        part_gui=PartGui,
        validate_brep=validate_brep,
    )


def is_active_design_geometry_source(
    document: Any,
    obj: Any,
    *,
    validate_brep: bool = True,
) -> bool:
    """Return whether one active public object is design, not analysis, geometry."""

    return is_active_public_geometry_source(
        document,
        obj,
        validate_brep=validate_brep,
    ) and not bool(getattr(obj, "VibeCADAnalysisDomain", False))


def is_potential_design_geometry_source(document: Any, obj: Any) -> bool:
    """Identify a Drawing candidate without reading its potentially huge Shape."""

    if obj is None or _is_body_member(obj) or _is_internal_resource(obj):
        return False
    try:
        import PartGui
    except ImportError:
        return False
    try:
        str(document.Uid)
        int(obj.ID)
        properties = tuple(getattr(obj, "PropertiesList", ()) or ())
        type_id = str(getattr(obj, "TypeId", "") or "")
        return (
            ("Shape" in properties or type_id in {"App::Part", "App::Link"})
            and bool(PartGui.isModelingObjectActive(obj))
            and not bool(getattr(obj, "VibeCADAnalysisDomain", False))
        )
    except Exception:
        return False


def active_public_geometry_sources(
    document: Any,
    *,
    validate_brep: bool = True,
) -> tuple[Any, ...]:
    """Return each active usable shape once at its public Body boundary.

    Exact callers retain BREP validation by default.  Responsive source
    catalogs may defer that unbounded check until an exact source is used.
    """

    if type(validate_brep) is not bool:
        raise TypeError("validate_brep must be a boolean")

    try:
        import PartGui
    except ImportError:
        return ()
    result = []
    seen: set[tuple[str, int]] = set()
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        if not _is_active_public_geometry_source(
            document,
            obj,
            part_gui=PartGui,
            validate_brep=validate_brep,
        ):
            continue
        try:
            identity = (str(document.Uid), int(obj.ID))
        except Exception:
            continue
        if identity in seen:
            continue
        seen.add(identity)
        result.append(obj)
    return tuple(result)


def active_design_geometry_sources(
    document: Any,
    *,
    validate_brep: bool = True,
) -> tuple[Any, ...]:
    """Return design geometry without downstream analysis-domain artifacts."""

    return tuple(
        obj
        for obj in active_public_geometry_sources(
            document,
            validate_brep=validate_brep,
        )
        if not bool(getattr(obj, "VibeCADAnalysisDomain", False))
    )


__all__ = [
    "active_design_geometry_sources",
    "active_public_geometry_sources",
    "is_active_design_geometry_source",
    "is_active_public_geometry_source",
    "is_potential_design_geometry_source",
]
