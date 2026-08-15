# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded geometry state for extendable Drawing lines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from VibeCADNativeDrawingViewState import is_part_drawing_view


MAX_DRAWING_LINE_LENGTHS = 512
MAX_DRAWING_LINE_LENGTH_PAGE_SIZE = 48
MIN_DRAWING_LINE_DELTA_MM = 0.000001
MAX_DRAWING_LINE_DELTA_MM = 1_000_000.0
_KINDS = frozenset({"cosmetic_edge", "centerline"})
_TAG = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SUBELEMENT = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")


class NativeDrawingLineLengthStateError(RuntimeError):
    """Extendable Drawing line state is unavailable or malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _number(
    value: Any,
    noun: str,
    *,
    minimum: float = -1_000_000_000.0,
    maximum: float = 1_000_000_000.0,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingLineLengthStateError(
            f"Drawing line {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingLineLengthStateError(
            f"Drawing line {noun} is outside the supported range."
        )
    return round(result, 12)


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"x_mm", "y_mm"}
    ):
        raise NativeDrawingLineLengthStateError(
            f"Drawing line {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x_mm"], f"{noun} X coordinate"),
        "y_mm": _number(value["y_mm"], f"{noun} Y coordinate"),
    }


def _line(raw: Any) -> dict[str, Any]:
    fields = frozenset(
        {
            "kind",
            "tag",
            "subelement",
            "start_in_view_mm",
            "end_in_view_mm",
            "length_mm",
            "centerline_extension_mm",
        }
    )
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingLineLengthStateError(
            "Drawing line geometry contains a malformed target."
        )
    kind = str(raw["kind"] or "")
    tag = str(raw["tag"] or "")
    subelement = str(raw["subelement"] or "")
    if kind not in _KINDS or _TAG.fullmatch(tag) is None:
        raise NativeDrawingLineLengthStateError(
            "Drawing line target identity is malformed."
        )
    if _SUBELEMENT.fullmatch(subelement) is None:
        raise NativeDrawingLineLengthStateError(
            "Drawing line target has no current projected EdgeN selection name."
        )
    start = _point(raw["start_in_view_mm"], "start point")
    end = _point(raw["end_in_view_mm"], "end point")
    length = _number(raw["length_mm"], "length", minimum=0.0)
    calculated = math.hypot(
        end["x_mm"] - start["x_mm"],
        end["y_mm"] - start["y_mm"],
    )
    if length <= 0.0 or not math.isclose(
        length,
        calculated,
        rel_tol=1.0e-10,
        abs_tol=1.0e-9,
    ):
        raise NativeDrawingLineLengthStateError(
            "Drawing line endpoints and length are inconsistent."
        )
    extension_raw = raw["centerline_extension_mm"]
    if kind == "centerline":
        if extension_raw is None:
            raise NativeDrawingLineLengthStateError(
                "Drawing centerline extension state is missing."
            )
        extension = _number(extension_raw, "centerline extension")
    else:
        if extension_raw is not None:
            raise NativeDrawingLineLengthStateError(
                "Drawing cosmetic line has unexpected centerline extension state."
            )
        extension = None
    exact = {
        "kind": kind,
        "tag": tag,
        "subelement": subelement,
        "start_in_view_mm": start,
        "end_in_view_mm": end,
        "length_mm": length,
        "centerline_extension_mm": extension,
    }
    return {**exact, "line_length_state_sha256": _digest(exact)}


def drawing_line_length_inventory_state(view: Any) -> dict[str, Any]:
    """Return all straight persistent lines eligible for symmetric resizing."""

    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    import TechDrawGui

    raw = TechDrawGui.drawingLineLengths(view)
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_DRAWING_LINE_LENGTHS:
        raise NativeDrawingLineLengthStateError(
            "Drawing line-length inventory is unavailable or exceeds 512 targets."
        )
    lines = [_line(item) for item in raw]
    identities = [(line["kind"], line["tag"]) for line in lines]
    subelements = [line["subelement"] for line in lines]
    if len(identities) != len(set(identities)) or len(subelements) != len(
        set(subelements)
    ):
        raise NativeDrawingLineLengthStateError(
            "Drawing line-length inventory contains duplicate identities."
        )
    exact = {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "lines": lines,
    }
    cosmetic_count = sum(line["kind"] == "cosmetic_edge" for line in lines)
    return {
        **exact,
        "inventory_state_sha256": _digest(exact),
        "line_count": len(lines),
        "cosmetic_edge_count": cosmetic_count,
        "centerline_count": len(lines) - cosmetic_count,
        "valid": True,
        "issues": [],
    }


def drawing_line_length_page(
    view: Any,
    *,
    expected_inventory_state_sha256: str,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Read one hash-pinned bounded page of extendable line targets."""

    if type(offset) is not int or not 0 <= offset <= MAX_DRAWING_LINE_LENGTHS:
        raise ValueError("Drawing line-length offset must be 0 through 512.")
    if (
        type(page_size) is not int
        or not 1 <= page_size <= MAX_DRAWING_LINE_LENGTH_PAGE_SIZE
    ):
        raise ValueError("Drawing line-length page_size must be 1 through 48.")
    state = drawing_line_length_inventory_state(view)
    if str(expected_inventory_state_sha256) != state["inventory_state_sha256"]:
        raise NativeDrawingLineLengthStateError(
            "The Drawing line-length inventory changed after it was inspected."
        )
    if offset > state["line_count"]:
        raise ValueError("Drawing line-length offset exceeds the current target count.")
    stop = min(offset + page_size, state["line_count"])
    return {
        name: state[name]
        for name in (
            "view_object_name",
            "coordinate_space",
            "axis_convention",
            "inventory_state_sha256",
            "line_count",
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
