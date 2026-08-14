# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact host plans and durable results for Drawing thread representations."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineLengthState import (
    drawing_line_length_inventory_state,
)


MAX_DRAWING_THREAD_BOTTOM_TARGETS = 32
DRAWING_THREAD_KINDS = frozenset(
    {"hole_side", "hole_bottom", "bolt_side", "bolt_bottom"}
)
_SIDE_KINDS = frozenset({"hole_side", "bolt_side"})
_BOTTOM_KINDS = frozenset({"hole_bottom", "bolt_bottom"})
_FACTORS = {
    "hole_side": 1.176,
    "hole_bottom": 1.176,
    "bolt_side": 0.85,
    "bolt_bottom": 0.85,
}
_ROLES = {
    "hole_side": (
        "first_thread_boundary",
        "second_thread_boundary",
        "thread_end",
    ),
    "bolt_side": (
        "first_thread_boundary",
        "second_thread_boundary",
    ),
}
_EDGE = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")
_TAG = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class NativeDrawingThreadRepresentationStateError(RuntimeError):
    """Thread-representation host or persistent state is malformed."""


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
        raise NativeDrawingThreadRepresentationStateError(
            f"Drawing thread {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingThreadRepresentationStateError(
            f"Drawing thread {noun} is outside the supported range."
        )
    return round(result, 12)


def _integer(value: Any, noun: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise NativeDrawingThreadRepresentationStateError(
            f"Drawing thread {noun} is invalid."
        )
    return value


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"x_mm", "y_mm"}
    ):
        raise NativeDrawingThreadRepresentationStateError(
            f"Drawing thread {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x_mm"], f"{noun} X coordinate"),
        "y_mm": _number(value["y_mm"], f"{noun} Y coordinate"),
    }


