# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact host plans and durable state for Drawing cosmetic vertices."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingViewState import is_part_drawing_view


MAX_DRAWING_INTERSECTION_VERTICES = 64
MAX_DRAWING_MIDPOINT_VERTICES = 64
MAX_DRAWING_QUADRANT_SOURCES = 64
MAX_DRAWING_COSMETIC_VERTICES = 4096
MAX_DRAWING_VERTEX_OFFSET_MM = 1_000_000_000.0
_EDGE = re.compile(r"^Edge(?:0|[1-9][0-9]*)$")
_VERTEX = re.compile(r"^Vertex(?:0|[1-9][0-9]*)$")
_TAG = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class NativeDrawingCosmeticVertexStateError(RuntimeError):
    """Cosmetic-vertex host or persistent state is malformed."""


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
    minimum: float = -MAX_DRAWING_VERTEX_OFFSET_MM,
    maximum: float = MAX_DRAWING_VERTEX_OFFSET_MM,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingCosmeticVertexStateError(
            f"Drawing cosmetic vertex {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingCosmeticVertexStateError(
            f"Drawing cosmetic vertex {noun} is outside the supported range."
        )
    return round(result, 12)


def _integer(value: Any, noun: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise NativeDrawingCosmeticVertexStateError(
            f"Drawing cosmetic vertex {noun} is invalid."
        )
    return value


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"x_mm", "y_mm"}
    ):
        raise NativeDrawingCosmeticVertexStateError(
            f"Drawing cosmetic vertex {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x_mm"], f"{noun} X coordinate"),
        "y_mm": _number(value["y_mm"], f"{noun} Y coordinate"),
    }


def normalize_drawing_vertex_offset(value: Any) -> dict[str, float]:
    """Validate one explicit unscaled Drawing-view X/Y offset."""

    return _point(value, "offset")


def normalize_drawing_vertex_point(value: Any) -> dict[str, float]:
    """Validate one explicit unscaled, unrotated Drawing-view X/Y point."""

    return _point(value, "point")


def _color(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"red", "green", "blue"}
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "Drawing cosmetic vertex color is malformed."
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
    fields = frozenset({"color_rgb", "size_mm", "style_code", "visible"})
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise NativeDrawingCosmeticVertexStateError(
            "Drawing cosmetic vertex format is malformed."
        )
    if type(value["visible"]) is not bool:
        raise NativeDrawingCosmeticVertexStateError(
            "Drawing cosmetic vertex visibility is not boolean."
        )
    result = {
        "color_rgb": _color(value["color_rgb"]),
        "size_mm": _number(value["size_mm"], "size", minimum=0.0),
        "style_code": _integer(value["style_code"], "style code"),
        "visible": value["visible"],
    }
    if (
        result["color_rgb"] != {"red": 0.0, "green": 0.0, "blue": 0.0}
        or not math.isclose(result["size_mm"], 1.0)
        or result["style_code"] != 1
        or not result["visible"]
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "Drawing cosmetic vertices must use the host default persistent format."
        )
    return result


def _tag(value: Any) -> str:
    result = str(value or "")
    if _TAG.fullmatch(result) is None:
        raise NativeDrawingCosmeticVertexStateError(
            "Drawing cosmetic vertex tag is invalid."
        )
    return result


def _point_plan(value: Any, *, created: bool) -> dict[str, Any]:
    fields = {"point_in_view_mm", "vertex_format"}
    if created:
        fields.add("tag")
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(fields):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned a malformed cosmetic-vertex point."
        )
    result: dict[str, Any] = {
        "point_in_view_mm": _point(value["point_in_view_mm"], "point"),
        "vertex_format": _format(value["vertex_format"]),
    }
    if created:
        result["tag"] = _tag(value["tag"])
    return result


def normalize_explicit_vertex_host_plan(
    raw: Any,
    *,
    created: bool,
) -> dict[str, Any]:
    """Validate one compiled explicit-point vertex plan or result."""

    return _point_plan(raw, created=created)


