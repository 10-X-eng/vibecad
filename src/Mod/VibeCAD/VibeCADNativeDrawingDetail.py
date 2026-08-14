# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation, publication, and verification for Drawing details."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeDrawingView import standard_view_line_flags
from VibeCADNativeDrawingViewState import (
    MAX_DRAWING_VIEW_SOURCES,
    drawing_source_state,
    drawing_view_state,
    is_detail_drawing_view,
    is_part_drawing_view,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


@dataclass(frozen=True, slots=True)
class DetailViewSpec:
    reference: str
    anchor_mm: tuple[float, float]
    radius_mm: float
    position_mm: tuple[float, float]
    scale_kind: str
    requested_scale: float | None


@dataclass(frozen=True, slots=True)
class PreparedDetailView:
    page: Any
    page_state_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    base_view: Any
    base_state_before: dict[str, Any]
    sources: tuple[Any, ...]
    source_states: tuple[dict[str, Any], ...]
    base_scale: float
    line_flags: Mapping[str, bool]
    matting_style: int
    show_matting: bool
    show_highlight: bool
    spec: DetailViewSpec
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


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


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


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
            "NATIVE_DRAWING_DETAIL_PARAMETERS_INVALID",
        )
    return result


def _point(
    value: Any,
    name: str,
    minimum: float,
    maximum: float,
) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x_mm", "y_mm"}:
        _error(
            f"{name} requires exactly x_mm and y_mm.",
            "NATIVE_DRAWING_DETAIL_PARAMETERS_INVALID",
        )
    return (
        _finite(value["x_mm"], f"{name}.x_mm", minimum, maximum),
        _finite(value["y_mm"], f"{name}.y_mm", minimum, maximum),
    )


