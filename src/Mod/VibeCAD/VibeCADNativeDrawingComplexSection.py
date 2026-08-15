# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation, publication, and verification for complex sections."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingSection import (
    PreparedSectionView,
    capture_section_view_commit_state,
    prepare_section_view_create,
    validate_prepared_section_view,
)
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeDrawingViewState import (
    drawing_source_state,
    drawing_view_state,
    is_complex_section_drawing_view,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    object_identity,
    read_current_selection,
    resolve_object,
)


_STRATEGIES = {
    "offset": "Offset",
    "aligned": "Aligned",
    "no_parallel": "NoParallel",
}


@dataclass(frozen=True, slots=True)
class PreparedComplexSectionView:
    section: PreparedSectionView
    profile: Any
    profile_state_before: dict[str, Any]
    projection_strategy: str


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


def _canonical(document: Any, obj: Any) -> Any | None:
    current = document.getObject(str(getattr(obj, "Name", "") or ""))
    return current if current is not None and _identity(current) == _identity(obj) else None


def _identities(objects: tuple[Any, ...]) -> tuple[tuple[int, str, str], ...]:
    return tuple(_identity(obj) for obj in objects)


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {"document_uid": str(document.Uid), "selected_count": 0, "items": []}


def _assert_presentation(document: Any, common: PreparedSectionView) -> None:
    if _selection(document) != common.selection_before:
        _error(
            "Complex-section creation changed the human selection.",
            "NATIVE_DRAWING_COMPLEX_SECTION_POSTCONDITION_FAILED",
        )
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in common.visibility_before
    )
    if visibility != common.visibility_before:
        _error(
            "Complex-section creation changed existing object visibility.",
            "NATIVE_DRAWING_COMPLEX_SECTION_POSTCONDITION_FAILED",
        )


def _require_usable(document: Any, obj: Any) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            "The exact complex-section profile is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _profile_state(profile: Any, strategy: str) -> dict[str, Any]:
    try:
        state = drawing_source_state(profile)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeDrawingError(
            "A complex section requires one valid whole wire or edge profile object.",
            error_code="NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_INVALID",
        ) from exc
    if state["shape_type"] not in {"Wire", "Edge"}:
        _error(
            "A complex section profile must be exactly one whole wire or edge object.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_INVALID",
        )
    shape = profile.Shape
    edge_count = len(tuple(shape.Edges))
    if edge_count < 1 or edge_count > 10_000:
        _error(
            "A complex section profile must contain 1 to 10000 connected edges.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_INVALID",
        )
    closed = bool(getattr(shape, "isClosed", lambda: False)())
    if closed and strategy != "offset":
        _error(
            "A closed profile supports the offset strategy only; aligned strategies require an open path.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_INVALID",
            repair={"projection_strategy": "offset"},
        )
    return state


