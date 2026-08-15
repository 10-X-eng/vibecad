# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded persisted state for owner-linked TechDraw Leader Lines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping


MAX_DRAWING_LEADER_POINTS = 64
_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ARROWS = (
    "filled_arrow",
    "open_arrow",
    "tick",
    "dot",
    "open_circle",
    "fork",
    "filled_triangle",
    "none",
)
_ARROW_LABELS = {
    "filled arrow": "filled_arrow",
    "open arrow": "open_arrow",
    "tick": "tick",
    "dot": "dot",
    "open circle": "open_circle",
    "fork": "fork",
    "filled triangle": "filled_triangle",
    "none": "none",
}
_LINE_STYLES = {
    "NoLine": "no_line",
    "Continuous": "continuous",
    "Dash": "dash",
    "Dot": "dot",
    "DashDot": "dash_dot",
    "DashDotDot": "dash_dot_dot",
}


class NativeDrawingLeaderStateError(RuntimeError):
    """A persisted Drawing Leader Line or owner definition is malformed."""


def _rounded(value: float) -> float:
    result = round(value, 12)
    return 0.0 if result == 0.0 else result


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derived(obj: Any, type_id: str) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(callable(checker) and checker(type_id))
    except Exception:
        return False


def is_drawing_leader(obj: Any) -> bool:
    return _derived(obj, "TechDraw::DrawLeaderLine")


def _number(
    value: Any,
    noun: str,
    *,
    minimum: float = -1_000_000.0,
    maximum: float = 1_000_000.0,
) -> float:
    raw = getattr(value, "Value", value)
    try:
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingLeaderStateError(
            f"Drawing leader {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingLeaderStateError(
            f"Drawing leader {noun} is outside the supported range."
        )
    return _rounded(result)


def _point(x: Any, y: Any, noun: str) -> dict[str, float]:
    return {
        "x_mm": _number(x, f"{noun} X coordinate"),
        "y_mm": _number(y, f"{noun} Y coordinate"),
    }


def _vector_point(value: Any, noun: str) -> dict[str, float]:
    return _point(
        getattr(value, "x", None),
        getattr(value, "y", None),
        noun,
    )


def _timeline_usable(obj: Any) -> bool:
    checker = getattr(
        getattr(obj, "Document", None),
        "isObjectUsableAtCurrentTimelinePosition",
        None,
    )
    try:
        return bool(checker(obj)) if callable(checker) else True
    except Exception:
        return False


def _projection_group(owner: Any) -> Any | None:
    if not _derived(owner, "TechDraw::DrawViewPart"):
        return None
    groups = []
    seen = set()
    for candidate in tuple(getattr(owner, "InList", ()) or ()):
        if not _derived(candidate, "TechDraw::DrawProjGroup"):
            continue
        identity = str(getattr(candidate, "Name", "") or "")
        if identity not in seen:
            seen.add(identity)
            groups.append(candidate)
    if len(groups) > 1:
        raise NativeDrawingLeaderStateError(
            "Drawing leader owner belongs to more than one projection group."
        )
    return groups[0] if groups else None


def _owner_position(owner: Any) -> tuple[dict[str, float], str | None]:
    group = _projection_group(owner)
    x = _number(getattr(owner, "X", None), "owner X coordinate")
    y = _number(getattr(owner, "Y", None), "owner Y coordinate")
    if group is None:
        return _point(x, y, "owner page position"), None
    return (
        _point(
            x + _number(getattr(group, "X", None), "projection group X coordinate"),
            y + _number(getattr(group, "Y", None), "projection group Y coordinate"),
            "owner page position",
        ),
        str(getattr(group, "Name", "") or ""),
    )


