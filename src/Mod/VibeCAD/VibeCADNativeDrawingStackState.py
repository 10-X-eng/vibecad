# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded graphical stacking state for Native Drawing."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingViewState import is_drawing_view


MAX_DRAWING_STACK_SCOPE_ITEMS = 128
MIN_STACK_ORDER = -(2**31)
MAX_STACK_ORDER = 2**31 - 1


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


def _bounded_z(value: Any) -> float:
    result = float(value)
    if (
        not math.isfinite(result)
        or result < MIN_STACK_ORDER
        or result > MAX_STACK_ORDER
    ):
        raise ValueError("Drawing graphical stack order is outside the supported range.")
    return round(result, 9)


def drawing_stack_state(view: Any) -> dict[str, Any]:
    """Return one view's exact current graphical sibling scope and stack state."""

    if not is_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawView")
    import TechDrawGui

    raw = TechDrawGui.getViewStackState(view)
    if not isinstance(raw, Mapping):
        raise RuntimeError("TechDraw returned malformed graphical stack state.")
    scope_kind = str(raw.get("scope_kind") or "")
    if scope_kind not in {"page", "owner", "unavailable"}:
        raise RuntimeError("TechDraw returned an unknown graphical stack scope.")
    raw_items = tuple(raw.get("scope_items") or ())
    if len(raw_items) > MAX_DRAWING_STACK_SCOPE_ITEMS:
        raise ValueError(
            "Drawing stack scope exceeds the supported 128 graphical items."
        )
    items = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise RuntimeError("TechDraw returned a malformed graphical stack item.")
        object_name = str(raw_item.get("object_name") or "")
        if len(object_name) > 128:
            raise RuntimeError("TechDraw returned an invalid graphical stack identity.")
        items.append(
            {
                "object_name": object_name,
                "z_value": _bounded_z(raw_item.get("z_value")),
            }
        )
    items.sort(key=lambda item: (item["object_name"], item["z_value"]))
    available = bool(raw.get("available"))
    minimum = int(raw.get("scope_minimum_order", 0)) if available else None
    maximum = int(raw.get("scope_maximum_order", 0)) if available else None
    if available and not (
        MIN_STACK_ORDER <= minimum <= maximum <= MAX_STACK_ORDER
    ):
        raise RuntimeError("TechDraw returned invalid graphical stack bounds.")
    exact = {
        "view": {
            "object_name": str(getattr(view, "Name", "") or ""),
            "label": str(getattr(view, "Label", "") or ""),
            "type_id": str(getattr(view, "TypeId", "") or ""),
        },
        "page_name": str(raw.get("page_name") or "") or None,
        "stack_order": int(raw.get("stack_order", 0)),
        "z_value": _bounded_z(raw.get("z_value", 0.0)),
        "scope_kind": scope_kind,
        "scope_items": items,
        "scope_minimum_order": minimum,
        "scope_maximum_order": maximum,
        "available": available,
        "timeline_usable": _timeline_usable(view),
        "valid": _valid(view),
    }
    return {
        **exact["view"],
        "state_sha256": _digest(exact),
        "page_name": exact["page_name"],
        "stack_order": exact["stack_order"],
        "z_value": exact["z_value"],
        "scope_kind": scope_kind,
        "scope_item_count": len(items),
        "scope_named_view_count": sum(bool(item["object_name"]) for item in items),
        "scope_minimum_order": minimum,
        "scope_maximum_order": maximum,
        "available": available,
        "timeline_usable": exact["timeline_usable"],
        "valid": exact["valid"],
    }