def _color(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"red", "green", "blue"}
    ):
        raise NativeDrawingThreadRepresentationStateError(
            "Drawing thread line color is malformed."
        )
    return {
        name: _number(
            value[name],
            f"line color {name}",
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
        raise NativeDrawingThreadRepresentationStateError(
            "Drawing thread line format is malformed."
        )
    if type(value["visible"]) is not bool:
        raise NativeDrawingThreadRepresentationStateError(
            "Drawing thread line visibility is not boolean."
        )
    result = {
        "line_number": _integer(value["line_number"], "line number"),
        "style_code": _integer(value["style_code"], "style code"),
        "width_mm": _number(
            value["width_mm"], "line width", minimum=0.0, maximum=1000.0
        ),
        "color_rgb": _color(value["color_rgb"]),
        "visible": value["visible"],
    }
    if result["line_number"] != 1 or result["style_code"] != 1:
        raise NativeDrawingThreadRepresentationStateError(
            "Drawing thread lines must use the host solid-line style."
        )
    return result


def _tag(value: Any, noun: str) -> str:
    result = str(value or "")
    if _TAG.fullmatch(result) is None:
        raise NativeDrawingThreadRepresentationStateError(
            f"Drawing thread {noun} tag is invalid."
        )
    return result


def _segment(value: Any, noun: str, *, created: bool) -> dict[str, Any]:
    fields = {"start_in_view_mm", "end_in_view_mm"}
    if created:
        fields.add("tag")
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(fields):
        raise NativeDrawingThreadRepresentationStateError(
            f"Drawing thread {noun} segment is malformed."
        )
    result: dict[str, Any] = {
        "start_in_view_mm": _point(value["start_in_view_mm"], f"{noun} start"),
        "end_in_view_mm": _point(value["end_in_view_mm"], f"{noun} end"),
    }
    if created:
        result["tag"] = _tag(value["tag"], noun)
    return result


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)


def _points_close(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _close(left["x_mm"], right["x_mm"]) and _close(left["y_mm"], right["y_mm"])


def _vector(start: Mapping[str, Any], end: Mapping[str, Any]) -> tuple[float, float]:
    return end["x_mm"] - start["x_mm"], end["y_mm"] - start["y_mm"]


def _expected_side_segments(plan: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    first = plan["source_lines"]["first"]
    second = plan["source_lines"]["second"]
    first_start = first["start_in_view_mm"]
    first_end = first["end_in_view_mm"]
    second_start = second["start_in_view_mm"]
    second_end = second["end_in_view_mm"]
    connector_x = second_start["x_mm"] - first_start["x_mm"]
    connector_y = second_start["y_mm"] - first_start["y_mm"]
    diameter = math.hypot(connector_x, connector_y)
    if not _close(diameter, plan["source_diameter_mm"]):
        raise NativeDrawingThreadRepresentationStateError(
            "Drawing thread source diameter is inconsistent."
        )
    first_dx, first_dy = _vector(first_start, first_end)
    second_dx, second_dy = _vector(second_start, second_end)
    if (
        math.hypot(first_dx, first_dy) <= 1.0e-12
        or math.hypot(second_dx, second_dy) <= 1.0e-12
        or abs(first_dx * second_dy - first_dy * second_dx)
        > 1.0e-6 * math.hypot(first_dx, first_dy) * math.hypot(second_dx, second_dy)
    ):
        raise NativeDrawingThreadRepresentationStateError(
            "Drawing thread source lines are not nonzero and parallel."
        )
    delta_scale = (plan["thread_factor"] - 1.0) / 2.0
    delta_x = connector_x * delta_scale
    delta_y = connector_y * delta_scale

    def shifted(point: Mapping[str, Any], sign: float) -> dict[str, float]:
        return {
            "x_mm": point["x_mm"] + sign * delta_x,
            "y_mm": point["y_mm"] + sign * delta_y,
        }

    first_thread = {
        "start_in_view_mm": shifted(first_start, -1.0),
        "end_in_view_mm": shifted(first_end, -1.0),
    }
    second_thread = {
        "start_in_view_mm": shifted(second_start, 1.0),
        "end_in_view_mm": shifted(second_end, 1.0),
    }
    result = [first_thread, second_thread]
    if plan["kind"] == "hole_side":
        result.append(
            {
                "start_in_view_mm": first_thread["end_in_view_mm"],
                "end_in_view_mm": second_thread["end_in_view_mm"],
            }
        )
    return tuple(result)


def normalize_thread_side_host_plan(raw: Any, *, created: bool) -> dict[str, Any]:
    """Validate and normalize one complete compiled-host side-thread plan."""

    fields = frozenset(
        {
            "kind",
            "thread_factor",
            "source_diameter_mm",
            "source_subelements",
            "source_lines",
            "lines",
        }
    )
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned malformed thread-side data."
        )
    kind = str(raw["kind"] or "")
    if kind not in _SIDE_KINDS:
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned an invalid thread-side kind."
        )
    sources_raw = raw["source_subelements"]
    if (
        not isinstance(sources_raw, Sequence)
        or isinstance(sources_raw, (str, bytes))
        or len(sources_raw) != 2
    ):
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned an invalid thread-side source list."
        )
    sources = tuple(str(item or "") for item in sources_raw)
    if any(_EDGE.fullmatch(item) is None for item in sources) or len(set(sources)) != 2:
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned invalid or duplicate thread-side sources."
        )
    source_lines_raw = raw["source_lines"]
    if not isinstance(source_lines_raw, Mapping) or frozenset(
        source_lines_raw
    ) != frozenset({"first", "second"}):
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned malformed thread-side source geometry."
        )
    factor = _number(raw["thread_factor"], "factor", minimum=0.000000001)
    if not _close(factor, _FACTORS[kind]):
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned the wrong thread-side factor."
        )
    plan: dict[str, Any] = {
        "kind": kind,
        "thread_factor": factor,
        "source_diameter_mm": _number(
            raw["source_diameter_mm"], "source diameter", minimum=0.000000001
        ),
        "source_subelements": list(sources),
        "source_lines": {
            "first": _segment(source_lines_raw["first"], "first source", created=False),
            "second": _segment(
                source_lines_raw["second"], "second source", created=False
            ),
        },
        "lines": [],
    }
    raw_lines = raw["lines"]
    expected_roles = _ROLES[kind]
    if (
        not isinstance(raw_lines, Sequence)
        or isinstance(raw_lines, (str, bytes))
        or len(raw_lines) != len(expected_roles)
    ):
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned an invalid thread-side line count."
        )
    for raw_line, expected_role in zip(raw_lines, expected_roles, strict=True):
        if not isinstance(raw_line, Mapping) or frozenset(raw_line) != frozenset(
            {"role", "segment", "line_format"}
        ):
            raise NativeDrawingThreadRepresentationStateError(
                "TechDraw returned a malformed thread-side line."
            )
        role = str(raw_line["role"] or "")
        if role != expected_role:
            raise NativeDrawingThreadRepresentationStateError(
                "TechDraw returned thread-side lines in the wrong role order."
            )
        plan["lines"].append(
            {
                "role": role,
                "segment": _segment(raw_line["segment"], role, created=created),
                "line_format": _format(raw_line["line_format"]),
            }
        )
    if plan["lines"][0]["line_format"] != plan["lines"][1]["line_format"]:
        raise NativeDrawingThreadRepresentationStateError(
            "Thread boundary lines do not share the host thin-line format."
        )
    if kind == "hole_side":
        thin = plan["lines"][0]["line_format"]
        end = plan["lines"][2]["line_format"]
        if thin["color_rgb"] != end["color_rgb"] or thin["visible"] != end["visible"]:
            raise NativeDrawingThreadRepresentationStateError(
                "Thread end and boundary lines do not share active host attributes."
            )
    expected_segments = _expected_side_segments(plan)
    for line, expected in zip(plan["lines"], expected_segments, strict=True):
        actual = line["segment"]
        if not _points_close(
            actual["start_in_view_mm"], expected["start_in_view_mm"]
        ) or not _points_close(actual["end_in_view_mm"], expected["end_in_view_mm"]):
            raise NativeDrawingThreadRepresentationStateError(
                "TechDraw returned inconsistent thread-side geometry."
            )
    if created:
        tags = [line["segment"]["tag"] for line in plan["lines"]]
        if len(tags) != len(set(tags)):
            raise NativeDrawingThreadRepresentationStateError(
                "TechDraw returned duplicate thread-side persistent tags."
            )
    return plan


