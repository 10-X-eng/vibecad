# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation, publication, and verification for Draft-source views."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDraftState import (
    DraftSourceFingerprint,
    draft_source_fingerprint,
    drawing_draft_view_state,
    is_draft_drawing_view,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeDrawingViewState import DRAWING_VIEW_ORIENTATIONS
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


@dataclass(frozen=True, slots=True)
class DraftViewStyle:
    line_width: float
    font_size: float
    color_rgb: tuple[int, int, int]
    line_style: str
    line_spacing: float
    override: bool


@dataclass(frozen=True, slots=True)
class DraftViewSpec:
    orientation: str
    position_mm: tuple[float, float]
    scale_kind: str
    requested_scale: float | None
    style: DraftViewStyle


@dataclass(frozen=True, slots=True)
class PreparedDraftView:
    page: Any
    page_state_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    source: Any
    source_fingerprint: DraftSourceFingerprint
    spec: DraftViewSpec
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
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    return result


def _position(value: Any) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x_mm", "y_mm"}:
        _error(
            "position_on_page_mm requires exactly x_mm and y_mm.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    return (
        _finite(value["x_mm"], "position_on_page_mm.x_mm", -10_000.0, 10_000.0),
        _finite(value["y_mm"], "position_on_page_mm.y_mm", -10_000.0, 10_000.0),
    )


