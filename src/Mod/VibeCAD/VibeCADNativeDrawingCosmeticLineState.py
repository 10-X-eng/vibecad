# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact host plans and durable state for Drawing cosmetic straight lines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingViewState import is_part_drawing_view


MAX_DRAWING_COSMETIC_LINES = 4096
DRAWING_COSMETIC_LINE_CONSTRUCTIONS = ("parallel", "perpendicular")
_EDGE = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")
_VERTEX = re.compile(r"^Vertex(?:0|[1-9][0-9]*)$")
_TAG = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MAX_COORDINATE_MM = 1_000_000_000.0


class NativeDrawingCosmeticLineStateError(RuntimeError):
    """Cosmetic-line host or persistent state is malformed."""


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
    minimum: float = -_MAX_COORDINATE_MM,
    maximum: float = _MAX_COORDINATE_MM,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingCosmeticLineStateError(
            f"Drawing cosmetic line {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingCosmeticLineStateError(
            f"Drawing cosmetic line {noun} is outside the supported range."
        )
    return round(result, 12)


def _integer(value: Any, noun: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise NativeDrawingCosmeticLineStateError(
            f"Drawing cosmetic line {noun} is invalid."
        )
    return value


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"x_mm", "y_mm"}
    ):
        raise NativeDrawingCosmeticLineStateError(
            f"Drawing cosmetic line {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x_mm"], f"{noun} X coordinate"),
        "y_mm": _number(value["y_mm"], f"{noun} Y coordinate"),
    }


def _ordered_points(
    first: Mapping[str, float], second: Mapping[str, float]
) -> tuple[dict[str, float], dict[str, float]]:
    result = sorted(
        (dict(first), dict(second)), key=lambda point: (point["x_mm"], point["y_mm"])
    )
    return result[0], result[1]


def _distance(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    return math.hypot(
        right["x_mm"] - left["x_mm"],
        right["y_mm"] - left["y_mm"],
    )


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)


def _points_close(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    return _close(left["x_mm"], right["x_mm"]) and _close(left["y_mm"], right["y_mm"])


def _color(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"red", "green", "blue"}
    ):
        raise NativeDrawingCosmeticLineStateError(
            "Drawing cosmetic line color is malformed."
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
        raise NativeDrawingCosmeticLineStateError(
            "Drawing cosmetic line format is malformed."
        )
    if type(value["visible"]) is not bool:
        raise NativeDrawingCosmeticLineStateError(
            "Drawing cosmetic line visibility is not boolean."
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
        raise NativeDrawingCosmeticLineStateError(
            "Drawing cosmetic line tag is invalid."
        )
    return result


def _line(value: Any) -> dict[str, Any]:
    fields = frozenset({"start_in_view_mm", "end_in_view_mm", "length_mm"})
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise NativeDrawingCosmeticLineStateError(
            "Drawing cosmetic line geometry is malformed."
        )
    start, end = _ordered_points(
        _point(value["start_in_view_mm"], "start point"),
        _point(value["end_in_view_mm"], "end point"),
    )
    length = _number(value["length_mm"], "length", minimum=1.0e-9)
    if not _close(length, _distance(start, end)):
        raise NativeDrawingCosmeticLineStateError(
            "Drawing cosmetic line length does not match its endpoints."
        )
    return {
        "start_in_view_mm": start,
        "end_in_view_mm": end,
        "length_mm": length,
    }


def _require_construction_geometry(
    construction: str,
    reference_start: Mapping[str, float],
    reference_end: Mapping[str, float],
    through_point: Mapping[str, float],
    line: Mapping[str, Any],
) -> None:
    reference_length = _distance(reference_start, reference_end)
    if reference_length <= 1.0e-9 or not _close(reference_length, line["length_mm"]):
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned a cosmetic line with the wrong reference length."
        )
    line_start = line["start_in_view_mm"]
    line_end = line["end_in_view_mm"]
    midpoint = {
        "x_mm": (line_start["x_mm"] + line_end["x_mm"]) / 2.0,
        "y_mm": (line_start["y_mm"] + line_end["y_mm"]) / 2.0,
    }
    if not _points_close(midpoint, through_point):
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned a cosmetic line not centered on the selected vertex."
        )
    reference_vector = (
        reference_end["x_mm"] - reference_start["x_mm"],
        reference_end["y_mm"] - reference_start["y_mm"],
    )
    line_vector = (
        line_end["x_mm"] - line_start["x_mm"],
        line_end["y_mm"] - line_start["y_mm"],
    )
    tolerance = 1.0e-8 * reference_length * line["length_mm"] + 1.0e-8
    cross = reference_vector[0] * line_vector[1] - reference_vector[1] * line_vector[0]
    dot = reference_vector[0] * line_vector[0] + reference_vector[1] * line_vector[1]
    if construction == "parallel" and abs(cross) > tolerance:
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned a line that is not parallel to its exact reference."
        )
    if construction == "perpendicular" and abs(dot) > tolerance:
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned a line that is not perpendicular to its exact reference."
        )


