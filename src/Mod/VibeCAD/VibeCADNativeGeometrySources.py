# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical active geometry at the public modeling-object boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping


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


def _visibility_parent(obj: Any) -> Any | None:
    for method_name in ("getParentGroup", "getParentGeoFeatureGroup"):
        reader = getattr(obj, method_name, None)
        if not callable(reader):
            continue
        try:
            parent = reader()
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        if parent is not None:
            return parent
    return None


def is_drawing_object_effectively_visible(obj: Any) -> bool:
    """Return whether one object and every visual container are shown."""

    visited: set[int] = set()
    current = obj
    while current is not None:
        identity = id(current)
        if identity in visited:
            return False
        visited.add(identity)
        view = getattr(current, "ViewObject", None)
        try:
            if (
                view is None
                or not bool(view.Visibility)
                or bool(getattr(current, "Suppressed", False))
            ):
                return False
        except (AttributeError, ReferenceError, RuntimeError):
            return False
        current = _visibility_parent(current)
    return True


def is_provider_object_effectively_available(document: Any, obj: Any) -> bool:
    """Return whether a live object is visible, unsuppressed, and History-usable."""

    if obj is None or not is_drawing_object_effectively_visible(obj):
        return False
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if not callable(checker):
        return True
    try:
        if bool(checker(obj)):
            return True
    except (AttributeError, ReferenceError, RuntimeError):
        return False
    if not _is_body(obj):
        return False
    try:
        import PartGui

        resolver = getattr(PartGui, "resolveModelingObject", None)
        history_target = resolver(obj) if callable(resolver) else None
        return history_target is not None and bool(checker(history_target))
    except (AttributeError, ImportError, ReferenceError, RuntimeError):
        return False


def _direct_analysis_artifact(obj: Any) -> bool:
    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id.startswith("Fem::"):
        return True
    try:
        if bool(getattr(obj, "VibeCADAnalysisDomain", False)):
            return True
    except (AttributeError, ReferenceError, RuntimeError):
        return True
    properties = set(str(name) for name in tuple(getattr(obj, "PropertiesList", ()) or ()))
    return bool(properties.intersection({"FemMesh", "FemSource", "FemResult"}))


def drawing_analysis_artifact_names(document: Any) -> frozenset[str]:
    """Return every FEM/Analyze artifact excluded from the Drawing surface."""

    objects = tuple(getattr(document, "Objects", ()) or ())
    names = {
        str(getattr(obj, "Name", "") or "")
        for obj in objects
        if _direct_analysis_artifact(obj)
    }
    pending = [
        member
        for analysis in objects
        if str(getattr(analysis, "TypeId", "") or "") == "Fem::FemAnalysis"
        for member in tuple(getattr(analysis, "Group", ()) or ())
    ]
    visited: set[int] = set()
    while pending:
        member = pending.pop()
        identity = id(member)
        if identity in visited:
            continue
        visited.add(identity)
        name = str(getattr(member, "Name", "") or "")
        if name:
            names.add(name)
        pending.extend(tuple(getattr(member, "Group", ()) or ()))
    names.discard("")
    return frozenset(names)


def drawing_source_exclusion_reason(
    document: Any,
    obj: Any,
    *,
    analysis_artifact_names: frozenset[str] | None = None,
) -> str | None:
    """Return the Drawing-only reason an object must not enter provider context."""

    if obj is None:
        return "hidden"
    artifact_names = (
        drawing_analysis_artifact_names(document)
        if analysis_artifact_names is None
        else analysis_artifact_names
    )
    name = str(getattr(obj, "Name", "") or "")
    if _direct_analysis_artifact(obj) or (name and name in artifact_names):
        return "analysis_artifact"
    if not is_provider_object_effectively_available(document, obj):
        return "hidden"
    return None


def _filter_selection(
    document: Any,
    selection: Mapping[str, Any],
    include_object: Callable[[Any], bool],
) -> dict[str, Any]:
    filtered = dict(selection)
    items = []
    get_object = getattr(document, "getObject", None)
    object_by_name = {
        str(getattr(obj, "Name", "") or ""): obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
    }
    for item in list(filtered.get("items") or ()):
        reference = item.get("object") if isinstance(item, Mapping) else None
        name = (
            str(reference.get("object_name") or "")
            if isinstance(reference, Mapping)
            else ""
        )
        obj = (
            get_object(name)
            if name and callable(get_object)
            else object_by_name.get(name)
        )
        if obj is None:
            continue
        if include_object(obj):
            items.append(item)
    filtered["items"] = items
    filtered["selected_count"] = len(items)
    return filtered


