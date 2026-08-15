# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional stack mutations for Native Drawing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingStackSchema import MAX_DRAWING_STACK_TARGETS
from VibeCADNativeDrawingStackState import (
    MAX_STACK_ORDER,
    MIN_STACK_ORDER,
    drawing_stack_state,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import is_drawing_view
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedDrawingStack:
    operation: str
    page: Any
    page_state_before: dict[str, Any]
    views: tuple[Any, ...]
    view_states_before: tuple[dict[str, Any], ...]
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]
    stack_orders_before: tuple[tuple[Any, int], ...]
    persistent_view_state_before: tuple[tuple[Any, tuple[Any, ...]], ...]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _visibility(document: Any) -> tuple[tuple[Any, bool], ...]:
    result = []
    for obj in tuple(document.Objects):
        view_object = getattr(obj, "ViewObject", None)
        if view_object is not None:
            result.append((obj, bool(getattr(view_object, "Visibility", False))))
    return tuple(result)


def _drawing_views(document: Any) -> tuple[Any, ...]:
    return tuple(obj for obj in tuple(document.Objects) if is_drawing_view(obj))


def _stack_orders(document: Any) -> tuple[tuple[Any, int], ...]:
    result = []
    for view in _drawing_views(document):
        view_object = getattr(view, "ViewObject", None)
        if view_object is not None and hasattr(view_object, "StackOrder"):
            result.append((view, int(view_object.StackOrder)))
    return tuple(result)


def _persistent_view_state(view: Any) -> tuple[Any, ...]:
    page = view.findParentPage()
    view_object = getattr(view, "ViewObject", None)
    return (
        str(getattr(view, "Name", "") or ""),
        str(getattr(view, "Label", "") or ""),
        str(getattr(view, "TypeId", "") or ""),
        str(getattr(page, "Name", "") or "") if page else "",
        round(float(getattr(view, "X", 0.0)), 9),
        round(float(getattr(view, "Y", 0.0)), 9),
        round(float(getattr(view, "Scale", 1.0)), 12),
        str(getattr(view, "ScaleType", "") or ""),
        round(float(getattr(view, "Rotation", 0.0)), 9),
        bool(getattr(view, "LockPosition", False)),
        bool(getattr(view_object, "KeepLabel", False)) if view_object else False,
    )


