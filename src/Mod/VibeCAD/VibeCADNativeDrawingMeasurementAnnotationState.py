# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact persisted state for Drawing area and arc-length annotations."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingBalloonState import (
    drawing_balloon_style_state,
    is_drawing_balloon,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state


MAX_DRAWING_MEASUREMENT_ELEMENTS = 64
MAX_DRAWING_MEASUREMENT_TEXT_CHARACTERS = 512
_KINDS = {
    "Area": ("area", "face", "mm^2"),
    "ArcLength": ("arc_length", "edge", "mm"),
}


class NativeDrawingMeasurementAnnotationStateError(RuntimeError):
    """A persisted measurement annotation is unavailable or malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _number(value: Any, noun: str) -> float:
    raw = getattr(value, "Value", value)
    try:
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingMeasurementAnnotationStateError(
            f"Drawing measurement {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or abs(result) > 1.0e18:
        raise NativeDrawingMeasurementAnnotationStateError(
            f"Drawing measurement {noun} is outside the supported range."
        )
    return round(result, 12)


def _same(first: float, second: float) -> bool:
    return math.isclose(first, second, rel_tol=1.0e-9, abs_tol=1.0e-8)


def _timeline_usable(annotation: Any) -> bool:
    checker = getattr(
        getattr(annotation, "Document", None),
        "isObjectUsableAtCurrentTimelinePosition",
        None,
    )
    try:
        return bool(checker(annotation)) if callable(checker) else True
    except Exception:
        return False


def is_drawing_measurement_annotation(obj: Any) -> bool:
    if not is_drawing_balloon(obj):
        return False
    return str(getattr(obj, "MeasurementKind", "") or "") in _KINDS


def _measurement_source(annotation: Any) -> tuple[Any, tuple[str, ...]]:
    raw = getattr(annotation, "MeasurementSource", None)
    if not isinstance(raw, tuple) or len(raw) != 2 or raw[0] is None:
        raise NativeDrawingMeasurementAnnotationStateError(
            "Drawing measurement has no persisted projected source."
        )
    names_raw = raw[1]
    names = (
        (str(names_raw),)
        if isinstance(names_raw, str)
        else tuple(str(name or "") for name in tuple(names_raw or ()))
    )
    if (
        not 1 <= len(names) <= MAX_DRAWING_MEASUREMENT_ELEMENTS
        or len(names) != len(set(names))
    ):
        raise NativeDrawingMeasurementAnnotationStateError(
            "Drawing measurement projected sources are malformed."
        )
    return raw[0], names


def drawing_measurement_annotation_state(annotation: Any) -> dict[str, Any]:
    """Return one exact host-measured annotation and its durable references."""

    if not is_drawing_measurement_annotation(annotation):
        raise TypeError(
            "annotation must be a measured TechDraw::DrawViewBalloon"
        )
    kind_property = str(annotation.MeasurementKind)
    kind, element_type, unit = _KINDS[kind_property]
    document = getattr(annotation, "Document", None)
    source_view, element_names = _measurement_source(annotation)
    if (
        document is None
        or getattr(source_view, "Document", None) is not document
        or getattr(annotation, "SourceView", None) is not source_view
    ):
        raise NativeDrawingMeasurementAnnotationStateError(
            "Drawing measurement source view links disagree."
        )
    page = annotation.findParentPage()
    if (
        page is None
        or getattr(page, "Document", None) is not document
        or source_view.findParentPage() is not page
        or annotation not in tuple(getattr(page, "Views", ()) or ())
    ):
        raise NativeDrawingMeasurementAnnotationStateError(
            "Drawing measurement is not attached to its source view's page."
        )

    projection = drawing_projected_geometry_state(source_view)
    by_name = {item["name"]: item for item in projection["elements"]}
    elements = []
    for name in element_names:
        element = by_name.get(name)
        if element is None or element["element_type"] != element_type:
            raise NativeDrawingMeasurementAnnotationStateError(
                f"Drawing measurement source {name!r} is unavailable."
            )
        elements.append(element)

    scale = _number(projection["view_scale"], "source-view scale")
    if scale <= 0.0:
        raise NativeDrawingMeasurementAnnotationStateError(
            "Drawing measurement source-view scale must be positive."
        )
    if kind == "area":
        value_expected = sum(float(item["area_view_mm2"]) for item in elements)
        value_expected /= scale * scale
        total_weight = sum(float(item["area_view_mm2"]) for item in elements)
        if total_weight <= 0.0:
            raise NativeDrawingMeasurementAnnotationStateError(
                "Drawing area annotation has no measurable source area."
            )
        anchor_expected = {
            "x_mm": sum(
                float(item["center_in_view_mm"]["x_mm"])
                * float(item["area_view_mm2"])
                for item in elements
            )
            / total_weight
            / scale,
            "y_mm": sum(
                float(item["center_in_view_mm"]["y_mm"])
                * float(item["area_view_mm2"])
                for item in elements
            )
            / total_weight
            / scale,
        }
        expected_offset = {"x_mm": 0.0, "y_mm": 0.0}
    else:
        value_expected = sum(float(item["length_view_mm"]) for item in elements)
        value_expected /= scale
        anchor_expected = None
        expected_offset = {"x_mm": 20.0, "y_mm": 20.0}

    value = _number(annotation.MeasurementValue, "value")
    if value <= 0.0:
        raise NativeDrawingMeasurementAnnotationStateError(
            "Drawing measurement value must be positive."
        )
    measurement_current = _same(value, value_expected)
    anchor = {
        "x_mm": _number(annotation.OriginX, "anchor X coordinate"),
        "y_mm": _number(annotation.OriginY, "anchor Y coordinate"),
    }
    anchor_matches_source = (
        None
        if anchor_expected is None
        else _same(anchor["x_mm"], anchor_expected["x_mm"])
        and _same(anchor["y_mm"], anchor_expected["y_mm"])
    )
    bubble = {
        "x_mm": _number(annotation.X, "bubble X coordinate"),
        "y_mm": _number(annotation.Y, "bubble Y coordinate"),
    }
    offset = {
        "x_mm": round((bubble["x_mm"] - anchor["x_mm"]) * scale, 12),
        "y_mm": round((bubble["y_mm"] - anchor["y_mm"]) * scale, 12),
    }
    default_placement = _same(
        offset["x_mm"], expected_offset["x_mm"]
    ) and _same(offset["y_mm"], expected_offset["y_mm"])

    text = str(getattr(annotation, "Text", "") or "")
    if not text or len(text) > MAX_DRAWING_MEASUREMENT_TEXT_CHARACTERS:
        raise NativeDrawingMeasurementAnnotationStateError(
            "Drawing measurement annotation text is missing or too long."
        )
    source_elements = [
        {
            "subelement": item["name"],
            "element_type": item["element_type"],
            "element_state_sha256": item["element_state_sha256"],
        }
        for item in elements
    ]
    exact = {
        "object_name": str(annotation.Name),
        "label": str(annotation.Label),
        "type_id": str(annotation.TypeId),
        "page_name": str(page.Name),
        "source_view_name": str(source_view.Name),
        "kind": kind,
        "unit": unit,
        "value": value,
        "current_source_value": round(value_expected, 12),
        "measurement_current": measurement_current,
        "source_elements": source_elements,
        "anchor_in_source_mm": anchor,
        "derived_anchor_in_source_mm": anchor_expected,
        "anchor_matches_source": anchor_matches_source,
        "bubble_offset_in_view_mm": offset,
        "default_placement": default_placement,
        "text": text,
        "style": drawing_balloon_style_state(annotation),
        "timeline_role": str(
            getattr(annotation, "VibeCADTimelineRole", "") or ""
        ),
        "timeline_owner_name": str(
            getattr(
                getattr(annotation, "VibeCADTimelineOwner", None),
                "Name",
                "",
            )
            or ""
        ),
        "timeline_usable": _timeline_usable(annotation),
        "valid": bool(annotation.isValid()),
    }
    return {**exact, "measurement_state_sha256": _digest(exact)}