def normalize_cosmetic_line_host_plan(
    raw: Any,
    *,
    created: bool,
) -> dict[str, Any]:
    """Validate one complete compiled cosmetic-line plan or result."""

    fields = {
        "construction",
        "reference_edge_subelement",
        "through_vertex_subelement",
        "reference_start_in_view_mm",
        "reference_end_in_view_mm",
        "through_point_in_view_mm",
        "line",
        "line_format",
    }
    if created:
        fields.add("line_tag")
    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(fields):
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned malformed cosmetic-line data."
        )
    construction = str(raw["construction"] or "")
    edge = str(raw["reference_edge_subelement"] or "")
    vertex = str(raw["through_vertex_subelement"] or "")
    if construction not in DRAWING_COSMETIC_LINE_CONSTRUCTIONS:
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned an invalid cosmetic-line construction."
        )
    if _EDGE.fullmatch(edge) is None or _VERTEX.fullmatch(vertex) is None:
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned invalid cosmetic-line source identities."
        )
    reference_start, reference_end = _ordered_points(
        _point(raw["reference_start_in_view_mm"], "reference start point"),
        _point(raw["reference_end_in_view_mm"], "reference end point"),
    )
    through_point = _point(raw["through_point_in_view_mm"], "through point")
    line = _line(raw["line"])
    _require_construction_geometry(
        construction,
        reference_start,
        reference_end,
        through_point,
        line,
    )
    result = {
        "construction": construction,
        "reference_edge_subelement": edge,
        "through_vertex_subelement": vertex,
        "reference_start_in_view_mm": reference_start,
        "reference_end_in_view_mm": reference_end,
        "through_point_in_view_mm": through_point,
        "line": line,
        "line_format": _format(raw["line_format"]),
    }
    if created:
        result["line_tag"] = _tag(raw["line_tag"])
    return result


def normalize_two_point_cosmetic_line_host_plan(
    raw: Any,
    *,
    created: bool,
) -> dict[str, Any]:
    """Validate one compiled two-vertex cosmetic-line plan or result."""

    fields = {
        "construction",
        "source_vertex_subelements",
        "line",
        "line_format",
    }
    if created:
        fields.add("line_tag")
    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(fields):
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned malformed two-point cosmetic-line data."
        )
    if raw["construction"] != "between_vertices":
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned an invalid two-point line construction."
        )
    sources_raw = raw["source_vertex_subelements"]
    if (
        not isinstance(sources_raw, Sequence)
        or isinstance(sources_raw, (str, bytes))
        or len(sources_raw) != 2
    ):
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned an invalid two-point source list."
        )
    sources = [str(item or "") for item in sources_raw]
    if any(_VERTEX.fullmatch(item) is None for item in sources) or len(
        set(sources)
    ) != 2:
        raise NativeDrawingCosmeticLineStateError(
            "TechDraw returned invalid or duplicate two-point line sources."
        )
    result = {
        "construction": "between_vertices",
        "source_vertex_subelements": sources,
        "line": _line(raw["line"]),
        "line_format": _format(raw["line_format"]),
    }
    if created:
        result["line_tag"] = _tag(raw["line_tag"])
    return result


def _persistent_line(raw: Any) -> dict[str, Any]:
    fields = frozenset({"tag", "subelement", "line", "line_format"})
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingCosmeticLineStateError(
            "Persistent Drawing cosmetic-line state is malformed."
        )
    subelement = str(raw["subelement"] or "")
    if _EDGE.fullmatch(subelement) is None:
        raise NativeDrawingCosmeticLineStateError(
            "A persistent cosmetic line has no current EdgeN selection name."
        )
    exact = {
        "tag": _tag(raw["tag"]),
        "subelement": subelement,
        "line": _line(raw["line"]),
        "line_format": _format(raw["line_format"]),
    }
    return {**exact, "line_state_sha256": _digest(exact)}