def normalize_thread_bottom_host_plans(
    raw: Any, *, created: bool
) -> list[dict[str, Any]]:
    """Validate and normalize complete compiled-host bottom-thread plans."""

    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or not 1 <= len(raw) <= MAX_DRAWING_THREAD_BOTTOM_TARGETS
    ):
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned an invalid thread-bottom result count."
        )
    fields = {
        "kind",
        "source_subelement",
        "center_in_view_mm",
        "source_radius_mm",
        "thread_factor",
        "thread_radius_mm",
        "start_angle_degrees",
        "end_angle_degrees",
        "line_format",
    }
    if created:
        fields.add("arc_tag")
    result = []
    for raw_plan in raw:
        if not isinstance(raw_plan, Mapping) or frozenset(raw_plan) != frozenset(
            fields
        ):
            raise NativeDrawingThreadRepresentationStateError(
                "TechDraw returned malformed thread-bottom data."
            )
        kind = str(raw_plan["kind"] or "")
        source = str(raw_plan["source_subelement"] or "")
        if kind not in _BOTTOM_KINDS or _EDGE.fullmatch(source) is None:
            raise NativeDrawingThreadRepresentationStateError(
                "TechDraw returned an invalid thread-bottom source or kind."
            )
        source_radius = _number(
            raw_plan["source_radius_mm"], "source radius", minimum=0.000000001
        )
        factor = _number(raw_plan["thread_factor"], "factor", minimum=0.000000001)
        radius = _number(
            raw_plan["thread_radius_mm"], "thread radius", minimum=0.000000001
        )
        start = _number(raw_plan["start_angle_degrees"], "start angle")
        end = _number(raw_plan["end_angle_degrees"], "end angle")
        if (
            not _close(factor, _FACTORS[kind])
            or not _close(radius, source_radius * factor)
            or not _close(start, 15.0)
            or not _close(end, 285.0)
        ):
            raise NativeDrawingThreadRepresentationStateError(
                "TechDraw returned inconsistent thread-bottom geometry."
            )
        plan: dict[str, Any] = {
            "kind": kind,
            "source_subelement": source,
            "center_in_view_mm": _point(raw_plan["center_in_view_mm"], "bottom center"),
            "source_radius_mm": source_radius,
            "thread_factor": factor,
            "thread_radius_mm": radius,
            "start_angle_degrees": start,
            "end_angle_degrees": end,
            "line_format": _format(raw_plan["line_format"]),
        }
        if created:
            plan["arc_tag"] = _tag(raw_plan["arc_tag"], "bottom arc")
        result.append(plan)
    sources = [item["source_subelement"] for item in result]
    if len(sources) != len(set(sources)) or len({item["kind"] for item in result}) != 1:
        raise NativeDrawingThreadRepresentationStateError(
            "TechDraw returned duplicate or mixed thread-bottom plans."
        )
    if created:
        tags = [item["arc_tag"] for item in result]
        if len(tags) != len(set(tags)):
            raise NativeDrawingThreadRepresentationStateError(
                "TechDraw returned duplicate thread-bottom persistent tags."
            )
    return result


