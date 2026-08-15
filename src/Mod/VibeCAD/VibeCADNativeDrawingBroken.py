# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preflight, publication, and verification for Native broken views."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeDrawingView import (
    PreparedStandardView,
    _apply_line_style,
    _current_selection,
    capture_standard_view_commit_state,
    prepare_standard_view_create,
    validate_prepared_standard_view,
)
from VibeCADNativeDrawingViewState import (
    DRAWING_VIEW_ORIENTATIONS,
    MAX_DRAWING_BREAKS,
    drawing_break_state,
    drawing_view_state,
    is_broken_drawing_view,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedBrokenView:
    base: PreparedStandardView
    breaks: tuple[Any, ...]
    break_states: tuple[dict[str, Any], ...]
    gap_mm: float


def _identity(obj: Any) -> tuple[int, str, str]:
    return (
        int(getattr(obj, "ID", -1)),
        str(getattr(obj, "Name", "") or ""),
        str(getattr(obj, "TypeId", "") or ""),
    )


def _normalized(values: tuple[float, float, float]) -> tuple[float, float, float]:
    length = sum(value * value for value in values) ** 0.5
    if length <= 1.0e-12:
        raise NativeDrawingError(
            "A broken-view orientation contains a zero direction.",
            error_code="NATIVE_DRAWING_BROKEN_PARAMETERS_INVALID",
        )
    return tuple(value / length for value in values)


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _validate_break_orientation(
    state: Mapping[str, Any],
    orientation: str,
) -> None:
    if str(state["kind"]) != "two_line_sketch":
        return
    direction, x_direction = DRAWING_VIEW_ORIENTATIONS[orientation]
    side = _normalized(tuple(float(value) for value in x_direction))
    up = _normalized(_cross(_normalized(tuple(direction)), side))
    for raw in state["line_directions"]:
        line = _normalized(tuple(float(value) for value in raw))
        side_dot = abs(sum(a * b for a, b in zip(line, side, strict=True)))
        up_dot = abs(sum(a * b for a, b in zip(line, up, strict=True)))
        if max(side_dot, up_dot) < 1.0 - 1.0e-7:
            raise NativeDrawingError(
                f"Break sketch {state['object_name']!r} is not horizontal or vertical "
                f"in the requested {orientation} projection.",
                error_code="NATIVE_DRAWING_BREAK_ORIENTATION_INVALID",
            )


def prepare_broken_view_create(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedBrokenView:
    base = prepare_standard_view_create(document, values=values)
    raw_gap = float(values["gap_mm"])
    if not math.isfinite(raw_gap) or not 0.0 <= raw_gap <= 10_000.0:
        raise NativeDrawingError(
            "Broken-view gap_mm must be finite and between 0 and 10000.",
            error_code="NATIVE_DRAWING_BROKEN_PARAMETERS_INVALID",
        )
    targets = tuple(values["breaks"])
    if not 1 <= len(targets) <= MAX_DRAWING_BREAKS:
        raise NativeDrawingError(
            f"A broken view requires 1 to {MAX_DRAWING_BREAKS} break definitions.",
            error_code="NATIVE_DRAWING_BREAK_LIMIT",
        )
    names = tuple(str(target["object_name"]) for target in targets)
    if len(names) != len(set(names)):
        raise NativeDrawingError(
            "Each Drawing break definition may appear only once.",
            error_code="NATIVE_DRAWING_BREAKS_INVALID",
        )
    source_names = {str(source.Name) for source in base.sources}
    if source_names.intersection(names):
        raise NativeDrawingError(
            "A broken-view source cannot also be one of its break definitions.",
            error_code="NATIVE_DRAWING_BREAKS_INVALID",
        )
    breaks = []
    states = []
    for target in targets:
        obj = resolve_object(
            document,
            {
                "document_uid": str(document.Uid),
                "object_name": target["object_name"],
            },
        )
        try:
            state = drawing_break_state(obj)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_BREAK_INVALID",
            ) from exc
        if str(target["expected_state_sha256"]) != state["state_sha256"]:
            raise NativeDrawingError(
                f"Drawing break definition {obj.Name!r} changed after inspection.",
                error_code="NATIVE_DRAWING_BREAK_STALE",
                repair={
                    "object_name": str(obj.Name),
                    "current_state_sha256": state["state_sha256"],
                },
            )
        _validate_break_orientation(state, base.spec.orientation)
        breaks.append(obj)
        states.append(state)
    return PreparedBrokenView(
        base=base,
        breaks=tuple(breaks),
        break_states=tuple(states),
        gap_mm=raw_gap,
    )


def validate_prepared_broken_view(
    document: Any,
    prepared: PreparedBrokenView,
) -> None:
    if not isinstance(prepared, PreparedBrokenView):
        raise TypeError("prepared must be a PreparedBrokenView")
    validate_prepared_standard_view(document, prepared.base)
    for obj, expected in zip(
        prepared.breaks,
        prepared.break_states,
        strict=True,
    ):
        current = document.getObject(str(expected["object_name"]))
        if current is None or _identity(current) != _identity(obj):
            raise NativeDrawingError(
                f"Drawing break definition {expected['object_name']!r} is unavailable.",
                error_code="NATIVE_DRAWING_BREAK_STALE",
            )
        state = drawing_break_state(current)
        if state["state_sha256"] != expected["state_sha256"]:
            raise NativeDrawingError(
                f"Drawing break definition {current.Name!r} changed during projection.",
                error_code="NATIVE_DRAWING_BREAK_STALE",
                repair={
                    "object_name": str(current.Name),
                    "current_state_sha256": state["state_sha256"],
                },
            )
        _validate_break_orientation(state, prepared.base.spec.orientation)


def capture_broken_view_commit_state(
    document: Any,
    prepared: PreparedBrokenView,
) -> PreparedBrokenView:
    validate_prepared_broken_view(document, prepared)
    return replace(
        prepared,
        base=capture_standard_view_commit_state(document, prepared.base),
    )


def create_broken_view(
    document: Any,
    *,
    prepared: PreparedBrokenView,
    projection_snapshot: Mapping[str, Any],
    worker_breaks: tuple[Mapping[str, Any], ...],
) -> NativeMutationDraft:
    import FreeCAD as App

    if not isinstance(prepared, PreparedBrokenView):
        raise TypeError("prepared must be a PreparedBrokenView")
    base = prepared.base
    spec = base.spec
    view = document.addObject("TechDraw::DrawBrokenView", "BrokenView")
    if not is_broken_drawing_view(view):
        raise NativeDrawingError(
            "The broken Drawing view factory returned the wrong object type.",
            error_code="NATIVE_DRAWING_BROKEN_CREATE_FAILED",
        )
    direction, x_direction = DRAWING_VIEW_ORIENTATIONS[spec.orientation]
    view.Label = spec.label
    view.Source = list(base.sources)
    view.Breaks = list(prepared.breaks)
    view.Gap = prepared.gap_mm
    view.Direction = App.Vector(*direction)
    view.XDirection = App.Vector(*x_direction)
    view.X = spec.x_mm
    view.Y = spec.y_mm
    view.ScaleType = "Page" if spec.scale_kind == "page" else "Custom"
    view.Scale = (
        float(base.page_state_before["scale"])
        if spec.scale is None
        else spec.scale
    )
    _apply_line_style(view, spec.line_style)
    document.publishProvisionalTimelineOperationBlock(view, (), ())
    setter = getattr(view, "setPrecomputedProjection", None)
    if not callable(setter):
        raise NativeDrawingError(
            "The installed TechDraw runtime cannot adopt detached broken-view geometry.",
            error_code="NATIVE_DRAWING_PROJECTION_RUNTIME_UNAVAILABLE",
        )
    setter(dict(projection_snapshot))
    # A live page may schedule HLR as soon as a touched view joins it.  Adopt
    # the authenticated cache after History enrollment but before page
    # membership, so its source signature is final and no competing HLR job
    # can race the atomic commit.
    if int(base.page.addPrecomputedView(view)) < 1:
        raise NativeDrawingError(
            "The broken Drawing view could not join its exact page.",
            error_code="NATIVE_DRAWING_BROKEN_CREATE_FAILED",
        )
    view.purgeTouched()
    base.page.requestPaint()
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "view": view,
            "worker_breaks": worker_breaks,
        },
        # The detached cache is the completed recompute.  Recomputing the page
        # here would execute DrawBrokenView again on the UI thread and can race
        # TechDraw's HLR machinery.  Page membership and the persisted cache
        # are already transaction-owned property changes.
        recompute_targets=(),
        created=(object_identity(view),),
        changed=(object_identity(base.page),),
    )


def _same_vector(actual: Any, expected: tuple[float, float, float]) -> bool:
    return all(
        math.isclose(float(getattr(actual, name)), value, abs_tol=1.0e-10)
        for name, value in zip(("x", "y", "z"), expected, strict=True)
    )


def verify_broken_view_create(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedBrokenView = draft.value["prepared"]
    view = draft.value["view"]
    base = prepared.base
    spec = base.spec
    object_ids_before = {_identity(obj) for obj in base.objects_before}
    new_objects = tuple(
        obj for obj in document.Objects if _identity(obj) not in object_ids_before
    )
    if len(new_objects) != 1 or _identity(new_objects[0]) != _identity(view):
        raise NativeDrawingError(
            "Broken-view creation changed objects outside its exact view.",
            error_code="NATIVE_DRAWING_BROKEN_POSTCONDITION_FAILED",
        )
    direction, x_direction = DRAWING_VIEW_ORIENTATIONS[spec.orientation]
    source_identities = tuple(_identity(item) for item in tuple(view.Source or ()))
    break_identities = tuple(_identity(item) for item in tuple(view.Breaks or ()))
    expected_scale_type = "Page" if spec.scale_kind == "page" else "Custom"
    page_views = tuple(getattr(base.page, "Views", ()) or ())
    timeline = document.getObject("VibeCADTimeline")
    timeline_operations = tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()
    if (
        not is_drawing_page(base.page)
        or not is_broken_drawing_view(view)
        or str(view.Label) != spec.label
        or source_identities != tuple(_identity(item) for item in base.sources)
        or break_identities != tuple(_identity(item) for item in prepared.breaks)
        or not math.isclose(float(view.Gap), prepared.gap_mm, abs_tol=1.0e-9)
        or tuple(_identity(item) for item in page_views)
        != tuple(_identity(item) for item in (*base.page_views_before, view))
        or not _same_vector(view.Direction, direction)
        or not _same_vector(view.XDirection, x_direction)
        or not math.isclose(float(view.X), spec.x_mm, abs_tol=1.0e-9)
        or not math.isclose(float(view.Y), spec.y_mm, abs_tol=1.0e-9)
        or str(view.ScaleType) != expected_scale_type
        or (spec.scale is not None and not math.isclose(float(view.Scale), spec.scale))
        or str(getattr(view, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(view, "VibeCADTimelineOwner", None) is not None
        or tuple(_identity(item) for item in timeline_operations)
        != tuple(_identity(item) for item in (*base.timeline_before, view))
        or not bool(view.isValid())
    ):
        raise NativeDrawingError(
            "The broken Drawing view did not retain its exact authored state.",
            error_code="NATIVE_DRAWING_BROKEN_POSTCONDITION_FAILED",
        )
    state = drawing_view_state(view)
    worker_breaks = tuple(draft.value["worker_breaks"])
    expected_worker_breaks = tuple(
        (str(item["object_name"]), str(item["kind"]))
        for item in prepared.break_states
    )
    actual_worker_breaks = tuple(
        (str(item.get("object_name") or ""), str(item.get("kind") or ""))
        for item in worker_breaks
    )
    if (
        state["visible_edge_count"] is None
        or state["visible_edge_count"] < 1
        or len(state.get("breaks", ())) != len(prepared.breaks)
        or actual_worker_breaks != expected_worker_breaks
        or any(
            not math.isfinite(float(item.get("removed_length_mm", 0.0)))
            or float(item.get("removed_length_mm", 0.0)) <= 1.0e-9
            for item in worker_breaks
        )
    ):
        raise NativeDrawingError(
            "The broken Drawing view produced no verified broken geometry.",
            error_code="NATIVE_DRAWING_BROKEN_PROJECTION_FAILED",
        )
    if _current_selection(document) != base.selection_before:
        raise NativeDrawingError(
            "Broken-view creation changed the human selection.",
            error_code="NATIVE_DRAWING_BROKEN_POSTCONDITION_FAILED",
        )
    if tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in base.visibility_before
    ) != base.visibility_before:
        raise NativeDrawingError(
            "Broken-view creation changed existing object visibility.",
            error_code="NATIVE_DRAWING_BROKEN_POSTCONDITION_FAILED",
        )
    page_state = drawing_page_state(base.page)
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "view": state,
    }