def normalize_vertex_intersection_host_plan(
    raw: Any,
    *,
    created: bool,
) -> dict[str, Any]:
    """Validate every point returned by the compiled intersection primitive."""

    fields = frozenset({"source_subelements", "vertices"})
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned malformed intersection-vertex data."
        )
    sources_raw = raw["source_subelements"]
    if (
        not isinstance(sources_raw, Sequence)
        or isinstance(sources_raw, (str, bytes))
        or len(sources_raw) != 2
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned an invalid intersection source list."
        )
    sources = tuple(str(item or "") for item in sources_raw)
    if any(_EDGE.fullmatch(item) is None for item in sources) or len(set(sources)) != 2:
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned invalid or duplicate intersection sources."
        )
    vertices_raw = raw["vertices"]
    if (
        not isinstance(vertices_raw, Sequence)
        or isinstance(vertices_raw, (str, bytes))
        or not 1 <= len(vertices_raw) <= MAX_DRAWING_INTERSECTION_VERTICES
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned an unsupported intersection count."
        )
    vertices = [_point_plan(item, created=created) for item in vertices_raw]
    if created:
        tags = [item["tag"] for item in vertices]
        if len(tags) != len(set(tags)):
            raise NativeDrawingCosmeticVertexStateError(
                "TechDraw returned duplicate intersection-vertex tags."
            )
    return {"source_subelements": list(sources), "vertices": vertices}


def normalize_midpoint_vertex_host_plan(
    raw: Any,
    *,
    created: bool,
) -> dict[str, Any]:
    """Validate compiled midpoint plans and their source-to-vertex association."""

    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset({"midpoints"}):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned malformed midpoint-vertex data."
        )
    raw_midpoints = raw["midpoints"]
    if (
        not isinstance(raw_midpoints, Sequence)
        or isinstance(raw_midpoints, (str, bytes))
        or not 1 <= len(raw_midpoints) <= MAX_DRAWING_MIDPOINT_VERTICES
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned an unsupported midpoint count."
        )
    midpoints = []
    for raw_midpoint in raw_midpoints:
        if not isinstance(raw_midpoint, Mapping) or frozenset(
            raw_midpoint
        ) != frozenset({"source_subelement", "vertex"}):
            raise NativeDrawingCosmeticVertexStateError(
                "TechDraw returned a malformed midpoint association."
            )
        source = str(raw_midpoint["source_subelement"] or "")
        if _EDGE.fullmatch(source) is None:
            raise NativeDrawingCosmeticVertexStateError(
                "TechDraw returned an invalid midpoint source."
            )
        midpoints.append(
            {
                "source_subelement": source,
                "vertex": _point_plan(raw_midpoint["vertex"], created=created),
            }
        )
    sources = [item["source_subelement"] for item in midpoints]
    if len(sources) != len(set(sources)):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned duplicate midpoint sources."
        )
    if created:
        tags = [item["vertex"]["tag"] for item in midpoints]
        if len(tags) != len(set(tags)):
            raise NativeDrawingCosmeticVertexStateError(
                "TechDraw returned duplicate midpoint-vertex tags."
            )
    return {"midpoints": midpoints}


def normalize_quadrant_vertex_host_plan(
    raw: Any,
    *,
    created: bool,
) -> dict[str, Any]:
    """Validate ordered quarter-parameter vertices and their source edges."""

    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset({"sources"}):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned malformed quadrant-vertex data."
        )
    raw_sources = raw["sources"]
    if (
        not isinstance(raw_sources, Sequence)
        or isinstance(raw_sources, (str, bytes))
        or not 1 <= len(raw_sources) <= MAX_DRAWING_QUADRANT_SOURCES
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned an unsupported quadrant source count."
        )
    sources = []
    for raw_source in raw_sources:
        if not isinstance(raw_source, Mapping) or frozenset(
            raw_source
        ) != frozenset({"source_subelement", "vertices"}):
            raise NativeDrawingCosmeticVertexStateError(
                "TechDraw returned a malformed quadrant source association."
            )
        source = str(raw_source["source_subelement"] or "")
        if _EDGE.fullmatch(source) is None:
            raise NativeDrawingCosmeticVertexStateError(
                "TechDraw returned an invalid quadrant source."
            )
        raw_vertices = raw_source["vertices"]
        if (
            not isinstance(raw_vertices, Sequence)
            or isinstance(raw_vertices, (str, bytes))
            or len(raw_vertices) != 3
        ):
            raise NativeDrawingCosmeticVertexStateError(
                "Each quadrant source must have exactly three ordered vertices."
            )
        sources.append(
            {
                "source_subelement": source,
                "vertices": [
                    _point_plan(item, created=created) for item in raw_vertices
                ],
            }
        )
    source_names = [item["source_subelement"] for item in sources]
    if len(source_names) != len(set(source_names)):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned duplicate quadrant sources."
        )
    if created:
        tags = [
            vertex["tag"]
            for source in sources
            for vertex in source["vertices"]
        ]
        if len(tags) != len(set(tags)):
            raise NativeDrawingCosmeticVertexStateError(
                "TechDraw returned duplicate quadrant-vertex tags."
            )
    return {"sources": sources}