def drawing_cosmetic_line_inventory_state(view: Any) -> dict[str, Any]:
    """Return every persistent straight cosmetic line in property-list order."""

    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    import TechDrawGui

    raw = TechDrawGui.drawingCosmeticLines(view)
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_DRAWING_COSMETIC_LINES:
        raise NativeDrawingCosmeticLineStateError(
            "The Drawing cosmetic-line inventory exceeds 4096 targets."
        )
    lines = [_persistent_line(item) for item in raw]
    tags = [item["tag"] for item in lines]
    subelements = [item["subelement"] for item in lines]
    if len(tags) != len(set(tags)) or len(subelements) != len(set(subelements)):
        raise NativeDrawingCosmeticLineStateError(
            "The Drawing cosmetic-line inventory contains duplicate identities."
        )
    exact = {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "lines": lines,
    }
    return {
        **exact,
        "line_count": len(lines),
        "inventory_state_sha256": _digest(exact),
        "valid": True,
        "issues": [],
    }


def drawing_cosmetic_line_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve one created durable tag and its exact role-qualified sources."""

    if (
        len(source_elements) != 2
        or source_elements[0]["name"] != created_plan["reference_edge_subelement"]
        or source_elements[0]["element_type"] != "edge"
        or source_elements[1]["name"] != created_plan["through_vertex_subelement"]
        or source_elements[1]["element_type"] != "vertex"
    ):
        raise NativeDrawingCosmeticLineStateError(
            "Cosmetic-line sources do not match the exact projected targets."
        )
    inventory = drawing_cosmetic_line_inventory_state(view)
    persistent = next(
        (
            item
            for item in inventory["lines"]
            if item["tag"] == created_plan["line_tag"]
        ),
        None,
    )
    if persistent is None:
        raise NativeDrawingCosmeticLineStateError(
            "The created cosmetic line's durable tag is not present in the view."
        )
    differences = []
    if persistent["line"] != created_plan["line"]:
        differences.append(
            f"geometry planned={created_plan['line']!r} durable={persistent['line']!r}"
        )
    if persistent["line_format"] != created_plan["line_format"]:
        differences.append(
            "format planned="
            f"{created_plan['line_format']!r} durable={persistent['line_format']!r}"
        )
    if differences:
        raise NativeDrawingCosmeticLineStateError(
            "The created cosmetic line differs from the exact host plan: "
            + "; ".join(differences)
            + "."
        )
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "construction": created_plan["construction"],
        "reference_edge": {
            "subelement": source_elements[0]["name"],
            "element_state_sha256": source_elements[0]["element_state_sha256"],
            "start_in_view_mm": created_plan["reference_start_in_view_mm"],
            "end_in_view_mm": created_plan["reference_end_in_view_mm"],
        },
        "through_vertex": {
            "subelement": source_elements[1]["name"],
            "element_state_sha256": source_elements[1]["element_state_sha256"],
            "point_in_view_mm": created_plan["through_point_in_view_mm"],
        },
        "line": dict(persistent),
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }


def drawing_two_point_cosmetic_line_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve one two-vertex cosmetic line and its exact sources."""

    if (
        len(source_elements) != 2
        or [item["name"] for item in source_elements]
        != created_plan["source_vertex_subelements"]
        or any(item["element_type"] != "vertex" for item in source_elements)
    ):
        raise NativeDrawingCosmeticLineStateError(
            "Two-point cosmetic-line sources do not match the exact projected targets."
        )
    inventory = drawing_cosmetic_line_inventory_state(view)
    persistent = next(
        (
            item
            for item in inventory["lines"]
            if item["tag"] == created_plan["line_tag"]
        ),
        None,
    )
    if (
        persistent is None
        or persistent["line"] != created_plan["line"]
        or persistent["line_format"] != created_plan["line_format"]
    ):
        raise NativeDrawingCosmeticLineStateError(
            "The durable two-point cosmetic line differs from the exact host plan."
        )
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "construction": "between_vertices",
        "source_vertices": [
            {
                "subelement": source["name"],
                "element_state_sha256": source["element_state_sha256"],
                "point_in_view_mm": source["point_in_view_mm"],
            }
            for source in source_elements
        ],
        "line": dict(persistent),
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }
