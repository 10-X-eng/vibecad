# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact reads and atomic explicit lock changes for Drawing views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewLockState import (
    NativeDrawingViewLockStateError,
    drawing_view_lock_inventory_state,
    drawing_view_lock_page,
)
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_CHANGE_FIELDS = frozenset(
    {"object_name", "expected_view_lock_state_sha256", "locked"}
)


@dataclass(frozen=True, slots=True)
class PreparedDrawingViewLockTarget:
    view: Any
    state_before: dict[str, Any]
    definition_before: dict[str, Any]
    locked: bool


@dataclass(frozen=True, slots=True)
class PreparedDrawingViewLockChange:
    page: Any
    page_state_before: dict[str, Any]
    inventory_before: dict[str, Any]
    changes: tuple[PreparedDrawingViewLockTarget, ...]
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _resolve_page(document: Any, value: Any) -> tuple[Any, dict[str, Any]]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"object_name", "expected_state_sha256"}),
        "page target",
        family="view lock",
        error_code="NATIVE_DRAWING_VIEW_LOCK_PARAMETERS_INVALID",
    )
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(exact["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    return page, state


def _inventory(page: Any, expected_sha256: Any) -> dict[str, Any]:
    try:
        state = drawing_view_lock_inventory_state(page)
    except (AttributeError, NativeDrawingViewLockStateError, TypeError) as exc:
        _error(
            f"The Drawing view-lock inventory is unavailable: {str(exc).strip()}",
            "NATIVE_DRAWING_VIEW_LOCK_STATE_INVALID",
        )
    if str(expected_sha256) != state["inventory_state_sha256"]:
        _error(
            "The Drawing view-lock inventory changed after it was inspected.",
            "NATIVE_DRAWING_VIEW_LOCK_INVENTORY_STALE",
            repair={
                "current_inventory_state_sha256": state[
                    "inventory_state_sha256"
                ]
            },
        )
    return state


def read_drawing_view_locks(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Read one exact bounded page of Drawing view-lock targets."""

    page, _page_state = _resolve_page(document, values["page"])
    try:
        result = drawing_view_lock_page(
            page,
            expected_inventory_state_sha256=str(
                values["expected_inventory_state_sha256"]
            ),
            offset=values["offset"],
            page_size=values["page_size"],
        )
    except (NativeDrawingViewLockStateError, TypeError, ValueError) as exc:
        _error(str(exc), "NATIVE_DRAWING_VIEW_LOCK_READ_INVALID")
    return {"view_locks": result}


def _resolve_changes(
    document: Any,
    page: Any,
    inventory: Mapping[str, Any],
    values: Any,
) -> tuple[PreparedDrawingViewLockTarget, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 32:
        _error(
            "A Drawing view-lock operation requires 1 to 32 exact views.",
            "NATIVE_DRAWING_VIEW_LOCK_PARAMETERS_INVALID",
        )
    by_name = {state["object_name"]: state for state in inventory["views"]}
    seen: set[str] = set()
    result = []
    for value in values:
        exact = exact_drawing_mapping(
            value,
            _CHANGE_FIELDS,
            "view change",
            family="view lock",
            error_code="NATIVE_DRAWING_VIEW_LOCK_PARAMETERS_INVALID",
        )
        name = str(exact["object_name"] or "")
        if name in seen:
            _error(
                "Each Drawing view may appear only once in one lock operation.",
                "NATIVE_DRAWING_VIEW_LOCK_TARGETS_INVALID",
            )
        seen.add(name)
        if type(exact["locked"]) is not bool:
            _error(
                "Every Drawing view lock target requires a boolean locked value.",
                "NATIVE_DRAWING_VIEW_LOCK_PARAMETERS_INVALID",
            )
        state = by_name.get(name)
        if state is None:
            _error(
                f"Drawing view {name!r} is not lockable on the exact page.",
                "NATIVE_DRAWING_VIEW_LOCK_PAGE_MISMATCH",
            )
        if (
            str(exact["expected_view_lock_state_sha256"])
            != state["view_lock_state_sha256"]
        ):
            _error(
                f"Drawing view {name!r} changed after it was inspected.",
                "NATIVE_DRAWING_VIEW_LOCK_TARGET_STALE",
                repair={
                    "object_name": name,
                    "current_view_lock_state_sha256": state[
                        "view_lock_state_sha256"
                    ],
                },
            )
        if not state["valid"] or not state["timeline_usable"]:
            _error(
                f"Drawing view {name!r} is not valid at the current History position.",
                "NATIVE_DRAWING_VIEW_LOCK_TARGET_INVALID",
            )
        desired = exact["locked"]
        if state["locked"] == desired:
            _error(
                f"Drawing view {name!r} is already "
                f"{'locked' if desired else 'unlocked'}.",
                "NATIVE_DRAWING_VIEW_LOCK_NO_CHANGE",
                repair={"object_name": name, "locked": state["locked"]},
            )
        view = resolve_object(
            document,
            {"document_uid": str(document.Uid), "object_name": name},
            expected_types=("TechDraw::DrawViewPart",),
        )
        if view.findParentPage() is not page or view not in tuple(page.Views or ()):
            _error(
                "Every Drawing view lock target must belong to the exact page.",
                "NATIVE_DRAWING_VIEW_LOCK_PAGE_MISMATCH",
            )
        _require_usable(document, view, "Drawing view lock target")
        result.append(
            PreparedDrawingViewLockTarget(
                view=view,
                state_before=state,
                definition_before=drawing_view_state(view),
                locked=desired,
            )
        )
    return tuple(result)


def prepare_drawing_view_lock_change(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDrawingViewLockChange:
    page, page_state = _resolve_page(document, values["page"])
    inventory = _inventory(page, values["expected_inventory_state_sha256"])
    changes = _resolve_changes(document, page, inventory, values["views"])
    selection = drawing_selection_state(document)
    if (
        bool(selection.get("truncated"))
        or int(selection.get("selected_count", 0))
        != len(tuple(selection.get("items", ()) or ()))
    ):
        _error(
            "Reduce the current selection to at most 32 exact objects before "
            "changing Drawing view locks.",
            "NATIVE_DRAWING_VIEW_LOCK_SELECTION_TOO_LARGE",
        )
    return PreparedDrawingViewLockChange(
        page=page,
        page_state_before=page_state,
        inventory_before=inventory,
        changes=changes,
        objects_before=tuple(document.Objects),
        timeline_before=drawing_timeline_operations(document),
        page_views_before=tuple(page.Views or ()),
        selection_before=selection,
        visibility_before=drawing_visibility_state(document),
    )


def mutate_drawing_view_locks(
    _document: Any,
    *,
    prepared: PreparedDrawingViewLockChange,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingViewLockChange):
        raise TypeError("prepared must be a PreparedDrawingViewLockChange")
    import TechDrawGui

    try:
        returned = TechDrawGui.changeDrawingViewLocks(
            prepared.page,
            tuple((change.view, change.locked) for change in prepared.changes),
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_VIEW_LOCK_CHANGE_FAILED",
            "TechDraw could not set the exact view lock states: "
            f"{str(exc).strip()}",
        ) from exc
    expected = [
        {"object_name": str(change.view.Name), "locked": change.locked}
        for change in prepared.changes
    ]
    if returned != expected:
        raise NativeMutationError(
            "NATIVE_DRAWING_VIEW_LOCK_CHANGE_FAILED",
            "TechDraw returned inconsistent Drawing view lock results.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=tuple(change.view for change in prepared.changes)
        + (prepared.page,),
        changed=tuple(object_identity(change.view) for change in prepared.changes),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_VIEW_LOCK_POSTCONDITION_FAILED",
        message,
    )


def _lock_boundary(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    ignored = frozenset({"view_lock_state_sha256", "locked"})
    return {key: value for key, value in left.items() if key not in ignored} == {
        key: value for key, value in right.items() if key not in ignored
    }


def _verify_drawing_view_locks(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingViewLockChange = draft.value["prepared"]
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
    ):
        _postcondition_error(
            "View locking altered objects, page membership, or History."
        )
    if drawing_selection_state(document) != prepared.selection_before:
        _postcondition_error("View locking altered the human selection.")
    if drawing_visibility_state(document) != prepared.visibility_before:
        _postcondition_error("View locking altered object visibility.")
    if (
        drawing_page_state(prepared.page)["state_sha256"]
        != prepared.page_state_before["state_sha256"]
    ):
        _postcondition_error("View locking altered the Drawing page definition.")

    inventory = drawing_view_lock_inventory_state(prepared.page)
    before_by_name = {
        state["object_name"]: state for state in prepared.inventory_before["views"]
    }
    after_by_name = {state["object_name"]: state for state in inventory["views"]}
    if tuple(before_by_name) != tuple(after_by_name):
        _postcondition_error("View locking altered lockable view identities or order.")
    changes_by_name = {
        str(change.view.Name): change for change in prepared.changes
    }
    for name, before in before_by_name.items():
        after = after_by_name[name]
        change = changes_by_name.get(name)
        if change is None:
            if after != before:
                _postcondition_error(
                    f"Non-target Drawing view {name!r} changed unexpectedly."
                )
            continue
        if (
            not _lock_boundary(before, after)
            or after["locked"] is not change.locked
            or after["view_lock_state_sha256"]
            == before["view_lock_state_sha256"]
        ):
            _postcondition_error(
                f"Drawing view {name!r} did not retain its exact requested lock state."
            )
        if drawing_view_state(change.view) != change.definition_before:
            _postcondition_error(
                f"View locking altered Drawing view {name!r} outside LockPosition."
            )
    if (
        inventory["inventory_state_sha256"]
        == prepared.inventory_before["inventory_state_sha256"]
    ):
        _postcondition_error("The Drawing view-lock inventory did not change.")
    return {
        "operation": "set",
        "view_locks": {
            "page_object_name": inventory["page_object_name"],
            "inventory_state_sha256": inventory["inventory_state_sha256"],
            "changed_view_count": len(prepared.changes),
            "views": [after_by_name[name] for name in changes_by_name],
        },
    }


def verify_drawing_view_locks(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_view_locks(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_VIEW_LOCK_POSTCONDITION_FAILED",
            "The Drawing view-lock change could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
