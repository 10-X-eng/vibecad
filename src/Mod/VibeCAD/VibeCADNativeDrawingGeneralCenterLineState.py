# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled plans and durable state for general Drawing centerlines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingViewState import is_part_drawing_view


MAX_DRAWING_GENERAL_CENTER_LINES = 4096
_KINDS = frozenset({"face", "between_edges", "between_vertices"})
_MODES = frozenset({"vertical", "horizontal", "aligned"})
_SOURCE_PATTERNS = {
    "face": re.compile(r"^Face(?:0|[1-9][0-9]*)$"),
    "between_edges": re.compile(r"^Edge(?:0|[1-9][0-9]*)$"),
    "between_vertices": re.compile(r"^Vertex(?:0|[1-9][0-9]*)$"),
}
_EDGE = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")
_TAG = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MAXIMUM = 1_000_000_000.0


class NativeDrawingGeneralCenterLineStateError(RuntimeError):
    """General-centerline host or durable state is malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _number(
    value: Any,
    noun: str,
    *,
    minimum: float = -_MAXIMUM,
    maximum: float = _MAXIMUM,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingGeneralCenterLineStateError(
            f"Drawing centerline {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingGeneralCenterLineStateError(
            f"Drawing centerline {noun} is outside the supported range."
        )
    return round(result, 12)


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"x_mm", "y_mm"}
    ):
        raise NativeDrawingGeneralCenterLineStateError(
            f"Drawing centerline {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x_mm"], f"{noun} X coordinate"),
        "y_mm": _number(value["y_mm"], f"{noun} Y coordinate"),
    }


def _format(value: Any) -> dict[str, Any]:
    fields = frozenset(
        {"line_number", "style_code", "width_mm", "color_rgb", "visible"}
    )
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise NativeDrawingGeneralCenterLineStateError(
            "Drawing centerline format is malformed."
        )
    color = value["color_rgb"]
    if not isinstance(color, Mapping) or frozenset(color) != frozenset(
        {"red", "green", "blue"}
    ):
        raise NativeDrawingGeneralCenterLineStateError(
            "Drawing centerline color is malformed."
        )
    if type(value["visible"]) is not bool:
        raise NativeDrawingGeneralCenterLineStateError(
            "Drawing centerline visibility is not boolean."
        )
    integers = {}
    for field in ("line_number", "style_code"):
        raw = value[field]
        if type(raw) is not int or not 0 <= raw <= 2_147_483_647:
            raise NativeDrawingGeneralCenterLineStateError(
                f"Drawing centerline {field.replace('_', ' ')} is invalid."
            )
        integers[field] = raw
    return {
        **integers,
        "width_mm": _number(value["width_mm"], "width", minimum=0.0, maximum=1000.0),
        "color_rgb": {
            channel: _number(
                color[channel], channel, minimum=0.0, maximum=1.0
            )
            for channel in ("red", "green", "blue")
        },
        "visible": value["visible"],
    }


def _tag(value: Any) -> str:
    result = str(value or "")
    if _TAG.fullmatch(result) is None:
        raise NativeDrawingGeneralCenterLineStateError(
            "Drawing centerline tag is invalid."
        )
    return result


def normalize_general_center_line_host_plan(
    raw: Any,
    *,
    created: bool,
    persistent: bool = False,
) -> dict[str, Any]:
    """Validate one compiled preflight, creation result, or durable state."""

    fields = {"kind", "source_subelements", "settings", "line"}
    if created:
        fields.add("centerline_tag")
    if persistent:
        fields.add("subelement")
    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(fields):
        raise NativeDrawingGeneralCenterLineStateError(
            "TechDraw returned malformed general-centerline data."
        )
    kind = str(raw["kind"] or "")
    if kind not in _KINDS:
        raise NativeDrawingGeneralCenterLineStateError(
            "TechDraw returned an invalid centerline kind."
        )
    source_raw = raw["source_subelements"]
    expected_count = None if kind == "face" else 2
    if (
        not isinstance(source_raw, Sequence)
        or isinstance(source_raw, (str, bytes))
        or not source_raw
        or len(source_raw) > 64
        or (expected_count is not None and len(source_raw) != expected_count)
    ):
        raise NativeDrawingGeneralCenterLineStateError(
            "TechDraw returned an invalid centerline source list."
        )
    sources = [str(item or "") for item in source_raw]
    pattern = _SOURCE_PATTERNS[kind]
    if any(pattern.fullmatch(item) is None for item in sources) or len(
        sources
    ) != len(set(sources)):
        raise NativeDrawingGeneralCenterLineStateError(
            "TechDraw returned invalid or duplicate centerline sources."
        )
    settings_raw = raw["settings"]
    settings_fields = frozenset(
        {
            "mode",
            "horizontal_shift_mm",
            "vertical_shift_mm",
            "rotation_degrees",
            "extension_mm",
            "flip",
            "line_format",
        }
    )
    if not isinstance(settings_raw, Mapping) or frozenset(settings_raw) != settings_fields:
        raise NativeDrawingGeneralCenterLineStateError(
            "TechDraw returned malformed centerline settings."
        )
    mode = str(settings_raw["mode"] or "")
    if mode not in _MODES or type(settings_raw["flip"]) is not bool:
        raise NativeDrawingGeneralCenterLineStateError(
            "TechDraw returned invalid centerline mode or flip state."
        )
    settings = {
        "mode": mode,
        "horizontal_shift_mm": _number(
            settings_raw["horizontal_shift_mm"], "horizontal shift"
        ),
        "vertical_shift_mm": _number(
            settings_raw["vertical_shift_mm"], "vertical shift"
        ),
        "rotation_degrees": _number(
            settings_raw["rotation_degrees"], "rotation"
        ),
        "extension_mm": _number(
            settings_raw["extension_mm"], "extension", minimum=0.0
        ),
        "flip": settings_raw["flip"],
        "line_format": _format(settings_raw["line_format"]),
    }
    line_raw = raw["line"]
    if not isinstance(line_raw, Mapping) or frozenset(line_raw) != frozenset(
        {"start_in_view_mm", "end_in_view_mm", "length_mm"}
    ):
        raise NativeDrawingGeneralCenterLineStateError(
            "TechDraw returned malformed centerline geometry."
        )
    start = _point(line_raw["start_in_view_mm"], "start point")
    end = _point(line_raw["end_in_view_mm"], "end point")
    length = _number(line_raw["length_mm"], "length", minimum=1.0e-9)
    if not math.isclose(
        length,
        math.hypot(end["x_mm"] - start["x_mm"], end["y_mm"] - start["y_mm"]),
        rel_tol=1.0e-10,
        abs_tol=1.0e-8,
    ):
        raise NativeDrawingGeneralCenterLineStateError(
            "TechDraw returned inconsistent centerline length."
        )
    result = {
        "kind": kind,
        "source_subelements": sources,
        "settings": settings,
        "line": {
            "start_in_view_mm": start,
            "end_in_view_mm": end,
            "length_mm": length,
        },
    }
    if created:
        result["centerline_tag"] = _tag(raw["centerline_tag"])
    if persistent:
        subelement = str(raw["subelement"] or "")
        if _EDGE.fullmatch(subelement) is None:
            raise NativeDrawingGeneralCenterLineStateError(
                "A persistent centerline has no exact EdgeN identity."
            )
        result["subelement"] = subelement
    return result


def drawing_general_center_line_inventory_state(view: Any) -> dict[str, Any]:
    """Return every durable face/edge/vertex centerline in property-list order."""

    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    import TechDrawGui

    raw = TechDrawGui.drawingGeneralCenterLines(view)
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_DRAWING_GENERAL_CENTER_LINES:
        raise NativeDrawingGeneralCenterLineStateError(
            "The Drawing centerline inventory exceeds 4096 targets."
        )
    lines = [
        normalize_general_center_line_host_plan(
            item, created=True, persistent=True
        )
        for item in raw
    ]
    tags = [item["centerline_tag"] for item in lines]
    subelements = [item["subelement"] for item in lines]
    if len(tags) != len(set(tags)) or len(subelements) != len(set(subelements)):
        raise NativeDrawingGeneralCenterLineStateError(
            "The Drawing centerline inventory contains duplicate identities."
        )
    exact = {"view_object_name": str(view.Name), "centerlines": lines}
    return {
        **exact,
        "centerline_count": len(lines),
        "inventory_state_sha256": _digest(exact),
        "valid": True,
        "issues": [],
    }


def drawing_general_center_line_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve one created durable centerline and exact source associations."""

    if [item["name"] for item in source_elements] != created_plan[
        "source_subelements"
    ]:
        raise NativeDrawingGeneralCenterLineStateError(
            "Centerline sources do not match the exact projected targets."
        )
    inventory = drawing_general_center_line_inventory_state(view)
    persistent = next(
        (
            item
            for item in inventory["centerlines"]
            if item["centerline_tag"] == created_plan["centerline_tag"]
        ),
        None,
    )
    if persistent is None:
        raise NativeDrawingGeneralCenterLineStateError(
            "The durable centerline tag is absent from the Drawing view."
        )
    durable_plan = dict(persistent)
    if durable_plan != created_plan:
        differing_fields = sorted(
            key
            for key in set(durable_plan) | set(created_plan)
            if durable_plan.get(key) != created_plan.get(key)
        )
        raise NativeDrawingGeneralCenterLineStateError(
            "The durable centerline differs from the exact host creation plan in: "
            + ", ".join(differing_fields)
            + "."
        )
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "kind": created_plan["kind"],
        "sources": [
            {
                "subelement": source["name"],
                "element_state_sha256": source["element_state_sha256"],
                "element_type": source["element_type"],
            }
            for source in source_elements
        ],
        "centerline": dict(persistent),
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }
