# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standard projected-view creation for Native Drawing."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingHistory import require_drawing_source_history_usable
from VibeCADNativeDrawingDimensionSupport import drawing_position_within_page_bounds
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeDrawingViewState import (
    DRAWING_VIEW_ORIENTATIONS,
    drawing_source_state,
    drawing_view_state,
    is_part_drawing_view,
)
from VibeCADNativeGeometrySources import drawing_source_exclusion_reason
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


@dataclass(frozen=True, slots=True)
class StandardViewSpec:
    label: str
    orientation: str
    x_mm: float
    y_mm: float
    scale_kind: str
    scale: float | None
    line_style: str


@dataclass(frozen=True, slots=True)
class PreparedStandardView:
    page: Any
    page_state_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    sources: tuple[Any, ...]
    source_states: tuple[dict[str, Any], ...]
    spec: StandardViewSpec
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


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


def _canonical_object(document: Any, obj: Any) -> Any | None:
    current = document.getObject(str(getattr(obj, "Name", "") or ""))
    return current if current is not None and _identity(current) == _identity(obj) else None


def _visibility(document: Any) -> tuple[tuple[Any, bool], ...]:
    result = []
    for obj in tuple(document.Objects):
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            result.append((obj, bool(getattr(view, "Visibility", False))))
    return tuple(result)


def _current_selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _finite(value: Any, *, name: str, minimum: float, maximum: float) -> float:
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise NativeDrawingError(
            f"{name} must be finite and between {minimum:g} and {maximum:g}.",
            error_code="NATIVE_DRAWING_VIEW_PARAMETERS_INVALID",
        )
    return result


def _spec(values: Mapping[str, Any]) -> StandardViewSpec:
    label = str(values["label"] or "").strip()
    if not label or len(label) > 160:
        raise NativeDrawingError(
            "A Drawing view label must contain 1 to 160 characters.",
            error_code="NATIVE_DRAWING_VIEW_PARAMETERS_INVALID",
        )
    orientation = str(values["orientation"] or "")
    if orientation not in DRAWING_VIEW_ORIENTATIONS:
        raise NativeDrawingError(
            "A Drawing view requires one published standard orientation.",
            error_code="NATIVE_DRAWING_VIEW_PARAMETERS_INVALID",
        )
    position = values["position"]
    if not isinstance(position, Mapping) or set(position) != {"x_mm", "y_mm"}:
        raise NativeDrawingError(
            "Drawing view position requires only x_mm and y_mm.",
            error_code="NATIVE_DRAWING_VIEW_PARAMETERS_INVALID",
        )
    scale_value = values.get("scale", "page")
    if scale_value == "page":
        scale_kind = "page"
        scale = None
    elif (
        isinstance(scale_value, Mapping)
        and set(scale_value) == {"kind"}
        and scale_value["kind"] == "page"
    ):
        scale_kind = "page"
        scale = None
    elif (
        isinstance(scale_value, Mapping)
        and set(scale_value) == {"kind", "value"}
        and scale_value["kind"] == "custom"
    ):
        scale_kind = "custom"
        scale = _finite(
            scale_value["value"],
            name="Drawing view scale",
            minimum=1.0e-12,
            maximum=1_000.0,
        )
    elif type(scale_value) in {int, float}:
        scale_kind = "custom"
        scale = _finite(
            scale_value,
            name="Drawing view scale",
            minimum=1.0e-12,
            maximum=1_000.0,
        )
    else:
        raise NativeDrawingError(
            "Drawing view scale must select page or a positive custom value.",
            error_code="NATIVE_DRAWING_VIEW_PARAMETERS_INVALID",
        )
    line_style = str(values["line_style"] or "")
    if line_style not in {"visible", "visible_and_hidden", "hard_only"}:
        raise NativeDrawingError(
            "Drawing view line_style must be visible, visible_and_hidden, or hard_only.",
            error_code="NATIVE_DRAWING_VIEW_PARAMETERS_INVALID",
        )
    return StandardViewSpec(
        label=label,
        orientation=orientation,
        x_mm=_finite(
            position["x_mm"],
            name="Drawing view x_mm",
            minimum=-10_000.0,
            maximum=10_000.0,
        ),
        y_mm=_finite(
            position["y_mm"],
            name="Drawing view y_mm",
            minimum=-10_000.0,
            maximum=10_000.0,
        ),
        scale_kind=scale_kind,
        scale=scale,
        line_style=line_style,
    )


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        raise NativeDrawingError(
            f"The exact {noun} is not usable at the current History position.",
            error_code="NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _require_source_in_drawing_scope(document: Any, source: Any) -> None:
    reason = drawing_source_exclusion_reason(document, source)
    if reason is None:
        return
    if reason == "analysis_artifact":
        raise NativeDrawingError(
            f"Analysis artifact {source.Name!r} cannot be used as Drawing geometry.",
            error_code="NATIVE_DRAWING_VIEW_SOURCE_ANALYSIS_ARTIFACT",
        )
    raise NativeDrawingError(
        f"Drawing source {source.Name!r} is hidden from the Drawing workspace.",
        error_code="NATIVE_DRAWING_VIEW_SOURCE_HIDDEN",
        repair={"object_name": str(source.Name), "unhide_before_retry": True},
    )


def prepare_standard_view_create(
    document: Any,
    *,
    values: Mapping[str, Any],
    validate_position: bool = True,
) -> PreparedStandardView:
    spec = _spec(values)
    page_target = values["page"]
    page = resolve_object(
        document,
        {
            "document_uid": str(document.Uid),
            "object_name": page_target["object_name"],
        },
        expected_types=("TechDraw::DrawPage",),
    )
    page_state = drawing_page_state(page)
    if str(page_target["expected_state_sha256"]) != page_state["state_sha256"]:
        raise NativeDrawingError(
            "The exact Drawing page changed after it was inspected.",
            error_code="NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    if validate_position:
        drawing_position_within_page_bounds(
            page,
            {"x_mm": spec.x_mm, "y_mm": spec.y_mm},
            noun="view",
            error_code="NATIVE_DRAWING_VIEW_POSITION_INVALID",
        )
    source_targets = tuple(values["sources"])
    names = tuple(str(target["object_name"]) for target in source_targets)
    if len(names) != len(set(names)):
        raise NativeDrawingError(
            "Each Drawing view source may appear only once.",
            error_code="NATIVE_DRAWING_VIEW_SOURCES_INVALID",
        )
    sources = []
    states = []
    for target in source_targets:
        source = resolve_object(
            document,
            {
                "document_uid": str(document.Uid),
                "object_name": target["object_name"],
            },
        )
        _require_source_in_drawing_scope(document, source)
        require_drawing_source_history_usable(document, source)
        try:
            state = drawing_source_state(source)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_VIEW_SOURCE_INVALID",
            ) from exc
        if str(target["expected_state_sha256"]) != state["state_sha256"]:
            raise NativeDrawingError(
                f"Drawing source {source.Name!r} changed after it was inspected.",
                error_code="NATIVE_DRAWING_VIEW_SOURCE_STALE",
                repair={
                    "object_name": str(source.Name),
                    "current_state_sha256": state["state_sha256"],
                },
            )
        sources.append(source)
        states.append(state)
    return PreparedStandardView(
        page=page,
        page_state_before=page_state,
        page_views_before=tuple(getattr(page, "Views", ()) or ()),
        sources=tuple(sources),
        source_states=tuple(states),
        spec=spec,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_current_selection(document),
        visibility_before=_visibility(document),
    )


def validate_prepared_standard_view(
    document: Any,
    prepared: PreparedStandardView,
) -> None:
    """Revalidate every structural input immediately before background adoption."""

    if not isinstance(prepared, PreparedStandardView):
        raise TypeError("prepared must be a PreparedStandardView")
    page = _canonical_object(document, prepared.page)
    if (
        page is None
        or _identities(tuple(document.Objects)) != _identities(prepared.objects_before)
        or _identities(_timeline_operations(document))
        != _identities(prepared.timeline_before)
        or _identities(tuple(getattr(page, "Views", ()) or ()))
        != _identities(prepared.page_views_before)
    ):
        raise NativeDrawingError(
            "The exact Drawing page or document structure changed during projection.",
            error_code="NATIVE_DRAWING_VIEW_STALE",
        )
    page_state = drawing_page_state(page)
    if page_state["state_sha256"] != prepared.page_state_before["state_sha256"]:
        raise NativeDrawingError(
            "The exact Drawing page changed during projection.",
            error_code="NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    for source, expected in zip(
        prepared.sources,
        prepared.source_states,
        strict=True,
    ):
        current_source = _canonical_object(document, source)
        if current_source is None:
            raise NativeDrawingError(
                f"Drawing source {expected['object_name']!r} is no longer available.",
                error_code="NATIVE_DRAWING_VIEW_SOURCE_STALE",
            )
        _require_source_in_drawing_scope(document, current_source)
        current = drawing_source_state(current_source)
        if current["state_sha256"] != expected["state_sha256"]:
            raise NativeDrawingError(
                f"Drawing source {current_source.Name!r} changed during projection.",
                error_code="NATIVE_DRAWING_VIEW_SOURCE_STALE",
                repair={
                    "object_name": str(source.Name),
                    "current_state_sha256": current["state_sha256"],
                },
            )


def capture_standard_view_commit_state(
    document: Any,
    prepared: PreparedStandardView,
) -> PreparedStandardView:
    """Capture presentation state at commit time after exact revalidation."""

    validate_prepared_standard_view(document, prepared)
    page = _canonical_object(document, prepared.page)
    sources = tuple(_canonical_object(document, source) for source in prepared.sources)
    if page is None or any(source is None for source in sources):
        raise NativeDrawingError(
            "The exact Drawing page or source became unavailable before commit.",
            error_code="NATIVE_DRAWING_VIEW_STALE",
        )
    return replace(
        prepared,
        page=page,
        page_views_before=tuple(getattr(page, "Views", ()) or ()),
        sources=sources,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_current_selection(document),
        visibility_before=_visibility(document),
    )


def _apply_line_style(view: Any, style: str) -> None:
    for name, value in standard_view_line_flags(style).items():
        setattr(view, name, value)


def standard_view_line_flags(style: str) -> dict[str, bool]:
    if style not in {"visible", "visible_and_hidden", "hard_only"}:
        raise ValueError("style must be one published Drawing line style")
    return {
        "SmoothVisible": style != "hard_only",
        "SeamVisible": False,
        "IsoVisible": False,
        "HardHidden": style == "visible_and_hidden",
        "SmoothHidden": style == "visible_and_hidden",
        "SeamHidden": False,
        "IsoHidden": False,
    }


def create_standard_view(
    document: Any,
    *,
    prepared: PreparedStandardView,
    projection_snapshot: Mapping[str, Any],
) -> NativeMutationDraft:
    import FreeCAD as App

    if not isinstance(prepared, PreparedStandardView):
        raise TypeError("prepared must be a PreparedStandardView")
    view = document.addObject("TechDraw::DrawViewPart", "View")
    if not is_part_drawing_view(view):
        raise NativeDrawingError(
            "The standard Drawing view factory returned the wrong object type.",
            error_code="NATIVE_DRAWING_VIEW_CREATE_FAILED",
        )
    spec = prepared.spec
    direction, x_direction = DRAWING_VIEW_ORIENTATIONS[spec.orientation]
    view.Label = spec.label
    view.Source = list(prepared.sources)
    view.Direction = App.Vector(*direction)
    view.XDirection = App.Vector(*x_direction)
    view.X = spec.x_mm
    view.Y = spec.y_mm
    view.ScaleType = "Page" if spec.scale_kind == "page" else "Custom"
    view.Scale = (
        float(prepared.page_state_before["scale"])
        if spec.scale is None
        else spec.scale
    )
    _apply_line_style(view, spec.line_style)
    document.publishProvisionalTimelineOperationBlock(view, (), ())
    if int(prepared.page.addPrecomputedView(view)) < 1:
        raise NativeDrawingError(
            "The standard Drawing view could not join its exact page.",
            error_code="NATIVE_DRAWING_VIEW_CREATE_FAILED",
        )
    setter = getattr(view, "setPrecomputedProjection", None)
    if not callable(setter):
        raise NativeDrawingError(
            "The installed TechDraw runtime cannot adopt detached projection state.",
            error_code="NATIVE_DRAWING_PROJECTION_RUNTIME_UNAVAILABLE",
        )
    setter(dict(projection_snapshot))
    # The adopted cache represents every touched projection property above.
    # Leaving the view dirty would immediately launch a redundant GUI HLR job.
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


def _assert_presentation_unchanged(
    document: Any,
    prepared: PreparedStandardView,
) -> None:
    if _current_selection(document) != prepared.selection_before:
        raise NativeDrawingError(
            "Standard Drawing view creation changed the human selection.",
            error_code="NATIVE_DRAWING_VIEW_POSTCONDITION_FAILED",
        )
    actual = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if actual != prepared.visibility_before:
        raise NativeDrawingError(
            "Standard Drawing view creation changed existing object visibility.",
            error_code="NATIVE_DRAWING_VIEW_POSTCONDITION_FAILED",
        )


def verify_standard_view_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedStandardView = draft.value["prepared"]
    view = draft.value["view"]
    spec = prepared.spec
    object_ids_before = set(_identities(prepared.objects_before))
    new_objects = tuple(
        obj for obj in document.Objects if _identity(obj) not in object_ids_before
    )
    if _identities(new_objects) != (_identity(view),):
        raise NativeDrawingError(
            "Standard Drawing view creation changed objects outside its exact view.",
            error_code="NATIVE_DRAWING_VIEW_POSTCONDITION_FAILED",
        )
    direction, x_direction = DRAWING_VIEW_ORIENTATIONS[spec.orientation]
    sources_now = tuple(getattr(view, "Source", ()) or ())
    source_states_now = tuple(drawing_source_state(source) for source in sources_now)
    page_views = tuple(getattr(prepared.page, "Views", ()) or ())
    expected_scale_type = "Page" if spec.scale_kind == "page" else "Custom"
    if (
        not is_drawing_page(prepared.page)
        or not is_part_drawing_view(view)
        or str(view.Label) != spec.label
        or _identities(sources_now) != _identities(prepared.sources)
        or tuple(state["state_sha256"] for state in source_states_now)
        != tuple(state["state_sha256"] for state in prepared.source_states)
        or _identities(page_views)
        != _identities((*prepared.page_views_before, view))
        or not _same_vector(view.Direction, direction)
        or not _same_vector(view.XDirection, x_direction)
        or not math.isclose(float(view.X), spec.x_mm, abs_tol=1.0e-9)
        or not math.isclose(float(view.Y), spec.y_mm, abs_tol=1.0e-9)
        or str(view.ScaleType) != expected_scale_type
        or (spec.scale is not None and not math.isclose(float(view.Scale), spec.scale))
        or str(getattr(view, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(view, "VibeCADTimelineOwner", None) is not None
        or _identities(_timeline_operations(document))
        != _identities((*prepared.timeline_before, view))
        or not bool(view.isValid())
    ):
        raise NativeDrawingError(
            "The standard Drawing view did not retain its exact projected state.",
            error_code="NATIVE_DRAWING_VIEW_POSTCONDITION_FAILED",
        )
    view_state = drawing_view_state(view)
    visible_count = view_state["visible_edge_count"]
    if visible_count is None or visible_count < 1:
        raise NativeDrawingError(
            "The standard Drawing view produced no inspectable visible geometry.",
            error_code="NATIVE_DRAWING_VIEW_PROJECTION_FAILED",
        )
    page_state = drawing_page_state(prepared.page)
    if (
        page_state["view_count"] != prepared.page_state_before["view_count"] + 1
        or page_state["view_names"][-1:] != [str(view.Name)]
    ):
        raise NativeDrawingError(
            "The exact Drawing page did not retain the new standard view.",
            error_code="NATIVE_DRAWING_VIEW_POSTCONDITION_FAILED",
        )
    _assert_presentation_unchanged(document, prepared)
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "view": view_state,
    }