def prepare_complex_section_view_create(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedComplexSectionView:
    strategy = str(values.get("projection_strategy") or "")
    if strategy not in _STRATEGIES:
        _error(
            "projection_strategy must be offset, aligned, or no_parallel.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PARAMETERS_INVALID",
        )
    common = prepare_section_view_create(
        document,
        values={
            "label": values["label"],
            "symbol": values["symbol"],
            "page": values["page"],
            "base_view": values["base_view"],
            "section_origin_mm": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
            "view_direction_on_base": values["view_direction_on_base"],
            "scale": values["scale"],
        },
        allow_section_base=True,
    )
    target = values["profile"]
    profile = resolve_object(
        document,
        {
            "document_uid": str(document.Uid),
            "object_name": target["object_name"],
        },
    )
    _require_usable(document, profile)
    profile_state = _profile_state(profile, strategy)
    if str(target["expected_state_sha256"]) != profile_state["state_sha256"]:
        _error(
            "The exact complex-section profile changed after it was inspected.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_STALE",
            repair={"current_state_sha256": profile_state["state_sha256"]},
        )
    return PreparedComplexSectionView(
        section=common,
        profile=profile,
        profile_state_before=profile_state,
        projection_strategy=strategy,
    )


def validate_prepared_complex_section_view(
    document: Any,
    prepared: PreparedComplexSectionView,
) -> None:
    if not isinstance(prepared, PreparedComplexSectionView):
        raise TypeError("prepared must be a PreparedComplexSectionView")
    validate_prepared_section_view(document, prepared.section)
    profile = _canonical(document, prepared.profile)
    if profile is None:
        _error(
            "The exact complex-section profile became unavailable during computation.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_STALE",
        )
    current = _profile_state(profile, prepared.projection_strategy)
    if current["state_sha256"] != prepared.profile_state_before["state_sha256"]:
        _error(
            "The exact complex-section profile changed during computation.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_STALE",
            repair={"current_state_sha256": current["state_sha256"]},
        )


def capture_complex_section_view_commit_state(
    document: Any,
    prepared: PreparedComplexSectionView,
) -> PreparedComplexSectionView:
    validate_prepared_complex_section_view(document, prepared)
    profile = _canonical(document, prepared.profile)
    if profile is None:
        _error(
            "The exact complex-section profile became unavailable before publication.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_STALE",
        )
    return replace(
        prepared,
        section=capture_section_view_commit_state(document, prepared.section),
        profile=profile,
    )


def create_complex_section_view(
    document: Any,
    *,
    prepared: PreparedComplexSectionView,
    projection_snapshot: Mapping[str, Any],
    section_snapshot: Mapping[str, Any],
    effective_scale: float,
) -> NativeMutationDraft:
    import FreeCAD as App

    if not isinstance(prepared, PreparedComplexSectionView):
        raise TypeError("prepared must be a PreparedComplexSectionView")
    common = prepared.section
    view = document.addObject("TechDraw::DrawComplexSection", "ComplexSectionView")
    if not is_complex_section_drawing_view(view):
        _error(
            "The complex-section factory returned the wrong object type.",
            "NATIVE_DRAWING_COMPLEX_SECTION_CREATE_FAILED",
        )
    spec = common.spec
    view.Label = spec.label
    view.SectionSymbol = spec.symbol
    view.BaseView = common.base_view
    view.Source = list(common.sources)
    view.CuttingToolWireObject = prepared.profile
    view.ProjectionStrategy = _STRATEGIES[prepared.projection_strategy]
    view.SectionOrigin = App.Vector(0.0, 0.0, 0.0)
    view.SectionDirection = "Aligned"
    view.Direction = App.Vector(*spec.section_normal)
    view.SectionNormal = App.Vector(*spec.section_normal)
    view.XDirection = App.Vector(*spec.section_x_direction)
    view.Rotation = spec.rotation_degrees
    view.Scale = float(effective_scale)
    view.ScaleType = {
        "page": "Page",
        "automatic": "Automatic",
        "custom": "Custom",
    }[spec.scale_kind]
    view.FuseBeforeCut = common.fuse_before_cut
    view.TrimAfterCut = False
    view.UsePreviousCut = False
    for name, value in common.line_flags.items():
        setattr(view, name, bool(value))
    view.X = common.page_size_mm[0] / 2.0
    view.Y = common.page_size_mm[1] / 2.0
    document.publishProvisionalTimelineOperationBlock(view, (), ())
    if int(common.page.addPrecomputedView(view)) < 1:
        _error(
            "The complex section could not join its exact Drawing page.",
            "NATIVE_DRAWING_COMPLEX_SECTION_CREATE_FAILED",
        )
    projection_setter = getattr(view, "setPrecomputedProjection", None)
    section_setter = getattr(view, "setPrecomputedComplexSection", None)
    if not callable(projection_setter) or not callable(section_setter):
        _error(
            "The installed TechDraw runtime cannot adopt detached complex-section state.",
            "NATIVE_DRAWING_COMPLEX_SECTION_RUNTIME_UNAVAILABLE",
        )
    projection_setter(dict(projection_snapshot))
    section_setter(dict(section_snapshot))
    view.purgeTouched()
    return NativeMutationDraft(
        value={"prepared": prepared, "view": view},
        recompute_targets=(),
        created=(object_identity(view),),
        changed=(object_identity(common.page),),
    )


def _same_vector(actual: Any, expected: tuple[float, float, float]) -> bool:
    return all(
        math.isclose(float(getattr(actual, name)), value, abs_tol=1.0e-10)
        for name, value in zip(("x", "y", "z"), expected, strict=True)
    )


def verify_complex_section_view_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedComplexSectionView = draft.value["prepared"]
    view = draft.value["view"]
    common = prepared.section
    spec = common.spec
    existing = set(_identities(common.objects_before))
    created = tuple(obj for obj in document.Objects if _identity(obj) not in existing)
    page_views = tuple(common.page.Views or ())
    if (
        _identities(created) != (_identity(view),)
        or not is_drawing_page(common.page)
        or not is_complex_section_drawing_view(view)
        or view.BaseView is not common.base_view
        or _identities(tuple(view.Source)) != _identities(common.sources)
        or view.CuttingToolWireObject is not prepared.profile
        or str(view.ProjectionStrategy) != _STRATEGIES[prepared.projection_strategy]
        or str(view.Label) != spec.label
        or str(view.SectionSymbol) != spec.symbol
        or not _same_vector(view.SectionNormal, spec.section_normal)
        or not _same_vector(view.XDirection, spec.section_x_direction)
        or not math.isclose(float(view.Rotation), spec.rotation_degrees, abs_tol=1.0e-9)
        or _identities(page_views)
        != _identities((*common.page_views_before, view))
        or _identities(_timeline_operations(document))
        != _identities((*common.timeline_before, view))
        or str(getattr(view, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(view, "VibeCADTimelineOwner", None) is not None
        or not bool(view.isValid())
    ):
        _error(
            "The complex section did not retain its exact page, base, profile, or strategy.",
            "NATIVE_DRAWING_COMPLEX_SECTION_POSTCONDITION_FAILED",
        )
    state = drawing_view_state(view)
    section_state = state.get("section") or {}
    complex_state = section_state.get("complex") or {}
    if (
        not state["visible_edge_count"]
        or not section_state.get("section_face_count")
        or complex_state.get("profile", {}).get("state_sha256")
        != prepared.profile_state_before["state_sha256"]
        or complex_state.get("projection_strategy")
        != _STRATEGIES[prepared.projection_strategy]
    ):
        _error(
            "The complex section produced no inspectable projected or cut-surface geometry.",
            "NATIVE_DRAWING_COMPLEX_SECTION_PROJECTION_FAILED",
        )
    page_state = drawing_page_state(common.page)
    if (
        page_state["view_count"] != common.page_state_before["view_count"] + 1
        or page_state["view_names"][-1:] != [str(view.Name)]
    ):
        _error(
            "The exact Drawing page did not retain the new complex section.",
            "NATIVE_DRAWING_COMPLEX_SECTION_POSTCONDITION_FAILED",
        )
    _assert_presentation(document, common)
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "view": state,
    }
