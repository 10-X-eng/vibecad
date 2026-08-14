# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact host plans and durable results for Drawing bolt-circle centerlines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineLengthState import (
    drawing_line_length_inventory_state,
)


MIN_DRAWING_BOLT_CIRCLE_TARGETS = 3
MAX_DRAWING_BOLT_CIRCLE_TARGETS = 32
_EDGE = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")
_TAG = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_CONFIGURATIONS = frozenset({"circle", "circular_arc"})


class NativeDrawingBoltCircleCenterLineStateError(RuntimeError):
    """Bolt-circle host or persistent state is malformed."""


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
        raise NativeDrawingBoltCircleCenterLineStateError(
            f"Drawing bolt-circle {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingBoltCircleCenterLineStateError(
            f"Drawing bolt-circle {noun} is outside the supported range."
        )
    return round(result, 12)


def _integer(value: Any, noun: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise NativeDrawingBoltCircleCenterLineStateError(
            f"Drawing bolt-circle {noun} is invalid."
        )
    return value


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"x_mm", "y_mm"}
    ):
        raise NativeDrawingBoltCircleCenterLineStateError(
            f"Drawing bolt-circle {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x_mm"], f"{noun} X coordinate"),
        "y_mm": _number(value["y_mm"], f"{noun} Y coordinate"),
    }


def _color(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"red", "green", "blue"}
    ):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "Drawing bolt-circle line color is malformed."
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
        raise NativeDrawingBoltCircleCenterLineStateError(
            "Drawing bolt-circle line format is malformed."
        )
    if type(value["visible"]) is not bool:
        raise NativeDrawingBoltCircleCenterLineStateError(
            "Drawing bolt-circle line visibility is not boolean."
        )
    return {
        "line_number": _integer(value["line_number"], "line number"),
        "style_code": _integer(value["style_code"], "style code"),
        "width_mm": _number(
            value["width_mm"], "line width", minimum=0.0, maximum=1000.0
        ),
        "color_rgb": _color(value["color_rgb"]),
        "visible": value["visible"],
    }


def _segment(value: Any, *, created: bool) -> dict[str, Any]:
    fields = {"start_in_view_mm", "end_in_view_mm"}
    if created:
        fields.add("tag")
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(fields):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "Drawing bolt-hole centerline segment is malformed."
        )
    result: dict[str, Any] = {
        "start_in_view_mm": _point(value["start_in_view_mm"], "line start"),
        "end_in_view_mm": _point(value["end_in_view_mm"], "line end"),
    }
    if created:
        tag = str(value["tag"] or "")
        if _TAG.fullmatch(tag) is None:
            raise NativeDrawingBoltCircleCenterLineStateError(
                "Drawing bolt-hole centerline tag is invalid."
            )
        result["tag"] = tag
    return result


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)


def _points_close(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _close(left["x_mm"], right["x_mm"]) and _close(
        left["y_mm"], right["y_mm"]
    )


def _validate_hole_geometry(
    hole: Mapping[str, Any],
    *,
    pattern_center: Mapping[str, Any],
    pattern_radius: float,
    extension_factor: float,
) -> None:
    center = hole["center_in_view_mm"]
    start = hole["center_line"]["start_in_view_mm"]
    end = hole["center_line"]["end_in_view_mm"]
    midpoint = {
        "x_mm": (start["x_mm"] + end["x_mm"]) / 2.0,
        "y_mm": (start["y_mm"] + end["y_mm"]) / 2.0,
    }
    radial_x = center["x_mm"] - pattern_center["x_mm"]
    radial_y = center["y_mm"] - pattern_center["y_mm"]
    radial_length = math.hypot(radial_x, radial_y)
    line_x = end["x_mm"] - start["x_mm"]
    line_y = end["y_mm"] - start["y_mm"]
    line_length = math.hypot(line_x, line_y)
    expected_length = 2.0 * hole["radius_mm"] * extension_factor
    if (
        not _points_close(midpoint, center)
        or not _close(radial_length, hole["pattern_radius_at_center_mm"])
        or not _close(
            radial_length - pattern_radius,
            hole["pattern_radius_deviation_mm"],
        )
        or not _close(line_length, expected_length)
        or not math.isclose(
            radial_x * line_y - radial_y * line_x,
            0.0,
            rel_tol=0.0,
            abs_tol=max(1.0e-8, radial_length * line_length * 1.0e-9),
        )
    ):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "TechDraw returned inconsistent bolt-hole centerline geometry."
        )


