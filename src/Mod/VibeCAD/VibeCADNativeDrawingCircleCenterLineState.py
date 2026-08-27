# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact host plans and durable results for Drawing circle centerlines."""

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


MAX_DRAWING_CIRCLE_CENTER_LINE_TARGETS = 32
_EDGE = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")
_TAG = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_CONFIGURATIONS = frozenset({"circle", "circular_arc"})


class NativeDrawingCircleCenterLineStateError(RuntimeError):
    """Circle-centerline host or persistent state is malformed."""


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
        raise NativeDrawingCircleCenterLineStateError(
            f"Drawing circle centerline {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingCircleCenterLineStateError(
            f"Drawing circle centerline {noun} is outside the supported range."
        )
    return round(result, 12)


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"x_mm", "y_mm"}
    ):
        raise NativeDrawingCircleCenterLineStateError(
            f"Drawing circle centerline {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x_mm"], f"{noun} X coordinate"),
        "y_mm": _number(value["y_mm"], f"{noun} Y coordinate"),
    }


def _color(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"red", "green", "blue"}
    ):
        raise NativeDrawingCircleCenterLineStateError(
            "Drawing circle centerline color is malformed."
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


def _integer(value: Any, noun: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise NativeDrawingCircleCenterLineStateError(
            f"Drawing circle centerline {noun} is invalid."
        )
    return value


def _format(value: Any) -> dict[str, Any]:
    fields = frozenset(
        {"line_number", "style_code", "width_mm", "color_rgb", "visible"}
    )
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise NativeDrawingCircleCenterLineStateError(
            "Drawing circle centerline format is malformed."
        )
    if type(value["visible"]) is not bool:
        raise NativeDrawingCircleCenterLineStateError(
            "Drawing circle centerline visibility is not boolean."
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


def _segment(value: Any, noun: str, *, created: bool) -> dict[str, Any]:
    fields = {"start_in_view_mm", "end_in_view_mm"}
    if created:
        fields.add("tag")
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(fields):
        raise NativeDrawingCircleCenterLineStateError(
            f"Drawing circle centerline {noun} segment is malformed."
        )
    result: dict[str, Any] = {
        "start_in_view_mm": _point(value["start_in_view_mm"], f"{noun} start"),
        "end_in_view_mm": _point(value["end_in_view_mm"], f"{noun} end"),
    }
    if created:
        tag = str(value["tag"] or "")
        if _TAG.fullmatch(tag) is None:
            raise NativeDrawingCircleCenterLineStateError(
                f"Drawing circle centerline {noun} tag is invalid."
            )
        result["tag"] = tag
    return result


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)


def _points_close(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _close(left["x_mm"], right["x_mm"]) and _close(
        left["y_mm"], right["y_mm"]
    )


def _require_cross_geometry(pair: Mapping[str, Any]) -> None:
    center = pair["center_in_view_mm"]
    extent = pair["radius_mm"] + pair["outside_extension_mm"]
    horizontal = pair["horizontal"]
    vertical = pair["vertical"]
    expected = (
        (horizontal["start_in_view_mm"], center["x_mm"] + extent, center["y_mm"]),
        (horizontal["end_in_view_mm"], center["x_mm"] - extent, center["y_mm"]),
        (vertical["start_in_view_mm"], center["x_mm"], center["y_mm"] + extent),
        (vertical["end_in_view_mm"], center["x_mm"], center["y_mm"] - extent),
    )
    if any(
        not _close(point["x_mm"], x) or not _close(point["y_mm"], y)
        for point, x, y in expected
    ):
        raise NativeDrawingCircleCenterLineStateError(
            "TechDraw returned an inconsistent circle centerline cross."
        )


def normalize_circle_center_line_host_pairs(
    raw: Any,
    *,
    created: bool,
) -> list[dict[str, Any]]:
    """Validate and normalize the complete compiled-host response."""

    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise NativeDrawingCircleCenterLineStateError(
            "TechDraw returned malformed circle centerline data."
        )
    if not 1 <= len(raw) <= MAX_DRAWING_CIRCLE_CENTER_LINE_TARGETS:
        raise NativeDrawingCircleCenterLineStateError(
            "TechDraw returned an unsupported circle centerline count."
        )
    fields = frozenset(
        {
            "source_subelement",
            "geometry_configuration",
            "center_in_view_mm",
            "radius_mm",
            "outside_extension_mm",
            "horizontal",
            "vertical",
            "line_format",
        }
    )
    result = []
    for raw_pair in raw:
        if not isinstance(raw_pair, Mapping) or frozenset(raw_pair) != fields:
            raise NativeDrawingCircleCenterLineStateError(
                "TechDraw returned a malformed circle centerline pair."
            )
        source = str(raw_pair["source_subelement"] or "")
        configuration = str(raw_pair["geometry_configuration"] or "")
        if _EDGE.fullmatch(source) is None or configuration not in _CONFIGURATIONS:
            raise NativeDrawingCircleCenterLineStateError(
                "TechDraw returned an invalid circle centerline source."
            )
        pair = {
            "source_subelement": source,
            "geometry_configuration": configuration,
            "center_in_view_mm": _point(
                raw_pair["center_in_view_mm"], "source center"
            ),
            "radius_mm": _number(
                raw_pair["radius_mm"],
                "source radius",
                minimum=0.000000001,
            ),
            "outside_extension_mm": _number(
                raw_pair["outside_extension_mm"],
                "outside extension",
                minimum=0.0,
                maximum=1_000_000.0,
            ),
            "horizontal": _segment(
                raw_pair["horizontal"], "horizontal", created=created
            ),
            "vertical": _segment(
                raw_pair["vertical"], "vertical", created=created
            ),
            "line_format": _format(raw_pair["line_format"]),
        }
        _require_cross_geometry(pair)
        result.append(pair)
    sources = [pair["source_subelement"] for pair in result]
    if len(sources) != len(set(sources)):
        raise NativeDrawingCircleCenterLineStateError(
            "TechDraw returned duplicate circle centerline sources."
        )
    if created:
        tags = [
            segment["tag"]
            for pair in result
            for segment in (pair["horizontal"], pair["vertical"])
        ]
        if len(tags) != len(set(tags)):
            raise NativeDrawingCircleCenterLineStateError(
                "TechDraw returned duplicate circle centerline tags."
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
    if attribute is None or length is None:
        raise NativeDrawingCircleCenterLineStateError(
            "A created circle centerline tag is not in the persistent line inventory."
        )
    if (
        attribute["kind"] != "cosmetic_edge"
        or length["kind"] != "cosmetic_edge"
        or attribute["subelement"] != length["subelement"]
        or attribute["format"] != expected_format
        or not _points_close(
            length["start_in_view_mm"],
            expected_segment["start_in_view_mm"],
        )
        or not _points_close(
            length["end_in_view_mm"],
            expected_segment["end_in_view_mm"],
        )
    ):
        raise NativeDrawingCircleCenterLineStateError(
            "A created circle centerline does not match the exact host plan."
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


def drawing_circle_center_line_result_state(
    view: Any,
    created_pairs: Sequence[Mapping[str, Any]],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve created tags to their exact persisted format and geometry."""

    if len(created_pairs) != len(source_elements):
        raise NativeDrawingCircleCenterLineStateError(
            "Circle centerline source and result counts differ."
        )
    attributes = drawing_line_attribute_inventory_state(view)
    lengths = drawing_line_length_inventory_state(view)
    attribute_by_tag = {
        line["tag"]: line for line in attributes["lines"] if "tag" in line
    }
    length_by_tag = {line["tag"]: line for line in lengths["lines"]}
    result_pairs = []
    for pair, source in zip(created_pairs, source_elements, strict=True):
        if (
            pair["source_subelement"] != source["name"]
            or source["element_type"] != "edge"
        ):
            raise NativeDrawingCircleCenterLineStateError(
                "A created circle centerline source does not match preflight."
            )
        result_pairs.append(
            {
                "source": {
                    "subelement": source["name"],
                    "element_state_sha256": source["element_state_sha256"],
                    "geometry_type": source["geometry_type"],
                    "geometry_configuration": pair[
                        "geometry_configuration"
                    ],
                    "center_in_view_mm": pair["center_in_view_mm"],
                    "radius_mm": pair["radius_mm"],
                },
                "horizontal": _compact_line(
                    pair["horizontal"]["tag"],
                    attribute_by_tag=attribute_by_tag,
                    length_by_tag=length_by_tag,
                    expected_segment=pair["horizontal"],
                    expected_format=pair["line_format"],
                ),
                "vertical": _compact_line(
                    pair["vertical"]["tag"],
                    attribute_by_tag=attribute_by_tag,
                    length_by_tag=length_by_tag,
                    expected_segment=pair["vertical"],
                    expected_format=pair["line_format"],
                ),
            }
        )
    line_formats = [pair["line_format"] for pair in created_pairs]
    if any(line_format != line_formats[0] for line_format in line_formats[1:]):
        raise NativeDrawingCircleCenterLineStateError(
            "Circle centerline pairs did not retain one host format."
        )
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "pair_count": len(result_pairs),
        "created_line_count": len(result_pairs) * 2,
        "line_format": line_formats[0],
        "pairs": result_pairs,
        "line_attribute_inventory_state_sha256": attributes[
            "inventory_state_sha256"
        ],
        "line_length_inventory_state_sha256": lengths[
            "inventory_state_sha256"
        ],
    }
