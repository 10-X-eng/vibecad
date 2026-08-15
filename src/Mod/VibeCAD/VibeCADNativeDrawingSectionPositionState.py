# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact target state for Drawing section-view positioning."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from SectionViewPosition import alignment_base
from VibeCADNativeDrawingState import is_drawing_page
from VibeCADNativeDrawingViewState import drawing_view_state, is_drawing_view


class NativeDrawingSectionPositionStateError(RuntimeError):
    """Section-view positioning state is unavailable or malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coordinate(value: Any, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingSectionPositionStateError(
            f"Drawing {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not -1_000_000_000.0 <= result <= 1_000_000_000.0:
        raise NativeDrawingSectionPositionStateError(
            f"Drawing {noun} is outside the supported range."
        )
    return round(result, 9)


def _timeline_usable(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    try:
        return bool(checker(obj)) if callable(checker) else True
    except Exception:
        return False


def _valid(obj: Any) -> bool:
    checker = getattr(obj, "isValid", None)
    try:
        return bool(checker()) if callable(checker) else True
    except Exception:
        return False


def _definition_state(view_state: Mapping[str, Any]) -> dict[str, Any]:
    ignored = frozenset(
        {
            "state_sha256",
            "x_mm",
            "y_mm",
            "visible_edge_count",
            "hidden_edge_count",
        }
    )
    result = {
        key: value
        for key, value in view_state.items()
        if key not in ignored
    }
    section = result.get("section")
    if isinstance(section, Mapping):
        result["section"] = {
            key: value
            for key, value in section.items()
            if key != "section_face_count"
        }
    return result


def drawing_alignment_base_state(base_view: Any) -> dict[str, Any]:
    """Return the exact page-position owner used by section alignment."""

    root = alignment_base(base_view)
    if root is None or not is_drawing_view(root):
        raise NativeDrawingSectionPositionStateError(
            "Drawing alignment base is unavailable."
        )
    page = root.findParentPage()
    if not is_drawing_page(page):
        raise NativeDrawingSectionPositionStateError(
            "Drawing alignment base is not attached to a live page."
        )
    exact = {
        "object_name": str(getattr(root, "Name", "") or ""),
        "label": str(getattr(root, "Label", "") or ""),
        "type_id": str(getattr(root, "TypeId", "") or ""),
        "page_name": str(getattr(page, "Name", "") or ""),
        "position_on_page_mm": {
            "x_mm": _coordinate(getattr(root, "X"), "alignment-base X position"),
            "y_mm": _coordinate(getattr(root, "Y"), "alignment-base Y position"),
        },
        "scale": _coordinate(root.getScale(), "alignment-base scale"),
        "timeline_usable": _timeline_usable(root),
        "valid": _valid(root),
    }
    return {**exact, "alignment_base_state_sha256": _digest(exact)}


def drawing_section_position_state(section_view: Any) -> dict[str, Any]:
    """Return one exact standard-section positioning target."""

    if str(getattr(section_view, "TypeId", "")) != "TechDraw::DrawViewSection":
        raise TypeError("section_view must be a standard TechDraw::DrawViewSection")
    page = section_view.findParentPage()
    base_view = getattr(section_view, "BaseView", None)
    if not is_drawing_page(page) or not is_drawing_view(base_view):
        raise NativeDrawingSectionPositionStateError(
            "Drawing section view has no live page and base view."
        )
    if base_view.findParentPage() is not page:
        raise NativeDrawingSectionPositionStateError(
            "Drawing section view and base view do not share one page."
        )
    view_state = drawing_view_state(section_view)
    definition_state = _definition_state(view_state)
    base_state = drawing_alignment_base_state(base_view)
    exact = {
        "object_name": str(section_view.Name),
        "label": str(section_view.Label),
        "type_id": str(section_view.TypeId),
        "page_name": str(page.Name),
        "base_view_name": str(base_view.Name),
        "definition_state_sha256": _digest(definition_state),
        "alignment_base": base_state,
        "position_on_page_mm": {
            "x_mm": _coordinate(section_view.X, "section-view X position"),
            "y_mm": _coordinate(section_view.Y, "section-view Y position"),
        },
        "locked": bool(section_view.LockPosition),
        "timeline_usable": _timeline_usable(section_view),
        "valid": _valid(section_view),
    }
    return {**exact, "section_position_state_sha256": _digest(exact)}