def _style(value: Any) -> DraftViewStyle:
    if not isinstance(value, Mapping):
        _error(
            "style requires one exact source or override choice.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    kind = str(value.get("kind") or "")
    if kind == "source" and set(value) == {"kind"}:
        return DraftViewStyle(0.35, 12.0, (0, 0, 0), "Solid", 1.0, False)
    required = {
        "kind",
        "line_width_mm",
        "font_size_pt",
        "color_rgb",
        "line_style",
        "line_spacing",
    }
    if kind != "override" or set(value) != required:
        _error(
            "style must be exactly source or a complete override.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    color = value["color_rgb"]
    if not isinstance(color, Mapping) or set(color) != {"red", "green", "blue"}:
        _error(
            "style.color_rgb requires exactly red, green, and blue.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    components = tuple(color[name] for name in ("red", "green", "blue"))
    if any(type(component) is not int or not 0 <= component <= 255 for component in components):
        _error(
            "style.color_rgb components must be integers from 0 through 255.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    line_style = str(value["line_style"] or "")
    if line_style not in {"Solid", "Dashed", "Dashdot", "Dot"}:
        _error(
            "style.line_style must be Solid, Dashed, Dashdot, or Dot.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    return DraftViewStyle(
        line_width=_finite(value["line_width_mm"], "style.line_width_mm", 1.0e-12, 100.0),
        font_size=_finite(value["font_size_pt"], "style.font_size_pt", 1.0e-12, 10_000.0),
        color_rgb=components,
        line_style=line_style,
        line_spacing=_finite(value["line_spacing"], "style.line_spacing", 1.0e-12, 100.0),
        override=True,
    )


def _spec(values: Mapping[str, Any]) -> DraftViewSpec:
    orientation = str(values["orientation"] or "")
    if orientation not in DRAWING_VIEW_ORIENTATIONS:
        _error(
            "orientation must be one published deterministic Drawing orientation.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    scale = values["scale"]
    if not isinstance(scale, Mapping):
        _error(
            "scale requires one exact page or custom choice.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    kind = str(scale.get("kind") or "")
    if kind == "page" and set(scale) == {"kind"}:
        requested = None
    elif kind == "custom" and set(scale) == {"kind", "value"}:
        requested = _finite(scale["value"], "scale.value", 1.0e-12, 1_000.0)
    else:
        _error(
            "scale must be exactly page or custom with value.",
            "NATIVE_DRAWING_DRAFT_PARAMETERS_INVALID",
        )
    return DraftViewSpec(
        orientation=orientation,
        position_mm=_position(values["position_on_page_mm"]),
        scale_kind=kind,
        requested_scale=requested,
        style=_style(values["style"]),
    )


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _source_change_summary(
    expected: DraftSourceFingerprint,
    current: DraftSourceFingerprint,
) -> str:
    expected_members = {member.object_name: member for member in expected.members}
    current_members = {member.object_name: member for member in current.members}
    if expected_members.keys() != current_members.keys():
        return "dependency graph"
    changes = []
    for object_name in sorted(expected_members):
        before = expected_members[object_name]
        after = current_members[object_name]
        for scope, old_properties, new_properties in (
            ("object", before.app_properties, after.app_properties),
            ("presentation", before.view_properties, after.view_properties),
        ):
            old = {item.name: item for item in old_properties}
            new = {item.name: item for item in new_properties}
            for property_name in sorted(old.keys() | new.keys()):
                if old.get(property_name) != new.get(property_name):
                    changes.append(f"{object_name}.{scope}.{property_name}")
                    if len(changes) >= 8:
                        return ", ".join(changes)
    return ", ".join(changes) if changes else "persistent state"


def prepare_draft_view_create(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDraftView:
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
    source_target = values["source"]
    source = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": source_target["object_name"]},
    )
    try:
        fingerprint = draft_source_fingerprint(source)
    except ValueError as exc:
        raise NativeDrawingError(
            str(exc),
            error_code="NATIVE_DRAWING_DRAFT_SOURCE_INVALID",
        ) from exc
    if str(source_target["expected_state_sha256"]) != fingerprint.state_sha256:
        _error(
            "The exact Draft source changed after it was inspected.",
            "NATIVE_DRAWING_DRAFT_SOURCE_STALE",
            repair={"current_state_sha256": fingerprint.state_sha256},
        )
    _require_usable(document, page, "Drawing page")
    _require_usable(document, source, "Draft source")
    return PreparedDraftView(
        page=page,
        page_state_before=page_state,
        page_views_before=tuple(page.Views or ()),
        source=source,
        source_fingerprint=fingerprint,
        spec=spec,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_selection(document),
        visibility_before=_visibility(document),
    )


def validate_prepared_draft_view(document: Any, prepared: PreparedDraftView) -> None:
    if not isinstance(prepared, PreparedDraftView):
        raise TypeError("prepared must be a PreparedDraftView")
    page = _canonical(document, prepared.page)
    source = _canonical(document, prepared.source)
    if (
        page is None
        or source is None
        or _identities(tuple(document.Objects)) != _identities(prepared.objects_before)
        or _identities(_timeline_operations(document)) != _identities(prepared.timeline_before)
        or _identities(tuple(page.Views or ())) != _identities(prepared.page_views_before)
    ):
        _error(
            "The exact Drawing graph changed while the Draft view was rendered.",
            "NATIVE_DRAWING_DRAFT_STALE",
        )
    page_state = drawing_page_state(page)
    if page_state["state_sha256"] != prepared.page_state_before["state_sha256"]:
        _error(
            "The exact Drawing page changed while the Draft view was rendered.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    try:
        current = draft_source_fingerprint(source)
    except ValueError as exc:
        raise NativeDrawingError(
            str(exc),
            error_code="NATIVE_DRAWING_DRAFT_SOURCE_INVALID",
        ) from exc
    if current.state_sha256 != prepared.source_fingerprint.state_sha256:
        changed = _source_change_summary(prepared.source_fingerprint, current)
        _error(
            "The exact Draft source changed during rendering: " + changed + ".",
            "NATIVE_DRAWING_DRAFT_SOURCE_STALE",
            repair={"current_state_sha256": current.state_sha256},
        )


def capture_draft_view_commit_state(
    document: Any,
    prepared: PreparedDraftView,
) -> PreparedDraftView:
    validate_prepared_draft_view(document, prepared)
    page = _canonical(document, prepared.page)
    source = _canonical(document, prepared.source)
    if page is None or source is None:
        _error(
            "The exact Draft view inputs became unavailable before publication.",
            "NATIVE_DRAWING_DRAFT_STALE",
        )
    return replace(
        prepared,
        page=page,
        page_views_before=tuple(page.Views or ()),
        source=source,
        source_fingerprint=draft_source_fingerprint(source),
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_selection(document),
        visibility_before=_visibility(document),
    )


def _color_floats(style: DraftViewStyle) -> tuple[float, float, float]:
    return tuple(component / 255.0 for component in style.color_rgb)


def create_draft_view(
    document: Any,
    *,
    prepared: PreparedDraftView,
    symbol: str,
) -> NativeMutationDraft:
    import FreeCAD as App

    if not isinstance(prepared, PreparedDraftView):
        raise TypeError("prepared must be a PreparedDraftView")
    view = document.addObject("TechDraw::DrawViewDraft", "DraftView")
    if not is_draft_drawing_view(view):
        _error(
            "The Draft drawing view factory returned the wrong object type.",
            "NATIVE_DRAWING_DRAFT_CREATE_FAILED",
        )
    spec = prepared.spec
    style = spec.style
    direction, _x_direction = DRAWING_VIEW_ORIENTATIONS[spec.orientation]
    view.Source = prepared.source
    view.Direction = App.Vector(*direction)
    view.X, view.Y = spec.position_mm
    # DrawViewDraft is intentionally Custom even when it snapshots the page scale;
    # this is the durable behavior of the shipped human command.
    view.ScaleType = "Custom"
    view.Scale = (
        float(prepared.page_state_before["scale"])
        if spec.requested_scale is None
        else spec.requested_scale
    )
    view.LineWidth = style.line_width
    view.FontSize = style.font_size
    view.Color = _color_floats(style)
    view.LineStyle = style.line_style
    view.LineSpacing = style.line_spacing
    view.OverrideStyle = style.override
    adopt = getattr(view, "setPrecomputedDraft", None)
    if not callable(adopt):
        _error(
            "The TechDraw Draft view does not support authenticated worker results.",
            "NATIVE_DRAWING_DRAFT_CREATE_FAILED",
        )
    adopt(
        {
            "symbol": symbol,
            "source_state_sha256": prepared.source_fingerprint.state_sha256,
        }
    )
    document.publishProvisionalTimelineOperationBlock(view, (), ())
    if int(prepared.page.addPrecomputedView(view)) < 1:
        _error(
            "The Draft drawing view could not join its exact page.",
            "NATIVE_DRAWING_DRAFT_CREATE_FAILED",
        )
    view.purgeTouched()
    return NativeMutationDraft(
        value={"prepared": prepared, "view": view},
        recompute_targets=(prepared.page,),
        created=(object_identity(view),),
        changed=(object_identity(prepared.page),),
    )


def _same_vector(actual: Any, expected: tuple[float, float, float]) -> bool:
    return all(
        math.isclose(float(getattr(actual, name)), value, abs_tol=1.0e-10)
        for name, value in zip(("x", "y", "z"), expected, strict=True)
    )


def _same_color(actual: Any, expected: tuple[float, float, float]) -> bool:
    values = tuple(float(value) for value in actual)[:3]
    return len(values) == 3 and all(
        math.isclose(value, wanted, abs_tol=1.0 / 255.0 + 1.0e-8)
        for value, wanted in zip(values, expected, strict=True)
    )


def _assert_presentation(document: Any, prepared: PreparedDraftView) -> None:
    if _selection(document) != prepared.selection_before:
        _error(
            "Draft view creation changed the human selection.",
            "NATIVE_DRAWING_DRAFT_POSTCONDITION_FAILED",
        )
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if visibility != prepared.visibility_before:
        _error(
            "Draft view creation changed existing object visibility.",
            "NATIVE_DRAWING_DRAFT_POSTCONDITION_FAILED",
        )


def verify_draft_view_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDraftView = draft.value["prepared"]
    view = draft.value["view"]
    spec = prepared.spec
    style = spec.style
    existing = set(_identities(prepared.objects_before))
    created = tuple(obj for obj in document.Objects if _identity(obj) not in existing)
    direction, _x_direction = DRAWING_VIEW_ORIENTATIONS[spec.orientation]
    effective_scale = (
        float(prepared.page_state_before["scale"])
        if spec.requested_scale is None
        else spec.requested_scale
    )
    if (
        _identities(created) != (_identity(view),)
        or not is_drawing_page(prepared.page)
        or not is_draft_drawing_view(view)
        or view.Source is not prepared.source
        or draft_source_fingerprint(view.Source).state_sha256
        != prepared.source_fingerprint.state_sha256
        or _identities(tuple(prepared.page.Views or ()))
        != _identities((*prepared.page_views_before, view))
        or not _same_vector(view.Direction, direction)
        or not math.isclose(float(view.X), spec.position_mm[0], abs_tol=1.0e-9)
        or not math.isclose(float(view.Y), spec.position_mm[1], abs_tol=1.0e-9)
        or str(view.ScaleType) != "Custom"
        or not math.isclose(float(view.Scale), effective_scale, abs_tol=1.0e-12)
        or not math.isclose(float(view.LineWidth), style.line_width, abs_tol=1.0e-9)
        or not math.isclose(float(view.FontSize), style.font_size, abs_tol=1.0e-9)
        or not _same_color(view.Color, _color_floats(style))
        or str(view.LineStyle) != style.line_style
        or not math.isclose(float(view.LineSpacing), style.line_spacing, abs_tol=1.0e-9)
        or bool(view.OverrideStyle) is not style.override
        or not str(view.Symbol or "")
        or view.getPrecomputedDraft()
        != {
            "symbol": str(view.Symbol),
            "source_state_sha256": prepared.source_fingerprint.state_sha256,
        }
        or str(getattr(view, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(view, "VibeCADTimelineOwner", None) is not None
        or _identities(_timeline_operations(document))
        != _identities((*prepared.timeline_before, view))
        or not bool(view.isValid())
    ):
        _error(
            "The Draft drawing view did not retain its exact source, placement, scale, or style.",
            "NATIVE_DRAWING_DRAFT_POSTCONDITION_FAILED",
        )
    state = drawing_draft_view_state(view)
    symbol = str(view.Symbol)
    if (
        state["source"]["state_sha256"] != prepared.source_fingerprint.state_sha256
        or state["svg_bytes"] < 32
        or state["svg_sha256"] != hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    ):
        _error(
            "The Draft drawing view produced no authenticated SVG geometry.",
            "NATIVE_DRAWING_DRAFT_RENDER_FAILED",
        )
    page_state = drawing_page_state(prepared.page)
    if (
        page_state["view_count"] != prepared.page_state_before["view_count"] + 1
        or page_state["view_names"][-1:] != [str(view.Name)]
    ):
        _error(
            "The exact Drawing page did not retain the new Draft view.",
            "NATIVE_DRAWING_DRAFT_POSTCONDITION_FAILED",
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
