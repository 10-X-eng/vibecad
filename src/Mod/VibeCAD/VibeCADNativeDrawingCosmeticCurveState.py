# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact host plans and durable state for Drawing cosmetic curves."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingViewState import is_part_drawing_view


MAX_DRAWING_COSMETIC_CURVES = 4096
MAX_DRAWING_COSMETIC_RADIUS_MM = 1_000_000_000.0
DRAWING_COSMETIC_CURVE_KINDS = (
    "one_point_circle",
    "two_point_circle",
    "three_point_circle",
    "center_start_end_arc",
)
_SOURCE_COUNTS = {
    "one_point_circle": 1,
    "two_point_circle": 2,
    "three_point_circle": 3,
    "center_start_end_arc": 3,
}
_VERTEX = re.compile(r"^Vertex(?:0|[1-9][0-9]*)$")
_EDGE = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")
_TAG = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class NativeDrawingCosmeticCurveStateError(RuntimeError):
    """Cosmetic-curve host or persistent state is malformed."""


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
    minimum: float = -MAX_DRAWING_COSMETIC_RADIUS_MM,
    maximum: float = MAX_DRAWING_COSMETIC_RADIUS_MM,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingCosmeticCurveStateError(
            f"Drawing cosmetic curve {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingCosmeticCurveStateError(
            f"Drawing cosmetic curve {noun} is outside the supported range."
        )
    return round(result, 12)


def _integer(value: Any, noun: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise NativeDrawingCosmeticCurveStateError(
            f"Drawing cosmetic curve {noun} is invalid."
        )
    return value


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"x_mm", "y_mm"}
    ):
        raise NativeDrawingCosmeticCurveStateError(
            f"Drawing cosmetic curve {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x_mm"], f"{noun} X coordinate"),
        "y_mm": _number(value["y_mm"], f"{noun} Y coordinate"),
    }


def _color(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"red", "green", "blue"}
    ):
        raise NativeDrawingCosmeticCurveStateError(
            "Drawing cosmetic curve color is malformed."
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


def _format(value: Any) -> dict[str, Any]:
    fields = frozenset(
        {"line_number", "style_code", "width_mm", "color_rgb", "visible"}
    )
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise NativeDrawingCosmeticCurveStateError(
            "Drawing cosmetic curve line format is malformed."
        )
    if type(value["visible"]) is not bool:
        raise NativeDrawingCosmeticCurveStateError(
            "Drawing cosmetic curve visibility is not boolean."
        )
    return {
        "line_number": _integer(value["line_number"], "line number"),
        "style_code": _integer(value["style_code"], "style code"),
        "width_mm": _number(
            value["width_mm"],
            "line width",
            minimum=0.0,
            maximum=1000.0,
        ),
        "color_rgb": _color(value["color_rgb"]),
        "visible": value["visible"],
    }


def _tag(value: Any) -> str:
    result = str(value or "")
    if _TAG.fullmatch(result) is None:
        raise NativeDrawingCosmeticCurveStateError(
            "Drawing cosmetic curve tag is invalid."
        )
    return result


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)


