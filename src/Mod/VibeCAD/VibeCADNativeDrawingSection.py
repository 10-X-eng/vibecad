# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation, publication, and verification for a straight section view."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingHistory import require_drawing_source_history_usable
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeDrawingView import standard_view_line_flags
from VibeCADNativeDrawingViewState import (
    MAX_DRAWING_VIEW_SOURCES,
    drawing_source_state,
    drawing_view_state,
    is_part_drawing_view,
    is_section_drawing_view,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


@dataclass(frozen=True, slots=True)
class SectionViewSpec:
    label: str
    symbol: str
    origin_mm: tuple[float, float, float]
    view_direction: tuple[float, float]
    section_normal: tuple[float, float, float]
    section_x_direction: tuple[float, float, float]
    rotation_degrees: float
    scale_kind: str
    requested_scale: float | None


@dataclass(frozen=True, slots=True)
class PreparedSectionView:
    page: Any
    page_state_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    page_size_mm: tuple[float, float]
    base_view: Any
    base_state_before: dict[str, Any]
    sources: tuple[Any, ...]
    source_states: tuple[dict[str, Any], ...]
    base_scale: float
    line_flags: Mapping[str, bool]
    fuse_before_cut: bool
    spec: SectionViewSpec
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def _error(message: str, code: str, *, repair: Mapping[str, Any] | None = None) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _identity(obj: Any) -> tuple[int, str, str]:
    return (
        int(getattr(obj, "ID", -1)),
        str(getattr(obj, "Name", "") or ""),
        str(getattr(obj, "TypeId", "") or ""),
    )


def _identities(objects: tuple[Any, ...]) -> tuple[tuple[int, str, str], ...]:
    return tuple(_identity(obj) for obj in objects)


def _canonical(document: Any, obj: Any) -> Any | None:
    current = document.getObject(str(getattr(obj, "Name", "") or ""))
    return current if current is not None and _identity(current) == _identity(obj) else None


def _selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {"document_uid": str(document.Uid), "selected_count": 0, "items": []}


def _visibility(document: Any) -> tuple[tuple[Any, bool], ...]:
    return tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj in tuple(document.Objects)
        if getattr(obj, "ViewObject", None) is not None
    )


def _finite(value: Any, name: str, minimum: float, maximum: float) -> float:
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        _error(
            f"{name} must be finite and between {minimum:g} and {maximum:g}.",
            "NATIVE_DRAWING_SECTION_PARAMETERS_INVALID",
        )
    return result


def _normalize3(value: tuple[float, float, float], name: str) -> tuple[float, float, float]:
    length = sum(item * item for item in value) ** 0.5
    if not math.isfinite(length) or length <= 1.0e-12:
        _error(
            f"{name} must be nonzero.",
            "NATIVE_DRAWING_SECTION_BASE_INVALID",
        )
    return tuple(item / length for item in value)


