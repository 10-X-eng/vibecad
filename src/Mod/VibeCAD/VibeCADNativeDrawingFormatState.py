# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state for Drawing dimension formats and Balloon text."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingBalloonState import is_drawing_balloon
from VibeCADNativeDrawingDimensionState import is_drawing_dimension


MAX_DRAWING_FORMAT_CHARACTERS = 512


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _state_messages(obj: Any) -> list[str]:
    result = []
    for raw in tuple(getattr(obj, "State", ()) or ()):
        value = str(raw or "").strip()
        if value:
            result.append(value[:256])
        if len(result) >= 16:
            break
    return result


def _finite_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or abs(result) > 1.0e18:
        return None
    rounded = round(result, 12)
    return 0.0 if rounded == 0.0 else rounded


def _dimension_rendered_text(dimension: Any) -> str | None:
    try:
        import TechDrawGui

        current = str(getattr(dimension, "FormatSpec", "") or "")
        validation = TechDrawGui.validateDrawingFormatCustomization(
            dimension,
            current,
        )
        return str(validation["preview"] or "")
    except Exception:
        return None


def drawing_format_state(obj: Any) -> dict[str, Any]:
    """Return one exact selected target for Customize Format."""

    dimension = is_drawing_dimension(obj)
    balloon = is_drawing_balloon(obj)
    if not dimension and not balloon:
        raise TypeError("format target must be a Drawing dimension or balloon")
    document = getattr(obj, "Document", None)
    page = obj.findParentPage()
    if (
        document is None
        or page is None
        or getattr(page, "Document", None) is not document
        or obj not in tuple(getattr(page, "Views", ()) or ())
    ):
        raise ValueError("Drawing format target is not attached to a live page.")

    if dimension:
        target_kind = "dimension"
        current_value = str(getattr(obj, "FormatSpec", "") or "")
        rendered_text = _dimension_rendered_text(obj)
        try:
            measured_value = _finite_or_none(obj.getRawValue())
        except Exception:
            measured_value = None
        semantic = {
            "dimension_type": str(getattr(obj, "Type", "") or ""),
            "measure_type": str(getattr(obj, "MeasureType", "") or ""),
            "arbitrary_display": bool(getattr(obj, "Arbitrary", False)),
            "measured_value": measured_value,
            "tolerance": {
                "equal": bool(getattr(obj, "EqualTolerance", False)),
                "over_mm": _finite_or_none(getattr(obj, "OverTolerance", 0.0)),
                "under_mm": _finite_or_none(getattr(obj, "UnderTolerance", 0.0)),
                "over_format": str(
                    getattr(obj, "FormatSpecOverTolerance", "") or ""
                )[:128],
                "under_format": str(
                    getattr(obj, "FormatSpecUnderTolerance", "") or ""
                )[:128],
            },
        }
    else:
        target_kind = "balloon"
        current_value = str(getattr(obj, "Text", "") or "")
        rendered_text = current_value
        semantic = {}

    rendered_sha256 = (
        _text_digest(rendered_text) if rendered_text is not None else None
    )
    exact = {
        "object_name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type_id": str(getattr(obj, "TypeId", "") or ""),
        "page_name": str(getattr(page, "Name", "") or ""),
        "target_kind": target_kind,
        "current_value_sha256": _text_digest(current_value),
        "current_value_characters": len(current_value),
        "rendered_text_sha256": rendered_sha256,
        "rendered_text_characters": (
            len(rendered_text) if rendered_text is not None else None
        ),
        **semantic,
        "timeline_usable": _timeline_usable(obj),
        "valid": bool(obj.isValid()),
    }
    result = {
        **exact,
        "format_state_sha256": _digest(exact),
        "state_messages": _state_messages(obj),
        "current_value": current_value[:MAX_DRAWING_FORMAT_CHARACTERS],
        "rendered_text": (
            rendered_text[:MAX_DRAWING_FORMAT_CHARACTERS]
            if rendered_text is not None
            else None
        ),
    }
    if len(current_value) > MAX_DRAWING_FORMAT_CHARACTERS:
        result["current_value_truncated"] = True
    if (
        rendered_text is not None
        and len(rendered_text) > MAX_DRAWING_FORMAT_CHARACTERS
    ):
        result["rendered_text_truncated"] = True
    return result
