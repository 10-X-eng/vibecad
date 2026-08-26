# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded projected-element state for Native Drawing."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from VibeCADNativeDrawingViewState import drawing_view_state, is_part_drawing_view


MAX_DRAWING_PROJECTED_ELEMENTS = 4096
MAX_DRAWING_PROJECTED_PAGE_SIZE = 48
MAX_DRAWING_SOURCE_CANDIDATES = 32
MAX_SELECTED_DRAWING_PROJECTED_ELEMENTS = 64
_PROJECTED_NAME = re.compile(r"^(Edge|Vertex|Face)(0|[1-9][0-9]*)$")
_SOURCE_SUBELEMENT = re.compile(r"^(Edge|Vertex|Face)[1-9][0-9]*$")
_SOURCE_STATUS = frozenset(
    {
        "exact",
        "ambiguous",
        "generated_projection",
        "generated_center",
        "unmapped",
    }
)


class NativeDrawingGeometryStateError(RuntimeError):
    """Projected Drawing geometry is unavailable or malformed."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_DRAWING_GEOMETRY_STATE_INVALID",
            "message": str(self),
        }


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _number(value: Any, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingGeometryStateError(
            f"Projected Drawing {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or abs(result) > 1_000_000_000.0:
        raise NativeDrawingGeometryStateError(
            f"Projected Drawing {noun} is outside the supported range."
        )
    return round(result, 12)


def _nonnegative_number(value: Any, noun: str) -> float:
    result = _number(value, noun)
    if result < 0.0:
        raise NativeDrawingGeometryStateError(
            f"Projected Drawing {noun} cannot be negative."
        )
    return result


def _integer(
    value: Any,
    noun: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int:
        raise NativeDrawingGeometryStateError(
            f"Projected Drawing {noun} is not an integer."
        )
    result = value
    if not minimum <= result <= maximum:
        raise NativeDrawingGeometryStateError(
            f"Projected Drawing {noun} is outside the supported range."
        )
    return result


def _boolean(value: Any, noun: str) -> bool:
    if type(value) is not bool:
        raise NativeDrawingGeometryStateError(
            f"Projected Drawing {noun} is not a boolean."
        )
    return value


def _point(value: Any, noun: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y"}:
        raise NativeDrawingGeometryStateError(
            f"Projected Drawing {noun} is malformed."
        )
    return {
        "x_mm": _number(value["x"], f"{noun} X coordinate"),
        "y_mm": _number(value["y"], f"{noun} Y coordinate"),
    }


def _bounds(value: Any) -> dict[str, float]:
    names = ("min_x", "min_y", "max_x", "max_y", "width", "height")
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise NativeDrawingGeometryStateError(
            "Projected Drawing element bounds are malformed."
        )
    result = {
        f"{name}_mm": _number(value[name], f"bounds {name}")
        for name in names
    }
    if (
        result["max_x_mm"] < result["min_x_mm"]
        or result["max_y_mm"] < result["min_y_mm"]
        or result["width_mm"] < 0.0
        or result["height_mm"] < 0.0
        or not math.isclose(
            result["width_mm"],
            result["max_x_mm"] - result["min_x_mm"],
            rel_tol=1.0e-10,
            abs_tol=1.0e-9,
        )
        or not math.isclose(
            result["height_mm"],
            result["max_y_mm"] - result["min_y_mm"],
            rel_tol=1.0e-10,
            abs_tol=1.0e-9,
        )
    ):
        raise NativeDrawingGeometryStateError(
            "Projected Drawing element bounds are inconsistent."
        )
    return result


def _view_bounds(elements: list[Mapping[str, Any]]) -> dict[str, float]:
    minimum_x = math.inf
    minimum_y = math.inf
    maximum_x = -math.inf
    maximum_y = -math.inf
    for item in elements:
        bounds = item.get("bounds_in_view_mm")
        if isinstance(bounds, Mapping):
            minimum_x = min(minimum_x, float(bounds["min_x_mm"]))
            minimum_y = min(minimum_y, float(bounds["min_y_mm"]))
            maximum_x = max(maximum_x, float(bounds["max_x_mm"]))
            maximum_y = max(maximum_y, float(bounds["max_y_mm"]))
            continue
        point = item.get("point_in_view_mm")
        if isinstance(point, Mapping):
            x_mm = float(point["x_mm"])
            y_mm = float(point["y_mm"])
            minimum_x = min(minimum_x, x_mm)
            minimum_y = min(minimum_y, y_mm)
            maximum_x = max(maximum_x, x_mm)
            maximum_y = max(maximum_y, y_mm)
    if not all(math.isfinite(value) for value in (
        minimum_x,
        minimum_y,
        maximum_x,
        maximum_y,
    )):
        raise NativeDrawingGeometryStateError(
            "The Drawing view has no bounded projected geometry."
        )
    return {
        "min_x_mm": round(minimum_x, 12),
        "min_y_mm": round(minimum_y, 12),
        "max_x_mm": round(maximum_x, 12),
        "max_y_mm": round(maximum_y, 12),
    }


def _source_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeDrawingGeometryStateError(
            "Projected Drawing source mapping is malformed."
        )
    status = str(value.get("status") or "")
    candidates_raw = tuple(value.get("candidates") or ())
    if status not in _SOURCE_STATUS or len(candidates_raw) > MAX_DRAWING_SOURCE_CANDIDATES:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing source mapping is unsupported."
        )
    candidates = []
    for raw in candidates_raw:
        if not isinstance(raw, Mapping) or set(raw) != {
            "object_name",
            "subelement",
        }:
            raise NativeDrawingGeometryStateError(
                "Projected Drawing source candidate is malformed."
            )
        object_name = str(raw["object_name"] or "")
        subelement = str(raw["subelement"] or "")
        if (
            not object_name
            or len(object_name) > 128
            or not _SOURCE_SUBELEMENT.fullmatch(subelement)
        ):
            raise NativeDrawingGeometryStateError(
                "Projected Drawing source candidate is invalid."
            )
        candidates.append(
            {"object_name": object_name, "subelement": subelement}
        )
    if (status == "exact") != (len(candidates) == 1):
        raise NativeDrawingGeometryStateError(
            "Projected Drawing exact source mapping is inconsistent."
        )
    if status == "ambiguous" and len(candidates) < 2:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing ambiguous source mapping is inconsistent."
        )
    if status not in {"exact", "ambiguous"} and candidates:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing unmapped source state cannot contain candidates."
        )
    candidate_keys = {
        (item["object_name"], item["subelement"]) for item in candidates
    }
    if len(candidate_keys) != len(candidates):
        raise NativeDrawingGeometryStateError(
            "Projected Drawing source mapping contains duplicate candidates."
        )
    return {"status": status, "candidates": candidates}


def _identity(raw: Mapping[str, Any], expected_kind: str) -> str:
    if str(raw.get("element_type") or "") != expected_kind:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing element type is inconsistent."
        )
    name = str(raw.get("name") or "")
    match = _PROJECTED_NAME.fullmatch(name)
    if match is None or match.group(1).casefold() != expected_kind:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing element name is invalid."
        )
    return name


def _edge(raw: Any, view: Any | None = None) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise NativeDrawingGeometryStateError("Projected Drawing edge is malformed.")
    name = _identity(raw, "edge")
    geometry_type = str(raw.get("geometry_type") or "")
    edge_class = str(raw.get("edge_class") or "")
    if not geometry_type or len(geometry_type) > 64 or len(edge_class) > 32:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing edge classification is invalid."
        )
    exact: dict[str, Any] = {
        "name": name,
        "element_type": "edge",
        "geometry_type": geometry_type,
        "edge_class": edge_class,
        "visible": _boolean(raw.get("visible"), "edge visibility"),
        "closed": _boolean(raw.get("closed"), "edge closed state"),
        "length_view_mm": _nonnegative_number(
            raw.get("length_view_mm"),
            "edge length",
        ),
        "bounds_in_view_mm": _bounds(raw.get("bounds_2d")),
        "start_in_view_mm": _point(raw.get("start_2d"), "edge start"),
        "end_in_view_mm": _point(raw.get("end_2d"), "edge end"),
        "midpoint_in_view_mm": _point(raw.get("midpoint_2d"), "edge midpoint"),
        "hlr_source_index": _integer(
            raw.get("hlr_source_index"),
            "edge HLR source index",
            minimum=-1,
            maximum=2_147_483_647,
        ),
        "source_mapping": _source_mapping(raw.get("source_mapping")),
    }
    if "center_2d" in raw or "radius_view_mm" in raw:
        if "center_2d" not in raw or "radius_view_mm" not in raw:
            raise NativeDrawingGeometryStateError(
                "Projected Drawing curved-edge data is incomplete."
            )
        exact["center_in_view_mm"] = _point(raw["center_2d"], "edge center")
        exact["radius_view_mm"] = _nonnegative_number(
            raw["radius_view_mm"],
            "edge radius",
        )
    if (
        view is not None
        and not exact["closed"]
        and "line" in geometry_type.casefold()
    ):
        try:
            from TechDrawTools.AxoLengthDimension import axonometric_value_mode

            exact["axonometric_value_mode"] = axonometric_value_mode(view, name)
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            pass
    return {**exact, "element_state_sha256": _digest(exact)}


def _vertex(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise NativeDrawingGeometryStateError("Projected Drawing vertex is malformed.")
    name = _identity(raw, "vertex")
    exact = {
        "name": name,
        "element_type": "vertex",
        "point_in_view_mm": _point(raw.get("point_2d"), "vertex point"),
        "visible": _boolean(raw.get("visible"), "vertex visibility"),
        "is_center": _boolean(raw.get("is_center"), "vertex center state"),
        "is_reference": _boolean(
            raw.get("is_reference"),
            "vertex reference state",
        ),
        "source_mapping": _source_mapping(raw.get("source_mapping")),
    }
    return {**exact, "element_state_sha256": _digest(exact)}


def _face(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise NativeDrawingGeometryStateError("Projected Drawing face is malformed.")
    name = _identity(raw, "face")
    wire_count = _integer(
        raw.get("wire_count"),
        "face wire count",
        minimum=1,
        maximum=MAX_DRAWING_PROJECTED_ELEMENTS,
    )
    exact = {
        "name": name,
        "element_type": "face",
        "visible": _boolean(raw.get("visible"), "face visibility"),
        "area_view_mm2": _nonnegative_number(
            raw.get("area_view_mm2"),
            "face area",
        ),
        "center_in_view_mm": _point(raw.get("center_2d"), "face center"),
        "bounds_in_view_mm": _bounds(raw.get("bounds_2d")),
        "wire_count": wire_count,
    }
    return {**exact, "element_state_sha256": _digest(exact)}


def drawing_projected_geometry_state(view: Any) -> dict[str, Any]:
    """Return the complete exact projected geometry state for one Drawing view."""

    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    reader = getattr(view, "getExactProjectedElementDescriptors", None)
    if not callable(reader):
        raise NativeDrawingGeometryStateError(
            "The Drawing view cannot report projected geometry."
        )
    try:
        raw = reader()
    except Exception as exc:
        raise NativeDrawingGeometryStateError(
            "The Drawing view has no current projected geometry."
        ) from exc
    if not isinstance(raw, Mapping):
        raise NativeDrawingGeometryStateError(
            "The Drawing view returned malformed projected geometry."
        )
    edges_raw = tuple(raw.get("edges") or ())
    vertices_raw = tuple(raw.get("vertices") or ())
    faces_raw = tuple(raw.get("faces") or ())
    total = len(edges_raw) + len(vertices_raw) + len(faces_raw)
    if total > MAX_DRAWING_PROJECTED_ELEMENTS:
        raise NativeDrawingGeometryStateError(
            "Drawing projected geometry exceeds the supported 4096 elements."
        )
    coordinate_space = str(raw.get("coordinate_space") or "")
    axis_convention = str(raw.get("axis_convention") or "")
    if (
        coordinate_space != "view_projection_scaled_centered"
        or axis_convention != "x_right_y_up"
    ):
        raise NativeDrawingGeometryStateError(
            "The Drawing projected coordinate system is unsupported."
        )
    elements = [
        *(_edge(raw_edge, view) for raw_edge in edges_raw),
        *map(_vertex, vertices_raw),
        *map(_face, faces_raw),
    ]
    identities = [item["name"] for item in elements]
    if len(identities) != len(set(identities)):
        raise NativeDrawingGeometryStateError(
            "The Drawing view returned duplicate projected element names."
        )
    view_state = drawing_view_state(view)
    view_scale = _number(raw.get("view_scale"), "view scale")
    if view_scale <= 0.0:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing view scale must be positive."
        )
    exact = {
        "view": {
            "object_name": view_state["object_name"],
            "type_id": view_state["type_id"],
            "view_state_sha256": view_state["state_sha256"],
        },
        "coordinate_space": coordinate_space,
        "axis_convention": axis_convention,
        "view_scale": view_scale,
        "elements": elements,
    }
    return {
        **exact,
        "projection_state_sha256": _digest(exact),
        "edge_count": len(edges_raw),
        "vertex_count": len(vertices_raw),
        "face_count": len(faces_raw),
        "element_count": total,
    }


def drawing_projected_geometry_page(
    view: Any,
    *,
    offset: int,
    page_size: int,
    expected_projection_state_sha256: str | None = None,
) -> dict[str, Any]:
    """Read one exact bounded page from a projected Drawing view."""

    if not 0 <= int(offset) <= MAX_DRAWING_PROJECTED_ELEMENTS:
        raise ValueError("Projected Drawing geometry offset must be 0 through 4096.")
    if not 1 <= int(page_size) <= MAX_DRAWING_PROJECTED_PAGE_SIZE:
        raise ValueError(
            "Projected Drawing geometry page_size must be 1 through "
            f"{MAX_DRAWING_PROJECTED_PAGE_SIZE}."
        )
    state = drawing_projected_geometry_state(view)
    expected = str(expected_projection_state_sha256 or "")
    if int(offset) > 0 and not expected:
        raise ValueError(
            "A continued projected-geometry read requires its prior projection hash."
        )
    if expected and expected != state["projection_state_sha256"]:
        raise NativeDrawingGeometryStateError(
            "The Drawing projection changed between paginated reads."
        )
    elements = state["elements"]
    start = int(offset)
    stop = min(start + int(page_size), len(elements))
    if start > len(elements):
        raise ValueError("Projected Drawing geometry offset exceeds the element count.")
    return {
        "view": {
            **state["view"],
            "projection_state_sha256": state["projection_state_sha256"],
        },
        "coordinate_space": state["coordinate_space"],
        "axis_convention": state["axis_convention"],
        "view_scale": state["view_scale"],
        "view_bounds_in_view_mm": _view_bounds(elements),
        "counts": {
            "edges": state["edge_count"],
            "vertices": state["vertex_count"],
            "faces": state["face_count"],
            "total": state["element_count"],
        },
        "offset": start,
        "returned_count": stop - start,
        "next_offset": stop if stop < len(elements) else None,
        "elements": elements[start:stop],
    }


def _provider_point(value: Mapping[str, Any]) -> dict[str, float]:
    return {
        "x_mm": float(value["x_mm"]),
        "y_mm": float(value["y_mm"]),
    }


def _provider_source(value: Mapping[str, Any], result: dict[str, Any]) -> None:
    status = str(value.get("status") or "")
    candidates = list(value.get("candidates") or ())
    if status == "exact" and len(candidates) == 1:
        result["source"] = dict(candidates[0])
    elif status and status != "unmapped":
        result["source_status"] = status


def _provider_edge(
    value: Mapping[str, Any],
    view_scale: float,
) -> dict[str, Any]:
    geometry_type = str(value["geometry_type"] or "")
    kind = {
        "arcofcircle": "circular_arc",
        "arcofellipse": "elliptical_arc",
        "bsplinecircle": "b_spline_circle",
    }.get(geometry_type.casefold(), geometry_type.casefold())
    result: dict[str, Any] = {
        "name": str(value["name"]),
        "kind": kind,
        "visible": bool(value["visible"]),
        "edge_class": str(value["edge_class"]),
        "start_in_view_mm": _provider_point(value["start_in_view_mm"]),
        "end_in_view_mm": _provider_point(value["end_in_view_mm"]),
        "length_mm": float(value["length_view_mm"]) / view_scale,
    }
    if kind == "line":
        dx = (
            result["end_in_view_mm"]["x_mm"]
            - result["start_in_view_mm"]["x_mm"]
        )
        dy = (
            result["end_in_view_mm"]["y_mm"]
            - result["start_in_view_mm"]["y_mm"]
        )
        tolerance = 1.0e-9
        result["orientation"] = (
            "vertical"
            if abs(dx) <= tolerance and abs(dy) > tolerance
            else "horizontal"
            if abs(dy) <= tolerance and abs(dx) > tolerance
            else "diagonal"
        )
    if "center_in_view_mm" in value:
        radius = float(value["radius_view_mm"]) / view_scale
        result["center_in_view_mm"] = _provider_point(
            value["center_in_view_mm"]
        )
        result["radius_mm"] = radius
        result["diameter_mm"] = radius * 2.0
        result["closed"] = bool(value["closed"])
    value_mode = str(value.get("axonometric_value_mode") or "")
    if value_mode.endswith("_axis_true_length"):
        result["true_length_axis"] = value_mode[0]
    _provider_source(value["source_mapping"], result)
    return result


def _provider_vertex(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": str(value["name"]),
        "kind": "point",
        "visible": bool(value["visible"]),
        "point_in_view_mm": _provider_point(value["point_in_view_mm"]),
    }
    if value["is_center"]:
        result["center"] = True
    if value["is_reference"]:
        result["reference"] = True
    _provider_source(value["source_mapping"], result)
    return result


def _provider_face(
    value: Mapping[str, Any],
    view_scale: float,
) -> dict[str, Any]:
    bounds = value["bounds_in_view_mm"]
    return {
        "name": str(value["name"]),
        "kind": "face",
        "visible": bool(value["visible"]),
        "area_mm2": float(value["area_view_mm2"]) / (view_scale * view_scale),
        "center_in_view_mm": _provider_point(value["center_in_view_mm"]),
        "bounds_in_view_mm": {
            "min_x_mm": float(bounds["min_x_mm"]),
            "min_y_mm": float(bounds["min_y_mm"]),
            "max_x_mm": float(bounds["max_x_mm"]),
            "max_y_mm": float(bounds["max_y_mm"]),
        },
        "wire_count": int(value["wire_count"]),
    }


def _dimension_applicability(
    view: Any,
    elements: list[dict[str, Any]],
) -> None:
    try:
        import TechDrawGui

        raw = TechDrawGui.inspectProjectedDimensionApplicability(
            view,
            [item["name"] for item in elements],
        )
    except Exception as exc:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing dimension applicability is unavailable."
        ) from exc
    allowed = frozenset(
        {
            "aligned",
            "horizontal",
            "vertical",
            "radius",
            "diameter",
            "area",
            "arc_length",
        }
    )
    if not isinstance(raw, Mapping) or set(raw) != {
        item["name"] for item in elements
    }:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing dimension applicability is malformed."
        )
    for item in elements:
        applicability = raw[item["name"]]
        if (
            not isinstance(applicability, Mapping)
            or set(applicability)
            != {"valid_dimensions", "approximate_dimensions"}
        ):
            raise NativeDrawingGeometryStateError(
                "Projected Drawing dimension applicability is malformed."
            )
        valid = [str(value) for value in applicability["valid_dimensions"]]
        approximate = [
            str(value) for value in applicability["approximate_dimensions"]
        ]
        if (
            len(valid) != len(set(valid))
            or len(approximate) != len(set(approximate))
            or any(value not in allowed for value in valid)
            or any(value not in valid for value in approximate)
        ):
            raise NativeDrawingGeometryStateError(
                "Projected Drawing dimension applicability is inconsistent."
            )
        if valid:
            item["valid_dimensions"] = valid
        if approximate:
            item["approximate_dimensions"] = approximate


def provider_projected_geometry_page(
    page: Mapping[str, Any],
    *,
    view: Any | None = None,
) -> dict[str, Any]:
    """Return the compact exact geometry needed to choose Drawing references."""

    view_scale = float(page["view_scale"])
    if not math.isfinite(view_scale) or view_scale <= 0.0:
        raise NativeDrawingGeometryStateError(
            "Projected Drawing view scale must be positive."
        )
    elements = []
    for value in page["elements"]:
        element_type = str(value["element_type"])
        if element_type == "edge":
            elements.append(_provider_edge(value, view_scale))
        elif element_type == "vertex":
            elements.append(_provider_vertex(value))
        elif element_type == "face":
            elements.append(_provider_face(value, view_scale))
        else:
            raise NativeDrawingGeometryStateError(
                "Projected Drawing element type is unsupported."
            )
    if view is not None and elements:
        _dimension_applicability(view, elements)
    return {**dict(page), "elements": elements}


def selected_projected_geometry_state(
    view: Any,
    subelements: tuple[str, ...],
) -> dict[str, Any]:
    """Return concise exact state for one selected view and its selected elements."""

    if (
        not isinstance(subelements, tuple)
        or not 1 <= len(subelements) <= MAX_SELECTED_DRAWING_PROJECTED_ELEMENTS
        or len(set(subelements)) != len(subelements)
        or any(_PROJECTED_NAME.fullmatch(name) is None for name in subelements)
    ):
        raise ValueError(
            "Selected Drawing geometry requires 1 to 64 unique projected element names."
        )
    state = drawing_projected_geometry_state(view)
    by_name = {item["name"]: item for item in state["elements"]}
    selected = []
    for name in subelements:
        if name not in by_name:
            raise NativeDrawingGeometryStateError(
                f"Selected projected element {name!r} is no longer available."
            )
        selected.append(by_name[name])
    return {
        "view": state["view"],
        "projection_state_sha256": state["projection_state_sha256"],
        "coordinate_space": state["coordinate_space"],
        "axis_convention": state["axis_convention"],
        "view_bounds_in_view_mm": _view_bounds(state["elements"]),
        "counts": {
            "edges": state["edge_count"],
            "vertices": state["vertex_count"],
            "faces": state["face_count"],
            "total": state["element_count"],
        },
        "selected_elements": selected,
    }
