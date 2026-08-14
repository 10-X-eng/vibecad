# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded persistent format state for Drawing cosmetic lines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from VibeCADNativeDrawingViewState import is_part_drawing_view


MAX_DRAWING_LINE_ATTRIBUTES = 512
MAX_DRAWING_LINE_ATTRIBUTE_PAGE_SIZE = 48
MAX_DRAWING_LINE_ATTRIBUTE_TARGETS = 32
_KINDS = frozenset({"projected_edge", "cosmetic_edge", "centerline"})
_TAG = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SUBELEMENT = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")


class NativeDrawingLineAttributeStateError(RuntimeError):
    """Persistent Drawing line state is unavailable or malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _integer(value: Any, noun: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NativeDrawingLineAttributeStateError(
            f"Drawing line {noun} is outside the supported integer range."
        )
    return value


def _number(value: Any, noun: str, *, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingLineAttributeStateError(
            f"Drawing line {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingLineAttributeStateError(
            f"Drawing line {noun} is outside the supported range."
        )
    return round(result, 12)


def _color(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"red", "green", "blue"}
    ):
        raise NativeDrawingLineAttributeStateError(
            "Drawing line color is malformed."
        )
    return {
        name: _number(
            value[name],
            f"color {name}",
            minimum=0.0,
            maximum=1.0,
        )
        for name in ("red", "green", "blue")
    }


def _line(raw: Any) -> dict[str, Any]:
    common = frozenset(
        {
            "kind",
            "subelement",
            "line_number",
            "style_code",
            "width_mm",
            "color_rgb",
            "visible",
        }
    )
    if not isinstance(raw, Mapping):
        raise NativeDrawingLineAttributeStateError(
            "Drawing line attributes contain a malformed target."
        )
    kind = str(raw["kind"] or "")
    expected = common if kind == "projected_edge" else common | {"tag"}
    if frozenset(raw) != expected:
        raise NativeDrawingLineAttributeStateError(
            "Drawing line attributes contain a malformed target."
        )
    tag = str(raw.get("tag", "") or "")
    subelement = str(raw["subelement"] or "")
    visible = raw["visible"]
    if kind not in _KINDS or (
        kind != "projected_edge" and _TAG.fullmatch(tag) is None
    ):
        raise NativeDrawingLineAttributeStateError(
            "Drawing line target identity is malformed."
        )
    if _SUBELEMENT.fullmatch(subelement) is None:
        raise NativeDrawingLineAttributeStateError(
            "Drawing line target has no current projected EdgeN selection name."
        )
    if type(visible) is not bool:
        raise NativeDrawingLineAttributeStateError(
            "Drawing line visibility is not boolean."
        )
    exact = {
        "kind": kind,
        "subelement": subelement,
        "format": {
            "line_number": _integer(
                raw["line_number"],
                "number",
                minimum=0,
                maximum=2_147_483_647,
            ),
            "style_code": _integer(
                raw["style_code"],
                "style code",
                minimum=0,
                maximum=2_147_483_647,
            ),
            "width_mm": _number(
                raw["width_mm"],
                "width",
                minimum=0.0,
                maximum=1000.0,
            ),
            "color_rgb": _color(raw["color_rgb"]),
            "visible": visible,
        },
    }
    if kind != "projected_edge":
        exact["tag"] = tag
    return {**exact, "line_state_sha256": _digest(exact)}


def drawing_line_attribute_inventory_state(view: Any) -> dict[str, Any]:
    """Return every persistent cosmetic-edge and centerline format in one view."""

    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    import TechDrawGui

    raw = TechDrawGui.drawingLineAttributes(view)
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_DRAWING_LINE_ATTRIBUTES:
        raise NativeDrawingLineAttributeStateError(
            "Drawing line inventory is unavailable or exceeds 512 targets."
        )
    lines = [_line(item) for item in raw]
    identities = [
        (
            line["kind"],
            line.get("tag", line["subelement"]),
        )
        for line in lines
    ]
    subelements = [line["subelement"] for line in lines]
    if len(identities) != len(set(identities)) or len(subelements) != len(
        set(subelements)
    ):
        raise NativeDrawingLineAttributeStateError(
            "Drawing line inventory contains duplicate identities."
        )
    exact = {
        "view_object_name": str(view.Name),
        "lines": lines,
    }
    cosmetic_count = sum(line["kind"] == "cosmetic_edge" for line in lines)
    centerline_count = sum(line["kind"] == "centerline" for line in lines)
    projected_count = len(lines) - cosmetic_count - centerline_count
    return {
        **exact,
        "inventory_state_sha256": _digest(exact),
        "line_count": len(lines),
        "projected_edge_count": projected_count,
        "cosmetic_edge_count": cosmetic_count,
        "centerline_count": centerline_count,
        "valid": True,
        "issues": [],
    }


def drawing_line_attribute_page(
    view: Any,
    *,
    expected_inventory_state_sha256: str,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Read one hash-pinned bounded page of line targets."""

    if type(offset) is not int or not 0 <= offset <= MAX_DRAWING_LINE_ATTRIBUTES:
        raise ValueError("Drawing line offset must be an integer from 0 through 512.")
    if type(page_size) is not int or not 1 <= page_size <= MAX_DRAWING_LINE_ATTRIBUTE_PAGE_SIZE:
        raise ValueError("Drawing line page_size must be an integer from 1 through 48.")
    state = drawing_line_attribute_inventory_state(view)
    if str(expected_inventory_state_sha256) != state["inventory_state_sha256"]:
        raise NativeDrawingLineAttributeStateError(
            "The Drawing line inventory changed after it was inspected."
        )
    if offset > state["line_count"]:
        raise ValueError("Drawing line offset exceeds the current target count.")
    stop = min(offset + page_size, state["line_count"])
    return {
        name: state[name]
        for name in (
            "view_object_name",
            "inventory_state_sha256",
            "line_count",
            "projected_edge_count",
            "cosmetic_edge_count",
            "centerline_count",
            "valid",
            "issues",
        )
    } | {
        "offset": offset,
        "returned_count": stop - offset,
        "next_offset": stop if stop < state["line_count"] else None,
        "lines": state["lines"][offset:stop],
    }
