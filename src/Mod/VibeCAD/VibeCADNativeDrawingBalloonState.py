# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded persisted state for projected TechDraw balloons."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state


MAX_DRAWING_BALLOON_TEXT_CHARACTERS = 512
MAX_DRAWING_BALLOON_STATE_MESSAGES = 16


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any, noun: str) -> float:
    raw = getattr(value, "Value", value)
    try:
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Drawing balloon {noun} is not numeric.") from exc
    if not math.isfinite(result) or abs(result) > 1.0e12:
        raise ValueError(f"Drawing balloon {noun} is outside the supported range.")
    return round(result, 12)


def _same(first: float, second: float) -> bool:
    return math.isclose(first, second, rel_tol=1.0e-10, abs_tol=1.0e-9)


def _color_rgb(value: Any) -> dict[str, int]:
    try:
        components = tuple(value)
    except TypeError as exc:
        raise ValueError("Drawing balloon color is malformed.") from exc
    if len(components) < 3:
        raise ValueError("Drawing balloon color is malformed.")
    channels = []
    for raw in components[:3]:
        channel = _finite(raw, "color channel")
        if channel < 0.0 or channel > 1.0:
            raise ValueError("Drawing balloon color channel is outside 0 to 1.")
        channels.append(int(round(channel * 255.0)))
    return {
        "red": channels[0],
        "green": channels[1],
        "blue": channels[2],
    }


def _is_derived(obj: Any, type_id: str) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    if callable(checker):
        try:
            return bool(checker(type_id))
        except Exception:
            return False
    return str(getattr(obj, "TypeId", "") or "") == type_id