def normalize_bolt_circle_center_line_host_plan(
    raw: Any,
    *,
    created: bool,
) -> dict[str, Any]:
    """Validate and normalize the complete compiled-host bolt-circle response."""

    fields = {
        "pattern_center_in_view_mm",
        "pattern_radius_mm",
        "maximum_pattern_radius_deviation_mm",
        "pattern_radius_tolerance_mm",
        "all_centers_on_pattern",
        "hole_center_line_extension_factor",
        "line_format",
        "holes",
    }
    if created:
        fields.add("pattern_circle_tag")
    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(fields):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "TechDraw returned malformed bolt-circle data."
        )
    holes_raw = raw["holes"]
    if (
        not isinstance(holes_raw, Sequence)
        or isinstance(holes_raw, (str, bytes))
        or not MIN_DRAWING_BOLT_CIRCLE_TARGETS
        <= len(holes_raw)
        <= MAX_DRAWING_BOLT_CIRCLE_TARGETS
    ):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "TechDraw returned an unsupported bolt-hole count."
        )
    all_on_pattern = raw["all_centers_on_pattern"]
    if type(all_on_pattern) is not bool:
        raise NativeDrawingBoltCircleCenterLineStateError(
            "TechDraw returned an invalid bolt-pattern uniformity state."
        )
    result: dict[str, Any] = {
        "pattern_center_in_view_mm": _point(
            raw["pattern_center_in_view_mm"], "pattern center"
        ),
        "pattern_radius_mm": _number(
            raw["pattern_radius_mm"], "pattern radius", minimum=0.000000001
        ),
        "maximum_pattern_radius_deviation_mm": _number(
            raw["maximum_pattern_radius_deviation_mm"],
            "maximum pattern-radius deviation",
            minimum=0.0,
        ),
        "pattern_radius_tolerance_mm": _number(
            raw["pattern_radius_tolerance_mm"],
            "pattern-radius tolerance",
            minimum=0.0,
        ),
        "all_centers_on_pattern": all_on_pattern,
        "hole_center_line_extension_factor": _number(
            raw["hole_center_line_extension_factor"],
            "hole centerline extension factor",
            minimum=1.0,
            maximum=1000.0,
        ),
        "line_format": _format(raw["line_format"]),
        "holes": [],
    }
    if created:
        pattern_tag = str(raw["pattern_circle_tag"] or "")
        if _TAG.fullmatch(pattern_tag) is None:
            raise NativeDrawingBoltCircleCenterLineStateError(
                "TechDraw returned an invalid bolt-pattern circle tag."
            )
        result["pattern_circle_tag"] = pattern_tag

    hole_fields = frozenset(
        {
            "source_subelement",
            "geometry_configuration",
            "center_in_view_mm",
            "radius_mm",
            "pattern_radius_at_center_mm",
            "pattern_radius_deviation_mm",
            "center_line",
        }
    )
    for raw_hole in holes_raw:
        if not isinstance(raw_hole, Mapping) or frozenset(raw_hole) != hole_fields:
            raise NativeDrawingBoltCircleCenterLineStateError(
                "TechDraw returned a malformed bolt-hole plan."
            )
        source = str(raw_hole["source_subelement"] or "")
        configuration = str(raw_hole["geometry_configuration"] or "")
        if _EDGE.fullmatch(source) is None or configuration not in _CONFIGURATIONS:
            raise NativeDrawingBoltCircleCenterLineStateError(
                "TechDraw returned an invalid bolt-hole source."
            )
        hole = {
            "source_subelement": source,
            "geometry_configuration": configuration,
            "center_in_view_mm": _point(
                raw_hole["center_in_view_mm"], "hole center"
            ),
            "radius_mm": _number(
                raw_hole["radius_mm"], "hole radius", minimum=0.000000001
            ),
            "pattern_radius_at_center_mm": _number(
                raw_hole["pattern_radius_at_center_mm"],
                "radius at hole center",
                minimum=0.000000001,
            ),
            "pattern_radius_deviation_mm": _number(
                raw_hole["pattern_radius_deviation_mm"],
                "hole pattern-radius deviation",
            ),
            "center_line": _segment(raw_hole["center_line"], created=created),
        }
        _validate_hole_geometry(
            hole,
            pattern_center=result["pattern_center_in_view_mm"],
            pattern_radius=result["pattern_radius_mm"],
            extension_factor=result["hole_center_line_extension_factor"],
        )
        result["holes"].append(hole)

    sources = [hole["source_subelement"] for hole in result["holes"]]
    if len(sources) != len(set(sources)):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "TechDraw returned duplicate bolt-hole sources."
        )
    deviations = [
        abs(hole["pattern_radius_deviation_mm"]) for hole in result["holes"]
    ]
    maximum = max(deviations)
    if (
        not _close(maximum, result["maximum_pattern_radius_deviation_mm"])
        or result["all_centers_on_pattern"]
        != (maximum <= result["pattern_radius_tolerance_mm"])
        or any(
            deviation > result["pattern_radius_tolerance_mm"]
            for deviation in deviations[:3]
        )
    ):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "TechDraw returned inconsistent bolt-pattern diagnostics."
        )
    if created:
        tags = [result["pattern_circle_tag"]] + [
            hole["center_line"]["tag"] for hole in result["holes"]
        ]
        if len(tags) != len(set(tags)):
            raise NativeDrawingBoltCircleCenterLineStateError(
                "TechDraw returned duplicate bolt-circle persistent tags."
            )
    return result


def _compact_line(
    tag: str,
    *,
    attribute_by_tag: Mapping[str, Mapping[str, Any]],
    length_by_tag: Mapping[str, Mapping[str, Any]],
    expected_segment: Mapping[str, Any],
    expected_format: Mapping[str, Any],
) -> dict[str, Any]:
    attribute = attribute_by_tag.get(tag)
    length = length_by_tag.get(tag)
    if (
        attribute is None
        or length is None
        or attribute["kind"] != "cosmetic_edge"
        or length["kind"] != "cosmetic_edge"
        or attribute["subelement"] != length["subelement"]
        or attribute["format"] != expected_format
        or not _points_close(
            length["start_in_view_mm"], expected_segment["start_in_view_mm"]
        )
        or not _points_close(
            length["end_in_view_mm"], expected_segment["end_in_view_mm"]
        )
    ):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "A created bolt-hole centerline does not match the exact host plan."
        )
    return {
        "tag": tag,
        "subelement": length["subelement"],
        "start_in_view_mm": length["start_in_view_mm"],
        "end_in_view_mm": length["end_in_view_mm"],
        "length_mm": length["length_mm"],
        "line_state_sha256": attribute["line_state_sha256"],
        "line_length_state_sha256": length["line_length_state_sha256"],
    }


def drawing_bolt_circle_center_line_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve bolt-pattern and radial-mark tags to exact persistent state."""

    holes = created_plan["holes"]
    if len(holes) != len(source_elements):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "Bolt-hole source and result counts differ."
        )
    import TechDrawGui

    raw_circle = TechDrawGui.drawingPersistentCosmeticCircle(
        view, created_plan["pattern_circle_tag"]
    )
    circle_fields = frozenset(
        {"tag", "subelement", "center_in_view_mm", "radius_mm", "line_format"}
    )
    if not isinstance(raw_circle, Mapping) or frozenset(raw_circle) != circle_fields:
        raise NativeDrawingBoltCircleCenterLineStateError(
            "The created bolt-pattern circle state is malformed."
        )
    circle = {
        "tag": str(raw_circle["tag"] or ""),
        "subelement": str(raw_circle["subelement"] or ""),
        "center_in_view_mm": _point(
            raw_circle["center_in_view_mm"], "persistent pattern center"
        ),
        "radius_mm": _number(
            raw_circle["radius_mm"],
            "persistent pattern radius",
            minimum=0.000000001,
        ),
        "line_format": _format(raw_circle["line_format"]),
    }
    if (
        circle["tag"] != created_plan["pattern_circle_tag"]
        or _EDGE.fullmatch(circle["subelement"]) is None
        or not _points_close(
            circle["center_in_view_mm"],
            created_plan["pattern_center_in_view_mm"],
        )
        or not _close(circle["radius_mm"], created_plan["pattern_radius_mm"])
        or circle["line_format"] != created_plan["line_format"]
    ):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "The persistent bolt-pattern circle does not match the host plan."
        )

    attributes = drawing_line_attribute_inventory_state(view)
    lengths = drawing_line_length_inventory_state(view)
    attribute_by_tag = {line["tag"]: line for line in attributes["lines"]}
    length_by_tag = {line["tag"]: line for line in lengths["lines"]}
    circle_attribute = attribute_by_tag.get(circle["tag"])
    if (
        circle_attribute is None
        or circle_attribute["kind"] != "cosmetic_edge"
        or circle_attribute["subelement"] != circle["subelement"]
        or circle_attribute["format"] != circle["line_format"]
    ):
        raise NativeDrawingBoltCircleCenterLineStateError(
            "The persistent bolt-pattern circle is missing from line state."
        )
    circle_exact = {
        "tag": circle["tag"],
        "subelement": circle["subelement"],
        "center_in_view_mm": circle["center_in_view_mm"],
        "radius_mm": circle["radius_mm"],
        "line_state_sha256": circle_attribute["line_state_sha256"],
    }
    circle_exact["cosmetic_circle_state_sha256"] = _digest(circle_exact)

    result_holes = []
    for hole, source in zip(holes, source_elements, strict=True):
        if hole["source_subelement"] != source["name"]:
            raise NativeDrawingBoltCircleCenterLineStateError(
                "A bolt-hole result does not match its exact source."
            )
        result_holes.append(
            {
                "source": {
                    "subelement": source["name"],
                    "element_state_sha256": source["element_state_sha256"],
                    "geometry_type": source["geometry_type"],
                    "geometry_configuration": hole["geometry_configuration"],
                    "center_in_view_mm": hole["center_in_view_mm"],
                    "radius_mm": hole["radius_mm"],
                },
                "pattern_radius_at_center_mm": hole[
                    "pattern_radius_at_center_mm"
                ],
                "pattern_radius_deviation_mm": hole[
                    "pattern_radius_deviation_mm"
                ],
                "center_line": _compact_line(
                    hole["center_line"]["tag"],
                    attribute_by_tag=attribute_by_tag,
                    length_by_tag=length_by_tag,
                    expected_segment=hole["center_line"],
                    expected_format=created_plan["line_format"],
                ),
            }
        )
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "pattern_definition": "ordered_first_three_hole_centers",
        "hole_count": len(result_holes),
        "created_cosmetic_edge_count": len(result_holes) + 1,
        "pattern_radius_tolerance_mm": created_plan[
            "pattern_radius_tolerance_mm"
        ],
        "maximum_pattern_radius_deviation_mm": created_plan[
            "maximum_pattern_radius_deviation_mm"
        ],
        "all_centers_on_pattern": created_plan["all_centers_on_pattern"],
        "hole_center_line_extension_factor": created_plan[
            "hole_center_line_extension_factor"
        ],
        "line_format": created_plan["line_format"],
        "pattern_circle": circle_exact,
        "holes": result_holes,
        "line_attribute_inventory_state_sha256": attributes[
            "inventory_state_sha256"
        ],
        "line_length_inventory_state_sha256": lengths[
            "inventory_state_sha256"
        ],
    }