def _vector3(value: Any) -> tuple[float, float, float]:
    return tuple(float(getattr(value, name)) for name in ("x", "y", "z"))


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def section_coordinate_system(
    base_view: Any,
    view_direction: tuple[float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    """Match TechDraw's aligned-section compass semantics deterministically."""

    base_direction = _normalize3(_vector3(base_view.Direction), "Base view direction")
    base_x = _normalize3(_vector3(base_view.XDirection), "Base view x direction")
    if abs(_dot(base_direction, base_x)) > 1.0e-8:
        _error(
            "The exact base view has a non-orthogonal projection coordinate system.",
            "NATIVE_DRAWING_SECTION_BASE_INVALID",
        )
    view_x, view_y = view_direction
    local_x, local_y = -view_x, -view_y
    angle = math.atan2(local_y, local_x)
    sine = math.sin(angle)
    cosine = math.cos(angle)
    cross = _cross(base_direction, base_x)
    projection = _dot(base_direction, base_x)
    normal = _normalize3(
        tuple(
            base_x[index] * cosine
            + cross[index] * sine
            + base_direction[index] * projection * (1.0 - cosine)
            for index in range(3)
        ),
        "Section normal",
    )
    section_x = _normalize3(_cross(base_direction, normal), "Section x direction")
    if math.isclose(abs(_dot(section_x, base_x)), 1.0, abs_tol=1.0e-8):
        section_x = base_x
    compass = math.degrees(math.atan2(view_y, view_x))
    if compass < 0.0:
        compass += 360.0
    rotation = compass - 90.0
    if math.isclose(rotation, 180.0, abs_tol=1.0e-10):
        rotation = 0.0
    return normal, section_x, rotation


def _spec(values: Mapping[str, Any], base_view: Any) -> SectionViewSpec:
    label = str(values["label"] or "").strip()
    symbol = str(values["symbol"] or "").strip()
    if not label or len(label) > 160 or not symbol or len(symbol) > 32:
        _error(
            "A section requires a 1 to 160 character label and 1 to 32 character symbol.",
            "NATIVE_DRAWING_SECTION_PARAMETERS_INVALID",
        )
    origin = values["section_origin_mm"]
    if not isinstance(origin, Mapping) or set(origin) != {"x_mm", "y_mm", "z_mm"}:
        _error(
            "section_origin_mm requires exactly x_mm, y_mm, and z_mm.",
            "NATIVE_DRAWING_SECTION_PARAMETERS_INVALID",
        )
    origin_mm = tuple(
        _finite(origin[name], f"section_origin_mm.{name}", -1.0e9, 1.0e9)
        for name in ("x_mm", "y_mm", "z_mm")
    )
    direction = values["view_direction_on_base"]
    if not isinstance(direction, Mapping) or set(direction) != {"x", "y"}:
        _error(
            "view_direction_on_base requires exactly x and y.",
            "NATIVE_DRAWING_SECTION_PARAMETERS_INVALID",
        )
    raw_direction = (
        _finite(direction["x"], "view_direction_on_base.x", -1.0, 1.0),
        _finite(direction["y"], "view_direction_on_base.y", -1.0, 1.0),
    )
    length = math.hypot(*raw_direction)
    if length <= 1.0e-9:
        _error(
            "view_direction_on_base must be nonzero.",
            "NATIVE_DRAWING_SECTION_PARAMETERS_INVALID",
        )
    view_direction = tuple(value / length for value in raw_direction)
    normal, x_direction, rotation = section_coordinate_system(base_view, view_direction)
    scale = values["scale"]
    if not isinstance(scale, Mapping):
        _error(
            "scale requires one exact page, automatic, or custom choice.",
            "NATIVE_DRAWING_SECTION_PARAMETERS_INVALID",
        )
    kind = str(scale.get("kind") or "")
    if kind in {"page", "automatic"} and set(scale) == {"kind"}:
        requested_scale = None
    elif kind == "custom" and set(scale) == {"kind", "value"}:
        requested_scale = _finite(scale["value"], "scale.value", 1.0e-12, 1_000.0)
    else:
        _error(
            "scale must be exactly page, automatic, or custom with value.",
            "NATIVE_DRAWING_SECTION_PARAMETERS_INVALID",
        )
    return SectionViewSpec(
        label=label,
        symbol=symbol,
        origin_mm=origin_mm,
        view_direction=view_direction,
        section_normal=normal,
        section_x_direction=x_direction,
        rotation_degrees=rotation,
        scale_kind=kind,
        requested_scale=requested_scale,
    )


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _page_size(page_state: Mapping[str, Any]) -> tuple[float, float]:
    geometry = page_state.get("template_geometry")
    if not isinstance(geometry, Mapping):
        _error(
            "The exact Drawing page has no valid template geometry.",
            "NATIVE_DRAWING_SECTION_PAGE_INVALID",
        )
    width = _finite(geometry.get("width_mm"), "Drawing page width", 1.0e-9, 1.0e6)
    height = _finite(geometry.get("height_mm"), "Drawing page height", 1.0e-9, 1.0e6)
    return width, height


def prepare_section_view_create(
    document: Any,
    *,
    values: Mapping[str, Any],
    allow_section_base: bool = False,
) -> PreparedSectionView:
    page_target = values["page"]
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": page_target["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    page_state = drawing_page_state(page)
    if str(page_target["expected_state_sha256"]) != page_state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    base_target = values["base_view"]
    base = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": base_target["object_name"]},
    )
    if not is_part_drawing_view(base):
        _error(
            "A straight section requires one TechDraw part projection as its base view.",
            "NATIVE_DRAWING_SECTION_BASE_INVALID",
        )
    if is_section_drawing_view(base) and not allow_section_base:
        _error(
            "A straight section of another section requires the later nested-section operation.",
            "NATIVE_DRAWING_SECTION_BASE_UNSUPPORTED",
        )
    if base not in tuple(getattr(page, "Views", ()) or ()):
        _error(
            "The exact base view does not belong to the exact Drawing page.",
            "NATIVE_DRAWING_SECTION_BASE_INVALID",
        )
    _require_usable(document, base, "section base view")
    base_state = drawing_view_state(base)
    if str(base_target["expected_state_sha256"]) != base_state["state_sha256"]:
        _error(
            "The exact section base view changed after it was inspected.",
            "NATIVE_DRAWING_SECTION_BASE_STALE",
            repair={"current_state_sha256": base_state["state_sha256"]},
        )
    if not base_state["visible_edge_count"]:
        _error(
            "The exact section base view has no current projected geometry.",
            "NATIVE_DRAWING_SECTION_BASE_INVALID",
        )
    sources = tuple(getattr(base, "Source", ()) or ())
    if not 1 <= len(sources) <= MAX_DRAWING_VIEW_SOURCES:
        _error(
            "The exact section base view must contain 1 to 12 whole-object sources.",
            "NATIVE_DRAWING_SECTION_BASE_INVALID",
        )
    source_states = tuple(drawing_source_state(source) for source in sources)
    for source in sources:
        require_drawing_source_history_usable(document, source, "section source")
    spec = _spec(values, base)
    line_flags = {
        name: bool(base_state["line_visibility"][name])
        for name in standard_view_line_flags("visible")
    }
    import FreeCAD as App

    general = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/General")
    return PreparedSectionView(
        page=page,
        page_state_before=page_state,
        page_views_before=tuple(getattr(page, "Views", ()) or ()),
        page_size_mm=_page_size(page_state),
        base_view=base,
        base_state_before=base_state,
        sources=sources,
        source_states=source_states,
        base_scale=float(base.Scale),
        line_flags=line_flags,
        fuse_before_cut=bool(general.GetBool("SectionFuseFirst", False)),
        spec=spec,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_selection(document),
        visibility_before=_visibility(document),
    )


def validate_prepared_section_view(document: Any, prepared: PreparedSectionView) -> None:
    if not isinstance(prepared, PreparedSectionView):
        raise TypeError("prepared must be a PreparedSectionView")
    page = _canonical(document, prepared.page)
    base = _canonical(document, prepared.base_view)
    if (
        page is None
        or base is None
        or _identities(tuple(document.Objects)) != _identities(prepared.objects_before)
        or _identities(_timeline_operations(document)) != _identities(prepared.timeline_before)
        or _identities(tuple(getattr(page, "Views", ()) or ()))
        != _identities(prepared.page_views_before)
    ):
        _error(
            "The exact Drawing graph changed while the section was computed.",
            "NATIVE_DRAWING_SECTION_STALE",
        )
    page_state = drawing_page_state(page)
    if page_state["state_sha256"] != prepared.page_state_before["state_sha256"]:
        _error(
            "The exact Drawing page changed while the section was computed.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    base_state = drawing_view_state(base)
    if base_state["state_sha256"] != prepared.base_state_before["state_sha256"]:
        _error(
            "The exact base view changed while the section was computed.",
            "NATIVE_DRAWING_SECTION_BASE_STALE",
            repair={"current_state_sha256": base_state["state_sha256"]},
        )
    for source, expected in zip(prepared.sources, prepared.source_states, strict=True):
        current = _canonical(document, source)
        if current is None or drawing_source_state(current)["state_sha256"] != expected["state_sha256"]:
            _error(
                f"Section source {expected['object_name']!r} changed during computation.",
                "NATIVE_DRAWING_SECTION_SOURCE_STALE",
            )


def capture_section_view_commit_state(
    document: Any,
    prepared: PreparedSectionView,
) -> PreparedSectionView:
    validate_prepared_section_view(document, prepared)
    page = _canonical(document, prepared.page)
    base = _canonical(document, prepared.base_view)
    sources = tuple(_canonical(document, source) for source in prepared.sources)
    if page is None or base is None or any(source is None for source in sources):
        _error(
            "The exact section inputs became unavailable before publication.",
            "NATIVE_DRAWING_SECTION_STALE",
        )
    return replace(
        prepared,
        page=page,
        page_views_before=tuple(getattr(page, "Views", ()) or ()),
        base_view=base,
        sources=sources,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_selection(document),
        visibility_before=_visibility(document),
    )


def create_section_view(
    document: Any,
    *,
    prepared: PreparedSectionView,
    projection_snapshot: Mapping[str, Any],
    section_snapshot: Mapping[str, Any],
    effective_scale: float,
) -> NativeMutationDraft:
    import FreeCAD as App

    if not isinstance(prepared, PreparedSectionView):
        raise TypeError("prepared must be a PreparedSectionView")
    section = document.addObject("TechDraw::DrawViewSection", "SectionView")
    if not is_section_drawing_view(section):
        _error(
            "The section factory returned the wrong object type.",
            "NATIVE_DRAWING_SECTION_CREATE_FAILED",
        )
    spec = prepared.spec
    section.Label = spec.label
    section.SectionSymbol = spec.symbol
    section.BaseView = prepared.base_view
    section.Source = list(prepared.sources)
    section.SectionOrigin = App.Vector(*spec.origin_mm)
    section.SectionDirection = "Aligned"
    section.Direction = App.Vector(*spec.section_normal)
    section.SectionNormal = App.Vector(*spec.section_normal)
    section.XDirection = App.Vector(*spec.section_x_direction)
    section.Rotation = spec.rotation_degrees
    section.Scale = float(effective_scale)
    section.ScaleType = {
        "page": "Page",
        "automatic": "Automatic",
        "custom": "Custom",
    }[spec.scale_kind]
    section.FuseBeforeCut = prepared.fuse_before_cut
    section.TrimAfterCut = False
    section.UsePreviousCut = False
    for name, value in prepared.line_flags.items():
        setattr(section, name, bool(value))
    section.X = prepared.page_size_mm[0] / 2.0
    section.Y = prepared.page_size_mm[1] / 2.0
    document.publishProvisionalTimelineOperationBlock(section, (), ())
    if int(prepared.page.addPrecomputedView(section)) < 1:
        _error(
            "The section could not join its exact Drawing page.",
            "NATIVE_DRAWING_SECTION_CREATE_FAILED",
        )
    projection_setter = getattr(section, "setPrecomputedProjection", None)
    section_setter = getattr(section, "setPrecomputedSection", None)
    if not callable(projection_setter) or not callable(section_setter):
        _error(
            "The installed TechDraw runtime cannot adopt detached section state.",
            "NATIVE_DRAWING_SECTION_RUNTIME_UNAVAILABLE",
        )
    projection_setter(dict(projection_snapshot))
    section_setter(dict(section_snapshot))
    section.purgeTouched()
    return NativeMutationDraft(
        value={"prepared": prepared, "section": section},
        # Both the page membership and the complete view geometry were
        # adopted above. Recomputing the section here would discard that
        # authenticated cache and launch the same cut again on the UI thread.
        recompute_targets=(),
        created=(object_identity(section),),
        changed=(object_identity(prepared.page),),
    )


def _same_vector(actual: Any, expected: tuple[float, float, float]) -> bool:
    return all(
        math.isclose(float(getattr(actual, name)), value, abs_tol=1.0e-10)
        for name, value in zip(("x", "y", "z"), expected, strict=True)
    )


def _assert_presentation(document: Any, prepared: PreparedSectionView) -> None:
    if _selection(document) != prepared.selection_before:
        _error(
            "Section creation changed the human selection.",
            "NATIVE_DRAWING_SECTION_POSTCONDITION_FAILED",
        )
    actual = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if actual != prepared.visibility_before:
        _error(
            "Section creation changed existing object visibility.",
            "NATIVE_DRAWING_SECTION_POSTCONDITION_FAILED",
        )


def verify_section_view_create(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSectionView = draft.value["prepared"]
    section = draft.value["section"]
    spec = prepared.spec
    existing = set(_identities(prepared.objects_before))
    created = tuple(obj for obj in document.Objects if _identity(obj) not in existing)
    page_views = tuple(getattr(prepared.page, "Views", ()) or ())
    if (
        _identities(created) != (_identity(section),)
        or not is_drawing_page(prepared.page)
        or not is_section_drawing_view(section)
        or section.BaseView is not prepared.base_view
        or _identities(tuple(section.Source)) != _identities(prepared.sources)
        or str(section.Label) != spec.label
        or str(section.SectionSymbol) != spec.symbol
        or not _same_vector(section.SectionOrigin, spec.origin_mm)
        or not _same_vector(section.SectionNormal, spec.section_normal)
        or not _same_vector(section.XDirection, spec.section_x_direction)
        or not math.isclose(float(section.Rotation), spec.rotation_degrees, abs_tol=1.0e-9)
        or _identities(page_views) != _identities((*prepared.page_views_before, section))
        or _identities(_timeline_operations(document))
        != _identities((*prepared.timeline_before, section))
        or str(getattr(section, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(section, "VibeCADTimelineOwner", None) is not None
        or not bool(section.isValid())
    ):
        _error(
            "The section did not retain its exact page, base, plane, or History state.",
            "NATIVE_DRAWING_SECTION_POSTCONDITION_FAILED",
        )
    state = drawing_view_state(section)
    section_state = state.get("section") or {}
    if (
        not state["visible_edge_count"]
        or not section_state.get("section_face_count")
        or section_state.get("base_view", {}).get("state_sha256")
        != prepared.base_state_before["state_sha256"]
    ):
        _error(
            "The section produced no inspectable projected or cut-surface geometry.",
            "NATIVE_DRAWING_SECTION_PROJECTION_FAILED",
        )
    page_state = drawing_page_state(prepared.page)
    if (
        page_state["view_count"] != prepared.page_state_before["view_count"] + 1
        or page_state["view_names"][-1:] != [str(section.Name)]
    ):
        _error(
            "The exact Drawing page did not retain the new section.",
            "NATIVE_DRAWING_SECTION_POSTCONDITION_FAILED",
        )
    _assert_presentation(document, prepared)
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "view": state,
    }