def _spec(values: Mapping[str, Any]) -> DetailViewSpec:
    reference = str(values["reference"] or "").strip()
    if not reference or len(reference) > 32:
        _error(
            "A detail requires a 1 to 32 character reference.",
            "NATIVE_DRAWING_DETAIL_PARAMETERS_INVALID",
        )
    radius = _finite(values["radius_mm"], "radius_mm", 1.0e-9, 1.0e9)
    scale = values["scale"]
    if not isinstance(scale, Mapping):
        _error(
            "scale requires one exact page, automatic, or custom choice.",
            "NATIVE_DRAWING_DETAIL_PARAMETERS_INVALID",
        )
    kind = str(scale.get("kind") or "")
    if kind in {"page", "automatic"} and set(scale) == {"kind"}:
        requested_scale = None
    elif kind == "custom" and set(scale) == {"kind", "value"}:
        requested_scale = _finite(scale["value"], "scale.value", 1.0e-12, 1_000.0)
    else:
        _error(
            "scale must be exactly page, automatic, or custom with value.",
            "NATIVE_DRAWING_DETAIL_PARAMETERS_INVALID",
        )
    return DetailViewSpec(
        reference=reference,
        anchor_mm=_point(
            values["anchor_on_base_mm"],
            "anchor_on_base_mm",
            -1.0e9,
            1.0e9,
        ),
        radius_mm=radius,
        position_mm=_point(
            values["position_on_page_mm"],
            "position_on_page_mm",
            -10_000.0,
            10_000.0,
        ),
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


def _preferences() -> tuple[int, bool, bool]:
    import FreeCAD as App

    root = "User parameter:BaseApp/Preferences/Mod/TechDraw"
    general = App.ParamGet(f"{root}/General")
    decorations = App.ParamGet(f"{root}/Decorations")
    return (
        int(decorations.GetInt("MattingStyle", 0)),
        bool(general.GetBool("ShowDetailMatting", True)),
        bool(general.GetBool("ShowDetailHighlight", True)),
    )


def prepare_detail_view_create(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDetailView:
    spec = _spec(values)
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
    if not is_part_drawing_view(base) or base not in tuple(page.Views or ()):
        _error(
            "A detail requires one projected base view on the exact Drawing page.",
            "NATIVE_DRAWING_DETAIL_BASE_INVALID",
        )
    _require_usable(document, base, "detail base view")
    base_state = drawing_view_state(base)
    if str(base_target["expected_state_sha256"]) != base_state["state_sha256"]:
        _error(
            "The exact detail base view changed after it was inspected.",
            "NATIVE_DRAWING_DETAIL_BASE_STALE",
            repair={"current_state_sha256": base_state["state_sha256"]},
        )
    if not base_state["visible_edge_count"]:
        _error(
            "The exact detail base view has no current projected geometry.",
            "NATIVE_DRAWING_DETAIL_BASE_INVALID",
        )
    sources = tuple(getattr(base, "Source", ()) or ())
    if not 1 <= len(sources) <= MAX_DRAWING_VIEW_SOURCES:
        _error(
            "The exact detail base view must contain 1 to 12 whole-object sources.",
            "NATIVE_DRAWING_DETAIL_BASE_INVALID",
        )
    source_states = tuple(drawing_source_state(source) for source in sources)
    for source in sources:
        _require_usable(document, source, "detail source")
    line_flags = {
        name: bool(base_state["line_visibility"][name])
        for name in standard_view_line_flags("visible")
    }
    matting_style, show_matting, show_highlight = _preferences()
    return PreparedDetailView(
        page=page,
        page_state_before=page_state,
        page_views_before=tuple(page.Views or ()),
        base_view=base,
        base_state_before=base_state,
        sources=sources,
        source_states=source_states,
        base_scale=float(base.Scale),
        line_flags=line_flags,
        matting_style=matting_style,
        show_matting=show_matting,
        show_highlight=show_highlight,
        spec=spec,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_selection(document),
        visibility_before=_visibility(document),
    )


def validate_prepared_detail_view(document: Any, prepared: PreparedDetailView) -> None:
    if not isinstance(prepared, PreparedDetailView):
        raise TypeError("prepared must be a PreparedDetailView")
    page = _canonical(document, prepared.page)
    base = _canonical(document, prepared.base_view)
    if (
        page is None
        or base is None
        or _identities(tuple(document.Objects)) != _identities(prepared.objects_before)
        or _identities(_timeline_operations(document)) != _identities(prepared.timeline_before)
        or _identities(tuple(page.Views or ())) != _identities(prepared.page_views_before)
    ):
        _error(
            "The exact Drawing graph changed while the detail was computed.",
            "NATIVE_DRAWING_DETAIL_STALE",
        )
    page_state = drawing_page_state(page)
    if page_state["state_sha256"] != prepared.page_state_before["state_sha256"]:
        _error(
            "The exact Drawing page changed while the detail was computed.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    base_state = drawing_view_state(base)
    if base_state["state_sha256"] != prepared.base_state_before["state_sha256"]:
        _error(
            "The exact detail base view changed while the detail was computed.",
            "NATIVE_DRAWING_DETAIL_BASE_STALE",
            repair={"current_state_sha256": base_state["state_sha256"]},
        )
    for source, expected in zip(prepared.sources, prepared.source_states, strict=True):
        current = _canonical(document, source)
        if current is None or drawing_source_state(current)["state_sha256"] != expected["state_sha256"]:
            _error(
                f"Detail source {expected['object_name']!r} changed during computation.",
                "NATIVE_DRAWING_DETAIL_SOURCE_STALE",
            )
    if _preferences() != (
        prepared.matting_style,
        prepared.show_matting,
        prepared.show_highlight,
    ):
        _error(
            "The human's Detail View preferences changed during computation.",
            "NATIVE_DRAWING_DETAIL_PREFERENCES_STALE",
        )


def capture_detail_view_commit_state(
    document: Any,
    prepared: PreparedDetailView,
) -> PreparedDetailView:
    validate_prepared_detail_view(document, prepared)
    page = _canonical(document, prepared.page)
    base = _canonical(document, prepared.base_view)
    sources = tuple(_canonical(document, source) for source in prepared.sources)
    if page is None or base is None or any(source is None for source in sources):
        _error(
            "The exact detail inputs became unavailable before publication.",
            "NATIVE_DRAWING_DETAIL_STALE",
        )
    return replace(
        prepared,
        page=page,
        page_views_before=tuple(page.Views or ()),
        base_view=base,
        sources=sources,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_selection(document),
        visibility_before=_visibility(document),
    )


def create_detail_view(
    document: Any,
    *,
    prepared: PreparedDetailView,
    projection_snapshot: Mapping[str, Any],
    detail_snapshot: Mapping[str, Any],
    effective_scale: float,
) -> NativeMutationDraft:
    import FreeCAD as App

    if not isinstance(prepared, PreparedDetailView):
        raise TypeError("prepared must be a PreparedDetailView")
    spec = prepared.spec
    detail = document.addObject("TechDraw::DrawViewDetail", "DetailView")
    if not is_detail_drawing_view(detail):
        _error(
            "The detail factory returned the wrong object type.",
            "NATIVE_DRAWING_DETAIL_CREATE_FAILED",
        )
    detail.BaseView = prepared.base_view
    detail.Source = list(prepared.sources)
    detail.AnchorPoint = App.Vector(*spec.anchor_mm, 0.0)
    detail.Radius = spec.radius_mm
    detail.Reference = spec.reference
    detail.Label = f"Detail {spec.reference}"
    detail.Direction = prepared.base_view.Direction
    detail.XDirection = prepared.base_view.XDirection
    detail.Rotation = float(prepared.base_view.Rotation)
    detail.X, detail.Y = spec.position_mm
    detail.Scale = float(effective_scale)
    detail.ScaleType = {
        "page": "Page",
        "automatic": "Automatic",
        "custom": "Custom",
    }[spec.scale_kind]
    detail.ShowMatting = prepared.show_matting
    detail.ShowHighlight = prepared.show_highlight
    for name, value in prepared.line_flags.items():
        setattr(detail, name, bool(value))
    document.publishProvisionalTimelineOperationBlock(detail, (), ())
    if int(prepared.page.addPrecomputedView(detail)) < 1:
        _error(
            "The detail could not join its exact Drawing page.",
            "NATIVE_DRAWING_DETAIL_CREATE_FAILED",
        )
    projection_setter = getattr(detail, "setPrecomputedProjection", None)
    detail_setter = getattr(detail, "setPrecomputedDetail", None)
    if not callable(projection_setter) or not callable(detail_setter):
        _error(
            "The installed TechDraw runtime cannot adopt detached detail state.",
            "NATIVE_DRAWING_DETAIL_RUNTIME_UNAVAILABLE",
        )
    projection_setter(dict(projection_snapshot))
    detail_setter(dict(detail_snapshot))
    detail.purgeTouched()
    return NativeMutationDraft(
        value={"prepared": prepared, "detail": detail},
        recompute_targets=(),
        created=(object_identity(detail),),
        changed=(object_identity(prepared.page),),
    )


def _same_vector(actual: Any, expected: Any) -> bool:
    return all(
        math.isclose(float(getattr(actual, name)), float(getattr(expected, name)), abs_tol=1.0e-10)
        for name in ("x", "y", "z")
    )


def _assert_presentation(document: Any, prepared: PreparedDetailView) -> None:
    if _selection(document) != prepared.selection_before:
        _error(
            "Detail creation changed the human selection.",
            "NATIVE_DRAWING_DETAIL_POSTCONDITION_FAILED",
        )
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if visibility != prepared.visibility_before:
        _error(
            "Detail creation changed existing object visibility.",
            "NATIVE_DRAWING_DETAIL_POSTCONDITION_FAILED",
        )


def verify_detail_view_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDetailView = draft.value["prepared"]
    detail = draft.value["detail"]
    spec = prepared.spec
    existing = set(_identities(prepared.objects_before))
    created = tuple(obj for obj in document.Objects if _identity(obj) not in existing)
    if (
        _identities(created) != (_identity(detail),)
        or not is_drawing_page(prepared.page)
        or not is_detail_drawing_view(detail)
        or detail.BaseView is not prepared.base_view
        or _identities(tuple(detail.Source)) != _identities(prepared.sources)
        or str(detail.Label) != f"Detail {spec.reference}"
        or str(detail.Reference) != spec.reference
        or not math.isclose(float(detail.AnchorPoint.x), spec.anchor_mm[0], abs_tol=1.0e-9)
        or not math.isclose(float(detail.AnchorPoint.y), spec.anchor_mm[1], abs_tol=1.0e-9)
        or not math.isclose(float(detail.Radius), spec.radius_mm, abs_tol=1.0e-9)
        or not math.isclose(float(detail.X), spec.position_mm[0], abs_tol=1.0e-9)
        or not math.isclose(float(detail.Y), spec.position_mm[1], abs_tol=1.0e-9)
        or not _same_vector(detail.Direction, prepared.base_view.Direction)
        or not _same_vector(detail.XDirection, prepared.base_view.XDirection)
        or _identities(tuple(prepared.page.Views or ()))
        != _identities((*prepared.page_views_before, detail))
        or _identities(_timeline_operations(document))
        != _identities((*prepared.timeline_before, detail))
        or str(getattr(detail, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(detail, "VibeCADTimelineOwner", None) is not None
        or not bool(detail.isValid())
    ):
        _error(
            "The detail did not retain its exact page, base view, anchor, radius, or placement.",
            "NATIVE_DRAWING_DETAIL_POSTCONDITION_FAILED",
        )
    state = drawing_view_state(detail)
    detail_state = state.get("detail") or {}
    topology = detail_state.get("detail_topology") or {}
    if (
        not state["visible_edge_count"]
        or not topology.get("edges")
        or detail_state.get("base_view", {}).get("state_sha256")
        != prepared.base_state_before["state_sha256"]
    ):
        _error(
            "The detail produced no inspectable clipped or projected geometry.",
            "NATIVE_DRAWING_DETAIL_PROJECTION_FAILED",
        )
    page_state = drawing_page_state(prepared.page)
    if (
        page_state["view_count"] != prepared.page_state_before["view_count"] + 1
        or page_state["view_names"][-1:] != [str(detail.Name)]
    ):
        _error(
            "The exact Drawing page did not retain the new detail.",
            "NATIVE_DRAWING_DETAIL_POSTCONDITION_FAILED",
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
