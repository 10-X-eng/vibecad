# SPDX-License-Identifier: LGPL-2.1-or-later

"""Complete exact state for direct Drawing dimension editing."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionState import is_drawing_dimension
from VibeCADNativeDrawingFormatState import MAX_DRAWING_FORMAT_CHARACTERS


_STYLES_BY_HOST = {
    "ISO Oriented": "iso_oriented",
    "ISO Referencing": "iso_referencing",
    "ASME Inlined": "asme_inlined",
    "ASME Referencing": "asme_referencing",
}
_STYLES_BY_INDEX = tuple(_STYLES_BY_HOST.values())


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any, noun: str, *, limit: float = 1.0e9) -> float:
    raw = getattr(value, "Value", value)
    try:
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Drawing dimension {noun} is not numeric.") from exc
    if not math.isfinite(result) or abs(result) > limit:
        raise ValueError(f"Drawing dimension {noun} is outside the supported range.")
    rounded = round(result, 12)
    return 0.0 if rounded == 0.0 else rounded


def _text(value: Any, noun: str) -> str:
    result = str(value or "")
    if len(result) > MAX_DRAWING_FORMAT_CHARACTERS:
        raise ValueError(f"Drawing dimension {noun} exceeds 512 characters.")
    return result


def _color(value: Any) -> dict[str, int]:
    try:
        components = tuple(value)
    except TypeError as exc:
        raise ValueError("Drawing dimension color is malformed.") from exc
    if len(components) < 3:
        raise ValueError("Drawing dimension color is malformed.")
    channels = []
    for raw in components[:3]:
        channel = _finite(raw, "color channel", limit=1.0)
        if not 0.0 <= channel <= 1.0:
            raise ValueError("Drawing dimension color channel is outside 0 to 1.")
        channels.append(int(round(channel * 255.0)))
    return {"red": channels[0], "green": channels[1], "blue": channels[2]}


def _style(value: Any) -> str:
    raw = str(value or "")
    if raw in _STYLES_BY_HOST:
        return _STYLES_BY_HOST[raw]
    try:
        index = int(value)
    except (TypeError, ValueError):
        index = -1
    if 0 <= index < len(_STYLES_BY_INDEX):
        return _STYLES_BY_INDEX[index]
    raise ValueError("Drawing dimension standard/style is unsupported.")


def drawing_dimension_edit_state(dimension: Any) -> dict[str, Any]:
    """Return every value replaced by the direct exact-target editor."""

    if not is_drawing_dimension(dimension):
        raise TypeError("dimension must be a TechDraw::DrawViewDimension")
    document = getattr(dimension, "Document", None)
    page = dimension.findParentPage()
    view_object = getattr(dimension, "ViewObject", None)
    if (
        document is None
        or page is None
        or getattr(page, "Document", None) is not document
        or dimension not in tuple(getattr(page, "Views", ()) or ())
        or view_object is None
    ):
        raise ValueError(
            "Drawing dimension is not attached to a live page and view provider."
        )
    dimension_type = str(getattr(dimension, "Type", "") or "")
    tolerance_unit = "degrees" if dimension_type in {"Angle", "Angle3Pt"} else "mm"
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    timeline_usable = bool(not callable(checker) or checker(dimension))
    exact = {
        "object_name": str(getattr(dimension, "Name", "") or ""),
        "label": str(getattr(dimension, "Label", "") or ""),
        "type_id": str(getattr(dimension, "TypeId", "") or ""),
        "page_name": str(getattr(page, "Name", "") or ""),
        "dimension_type": dimension_type,
        "display": {
            "format_spec": _text(dimension.FormatSpec, "format"),
            "arbitrary": bool(dimension.Arbitrary),
        },
        "tolerance": {
            "unit": tolerance_unit,
            "theoretical_exact": bool(dimension.TheoreticalExact),
            "equal": bool(dimension.EqualTolerance),
            "over": _finite(dimension.OverTolerance, "over tolerance"),
            "under": _finite(dimension.UnderTolerance, "under tolerance"),
            "arbitrary": bool(dimension.ArbitraryTolerances),
            "over_format_spec": _text(
                dimension.FormatSpecOverTolerance,
                "over-tolerance format",
            ),
            "under_format_spec": _text(
                dimension.FormatSpecUnderTolerance,
                "under-tolerance format",
            ),
        },
        "layout": {
            "label_position_in_view_mm": {
                "x_mm": _finite(dimension.X, "label X coordinate", limit=10_000.0),
                "y_mm": _finite(dimension.Y, "label Y coordinate", limit=10_000.0),
            },
            "angle_override": bool(dimension.AngleOverride),
            "line_angle_degrees": _finite(
                dimension.LineAngle,
                "line angle",
                limit=360_000.0,
            ),
            "extension_angle_degrees": _finite(
                dimension.ExtensionAngle,
                "extension angle",
                limit=360_000.0,
            ),
        },
        "appearance": {
            "flip_arrowheads": bool(view_object.FlipArrowheads),
            "color_rgb": _color(view_object.Color),
            "font_size_mm": _finite(
                view_object.Fontsize,
                "font size",
                limit=1_000.0,
            ),
            "standard_and_style": _style(view_object.StandardAndStyle),
        },
        "timeline_usable": timeline_usable,
        "valid": bool(dimension.isValid()),
    }
    return {**exact, "edit_state_sha256": _digest(exact)}