def drawing_leader_owner_state(
    owner: Any,
    *,
    page: Any | None = None,
) -> dict[str, Any]:
    """Freeze the placement transform that controls a Leader Line."""

    if not _derived(owner, "TechDraw::DrawView"):
        raise TypeError("owner must be a TechDraw::DrawView")
    document = getattr(owner, "Document", None)
    object_name = str(getattr(owner, "Name", "") or "")
    parent_page = owner.findParentPage()
    if (
        document is None
        or _OBJECT_NAME.fullmatch(object_name) is None
        or parent_page is None
        or getattr(parent_page, "Document", None) is not document
        or (page is not None and parent_page is not page)
    ):
        raise NativeDrawingLeaderStateError(
            "Drawing leader owner is not attached to one live page."
        )
    position, projection_group_name = _owner_position(owner)
    scale_reader = getattr(owner, "getScale", None)
    if not callable(scale_reader):
        raise NativeDrawingLeaderStateError(
            "Drawing leader owner does not expose its effective scale."
        )
    exact = {
        "object_name": object_name,
        "type_id": str(getattr(owner, "TypeId", "") or ""),
        "page_name": str(parent_page.Name),
        "projection_group_name": projection_group_name,
        "position_on_page_mm": position,
        "scale": _number(
            scale_reader(),
            "owner scale",
            minimum=1.0e-12,
            maximum=1_000_000.0,
        ),
        "rotation_degrees": _number(
            getattr(owner, "Rotation", None),
            "owner rotation",
        ),
        "timeline_usable": _timeline_usable(owner),
        "valid": bool(owner.isValid()),
    }
    return {**exact, "owner_state_sha256": _digest(exact)}


def _arrow(value: Any, noun: str) -> str:
    if type(value) is int and 0 <= value < len(_ARROWS):
        return _ARROWS[value]
    normalized = str(value or "").strip().casefold()
    result = _ARROW_LABELS.get(normalized)
    if result is None:
        raise NativeDrawingLeaderStateError(
            f"Drawing leader {noun} symbol is invalid."
        )
    return result


def _color(value: Any) -> dict[str, float]:
    try:
        channels = tuple(value)
    except TypeError as exc:
        raise NativeDrawingLeaderStateError(
            "Drawing leader line color is malformed."
        ) from exc
    if len(channels) < 3:
        raise NativeDrawingLeaderStateError(
            "Drawing leader line color is malformed."
        )
    result = {}
    for name, channel in zip(("red", "green", "blue"), channels[:3], strict=True):
        normalized = _number(
            channel,
            f"line color {name}",
            minimum=0.0,
            maximum=1.0,
        )
        # App::PropertyColor persists eight-bit channels. Hash that durable
        # value even before the first save instead of transient float input.
        result[name] = _rounded(math.floor(normalized * 255.0 + 0.5) / 255.0)
    return result