def _points_close(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return math.isclose(
        left["x_mm"], right["x_mm"], rel_tol=1.0e-10, abs_tol=1.0e-8
    ) and math.isclose(left["y_mm"], right["y_mm"], rel_tol=1.0e-10, abs_tol=1.0e-8)


def normalize_offset_vertex_host_plan(raw: Any, *, created: bool) -> dict[str, Any]:
    """Validate one compiled exact offset-vertex plan or result."""

    fields = frozenset(
        {"source_subelement", "source_point_in_view_mm", "offset_mm", "vertex"}
    )
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned malformed offset-vertex data."
        )
    source = str(raw["source_subelement"] or "")
    if _VERTEX.fullmatch(source) is None:
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned an invalid offset-vertex source."
        )
    source_point = _point(raw["source_point_in_view_mm"], "source point")
    offset = normalize_drawing_vertex_offset(raw["offset_mm"])
    vertex = _point_plan(raw["vertex"], created=created)
    expected = {
        "x_mm": source_point["x_mm"] + offset["x_mm"],
        "y_mm": source_point["y_mm"] + offset["y_mm"],
    }
    if not _points_close(vertex["point_in_view_mm"], expected):
        raise NativeDrawingCosmeticVertexStateError(
            "TechDraw returned an inconsistent offset-vertex point."
        )
    return {
        "source_subelement": source,
        "source_point_in_view_mm": source_point,
        "offset_mm": offset,
        "vertex": vertex,
    }


def _persistent_vertex(raw: Any) -> dict[str, Any]:
    fields = frozenset({"tag", "subelement", "point_in_view_mm", "vertex_format"})
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingCosmeticVertexStateError(
            "Persistent Drawing cosmetic-vertex state is malformed."
        )
    subelement = str(raw["subelement"] or "")
    if _VERTEX.fullmatch(subelement) is None:
        raise NativeDrawingCosmeticVertexStateError(
            "A persistent cosmetic vertex has no current VertexN selection name."
        )
    exact = {
        "tag": _tag(raw["tag"]),
        "subelement": subelement,
        "point_in_view_mm": _point(raw["point_in_view_mm"], "persistent point"),
        "vertex_format": _format(raw["vertex_format"]),
    }
    return {**exact, "vertex_state_sha256": _digest(exact)}


def drawing_cosmetic_vertex_inventory_state(view: Any) -> dict[str, Any]:
    """Return every persistent cosmetic vertex in durable property-list order."""

    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    import TechDrawGui

    raw = TechDrawGui.drawingCosmeticVertices(view)
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_DRAWING_COSMETIC_VERTICES:
        raise NativeDrawingCosmeticVertexStateError(
            "The Drawing cosmetic-vertex inventory exceeds 4096 targets."
        )
    vertices = [_persistent_vertex(item) for item in raw]
    tags = [item["tag"] for item in vertices]
    subelements = [item["subelement"] for item in vertices]
    if len(tags) != len(set(tags)) or len(subelements) != len(set(subelements)):
        raise NativeDrawingCosmeticVertexStateError(
            "The Drawing cosmetic-vertex inventory contains duplicate identities."
        )
    exact = {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "vertices": vertices,
    }
    return {
        **exact,
        "vertex_count": len(vertices),
        "inventory_state_sha256": _digest(exact),
        "valid": True,
        "issues": [],
    }