def filter_analyze_selection(
    document: Any,
    selection: Mapping[str, Any],
    *,
    analysis_artifact_names: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Keep the FEM study graph plus effectively available model selections."""

    artifact_names = (
        drawing_analysis_artifact_names(document)
        if analysis_artifact_names is None
        else analysis_artifact_names
    )

    return _filter_selection(
        document,
        selection,
        lambda obj: is_analyze_context_reference(
            document,
            obj,
            analysis_artifact_names=artifact_names,
        ),
    )


def filter_drawing_selection(
    document: Any,
    selection: Mapping[str, Any],
    *,
    analysis_artifact_names: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Remove unavailable and Analyze/FEM objects from one Drawing selection."""

    artifact_names = (
        drawing_analysis_artifact_names(document)
        if analysis_artifact_names is None
        else analysis_artifact_names
    )
    return _filter_selection(
        document,
        selection,
        lambda obj: drawing_source_exclusion_reason(
            document,
            obj,
            analysis_artifact_names=artifact_names,
        )
        is None,
    )


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

    if drawing_source_exclusion_reason(document, obj) is not None:
        return False
    return is_active_public_geometry_source(
        document,
        obj,
        validate_brep=validate_brep,
    )


def is_potential_design_geometry_source(
    document: Any,
    obj: Any,
    *,
    analysis_artifact_names: frozenset[str] | None = None,
) -> bool:
    """Identify a Drawing candidate without reading its potentially huge Shape."""

    if (
        obj is None
        or _is_body_member(obj)
        or _is_internal_resource(obj)
        or drawing_source_exclusion_reason(
            document,
            obj,
            analysis_artifact_names=analysis_artifact_names,
        )
        is not None
    ):
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


def is_analyze_context_object(
    document: Any,
    obj: Any,
    *,
    analysis_artifact_names: frozenset[str] | None = None,
) -> bool:
    """Keep the non-rendering FEM graph plus visible public model geometry."""

    artifact_names = (
        drawing_analysis_artifact_names(document)
        if analysis_artifact_names is None
        else analysis_artifact_names
    )
    name = str(getattr(obj, "Name", "") or "")
    if _direct_analysis_artifact(obj) or (name and name in artifact_names):
        return True
    return is_potential_design_geometry_source(
        document,
        obj,
        analysis_artifact_names=artifact_names,
    )


def is_analyze_context_reference(
    document: Any,
    obj: Any,
    *,
    analysis_artifact_names: frozenset[str] | None = None,
) -> bool:
    """Keep required FEM definitions or one ordinarily available reference."""

    artifact_names = (
        drawing_analysis_artifact_names(document)
        if analysis_artifact_names is None
        else analysis_artifact_names
    )
    name = str(getattr(obj, "Name", "") or "")
    return bool(
        _direct_analysis_artifact(obj)
        or (name and name in artifact_names)
        or is_provider_object_effectively_available(document, obj)
    )


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

    if type(validate_brep) is not bool:
        raise TypeError("validate_brep must be a boolean")
    try:
        import PartGui
    except ImportError:
        return ()
    artifact_names = drawing_analysis_artifact_names(document)
    result = []
    seen: set[tuple[str, int]] = set()
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        if drawing_source_exclusion_reason(
            document,
            obj,
            analysis_artifact_names=artifact_names,
        ) is not None:
            continue
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


__all__ = [
    "active_design_geometry_sources",
    "active_public_geometry_sources",
    "drawing_analysis_artifact_names",
    "drawing_source_exclusion_reason",
    "filter_analyze_selection",
    "filter_drawing_selection",
    "is_active_design_geometry_source",
    "is_active_public_geometry_source",
    "is_analyze_context_object",
    "is_analyze_context_reference",
    "is_drawing_object_effectively_visible",
    "is_provider_object_effectively_available",
    "is_potential_design_geometry_source",
]