def is_drawing_balloon(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawViewBalloon")


def _anchor_source(balloon: Any) -> tuple[Any, str]:
    raw = getattr(balloon, "AnchorSource", None)
    if not isinstance(raw, tuple) or len(raw) != 2 or raw[0] is None:
        raise ValueError("Drawing balloon has no persisted projected anchor.")
    raw_names = raw[1]
    names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
    if len(names) != 1:
        raise ValueError("Drawing balloon projected anchor is malformed.")
    name = str(names[0] or "")
    if not name.startswith(("Edge", "Vertex")):
        raise ValueError("Drawing balloon anchor is not an EdgeN or VertexN.")
    return raw[0], name


def _state_messages(balloon: Any) -> list[str]:
    result = []
    for raw in tuple(getattr(balloon, "State", ()) or ()):
        message = str(raw or "").strip()
        if message:
            result.append(message[:256])
        if len(result) >= MAX_DRAWING_BALLOON_STATE_MESSAGES:
            break
    return result


def _timeline_usable(balloon: Any) -> bool:
    checker = getattr(
        getattr(balloon, "Document", None),
        "isObjectUsableAtCurrentTimelinePosition",
        None,
    )
    return bool(not callable(checker) or checker(balloon))


def drawing_balloon_style_state(balloon: Any) -> dict[str, Any]:
    """Return the exact visual style shared by balloons and measurements."""

    if not is_drawing_balloon(balloon):
        raise TypeError("balloon must be a TechDraw::DrawViewBalloon")
    view_object = getattr(balloon, "ViewObject", None)
    if view_object is None:
        raise ValueError("Drawing balloon has no graphical style provider.")
    return {
        "bubble_shape": str(getattr(balloon, "BubbleShape", "") or ""),
        "leader_end": str(getattr(balloon, "EndType", "") or ""),
        "bubble_scale": _finite(getattr(balloon, "ShapeScale", 0.0), "bubble scale"),
        "leader_end_scale": _finite(
            getattr(balloon, "EndTypeScale", 0.0),
            "leader end scale",
        ),
        "kink_length_mm": _finite(
            getattr(balloon, "KinkLength", 0.0),
            "kink length",
        ),
        "text_wrap_length": _finite(
            getattr(balloon, "TextWrapLen", 0.0),
            "text wrap length",
        ),
        "font": str(getattr(view_object, "Font", "") or "")[:160],
        "font_size_mm": _finite(
            getattr(view_object, "Fontsize", 0.0),
            "font size",
        ),
        "line_width_mm": _finite(
            getattr(view_object, "LineWidth", 0.0),
            "line width",
        ),
        "line_visible": bool(getattr(view_object, "LineVisible", False)),
        "color_rgb": _color_rgb(getattr(view_object, "Color", ())),
    }


def drawing_balloon_state(balloon: Any) -> dict[str, Any]:
    """Return one Balloon's exact projected anchor, placement, text, and style."""

    if not is_drawing_balloon(balloon):
        raise TypeError("balloon must be a TechDraw::DrawViewBalloon")
    document = getattr(balloon, "Document", None)
    source_view, anchor_name = _anchor_source(balloon)
    if (
        document is None
        or getattr(source_view, "Document", None) is not document
        or getattr(balloon, "SourceView", None) is not source_view
    ):
        raise ValueError("Drawing balloon source view and projected anchor disagree.")
    page = balloon.findParentPage()
    if (
        page is None
        or getattr(page, "Document", None) is not document
        or source_view.findParentPage() is not page
        or balloon not in tuple(getattr(page, "Views", ()) or ())
    ):
        raise ValueError("Drawing balloon is not attached to its source view's page.")

    projection = drawing_projected_geometry_state(source_view)
    anchor_element = next(
        (item for item in projection["elements"] if item["name"] == anchor_name),
        None,
    )
    expected_type = "edge" if anchor_name.startswith("Edge") else "vertex"
    if anchor_element is None or anchor_element["element_type"] != expected_type:
        raise ValueError("Drawing balloon projected anchor is unavailable.")
    anchor_point = (
        anchor_element["midpoint_in_view_mm"]
        if expected_type == "edge"
        else anchor_element["point_in_view_mm"]
    )
    scale = _finite(getattr(source_view, "Scale", 0.0), "source view scale")
    if scale <= 0.0:
        raise ValueError("Drawing balloon source view scale must be positive.")
    origin = {
        "x_mm": _finite(getattr(balloon, "OriginX", 0.0), "anchor X coordinate"),
        "y_mm": _finite(getattr(balloon, "OriginY", 0.0), "anchor Y coordinate"),
    }
    expected_origin = {
        "x_mm": round(float(anchor_point["x_mm"]) / scale, 12),
        "y_mm": round(float(anchor_point["y_mm"]) / scale, 12),
    }
    if not _same(origin["x_mm"], expected_origin["x_mm"]) or not _same(
        origin["y_mm"], expected_origin["y_mm"]
    ):
        raise ValueError("Drawing balloon origin no longer matches its projected anchor.")
    bubble = {
        "x_mm": _finite(getattr(balloon, "X", 0.0), "bubble X coordinate"),
        "y_mm": _finite(getattr(balloon, "Y", 0.0), "bubble Y coordinate"),
    }
    bubble_offset = {
        "x_mm": round((bubble["x_mm"] - origin["x_mm"]) * scale, 12),
        "y_mm": round((bubble["y_mm"] - origin["y_mm"]) * scale, 12),
    }

    style = drawing_balloon_style_state(balloon)
    text = str(getattr(balloon, "Text", "") or "")
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    exact = {
        "object_name": str(getattr(balloon, "Name", "") or ""),
        "label": str(getattr(balloon, "Label", "") or ""),
        "type_id": str(getattr(balloon, "TypeId", "") or ""),
        "page_name": str(page.Name),
        "source_view_name": str(source_view.Name),
        "anchor": {
            "subelement": anchor_name,
            "element_type": expected_type,
            "element_state_sha256": anchor_element["element_state_sha256"],
            "point_in_view_mm": anchor_point,
        },
        "anchor_in_source_mm": origin,
        "bubble_in_source_mm": bubble,
        "bubble_offset_in_view_mm": bubble_offset,
        "text_sha256": text_sha256,
        "text_characters": len(text),
        "style": style,
        "timeline_role": str(getattr(balloon, "VibeCADTimelineRole", "") or ""),
        "timeline_owner_name": str(
            getattr(getattr(balloon, "VibeCADTimelineOwner", None), "Name", "")
            or ""
        ),
        "timeline_usable": _timeline_usable(balloon),
        "valid": bool(balloon.isValid()),
    }
    result = {
        **exact,
        "state_messages": _state_messages(balloon),
        "state_sha256": _digest(exact),
        "text": text[:MAX_DRAWING_BALLOON_TEXT_CHARACTERS],
    }
    if len(text) > MAX_DRAWING_BALLOON_TEXT_CHARACTERS:
        result["text_truncated"] = True
    return result