def _created_vertex(
    persistent_by_tag: Mapping[str, Mapping[str, Any]],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    persistent = persistent_by_tag.get(plan["tag"])
    if (
        persistent is None
        or persistent["vertex_format"] != plan["vertex_format"]
        or not _points_close(persistent["point_in_view_mm"], plan["point_in_view_mm"])
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "A created cosmetic vertex does not match the exact host plan."
        )
    return dict(persistent)


def drawing_intersection_vertex_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve exact created intersection tags to persistent state."""

    if (
        len(source_elements) != 2
        or [item["name"] for item in source_elements]
        != created_plan["source_subelements"]
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "Intersection sources do not match the exact projected targets."
        )
    inventory = drawing_cosmetic_vertex_inventory_state(view)
    persistent_by_tag = {item["tag"]: item for item in inventory["vertices"]}
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "sources": [
            {
                "subelement": source["name"],
                "element_state_sha256": source["element_state_sha256"],
                "geometry_type": source["geometry_type"],
            }
            for source in source_elements
        ],
        "created_vertex_count": len(created_plan["vertices"]),
        "vertices": [
            _created_vertex(persistent_by_tag, item)
            for item in created_plan["vertices"]
        ],
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }


def drawing_offset_vertex_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_element: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one exact created offset-vertex tag to persistent state."""

    if source_element["name"] != created_plan["source_subelement"]:
        raise NativeDrawingCosmeticVertexStateError(
            "The offset source does not match the exact projected target."
        )
    inventory = drawing_cosmetic_vertex_inventory_state(view)
    persistent_by_tag = {item["tag"]: item for item in inventory["vertices"]}
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "source": {
            "subelement": source_element["name"],
            "element_state_sha256": source_element["element_state_sha256"],
            "element_type": source_element["element_type"],
            "point_in_view_mm": created_plan["source_point_in_view_mm"],
        },
        "offset_mm": created_plan["offset_mm"],
        "vertex": _created_vertex(persistent_by_tag, created_plan["vertex"]),
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }


def drawing_explicit_vertex_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one explicit-point creation result to exact persistent state."""

    inventory = drawing_cosmetic_vertex_inventory_state(view)
    persistent_by_tag = {item["tag"]: item for item in inventory["vertices"]}
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "vertex": _created_vertex(persistent_by_tag, created_plan),
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }


def drawing_midpoint_vertex_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve each exact midpoint source and created tag to persistent state."""

    midpoints = created_plan["midpoints"]
    if (
        len(source_elements) != len(midpoints)
        or [item["name"] for item in source_elements]
        != [item["source_subelement"] for item in midpoints]
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "Midpoint sources do not match the exact projected targets."
        )
    inventory = drawing_cosmetic_vertex_inventory_state(view)
    persistent_by_tag = {item["tag"]: item for item in inventory["vertices"]}
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "midpoint_count": len(midpoints),
        "midpoints": [
            {
                "source": {
                    "subelement": source["name"],
                    "element_state_sha256": source["element_state_sha256"],
                    "geometry_type": source["geometry_type"],
                },
                "vertex": _created_vertex(
                    persistent_by_tag,
                    midpoint["vertex"],
                ),
            }
            for source, midpoint in zip(source_elements, midpoints, strict=True)
        ],
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }


def drawing_quadrant_vertex_result_state(
    view: Any,
    created_plan: Mapping[str, Any],
    source_elements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve each exact quadrant source and its three persistent vertices."""

    sources = created_plan["sources"]
    if (
        len(source_elements) != len(sources)
        or [item["name"] for item in source_elements]
        != [item["source_subelement"] for item in sources]
    ):
        raise NativeDrawingCosmeticVertexStateError(
            "Quadrant sources do not match the exact projected targets."
        )
    inventory = drawing_cosmetic_vertex_inventory_state(view)
    persistent_by_tag = {item["tag"]: item for item in inventory["vertices"]}
    return {
        "view_object_name": str(view.Name),
        "coordinate_space": "drawing_view_unscaled_mm",
        "axis_convention": "x_right_y_up",
        "source_count": len(sources),
        "created_vertex_count": len(sources) * 3,
        "sources": [
            {
                "source": {
                    "subelement": source_element["name"],
                    "element_state_sha256": source_element[
                        "element_state_sha256"
                    ],
                    "geometry_type": source_element["geometry_type"],
                },
                "vertices": [
                    _created_vertex(persistent_by_tag, vertex)
                    for vertex in source_plan["vertices"]
                ],
            }
            for source_element, source_plan in zip(
                source_elements, sources, strict=True
            )
        ],
        "inventory_state_sha256": inventory["inventory_state_sha256"],
    }
