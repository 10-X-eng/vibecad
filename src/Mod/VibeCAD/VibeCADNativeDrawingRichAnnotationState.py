# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state for durable Drawing rich-text annotations."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping


MAX_DRAWING_RICH_ANNOTATION_PLAIN_CHARACTERS = 8 * 1024
MAX_DRAWING_RICH_ANNOTATION_HTML_CHARACTERS = 32 * 1024
MAX_DRAWING_RICH_ANNOTATION_PROVIDER_CONTENT_CHARACTERS = 4096
MAX_DRAWING_RICH_ANNOTATION_PREVIEW_CHARACTERS = 160
_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LINE_STYLES = {
    "NoLine": "no_line",
    "Continuous": "continuous",
    "Dash": "dash",
    "Dot": "dot",
    "DashDot": "dash_dot",
    "DashDotDot": "dash_dot_dot",
}


class NativeDrawingRichAnnotationStateError(RuntimeError):
    """A stored rich annotation or compiled inspection result is malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derived(obj: Any, type_id: str) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(callable(checker) and checker(type_id))
    except Exception:
        return False


def is_drawing_rich_annotation(obj: Any) -> bool:
    return _derived(obj, "TechDraw::DrawRichAnno")


def _number(
    value: Any,
    noun: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingRichAnnotationStateError(
            f"Drawing rich annotation {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingRichAnnotationStateError(
            f"Drawing rich annotation {noun} is outside the supported range."
        )
    return round(result, 12)


def _timeline_usable(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    try:
        return bool(checker(obj)) if callable(checker) else True
    except Exception:
        return False


def drawing_rich_annotation_owner_state(
    owner: Any,
    *,
    page: Any | None = None,
) -> dict[str, Any]:
    """Return the exact structural identity relevant to annotation ownership."""

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
        or owner not in tuple(getattr(parent_page, "Views", ()) or ())
        or (page is not None and parent_page is not page)
    ):
        raise NativeDrawingRichAnnotationStateError(
            "Drawing rich annotation owner is not attached to one live page."
        )
    exact = {
        "object_name": object_name,
        "type_id": str(getattr(owner, "TypeId", "") or ""),
        "page_name": str(parent_page.Name),
        "timeline_usable": _timeline_usable(owner),
        "valid": bool(owner.isValid()),
    }
    return {**exact, "owner_state_sha256": _digest(exact)}


def _content_state(annotation: Any) -> dict[str, Any]:
    try:
        import TechDrawGui

        raw = TechDrawGui.inspectDrawingRichAnnotationContent(annotation)
    except Exception as exc:
        raise NativeDrawingRichAnnotationStateError(
            f"Drawing rich annotation content cannot be inspected: {str(exc).strip()}"
        ) from exc
    fields = {
        "input_kind",
        "stored_html_sha256",
        "plain_text_sha256",
        "plain_text_preview",
        "plain_text_characters",
        "block_count",
        "fragment_count",
        "link_count",
        "has_rich_formatting",
    }
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise NativeDrawingRichAnnotationStateError(
            "TechDraw returned malformed rich annotation content state."
        )
    preview = str(raw["plain_text_preview"] or "")
    hashes = {
        name: str(raw[name] or "")
        for name in ("stored_html_sha256", "plain_text_sha256")
    }
    counts = {
        name: int(raw[name])
        for name in (
            "plain_text_characters",
            "block_count",
            "fragment_count",
            "link_count",
        )
    }
    if (
        any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in hashes.values())
        or len(preview) > MAX_DRAWING_RICH_ANNOTATION_PREVIEW_CHARACTERS
        or not 0 <= counts["plain_text_characters"] <= MAX_DRAWING_RICH_ANNOTATION_PLAIN_CHARACTERS
        or not 0 <= counts["block_count"] <= 256
        or not 0 <= counts["fragment_count"] <= 2048
        or not 0 <= counts["link_count"] <= 128
    ):
        raise NativeDrawingRichAnnotationStateError(
            "TechDraw returned out-of-range rich annotation content state."
        )
    return {
        **hashes,
        "plain_text_preview": preview,
        **counts,
        "has_rich_formatting": bool(raw["has_rich_formatting"]),
    }


def _color(value: Any) -> dict[str, float]:
    channels = tuple(value or ())
    if len(channels) < 3:
        raise NativeDrawingRichAnnotationStateError(
            "Drawing rich annotation frame color is malformed."
        )
    return {
        name: _number(channel, f"frame {name}", minimum=0.0, maximum=1.0)
        for name, channel in zip(("red", "green", "blue"), channels[:3], strict=True)
    }


def drawing_rich_annotation_state(annotation: Any) -> dict[str, Any]:
    """Return one persisted annotation without returning its stored HTML blob."""

    if not is_drawing_rich_annotation(annotation):
        raise TypeError("annotation must be a TechDraw::DrawRichAnno")
    document = getattr(annotation, "Document", None)
    object_name = str(getattr(annotation, "Name", "") or "")
    page = annotation.findParentPage()
    if (
        document is None
        or _OBJECT_NAME.fullmatch(object_name) is None
        or page is None
        or getattr(page, "Document", None) is not document
        or annotation not in tuple(getattr(page, "Views", ()) or ())
    ):
        raise NativeDrawingRichAnnotationStateError(
            "Drawing rich annotation is not attached to one live page."
        )
    raw_owner = getattr(annotation, "AnnoParent", None)
    if raw_owner is None:
        owner = {"kind": "page"}
    else:
        owner_state = drawing_rich_annotation_owner_state(raw_owner, page=page)
        owner = {
            "kind": "view",
            "object_name": owner_state["object_name"],
            "type_id": owner_state["type_id"],
            "owner_state_sha256": owner_state["owner_state_sha256"],
        }
    maximum_width = _number(
        getattr(annotation, "MaxWidth", None),
        "maximum width",
        minimum=-1.0,
        maximum=1_000_000.0,
    )
    if maximum_width == -1.0:
        width = {"mode": "automatic"}
    elif maximum_width > 0.0:
        width = {"mode": "fixed", "value_mm": maximum_width}
    else:
        raise NativeDrawingRichAnnotationStateError(
            "Drawing rich annotation has an invalid maximum width."
        )
    line_style = _LINE_STYLES.get(str(annotation.ViewObject.LineStyle))
    if line_style is None:
        raise NativeDrawingRichAnnotationStateError(
            "Drawing rich annotation frame style is invalid."
        )
    exact = {
        "object_name": object_name,
        "label": str(getattr(annotation, "Label", "") or "")[:160],
        "type_id": str(getattr(annotation, "TypeId", "") or ""),
        "page_name": str(page.Name),
        "owner": owner,
        "content": _content_state(annotation),
        "placement_on_page_mm": {
            "x_mm": _number(annotation.X, "X coordinate", minimum=-1_000_000.0, maximum=1_000_000.0),
            "y_mm": _number(annotation.Y, "Y coordinate", minimum=-1_000_000.0, maximum=1_000_000.0),
        },
        "width": width,
        "origin_centered": bool(annotation.OriginCentered),
        "frame": {
            "visible": bool(annotation.ShowFrame),
            "line_width_mm": _number(
                annotation.ViewObject.LineWidth,
                "frame line width",
                minimum=0.0,
                maximum=100.0,
            ),
            "line_style": line_style,
            "color_rgb": _color(annotation.ViewObject.LineColor),
        },
        "timeline_usable": _timeline_usable(annotation),
        "valid": bool(annotation.isValid()),
    }
    return {**exact, "annotation_state_sha256": _digest(exact)}