def _points_close(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _close(left["x_mm"], right["x_mm"]) and _close(left["y_mm"], right["y_mm"])


def _distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    return math.hypot(
        right["x_mm"] - left["x_mm"],
        right["y_mm"] - left["y_mm"],
    )


def _angle(center: Mapping[str, Any], point: Mapping[str, Any]) -> float:
    result = math.degrees(
        math.atan2(
            point["y_mm"] - center["y_mm"],
            point["x_mm"] - center["x_mm"],
        )
    )
    return result if result >= 0.0 else result + 360.0


def _angles_close(left: float, right: float) -> bool:
    difference = (left - right) % 360.0
    return _close(difference, 0.0) or _close(difference, 360.0)


def _geometry(value: Any) -> dict[str, Any]:
    fields = frozenset(
        {
            "geometry_configuration",
            "center_in_view_mm",
            "radius_mm",
            "start_angle_degrees",
            "end_angle_degrees",
            "clockwise",
        }
    )
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise NativeDrawingCosmeticCurveStateError(
            "Drawing cosmetic curve geometry is malformed."
        )
    configuration = str(value["geometry_configuration"] or "")
    clockwise = value["clockwise"]
    if configuration not in {"circle", "circular_arc"} or type(clockwise) is not bool:
        raise NativeDrawingCosmeticCurveStateError(
            "Drawing cosmetic curve configuration is invalid."
        )
    radius = _number(
        value["radius_mm"],
        "radius",
        minimum=1.0e-9,
    )
    if configuration == "circle":
        if (
            value["start_angle_degrees"] is not None
            or value["end_angle_degrees"] is not None
            or clockwise
        ):
            raise NativeDrawingCosmeticCurveStateError(
                "A full cosmetic circle must not carry arc direction data."
            )
        start = None
        end = None
    else:
        start = _number(value["start_angle_degrees"], "start angle")
        end = _number(value["end_angle_degrees"], "end angle")
    return {
        "geometry_configuration": configuration,
        "center_in_view_mm": _point(value["center_in_view_mm"], "center"),
        "radius_mm": radius,
        "start_angle_degrees": start,
        "end_angle_degrees": end,
        "clockwise": clockwise,
    }


def _require_host_geometry(
    kind: str,
    points: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
) -> None:
    expected_configuration = (
        "circular_arc" if kind == "center_start_end_arc" else "circle"
    )
    if geometry["geometry_configuration"] != expected_configuration:
        raise NativeDrawingCosmeticCurveStateError(
            "TechDraw returned the wrong cosmetic curve configuration."
        )
    center = geometry["center_in_view_mm"]
    radius = geometry["radius_mm"]
    if kind in {"one_point_circle", "two_point_circle", "center_start_end_arc"}:
        if not _points_close(center, points[0]):
            raise NativeDrawingCosmeticCurveStateError(
                "TechDraw returned a cosmetic curve with the wrong center."
            )
    if kind == "two_point_circle" and not _close(
        radius, _distance(points[0], points[1])
    ):
        raise NativeDrawingCosmeticCurveStateError(
            "TechDraw returned an inconsistent two-point circle radius."
        )
    if kind == "three_point_circle" and any(
        not _close(radius, _distance(center, point)) for point in points
    ):
        raise NativeDrawingCosmeticCurveStateError(
            "TechDraw returned a circle that does not pass through all three points."
        )
    if kind == "center_start_end_arc":
        if (
            not _close(radius, _distance(points[0], points[1]))
            or geometry["clockwise"]
            or not _angles_close(
                geometry["start_angle_degrees"], _angle(points[0], points[1])
            )
            or not _angles_close(
                geometry["end_angle_degrees"], _angle(points[0], points[2])
            )
        ):
            raise NativeDrawingCosmeticCurveStateError(
                "TechDraw returned inconsistent center/start/end arc geometry."
            )


def normalize_cosmetic_curve_host_plan(
    raw: Any,
    *,
    created: bool,
) -> dict[str, Any]:
    """Validate one complete compiled cosmetic-curve plan or result."""

    fields = {
        "kind",
        "source_subelements",
        "source_points_in_view_mm",
        "geometry",
        "line_format",
    }
    if created:
        fields.add("curve_tag")
    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(fields):
        raise NativeDrawingCosmeticCurveStateError(
            "TechDraw returned malformed cosmetic-curve data."
        )
    kind = str(raw["kind"] or "")
    if kind not in _SOURCE_COUNTS:
        raise NativeDrawingCosmeticCurveStateError(
            "TechDraw returned an invalid cosmetic-curve kind."
        )
    sources_raw = raw["source_subelements"]
    points_raw = raw["source_points_in_view_mm"]
    expected_count = _SOURCE_COUNTS[kind]
    if (
        not isinstance(sources_raw, Sequence)
        or isinstance(sources_raw, (str, bytes))
        or len(sources_raw) != expected_count
        or not isinstance(points_raw, Sequence)
        or isinstance(points_raw, (str, bytes))
        or len(points_raw) != expected_count
    ):
        raise NativeDrawingCosmeticCurveStateError(
            "TechDraw returned the wrong cosmetic-curve source count."
        )
    sources = [str(item or "") for item in sources_raw]
    if any(_VERTEX.fullmatch(item) is None for item in sources) or len(
        set(sources)
    ) != len(sources):
        raise NativeDrawingCosmeticCurveStateError(
            "TechDraw returned invalid or duplicate cosmetic-curve sources."
        )
    points = [
        _point(item, f"source point {index + 1}")
        for index, item in enumerate(points_raw)
    ]
    geometry = _geometry(raw["geometry"])
    _require_host_geometry(kind, points, geometry)
    result = {
        "kind": kind,
        "source_subelements": sources,
        "source_points_in_view_mm": points,
        "geometry": geometry,
        "line_format": _format(raw["line_format"]),
    }
    if created:
        result["curve_tag"] = _tag(raw["curve_tag"])
    return result


def _persistent_curve(raw: Any) -> dict[str, Any]:
    fields = frozenset({"tag", "subelement", "geometry", "line_format"})
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingCosmeticCurveStateError(
            "Persistent Drawing cosmetic-curve state is malformed."
        )
    subelement = str(raw["subelement"] or "")
    if _EDGE.fullmatch(subelement) is None:
        raise NativeDrawingCosmeticCurveStateError(
            "A persistent cosmetic curve has no current EdgeN selection name."
        )
    exact = {
        "tag": _tag(raw["tag"]),
        "subelement": subelement,
        "geometry": _geometry(raw["geometry"]),
        "line_format": _format(raw["line_format"]),
    }
    return {**exact, "curve_state_sha256": _digest(exact)}


def drawing_cosmetic_curve_inventory_state(view: Any) -> dict[str, Any]:
    """Return every persistent circle and circular arc in property-list order."""

    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    import TechDrawGui

    raw = TechDrawGui.drawingCosmeticCurves(view)
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_DRAWING_COSMETIC_CURVES:
        raise NativeDrawingCosmeticCurveStateError(
            "The Drawing cosmetic-curve inventory exceeds 4096 targets."
        )
    curves = [_persistent_curve(item) for item in raw]
    tags = [item["tag"] for item in curves]
    subelements = [item["subelement"] for item in curves]
    if len(tags) != len(set(tags)) or len(subelements) != len(set(subelements)):
        raise NativeDrawingCosmeticCurveStateError(
            "The Drawing cosmetic-curve inventory contains duplicate identities."
        )
    exact = {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "curves": curves,
    }
    return {
        **exact,
        "curve_count": len(curves),
        "inventory_state_sha256": _digest(exact),
        "valid": True,
        "issues": [],
    }


def drawing_cosmetic_curve_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve one created durable tag and its exact projected sources."""

    if [item["name"] for item in source_elements] != created_plan["source_subelements"]:
        raise NativeDrawingCosmeticCurveStateError(
            "Cosmetic-curve sources do not match the exact projected targets."
        )
    inventory = drawing_cosmetic_curve_inventory_state(view)
    persistent = next(
        (
            item
            for item in inventory["curves"]
            if item["tag"] == created_plan["curve_tag"]
        ),
        None,
    )
    if persistent is None:
        raise NativeDrawingCosmeticCurveStateError(
            "The created cosmetic curve's durable tag is not present in the view."
        )
    geometry_differences = [
        name
        for name in created_plan["geometry"]
        if persistent["geometry"][name] != created_plan["geometry"][name]
    ]
    if geometry_differences:
        differences = "; ".join(
            f"{name} planned={created_plan['geometry'][name]!r} "
            f"durable={persistent['geometry'][name]!r}"
            for name in geometry_differences
        )
        raise NativeDrawingCosmeticCurveStateError(
            "The created cosmetic curve's durable geometry differs from the host "
            f"plan: {differences}."
        )
    format_differences = [
        name
        for name in created_plan["line_format"]
        if persistent["line_format"][name] != created_plan["line_format"][name]
    ]
    if format_differences:
        differences = "; ".join(
            f"{name} planned={created_plan['line_format'][name]!r} "
            f"durable={persistent['line_format'][name]!r}"
            for name in format_differences
        )
        raise NativeDrawingCosmeticCurveStateError(
            "The created cosmetic curve's durable line format differs from the host "
            f"plan: {differences}."
        )
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "construction": created_plan["kind"],
        "sources": [
            {
                "role_index": index,
                "subelement": source["name"],
                "element_state_sha256": source["element_state_sha256"],
                "point_in_view_mm": created_plan["source_points_in_view_mm"][index],
            }
            for index, source in enumerate(source_elements)
        ],
        "curve": dict(persistent),
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }
