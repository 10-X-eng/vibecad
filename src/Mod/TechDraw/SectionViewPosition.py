# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact calculations and mutation for section-view positioning."""

from __future__ import annotations

import math

import FreeCAD as App


MAXIMUM_DRAWING_COORDINATE_MM = 1_000_000_000.0


class SectionViewPositionError(ValueError):
    """The requested section-view alignment is not exact or applicable."""


def alignment_base(base_view):
    """Return the page-position owner used by the shipped human command."""

    if base_view is None:
        return None
    if str(getattr(base_view, "TypeId", "")) != "TechDraw::DrawProjGroupItem":
        return base_view
    parents = [
        obj
        for obj in tuple(getattr(base_view, "InList", ()) or ())
        if str(getattr(obj, "TypeId", "")) == "TechDraw::DrawProjGroup"
    ]
    return parents[0] if len(parents) == 1 else None


def same_drawing(section_view, base_view) -> bool:
    """Return whether both live views resolve to one exact Drawing page."""

    if section_view is None or base_view is None:
        return False
    document = getattr(section_view, "Document", None)
    try:
        document_is_live = (
            document is not None and App.getDocument(document.Name) is document
        )
    except (NameError, ReferenceError, RuntimeError):
        document_is_live = False
    if not document_is_live or getattr(base_view, "Document", None) is not document:
        return False
    section_page = section_view.findParentPage()
    base_page = base_view.findParentPage()
    return section_page is not None and base_page is section_page


def _coordinate(value, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SectionViewPositionError(
            f"Section-view {noun} is not numeric."
        ) from exc
    if (
        not math.isfinite(result)
        or not -MAXIMUM_DRAWING_COORDINATE_MM
        <= result
        <= MAXIMUM_DRAWING_COORDINATE_MM
    ):
        raise SectionViewPositionError(
            f"Section-view {noun} is outside the supported range."
        )
    return result


def _position(view) -> tuple[float, float]:
    return (
        _coordinate(getattr(view, "X"), "X position"),
        _coordinate(getattr(view, "Y"), "Y position"),
    )


def _require_section_view(section_view) -> None:
    if (
        section_view is None
        or str(getattr(section_view, "TypeId", ""))
        != "TechDraw::DrawViewSection"
    ):
        raise SectionViewPositionError(
            "The target must be one standard TechDraw section view."
        )


def calculate_axis_alignment(section_view, axis: str):
    """Calculate the exact page position for base-axis alignment."""

    _require_section_view(section_view)
    base_view = alignment_base(getattr(section_view, "BaseView", None))
    if not same_drawing(section_view, base_view):
        raise SectionViewPositionError(
            "The section view and its alignment base must share one page."
        )
    if axis not in {"horizontal", "vertical", "nearest"}:
        raise SectionViewPositionError(
            "Section-view alignment axis must be horizontal or vertical."
        )
    section_x, section_y = _position(section_view)
    base_x, base_y = _position(base_view)
    resolved_axis = axis
    if resolved_axis == "nearest":
        resolved_axis = (
            "horizontal"
            if abs(section_x - base_x) > abs(section_y - base_y)
            else "vertical"
        )
    target_x = base_x if resolved_axis == "vertical" else section_x
    target_y = base_y if resolved_axis == "horizontal" else section_y
    return {
        "section_view": section_view,
        "base_view": base_view,
        "mode": "axis",
        "axis": resolved_axis,
        "target_x_mm": target_x,
        "target_y_mm": target_y,
        "move_vector": App.Vector(
            section_x - target_x,
            section_y - target_y,
            0.0,
        ),
    }


def triangle_point(point_on_line, direction, external_point):
    """Project one point orthogonally onto an exact directed 2D line."""

    a = -_coordinate(direction.y, "edge direction Y")
    b = _coordinate(direction.x, "edge direction X")
    c1 = _coordinate(point_on_line.x, "section edge X") * a + _coordinate(
        point_on_line.y, "section edge Y"
    ) * b
    c2 = -_coordinate(external_point.x, "base vertex X") * b + _coordinate(
        external_point.y, "base vertex Y"
    ) * a
    denominator = a * a + b * b
    if denominator <= 1.0e-24:
        raise SectionViewPositionError(
            "The selected section edge has no usable direction."
        )
    return App.Vector(
        (c1 * a - c2 * b) / denominator,
        (c2 * a + c1 * b) / denominator,
        0.0,
    )


def calculate_edge_vertex_alignment(
    section_view,
    section_edge_name: str,
    selected_base_view,
    base_vertex_name: str,
):
    """Calculate the exact position aligning a section edge to a base vertex."""

    _require_section_view(section_view)
    if (
        not isinstance(section_edge_name, str)
        or not section_edge_name.startswith("Edge")
        or not isinstance(base_vertex_name, str)
        or not base_vertex_name.startswith("Vertex")
    ):
        raise SectionViewPositionError(
            "Section positioning requires one EdgeN and one VertexN target."
        )
    base_view = alignment_base(selected_base_view)
    if not same_drawing(section_view, base_view):
        raise SectionViewPositionError(
            "The section view and selected base view must share one page."
        )
    edge_reader = getattr(section_view, "getEdgeBySelection", None)
    vertex_reader = getattr(selected_base_view, "getVertexBySelection", None)
    if not callable(edge_reader) or not callable(vertex_reader):
        raise SectionViewPositionError(
            "The selected views do not expose projected edge and vertex geometry."
        )
    section_edge = edge_reader(section_edge_name)
    base_vertex = vertex_reader(base_vertex_name)
    if (
        section_edge is None
        or base_vertex is None
        or len(tuple(getattr(section_edge, "Vertexes", ()) or ())) < 1
        or not hasattr(getattr(section_edge, "Curve", None), "Direction")
    ):
        raise SectionViewPositionError(
            "The selected projected edge or vertex cannot define the alignment."
        )
    direction = section_edge.Curve.Direction
    if float(direction.Length) <= 1.0e-12:
        raise SectionViewPositionError(
            "The selected section edge has no usable direction."
        )
    base_scale = _coordinate(base_view.getScale(), "base scale")
    section_scale = _coordinate(section_view.getScale(), "section scale")
    if base_scale <= 0.0 or section_scale <= 0.0:
        raise SectionViewPositionError(
            "Section positioning requires positive base and section scales."
        )
    base_x, base_y = _position(base_view)
    section_x, section_y = _position(section_view)
    base_point = App.Vector(base_x, base_y, 0.0) + base_vertex.Point * base_scale
    section_point = (
        App.Vector(section_x, section_y, 0.0)
        + section_edge.Vertexes[0].Point * section_scale
    )
    projected = triangle_point(section_point, direction, base_point)
    move = projected.sub(base_point)
    return {
        "section_view": section_view,
        "base_view": base_view,
        "selected_base_view": selected_base_view,
        "mode": "edge_to_vertex",
        "section_edge": section_edge_name,
        "base_vertex": base_vertex_name,
        "target_x_mm": section_x - move.x,
        "target_y_mm": section_y - move.y,
        "move_vector": move,
    }


def apply_section_view_position(section_view, x_mm: float, y_mm: float):
    """Apply one prevalidated final section-view position without a transaction."""

    _require_section_view(section_view)
    if not same_drawing(
        section_view,
        alignment_base(getattr(section_view, "BaseView", None)),
    ):
        raise SectionViewPositionError(
            "The section view is not attached to its live base page."
        )
    target_x = _coordinate(x_mm, "target X position")
    target_y = _coordinate(y_mm, "target Y position")
    current_x, current_y = _position(section_view)
    if math.isclose(current_x, target_x, abs_tol=1.0e-9) and math.isclose(
        current_y,
        target_y,
        abs_tol=1.0e-9,
    ):
        raise SectionViewPositionError(
            "The section view is already at the requested aligned position."
        )
    section_view.X = target_x
    section_view.Y = target_y
    applied_x, applied_y = _position(section_view)
    if not math.isclose(applied_x, target_x, abs_tol=1.0e-9) or not math.isclose(
        applied_y,
        target_y,
        abs_tol=1.0e-9,
    ):
        raise RuntimeError("The section view did not retain its requested position.")
    return {"x_mm": applied_x, "y_mm": applied_y}
