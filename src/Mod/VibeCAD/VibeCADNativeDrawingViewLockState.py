# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded lock state for positionable Drawing views."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingState import is_drawing_page
from VibeCADNativeDrawingViewState import is_part_drawing_view


MAX_DRAWING_VIEW_LOCKS = 512
MAX_DRAWING_VIEW_LOCK_PAGE_SIZE = 48


class NativeDrawingViewLockStateError(RuntimeError):
    """Drawing view-lock state is unavailable or malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid(obj: Any) -> bool:
    checker = getattr(obj, "isValid", None)
    try:
        return bool(checker()) if callable(checker) else True
    except Exception:
        return False


def _timeline_usable(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    try:
        return bool(checker(obj)) if callable(checker) else True
    except Exception:
        return False


def _coordinate(value: Any, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingViewLockStateError(
            f"Drawing view {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not -1_000_000_000.0 <= result <= 1_000_000_000.0:
        raise NativeDrawingViewLockStateError(
            f"Drawing view {noun} is outside the supported range."
        )
    return round(result, 9)


def drawing_view_lock_state(view: Any) -> dict[str, Any]:
    """Return one exact, low-cost target for Drawing position locking."""

    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    page = view.findParentPage()
    if not is_drawing_page(page) or view not in tuple(page.Views or ()):
        raise NativeDrawingViewLockStateError(
            "Drawing view is not attached to one live page."
        )
    exact = {
        "object_name": str(getattr(view, "Name", "") or ""),
        "label": str(getattr(view, "Label", "") or ""),
        "type_id": str(getattr(view, "TypeId", "") or ""),
        "page_name": str(getattr(page, "Name", "") or ""),
        "position_on_page_mm": {
            "x_mm": _coordinate(getattr(view, "X"), "X position"),
            "y_mm": _coordinate(getattr(view, "Y"), "Y position"),
        },
        "locked": bool(getattr(view, "LockPosition")),
        "timeline_usable": _timeline_usable(view),
        "valid": _valid(view),
    }
    return {**exact, "view_lock_state_sha256": _digest(exact)}


def drawing_view_lock_inventory_state(page: Any) -> dict[str, Any]:
    """Return every lockable view on one exact Drawing page."""

    if not is_drawing_page(page):
        raise TypeError("page must be a TechDraw::DrawPage")
    views = [
        drawing_view_lock_state(view)
        for view in tuple(getattr(page, "Views", ()) or ())
        if is_part_drawing_view(view)
    ]
    if len(views) > MAX_DRAWING_VIEW_LOCKS:
        raise NativeDrawingViewLockStateError(
            "Drawing page exceeds the supported 512 lockable views."
        )
    names = [state["object_name"] for state in views]
    if any(not name or len(name) > 128 for name in names) or len(names) != len(
        set(names)
    ):
        raise NativeDrawingViewLockStateError(
            "Drawing view-lock inventory contains invalid identities."
        )
    exact = {
        "page_object_name": str(getattr(page, "Name", "") or ""),
        "views": views,
    }
    return {
        **exact,
        "inventory_state_sha256": _digest(exact),
        "view_count": len(views),
        "locked_count": sum(state["locked"] for state in views),
        "unlocked_count": sum(not state["locked"] for state in views),
        "valid": all(state["valid"] and state["timeline_usable"] for state in views),
        "issues": [],
    }


def drawing_view_lock_page(
    page: Any,
    *,
    expected_inventory_state_sha256: str,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Read one hash-pinned page of exact view-lock targets."""

    if type(offset) is not int or not 0 <= offset <= MAX_DRAWING_VIEW_LOCKS:
        raise ValueError("Drawing view-lock offset must be 0 through 512.")
    if (
        type(page_size) is not int
        or not 1 <= page_size <= MAX_DRAWING_VIEW_LOCK_PAGE_SIZE
    ):
        raise ValueError("Drawing view-lock page_size must be 1 through 48.")
    state = drawing_view_lock_inventory_state(page)
    if str(expected_inventory_state_sha256) != state["inventory_state_sha256"]:
        raise NativeDrawingViewLockStateError(
            "The Drawing view-lock inventory changed after it was inspected."
        )
    if offset > state["view_count"]:
        raise ValueError("Drawing view-lock offset exceeds the current target count.")
    stop = min(offset + page_size, state["view_count"])
    return {
        name: state[name]
        for name in (
            "page_object_name",
            "inventory_state_sha256",
            "view_count",
            "locked_count",
            "unlocked_count",
            "valid",
            "issues",
        )
    } | {
        "offset": offset,
        "returned_count": stop - offset,
        "next_offset": stop if stop < state["view_count"] else None,
        "views": state["views"][offset:stop],
    }
