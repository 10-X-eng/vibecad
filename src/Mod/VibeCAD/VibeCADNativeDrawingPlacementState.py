# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state for positionable Drawing views and dimension labels."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionState import is_drawing_dimension
from VibeCADNativeDrawingRichAnnotationState import is_drawing_rich_annotation
from VibeCADNativeDrawingState import is_drawing_page
from VibeCADNativeDrawingViewState import is_drawing_view, is_part_drawing_view


_POSITIONABLE_VIEW_TYPES = frozenset(
    {
        "TechDraw::DrawProjGroup",
        "TechDraw::DrawViewClip",
        "TechDraw::DrawViewDraft",
        "TechDraw::DrawViewImage",
    }
)


class NativeDrawingPlacementStateError(RuntimeError):
    """Drawing placement state is unavailable or malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coordinate(value: Any, noun: str) -> float:
    raw = getattr(value, "Value", value)
    try:
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingPlacementStateError(
            f"Drawing {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or abs(result) > 10_000.0:
        raise NativeDrawingPlacementStateError(
            f"Drawing {noun} is outside the supported page range."
        )
    rounded = round(result, 9)
    return 0.0 if rounded == 0.0 else rounded


def _valid(obj: Any) -> bool:
    checker = getattr(obj, "isValid", None)
    try:
        return bool(checker()) if callable(checker) else True
    except Exception:
        return False


def _usable(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    try:
        return bool(checker(obj)) if callable(checker) else True
    except Exception:
        return False


def _page(obj: Any) -> Any:
    finder = getattr(obj, "findParentPage", None)
    page = finder() if callable(finder) else None
    if (
        not is_drawing_page(page)
        or getattr(page, "Document", None) is not getattr(obj, "Document", None)
        or obj not in tuple(getattr(page, "Views", ()) or ())
    ):
        raise NativeDrawingPlacementStateError(
            "Drawing item is not a top-level member of one live page."
        )
    return page


def is_positionable_drawing_view(obj: Any) -> bool:
    """Return whether the object is a top-level page view, not an annotation."""

    if obj is None or is_drawing_dimension(obj) or not is_drawing_view(obj):
        return False
    return is_part_drawing_view(obj) or str(getattr(obj, "TypeId", "") or "") in (
        _POSITIONABLE_VIEW_TYPES
    )


def is_positionable_drawing_note(obj: Any) -> bool:
    """Return whether the object is a rich-text note on one page."""

    return is_drawing_rich_annotation(obj)


def drawing_view_placement_owner(view: Any) -> Any:
    """Return the movable view that owns one visible Drawing view."""

    if view is None or not is_drawing_view(view):
        raise TypeError("view must be a Drawing view")
    finder = getattr(view, "findParentPage", None)
    page = finder() if callable(finder) else None
    page_views = tuple(getattr(page, "Views", ()) or ()) if is_drawing_page(page) else ()
    if view in page_views:
        if not is_positionable_drawing_view(view):
            raise NativeDrawingPlacementStateError(
                "Drawing view is not independently positionable."
            )
        return view
    if str(getattr(view, "TypeId", "") or "") != "TechDraw::DrawProjGroupItem":
        raise NativeDrawingPlacementStateError(
            "Drawing view has no top-level placement owner."
        )
    owners = tuple(
        candidate
        for candidate in page_views
        if str(getattr(candidate, "TypeId", "") or "")
        == "TechDraw::DrawProjGroup"
        and view in tuple(getattr(candidate, "Views", ()) or ())
    )
    if len(owners) != 1 or not is_positionable_drawing_view(owners[0]):
        raise NativeDrawingPlacementStateError(
            "Projected Drawing view has no unique projection-group owner."
        )
    return owners[0]


def drawing_view_position_on_page(view: Any) -> dict[str, float]:
    """Return one visible view position in page millimetres."""

    owner = drawing_view_placement_owner(view)
    owner_position = drawing_view_placement_state(owner)["position_on_page_mm"]
    if owner is view:
        return dict(owner_position)
    return {
        "x_mm": _coordinate(
            float(owner_position["x_mm"]) + float(getattr(view, "X")),
            "projected child X position",
        ),
        "y_mm": _coordinate(
            float(owner_position["y_mm"]) + float(getattr(view, "Y")),
            "projected child Y position",
        ),
    }


def drawing_view_placement_state(view: Any) -> dict[str, Any]:
    """Return exact placement state for one top-level Drawing view."""

    if not is_positionable_drawing_view(view):
        raise TypeError("view must be a positionable Drawing view")
    page = _page(view)
    exact = {
        "object_name": str(getattr(view, "Name", "") or ""),
        "label": str(getattr(view, "Label", "") or ""),
        "type_id": str(getattr(view, "TypeId", "") or ""),
        "page_name": str(getattr(page, "Name", "") or ""),
        "position_on_page_mm": {
            "x_mm": _coordinate(getattr(view, "X"), "view X position"),
            "y_mm": _coordinate(getattr(view, "Y"), "view Y position"),
        },
        "locked": bool(getattr(view, "LockPosition", False)),
        "timeline_usable": _usable(view),
        "valid": _valid(view),
    }
    return {**exact, "placement_state_sha256": _digest(exact)}


def drawing_dimension_label_placement_state(dimension: Any) -> dict[str, Any]:
    """Return exact placement state for one Drawing dimension label."""

    if not is_drawing_dimension(dimension):
        raise TypeError("dimension must be a Drawing dimension")
    page = _page(dimension)
    exact = {
        "object_name": str(getattr(dimension, "Name", "") or ""),
        "label": str(getattr(dimension, "Label", "") or ""),
        "type_id": str(getattr(dimension, "TypeId", "") or ""),
        "page_name": str(getattr(page, "Name", "") or ""),
        "dimension_type": str(getattr(dimension, "Type", "") or ""),
        "measure_type": str(getattr(dimension, "MeasureType", "") or ""),
        "label_position_in_view_mm": {
            "x_mm": _coordinate(getattr(dimension, "X"), "dimension-label X position"),
            "y_mm": _coordinate(getattr(dimension, "Y"), "dimension-label Y position"),
        },
        "timeline_usable": _usable(dimension),
        "valid": _valid(dimension),
    }
    return {**exact, "placement_state_sha256": _digest(exact)}


def drawing_dimension_view_origin_on_page(dimension: Any) -> dict[str, float]:
    """Return the page position of the view that owns a dimension label."""

    if not is_drawing_dimension(dimension):
        raise TypeError("dimension must be a Drawing dimension")
    references = tuple(getattr(dimension, "References2D", ()) or ())
    if not references or not isinstance(references[0], (list, tuple)):
        raise NativeDrawingPlacementStateError(
            "Drawing dimension has no projected parent view."
        )
    first = references[0]
    if not first:
        raise NativeDrawingPlacementStateError(
            "Drawing dimension has no projected parent view."
        )
    view = first[0]
    if getattr(view, "Document", None) is not getattr(dimension, "Document", None):
        raise NativeDrawingPlacementStateError(
            "Drawing dimension parent view is not in the live document."
        )
    return drawing_view_position_on_page(view)


def drawing_note_placement_state(note: Any) -> dict[str, Any]:
    """Return exact page placement state for one Drawing note."""

    if not is_positionable_drawing_note(note):
        raise TypeError("note must be a positionable Drawing note")
    page = _page(note)
    owner = getattr(note, "AnnoParent", None)
    exact = {
        "object_name": str(getattr(note, "Name", "") or ""),
        "label": str(getattr(note, "Label", "") or ""),
        "type_id": str(getattr(note, "TypeId", "") or ""),
        "page_name": str(getattr(page, "Name", "") or ""),
        "owner": (
            {"kind": "page"}
            if owner is None
            else {
                "kind": "view",
                "object_name": str(getattr(owner, "Name", "") or ""),
            }
        ),
        "position_on_page_mm": {
            "x_mm": _coordinate(getattr(note, "X"), "note X position"),
            "y_mm": _coordinate(getattr(note, "Y"), "note Y position"),
        },
        "locked": bool(getattr(note, "LockPosition", False)),
        "timeline_usable": _usable(note),
        "valid": _valid(note),
    }
    return {**exact, "placement_state_sha256": _digest(exact)}


__all__ = [
    "NativeDrawingPlacementStateError",
    "drawing_dimension_label_placement_state",
    "drawing_dimension_view_origin_on_page",
    "drawing_note_placement_state",
    "drawing_view_placement_owner",
    "drawing_view_placement_state",
    "drawing_view_position_on_page",
    "is_positionable_drawing_note",
    "is_positionable_drawing_view",
]