def _rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    radians = math.radians(degrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return x * cosine - y * sine, x * sine + y * cosine


def _rendered_points(
    leader: Any,
    owner_state: Mapping[str, Any],
    stored_waypoints: tuple[Any, ...],
) -> tuple[dict[str, float], list[dict[str, float]]]:
    scale = float(owner_state["scale"])
    rotation = float(owner_state["rotation_degrees"])
    anchor_local = _point(leader.X, leader.Y, "anchor in owner")
    anchor_offset = _rotate(
        anchor_local["x_mm"] * scale,
        anchor_local["y_mm"] * scale,
        rotation,
    )
    owner_position = owner_state["position_on_page_mm"]
    anchor_page = {
        "x_mm": _rounded(float(owner_position["x_mm"]) + anchor_offset[0]),
        "y_mm": _rounded(float(owner_position["y_mm"]) + anchor_offset[1]),
    }

    scalable = bool(leader.Scalable)
    rotates = bool(leader.RotatesWithParent)
    transformed: list[tuple[float, float]] = []
    for index, raw in enumerate(stored_waypoints):
        stored = _vector_point(raw, f"stored waypoint {index}")
        conventional_x = stored["x_mm"] * (scale if scalable else 1.0)
        conventional_y = -stored["y_mm"] * (scale if scalable else 1.0)
        if rotates:
            conventional_x, conventional_y = _rotate(
                conventional_x,
                conventional_y,
                rotation,
            )
        transformed.append((conventional_x, -conventional_y))

    if bool(leader.AutoHorizontal) and len(transformed) > 1:
        penultimate = transformed[-2]
        last = transformed[-1]
        segment_conventional = (
            last[0] - penultimate[0],
            -(last[1] - penultimate[1]),
        )
        length = math.hypot(*segment_conventional)
        direction = _rotate(*segment_conventional, rotation)
        sign = -1.0 if direction[0] < 0.0 else 1.0
        transformed[-1] = (
            penultimate[0] + sign * length,
            penultimate[1],
        )

    rendered = [
        {
            "x_mm": _rounded(anchor_page["x_mm"] + x),
            "y_mm": _rounded(anchor_page["y_mm"] - y),
        }
        for x, y in transformed
    ]
    return anchor_local, rendered


def drawing_leader_state(leader: Any) -> dict[str, Any]:
    """Return one Leader Line's exact owner, rendered geometry, and style."""

    if not is_drawing_leader(leader):
        raise TypeError("leader must be a TechDraw::DrawLeaderLine")
    document = getattr(leader, "Document", None)
    object_name = str(getattr(leader, "Name", "") or "")
    page = leader.findParentPage()
    owner = getattr(leader, "LeaderParent", None)
    if (
        document is None
        or _OBJECT_NAME.fullmatch(object_name) is None
        or page is None
        or getattr(page, "Document", None) is not document
        or leader not in tuple(getattr(page, "Views", ()) or ())
        or owner is None
        or getattr(owner, "Document", None) is not document
        or owner.findParentPage() is not page
    ):
        raise NativeDrawingLeaderStateError(
            "Drawing leader is not attached to one live owner and page."
        )
    owner_state = drawing_leader_owner_state(owner, page=page)
    stored_waypoints = tuple(getattr(leader, "WayPoints", ()) or ())
    if not 2 <= len(stored_waypoints) <= MAX_DRAWING_LEADER_POINTS:
        raise NativeDrawingLeaderStateError(
            "Drawing leader must retain 2 through 64 stored waypoints."
        )
    anchor_in_owner, rendered = _rendered_points(
        leader,
        owner_state,
        stored_waypoints,
    )
    line_style = _LINE_STYLES.get(str(leader.ViewObject.LineStyle))
    if line_style is None:
        raise NativeDrawingLeaderStateError(
            "Drawing leader line style is invalid."
        )
    storage = {
        "anchor_in_owner_mm": anchor_in_owner,
        "waypoints_in_owner_mm": [
            _vector_point(point, f"stored waypoint {index}")
            for index, point in enumerate(stored_waypoints)
        ],
    }
    exact = {
        "object_name": object_name,
        "label": str(getattr(leader, "Label", "") or "")[:160],
        "type_id": str(getattr(leader, "TypeId", "") or ""),
        "page_name": str(page.Name),
        "owner": {
            "object_name": owner_state["object_name"],
            "type_id": owner_state["type_id"],
            "owner_state_sha256": owner_state["owner_state_sha256"],
        },
        "point_count": len(rendered),
        "anchor_on_page_mm": rendered[0],
        "rendered_points_on_page_mm": rendered,
        "rendered_points_sha256": _digest(rendered),
        "anchor_in_owner_mm": anchor_in_owner,
        "storage_sha256": _digest(storage),
        "symbols": {
            "start": _arrow(leader.StartSymbol, "start"),
            "end": _arrow(leader.EndSymbol, "end"),
        },
        "behavior": {
            "scalable": bool(leader.Scalable),
            "auto_horizontal": bool(leader.AutoHorizontal),
            "rotates_with_owner": bool(leader.RotatesWithParent),
        },
        "line": {
            "line_width_mm": _number(
                leader.ViewObject.LineWidth,
                "line width",
                minimum=0.0,
                maximum=100.0,
            ),
            "line_style": line_style,
            "color_rgb": _color(leader.ViewObject.Color),
        },
        "timeline_role": str(getattr(leader, "VibeCADTimelineRole", "") or ""),
        "timeline_owner_name": str(
            getattr(getattr(leader, "VibeCADTimelineOwner", None), "Name", "") or ""
        ),
        "timeline_usable": _timeline_usable(leader),
        "valid": bool(leader.isValid()),
    }
    return {**exact, "leader_state_sha256": _digest(exact)}
