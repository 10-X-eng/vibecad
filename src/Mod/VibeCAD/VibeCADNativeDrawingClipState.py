# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded clip-group state for Native Drawing."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeDrawingState import is_drawing_page
from VibeCADNativeDrawingViewState import is_drawing_view


MAX_DRAWING_CLIP_MEMBERS = 48


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_derived(obj: Any, type_id: str) -> bool:
    check = getattr(obj, "isDerivedFrom", None)
    if callable(check):
        try:
            return bool(check(type_id))
        except Exception:
            return False
    return str(getattr(obj, "TypeId", "") or "") == type_id


def is_clip_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawViewClip")


def is_projection_group_item(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawProjGroupItem")


def _identity(obj: Any) -> dict[str, str]:
    return {
        "object_name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type_id": str(getattr(obj, "TypeId", "") or ""),
    }


def _parent_page(view: Any) -> Any | None:
    finder = getattr(view, "findParentPage", None)
    if callable(finder):
        try:
            page = finder()
            if is_drawing_page(page):
                return page
        except Exception:
            pass
    document = getattr(view, "Document", None)
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        if is_drawing_page(obj) and view in tuple(getattr(obj, "Views", ()) or ()):
            return obj
    return None


def _clip_groups(view: Any) -> tuple[Any, ...]:
    document = getattr(view, "Document", None)
    groups = []
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        if (
            is_clip_drawing_view(obj)
            and view in tuple(getattr(obj, "Views", ()) or ())
        ):
            groups.append(obj)
    return tuple(groups)


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


def drawing_clip_member_state(view: Any) -> dict[str, Any]:
    """Return the state that controls one view's clip membership and placement."""

    if not is_drawing_view(view) or is_clip_drawing_view(view):
        raise TypeError("view must be a non-clip TechDraw::DrawView")
    page = _parent_page(view)
    groups = _clip_groups(view)
    exact = {
        "view": _identity(view),
        "page_name": str(getattr(page, "Name", "") or "") if page else None,
        "position_mm": [
            round(float(getattr(view, "X", 0.0)), 9),
            round(float(getattr(view, "Y", 0.0)), 9),
        ],
        "clip_group_names": [str(group.Name) for group in groups],
        "timeline_usable": _timeline_usable(view),
        "valid": _valid(view),
    }
    return {
        **exact["view"],
        "state_sha256": _digest(exact),
        "page_name": exact["page_name"],
        "position_mm": exact["position_mm"],
        "clip_group_names": exact["clip_group_names"],
        "timeline_usable": exact["timeline_usable"],
        "valid": exact["valid"],
    }


def _clip_children(clip: Any) -> bool:
    view_object = getattr(clip, "ViewObject", None)
    try:
        return bool(getattr(view_object, "ClipChildren"))
    except Exception:
        return True


def drawing_clip_group_state(clip: Any) -> dict[str, Any]:
    """Return exact frame, ordered membership, and local member placement."""

    if not is_clip_drawing_view(clip):
        raise TypeError("clip must be a TechDraw::DrawViewClip")
    page = _parent_page(clip)
    members = tuple(getattr(clip, "Views", ()) or ())
    if len(members) > MAX_DRAWING_CLIP_MEMBERS:
        raise ValueError(
            f"A Drawing clip group may expose at most {MAX_DRAWING_CLIP_MEMBERS} members."
        )
    member_states = [drawing_clip_member_state(member) for member in members]
    frame = {
        "width_mm": round(float(clip.Width), 9),
        "height_mm": round(float(clip.Height), 9),
        "show_frame": bool(clip.ShowFrame),
        "clip_children": _clip_children(clip),
    }
    exact = {
        "clip": _identity(clip),
        "page_name": str(getattr(page, "Name", "") or "") if page else None,
        "position_on_page_mm": [
            round(float(clip.X), 9),
            round(float(clip.Y), 9),
        ],
        "frame": frame,
        "members": [
            {
                "object_name": state["object_name"],
                "state_sha256": state["state_sha256"],
                "position_in_clip_mm": state["position_mm"],
            }
            for state in member_states
        ],
        "timeline_usable": _timeline_usable(clip),
        "valid": _valid(clip),
    }
    return {
        **exact["clip"],
        "state_sha256": _digest(exact),
        "page_name": exact["page_name"],
        "position_on_page_mm": exact["position_on_page_mm"],
        "frame": frame,
        "member_count": len(member_states),
        "members": exact["members"],
        "timeline_usable": exact["timeline_usable"],
        "valid": exact["valid"],
    }
