# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state for TechDraw's current session line defaults."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


MAX_DRAWING_LINE_STYLES = 64
MAX_DRAWING_LINE_STYLE_NAME_CHARACTERS = 160


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(
    value: Any,
    noun: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Drawing {noun} is not numeric.") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise RuntimeError(f"Drawing {noun} is outside the supported range.")
    return round(number, 12)


def _string(value: Any, noun: str) -> str:
    result = str(value or "")
    if not result or len(result) > MAX_DRAWING_LINE_STYLE_NAME_CHARACTERS:
        raise RuntimeError(f"Drawing {noun} is missing or too long.")
    return result


def _available_styles(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= MAX_DRAWING_LINE_STYLES:
        raise RuntimeError("Drawing line-style catalog is unavailable or too large.")
    result = []
    for expected_number, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise RuntimeError("Drawing line-style catalog contains a malformed item.")
        line_number = int(item.get("line_number", 0))
        if line_number != expected_number:
            raise RuntimeError("Drawing line-style catalog is not contiguous and ordered.")
        result.append(
            {
                "line_number": line_number,
                "name": _string(item.get("name"), "line-style name"),
            }
        )
    return result


def drawing_line_defaults_state() -> dict[str, Any]:
    """Return the exact defaults shown by Select Line Attributes."""

    import TechDrawGui

    raw = TechDrawGui.currentLineDefaults()
    if not isinstance(raw, Mapping):
        raise RuntimeError("TechDraw returned malformed line defaults.")
    available_styles = _available_styles(raw.get("available_styles"))
    line_number = int(raw.get("line_number", 0))
    style_name = str(raw.get("style_name") or "")
    issues = []
    if not 1 <= line_number <= len(available_styles):
        issues.append("current line number is outside the active standard")
    elif style_name != available_styles[line_number - 1]["name"]:
        issues.append("current line name does not match the active standard")

    raw_widths = raw.get("available_widths")
    if not isinstance(raw_widths, Mapping):
        raise RuntimeError("TechDraw returned malformed line-width choices.")
    available_widths = {
        name: _finite(
            raw_widths.get(name),
            name.replace("_", " "),
            minimum=0.0,
            maximum=1000.0,
        )
        for name in ("thin_mm", "middle_mm", "thick_mm")
    }
    if not (
        available_widths["thin_mm"]
        <= available_widths["middle_mm"]
        <= available_widths["thick_mm"]
    ):
        issues.append("line-width choices are not ordered")

    raw_color = raw.get("color_rgb")
    if not isinstance(raw_color, Mapping):
        raise RuntimeError("TechDraw returned a malformed line color.")
    color_rgb = {
        name: _finite(
            raw_color.get(name),
            f"line color {name}",
            minimum=0.0,
            maximum=1.0,
        )
        for name in ("red", "green", "blue")
    }
    width_choice = str(raw.get("width_choice") or "")
    if width_choice not in {"thin", "middle", "thick"}:
        issues.append("current dialog width choice is unsupported")

    exact = {
        "scope": "application_session",
        "line_standard": _string(raw.get("line_standard"), "line standard"),
        "standards_body": _string(raw.get("standards_body"), "standards body"),
        "line_number": line_number,
        "style_code": int(raw.get("style_code", 0)),
        "style_name": style_name,
        "width_mm": _finite(
            raw.get("width_mm"),
            "line width",
            minimum=0.0,
            maximum=1000.0,
        ),
        "width_choice": width_choice,
        "available_widths": available_widths,
        "color_rgb": color_rgb,
        "visible": bool(raw.get("visible")),
        "cascade_spacing_mm": _finite(
            raw.get("cascade_spacing_mm"),
            "cascade spacing",
            minimum=0.0,
            maximum=1000.0,
        ),
        "delta_distance_mm": _finite(
            raw.get("delta_distance_mm"),
            "delta distance",
            minimum=0.0,
            maximum=1000.0,
        ),
        "available_styles": available_styles,
    }
    valid = not issues
    return {
        **exact,
        "available_style_count": len(available_styles),
        "valid": valid,
        "issues": issues,
        "state_sha256": _digest(exact),
    }


def read_drawing_line_defaults(document: Any) -> dict[str, Any]:
    """Read current session defaults without changing document or GUI state."""

    if document is None or getattr(document, "Uid", None) is None:
        raise RuntimeError("A live document is required to read Drawing line defaults.")
    return {"line_defaults": drawing_line_defaults_state()}