def _compact_line(
    plan: Mapping[str, Any],
    *,
    attribute_by_tag: Mapping[str, Mapping[str, Any]],
    length_by_tag: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    tag = plan["segment"]["tag"]
    attribute = attribute_by_tag.get(tag)
    length = length_by_tag.get(tag)
    if (
        attribute is None
        or length is None
        or attribute["kind"] != "cosmetic_edge"
        or length["kind"] != "cosmetic_edge"
        or attribute["subelement"] != length["subelement"]
        or attribute["format"] != plan["line_format"]
        or not _points_close(
            length["start_in_view_mm"], plan["segment"]["start_in_view_mm"]
        )
        or not _points_close(
            length["end_in_view_mm"], plan["segment"]["end_in_view_mm"]
        )
    ):
        raise NativeDrawingThreadRepresentationStateError(
            "A created thread-side line does not match the exact host plan."
        )
    return {
        "role": plan["role"],
        "tag": tag,
        "subelement": length["subelement"],
        "start_in_view_mm": length["start_in_view_mm"],
        "end_in_view_mm": length["end_in_view_mm"],
        "length_mm": length["length_mm"],
        "line_format": plan["line_format"],
        "line_state_sha256": attribute["line_state_sha256"],
        "line_length_state_sha256": length["line_length_state_sha256"],
    }


def drawing_thread_side_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve created side-thread tags to exact persistent line state."""

    if (
        len(source_elements) != 2
        or [item["name"] for item in source_elements]
        != (created_plan["source_subelements"])
    ):
        raise NativeDrawingThreadRepresentationStateError(
            "Thread-side sources do not match the exact projected targets."
        )
    attributes = drawing_line_attribute_inventory_state(view)
    lengths = drawing_line_length_inventory_state(view)
    attribute_by_tag = {line["tag"]: line for line in attributes["lines"]}
    length_by_tag = {line["tag"]: line for line in lengths["lines"]}
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "kind": created_plan["kind"],
        "thread_factor": created_plan["thread_factor"],
        "source_diameter_mm": created_plan["source_diameter_mm"],
        "sources": [
            {
                "subelement": source["name"],
                "element_state_sha256": source["element_state_sha256"],
                "geometry_type": source["geometry_type"],
                "segment": created_plan["source_lines"][position],
            }
            for source, position in zip(
                source_elements, ("first", "second"), strict=True
            )
        ],
        "created_cosmetic_edge_count": len(created_plan["lines"]),
        "lines": [
            _compact_line(
                line,
                attribute_by_tag=attribute_by_tag,
                length_by_tag=length_by_tag,
            )
            for line in created_plan["lines"]
        ],
        "line_attribute_inventory_state_sha256": attributes["inventory_state_sha256"],
        "line_length_inventory_state_sha256": lengths["inventory_state_sha256"],
    }


def _persistent_arc(raw: Any) -> dict[str, Any]:
    fields = frozenset(
        {
            "tag",
            "subelement",
            "center_in_view_mm",
            "radius_mm",
            "start_angle_degrees",
            "end_angle_degrees",
            "clockwise",
            "line_format",
        }
    )
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingThreadRepresentationStateError(
            "The persistent thread-bottom arc state is malformed."
        )
    if type(raw["clockwise"]) is not bool:
        raise NativeDrawingThreadRepresentationStateError(
            "The persistent thread-bottom arc direction is invalid."
        )
    subelement = str(raw["subelement"] or "")
    if _EDGE.fullmatch(subelement) is None:
        raise NativeDrawingThreadRepresentationStateError(
            "The persistent thread-bottom arc has an invalid projected edge."
        )
    return {
        "tag": _tag(raw["tag"], "persistent arc"),
        "subelement": subelement,
        "center_in_view_mm": _point(raw["center_in_view_mm"], "persistent arc center"),
        "radius_mm": _number(
            raw["radius_mm"], "persistent arc radius", minimum=0.000000001
        ),
        "start_angle_degrees": _number(
            raw["start_angle_degrees"], "persistent arc start angle"
        ),
        "end_angle_degrees": _number(
            raw["end_angle_degrees"], "persistent arc end angle"
        ),
        "clockwise": raw["clockwise"],
        "line_format": _format(raw["line_format"]),
    }


def drawing_thread_bottom_result_state(
    view: Any,
    created_plans: Sequence[Mapping[str, Any]],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve created bottom-thread tags to exact persistent arc state."""

    if len(created_plans) != len(source_elements):
        raise NativeDrawingThreadRepresentationStateError(
            "Thread-bottom source and result counts differ."
        )
    import TechDrawGui

    attributes = drawing_line_attribute_inventory_state(view)
    attribute_by_tag = {line["tag"]: line for line in attributes["lines"]}
    arcs = []
    for plan, source in zip(created_plans, source_elements, strict=True):
        if plan["source_subelement"] != source["name"]:
            raise NativeDrawingThreadRepresentationStateError(
                "A thread-bottom result does not match its exact source."
            )
        arc = _persistent_arc(
            TechDrawGui.drawingPersistentCosmeticArc(view, plan["arc_tag"])
        )
        attribute = attribute_by_tag.get(arc["tag"])
        if (
            arc["tag"] != plan["arc_tag"]
            or attribute is None
            or attribute["kind"] != "cosmetic_edge"
            or attribute["subelement"] != arc["subelement"]
            or attribute["format"] != plan["line_format"]
            or arc["line_format"] != plan["line_format"]
            or not _points_close(arc["center_in_view_mm"], plan["center_in_view_mm"])
            or not _close(arc["radius_mm"], plan["thread_radius_mm"])
            or not _close(arc["start_angle_degrees"], plan["start_angle_degrees"])
            or not _close(arc["end_angle_degrees"], plan["end_angle_degrees"])
        ):
            raise NativeDrawingThreadRepresentationStateError(
                "A persistent thread-bottom arc does not match the exact host plan."
            )
        arcs.append(
            {
                "source": {
                    "subelement": source["name"],
                    "element_state_sha256": source["element_state_sha256"],
                    "geometry_type": source["geometry_type"],
                    "center_in_view_mm": plan["center_in_view_mm"],
                    "radius_mm": plan["source_radius_mm"],
                },
                "thread_radius_mm": plan["thread_radius_mm"],
                "arc": {
                    **arc,
                    "line_state_sha256": attribute["line_state_sha256"],
                },
            }
        )
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "kind": created_plans[0]["kind"],
        "thread_factor": created_plans[0]["thread_factor"],
        "arc_span_degrees": 270.0,
        "created_cosmetic_edge_count": len(arcs),
        "threads": arcs,
        "line_attribute_inventory_state_sha256": attributes["inventory_state_sha256"],
    }