def _persistent_view_states(
    document: Any,
) -> tuple[tuple[Any, tuple[Any, ...]], ...]:
    return tuple((view, _persistent_view_state(view)) for view in _drawing_views(document))


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _resolve_page(
    document: Any,
    target: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": target["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(target["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    return page, state


def _resolve_views(
    document: Any,
    page: Any,
    targets: tuple[Mapping[str, Any], ...],
) -> tuple[tuple[Any, ...], tuple[dict[str, Any], ...]]:
    if not 1 <= len(targets) <= MAX_DRAWING_STACK_TARGETS:
        _error(
            "A Drawing stack operation requires 1 to 32 exact views.",
            "NATIVE_DRAWING_STACK_TARGETS_INVALID",
        )
    names = tuple(str(target.get("object_name") or "") for target in targets)
    if len(names) != len(set(names)):
        _error(
            "Each Drawing view may appear only once in one stack operation.",
            "NATIVE_DRAWING_STACK_TARGETS_INVALID",
        )
    page_views = tuple(getattr(page, "Views", ()) or ())
    views = []
    states = []
    for target in targets:
        view = resolve_object(
            document,
            {
                "document_uid": str(document.Uid),
                "object_name": target["object_name"],
            },
            expected_types=("TechDraw::DrawView",),
        )
        if view not in page_views or view.findParentPage() is not page:
            _error(
                "Every Drawing stack target must belong to the exact same page.",
                "NATIVE_DRAWING_STACK_PAGE_MISMATCH",
            )
        _require_usable(document, view, "Drawing stack target")
        state = drawing_stack_state(view)
        if str(target["expected_state_sha256"]) != state["state_sha256"]:
            _error(
                f"Drawing view {view.Name!r} changed after it was inspected.",
                "NATIVE_DRAWING_STACK_TARGET_STALE",
                repair={
                    "object_name": str(view.Name),
                    "current_state_sha256": state["state_sha256"],
                },
            )
        if not state["available"]:
            _error(
                f"Drawing view {view.Name!r} is not available in an open page scene.",
                "NATIVE_DRAWING_STACK_VIEW_UNAVAILABLE",
                repair={"show_page": str(page.Name)},
            )
        if not state["valid"]:
            _error(
                f"Drawing view {view.Name!r} is invalid and cannot be stacked.",
                "NATIVE_DRAWING_STACK_TARGET_INVALID",
            )
        views.append(view)
        states.append(state)
    return tuple(views), tuple(states)


def _require_order_headroom(
    operation: str,
    states: tuple[dict[str, Any], ...],
) -> None:
    count = len(states)
    if operation == "stack_top" and any(
        int(state["scope_maximum_order"]) > MAX_STACK_ORDER - count
        for state in states
    ):
        _error(
            "The Drawing stack has no remaining integer levels above its current top.",
            "NATIVE_DRAWING_STACK_ORDER_EXHAUSTED",
        )
    if operation == "stack_bottom" and any(
        int(state["scope_minimum_order"]) < MIN_STACK_ORDER + count
        for state in states
    ):
        _error(
            "The Drawing stack has no remaining integer levels below its current bottom.",
            "NATIVE_DRAWING_STACK_ORDER_EXHAUSTED",
        )
    if operation == "stack_up" and any(
        int(state["stack_order"]) == MAX_STACK_ORDER for state in states
    ):
        _error(
            "A Drawing stack target is already at the maximum integer level.",
            "NATIVE_DRAWING_STACK_ORDER_EXHAUSTED",
        )
    if operation == "stack_down" and any(
        int(state["stack_order"]) == MIN_STACK_ORDER for state in states
    ):
        _error(
            "A Drawing stack target is already at the minimum integer level.",
            "NATIVE_DRAWING_STACK_ORDER_EXHAUSTED",
        )


def prepare_drawing_stack(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingStack:
    if operation not in {"stack_top", "stack_bottom", "stack_up", "stack_down"}:
        raise ValueError("operation is not a Drawing stack operation")
    page, page_state = _resolve_page(document, values["page"])
    views, states = _resolve_views(document, page, tuple(values["views"]))
    _require_order_headroom(operation, states)
    selection = _selection(document)
    if (
        bool(selection.get("truncated"))
        or int(selection.get("selected_count", 0))
        != len(tuple(selection.get("items", ()) or ()))
    ):
        _error(
            "Reduce the current selection to at most 32 exact objects before stacking Drawing views.",
            "NATIVE_DRAWING_STACK_SELECTION_TOO_LARGE",
        )
    return PreparedDrawingStack(
        operation=operation,
        page=page,
        page_state_before=page_state,
        views=views,
        view_states_before=states,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        page_views_before=tuple(page.Views or ()),
        selection_before=selection,
        visibility_before=_visibility(document),
        stack_orders_before=_stack_orders(document),
        persistent_view_state_before=_persistent_view_states(document),
    )


def _expected_order(operation: str, state: Mapping[str, Any]) -> int:
    if operation == "stack_top":
        return int(state["scope_maximum_order"]) + 1
    if operation == "stack_bottom":
        return int(state["scope_minimum_order"]) - 1
    if operation == "stack_up":
        return int(state["stack_order"]) + 1
    return int(state["stack_order"]) - 1


def mutate_drawing_stack(
    document: Any,
    *,
    prepared: PreparedDrawingStack,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingStack):
        raise TypeError("prepared must be a PreparedDrawingStack")
    import TechDrawGui

    host_operation = prepared.operation.removeprefix("stack_")
    expected_orders = []
    for view in prepared.views:
        before = drawing_stack_state(view)
        expected = _expected_order(prepared.operation, before)
        TechDrawGui.stackView(view, host_operation)
        after = drawing_stack_state(view)
        if int(after["stack_order"]) != expected:
            _error(
                f"Drawing view {view.Name!r} did not reach its exact requested stack level.",
                "NATIVE_DRAWING_STACK_APPLY_FAILED",
            )
        expected_orders.append(expected)
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "expected_orders": tuple(expected_orders),
        },
        changed=tuple(object_identity(view) for view in prepared.views),
    )


def _assert_unchanged_state(
    document: Any,
    prepared: PreparedDrawingStack,
) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        _error(
            "Drawing stacking changed objects outside its exact target set.",
            "NATIVE_DRAWING_STACK_POSTCONDITION_FAILED",
        )
    if (
        _timeline_operations(document) != prepared.timeline_before
        or tuple(prepared.page.Views or ()) != prepared.page_views_before
        or drawing_page_state(prepared.page)["state_sha256"]
        != prepared.page_state_before["state_sha256"]
    ):
        _error(
            "Drawing stacking changed page membership or History.",
            "NATIVE_DRAWING_STACK_POSTCONDITION_FAILED",
        )
    if _selection(document) != prepared.selection_before:
        _error(
            "Drawing stacking changed the human selection.",
            "NATIVE_DRAWING_STACK_POSTCONDITION_FAILED",
        )
    if _visibility(document) != prepared.visibility_before:
        _error(
            "Drawing stacking changed existing object visibility.",
            "NATIVE_DRAWING_STACK_POSTCONDITION_FAILED",
        )
    if _persistent_view_states(document) != prepared.persistent_view_state_before:
        _error(
            "Drawing stacking changed persistent view state outside StackOrder.",
            "NATIVE_DRAWING_STACK_POSTCONDITION_FAILED",
        )


def verify_drawing_stack(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedDrawingStack = draft.value["prepared"]
    expected_orders: tuple[int, ...] = draft.value["expected_orders"]
    _assert_unchanged_state(document, prepared)
    expected_by_view = dict(zip(prepared.views, expected_orders, strict=True))
    for view, before_order in prepared.stack_orders_before:
        actual = int(view.ViewObject.StackOrder)
        expected = expected_by_view.get(view, before_order)
        if actual != expected:
            _error(
                f"Drawing view {view.Name!r} has an unexpected final stack level.",
                "NATIVE_DRAWING_STACK_POSTCONDITION_FAILED",
            )
    states = tuple(drawing_stack_state(view) for view in prepared.views)
    for state, expected in zip(states, expected_orders, strict=True):
        if (
            int(state["stack_order"]) != expected
            or state["page_name"] != str(prepared.page.Name)
            or not state["available"]
            or not state["timeline_usable"]
            or not state["valid"]
        ):
            _error(
                f"Drawing view {state['object_name']!r} did not retain its exact stack state.",
                "NATIVE_DRAWING_STACK_POSTCONDITION_FAILED",
            )
    return {
        "operation": prepared.operation,
        "page": drawing_page_state(prepared.page),
        "changed_view_count": len(states),
        "views": list(states),
    }
