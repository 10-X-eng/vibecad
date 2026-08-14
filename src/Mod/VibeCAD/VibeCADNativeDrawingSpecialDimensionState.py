# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact persisted state for specialized projected Drawing dimensions."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionState import drawing_dimension_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Drawing chamfer {noun} is not numeric.") from exc
    if not math.isfinite(result) or abs(result) > 1.0e12:
        raise ValueError(f"Drawing chamfer {noun} is outside the supported range.")
    return round(result, 12)


def is_drawing_chamfer_dimension(dimension: Any) -> bool:
    try:
        if (
            not dimension.isDerivedFrom("TechDraw::DrawViewDimension")
            or str(getattr(dimension, "Type", "") or "")
            not in {"DistanceX", "DistanceY"}
        ):
            return False
        references = tuple(getattr(dimension, "References2D", ()) or ())
        names = []
        for _obj, raw_names in references:
            values = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
            names.extend(str(value or "") for value in values)
        return (
            len(names) == 2
            and all(name.startswith("Vertex") for name in names)
            and " x" in str(getattr(dimension, "FormatSpec", "") or "")
            and str(getattr(dimension, "FormatSpec", "") or "").endswith("°")
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def drawing_chamfer_dimension_state(dimension: Any) -> dict[str, Any]:
    """Return exact output state for one horizontal or vertical chamfer."""

    base = drawing_dimension_state(dimension)
    dimension_type = base["dimension_type"]
    if dimension_type not in {"DistanceX", "DistanceY"}:
        raise ValueError("A chamfer dimension must be horizontal or vertical.")
    if len(base["references"]) != 2 or any(
        not item["subelement"].startswith("Vertex")
        for item in base["references"]
    ):
        raise ValueError("A chamfer dimension requires exactly two projected vertices.")
    points = dimension.getLinearPoints()
    if len(points) != 2:
        raise ValueError("A chamfer dimension did not retain two linear points.")
    dx = _finite(points[0].x - points[1].x, "point delta X")
    dy = _finite(points[0].y - points[1].y, "point delta Y")
    angle = int(
        round(
            math.degrees(
                abs(
                    math.atan2(dx, dy)
                    if dimension_type == "DistanceY"
                    else math.atan2(dy, dx)
                )
            )
        )
    )
    format_spec = str(getattr(dimension, "FormatSpec", "") or "")
    suffix = f" x{angle}°"
    if not format_spec.endswith(suffix):
        raise ValueError("A chamfer dimension format does not contain its exact angle.")
    chamfer = {
        "direction": "horizontal" if dimension_type == "DistanceX" else "vertical",
        "angle_degrees": angle,
        "format_spec_sha256": hashlib.sha256(
            format_spec.encode("utf-8")
        ).hexdigest(),
        "format_spec_characters": len(format_spec),
    }
    exact = {
        "base_state_sha256": base["state_sha256"],
        "chamfer": chamfer,
    }
    return {
        **base,
        "chamfer": {**chamfer, "format_spec": format_spec[:512]},
        "state_sha256": _digest(exact),
    }


def _arc_length_source(dimension: Any) -> tuple[Any, str]:
    raw = getattr(dimension, "ArcLengthSource", None)
    if not isinstance(raw, tuple) or len(raw) != 2 or raw[0] is None:
        raise ValueError("An arc-length dimension has no persisted source edge.")
    raw_names = raw[1]
    names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
    if len(names) != 1:
        raise ValueError("An arc-length dimension source edge is malformed.")
    name = str(names[0] or "")
    if not name.startswith("Edge"):
        raise ValueError("An arc-length dimension source is not an edge.")
    return raw[0], name


def is_drawing_arc_length_dimension(dimension: Any) -> bool:
    try:
        source_view, edge_name = _arc_length_source(dimension)
        return (
            dimension.isDerivedFrom("TechDraw::DrawViewDimension")
            and str(getattr(dimension, "Type", "") or "") == "Distance"
            and float(getattr(dimension, "ArcLengthValue", 0.0)) > 0.0
            and source_view is not None
            and edge_name.startswith("Edge")
            and str(getattr(dimension, "FormatSpec", "") or "").startswith("◠ ")
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def drawing_arc_length_dimension_state(dimension: Any) -> dict[str, Any]:
    """Return exact durable state for one projected circular arc length."""

    base = drawing_dimension_state(dimension)
    source_view, edge_name = _arc_length_source(dimension)
    if getattr(source_view, "Document", None) is not dimension.Document:
        raise ValueError("An arc-length dimension source is in another document.")
    page = dimension.findParentPage()
    if source_view.findParentPage() is not page:
        raise ValueError("An arc-length dimension source is on another page.")
    projection = drawing_projected_geometry_state(source_view)
    source_edge = next(
        (
            item
            for item in projection["elements"]
            if item["name"] == edge_name and item["element_type"] == "edge"
        ),
        None,
    )
    if source_edge is None or source_edge["closed"]:
        raise ValueError("An arc-length dimension source circular arc is unavailable.")
    arc_length = _finite(
        getattr(dimension, "ArcLengthValue", 0.0),
        "persisted length",
    )
    scale = _finite(getattr(source_view, "Scale", 0.0), "source view scale")
    if scale <= 0.0 or not math.isclose(
        arc_length,
        float(source_edge["length_view_mm"]) / scale,
        rel_tol=1.0e-9,
        abs_tol=1.0e-9,
    ):
        raise ValueError("An arc-length dimension value does not match its source arc.")
    format_spec = str(getattr(dimension, "FormatSpec", "") or "")
    if not format_spec.startswith("◠ "):
        raise ValueError("An arc-length dimension does not retain its arc symbol.")
    arbitrary_display = bool(getattr(dimension, "Arbitrary", False))
    if (
        len(base["references"]) != 1
        or base["references"][0]["view_name"] != str(source_view.Name)
        or base["references"][0]["subelement"] != edge_name
    ):
        raise ValueError("An arc-length dimension does not reference its source arc.")
    source = {
        "view_name": str(source_view.Name),
        "subelement": edge_name,
        "element_state_sha256": source_edge["element_state_sha256"],
    }
    arc = {
        "source": source,
        "length_mm": arc_length,
        "format_spec": format_spec[:512],
        "arbitrary_display": arbitrary_display,
    }
    exact = {
        "base_state_sha256": base["state_sha256"],
        "source": source,
        "length_mm": arc_length,
        "arbitrary_display": arbitrary_display,
        "format_spec_sha256": hashlib.sha256(
            format_spec.encode("utf-8")
        ).hexdigest(),
    }
    return {
        **base,
        "measured_value": {"value": arc_length, "unit": "mm"},
        "arc_length": arc,
        "state_sha256": _digest(exact),
    }
