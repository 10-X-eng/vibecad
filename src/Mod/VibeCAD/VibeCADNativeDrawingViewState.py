# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded source and projected-view state for Native Drawing."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeDrawingState import is_drawing_page


MAX_DRAWING_VIEW_SOURCES = 12
MAX_DRAWING_BREAKS = 16
DRAWING_VIEW_ORIENTATIONS = {
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    "rear": ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
    "isometric": (
        (1.0, -1.0, 1.0),
        (0.7071067811865476, 0.7071067811865476, 0.0),
    ),
}


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_derived(obj: Any, type_id: str) -> bool:
    check = getattr(obj, "isDerivedFrom", None)
    if callable(check):
        try:
            return bool(check(type_id))
        except Exception:
            return False
    return str(getattr(obj, "TypeId", "") or "") == type_id


def is_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawView")


def is_part_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawViewPart")


def is_broken_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawBrokenView")


def is_section_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawViewSection")


def is_complex_section_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawComplexSection")


def is_detail_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawViewDetail")


def _vector(value: Any) -> list[float]:
    return [
        round(float(getattr(value, name)), 12)
        for name in ("x", "y", "z")
    ]


def _placement_state(obj: Any) -> dict[str, list[float]] | None:
    placement = getattr(obj, "Placement", None)
    if placement is None:
        return None
    rotation = getattr(placement, "Rotation", None)
    quaternion = tuple(getattr(rotation, "Q", ()) or ())
    if len(quaternion) != 4:
        return None
    return {
        "base_mm": _vector(placement.Base),
        "quaternion": [round(float(value), 12) for value in quaternion],
    }


def _shape_sha256(shape: Any) -> str:
    canonical = shape.copy()
    canonical.Orientation = "Forward"
    exporter = getattr(canonical, "exportBrepToString", None)
    if not callable(exporter):
        raise ValueError("The Drawing source shape cannot be serialized exactly.")
    raw = exporter()
    encoded = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def drawing_source_state(obj: Any) -> dict[str, Any]:
    """Return one same-document, whole-object projection source state."""

    if obj is None or is_drawing_page(obj) or is_drawing_view(obj):
        raise ValueError("A Drawing view source must be a non-Drawing shape object.")
    shape = getattr(obj, "Shape", None)
    if (
        shape is None
        or bool(shape.isNull())
        or not bool(shape.isValid())
    ):
        raise ValueError("A Drawing view source must have one valid non-empty shape.")
    topology = {
        "solids": len(tuple(getattr(shape, "Solids", ()) or ())),
        "faces": len(tuple(getattr(shape, "Faces", ()) or ())),
        "edges": len(tuple(getattr(shape, "Edges", ()) or ())),
    }
    bounds = getattr(shape, "BoundBox", None)
    geometry = {
        "shape_type": str(getattr(shape, "ShapeType", "") or ""),
        "shape_sha256": _shape_sha256(shape),
        "placement": _placement_state(obj),
        "topology": topology,
        "bounds_size_mm": (
            [
                round(float(getattr(bounds, name)), 9)
                for name in ("XLength", "YLength", "ZLength")
            ]
            if bounds is not None
            else None
        ),
    }
    result = {
        "object_name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type_id": str(getattr(obj, "TypeId", "") or ""),
        **geometry,
    }
    result["state_sha256"] = _digest(
        {
            "object_name": result["object_name"],
            "type_id": result["type_id"],
            **geometry,
        }
    )
    return result


def _straight_edge_direction(edge: Any) -> tuple[float, float, float]:
    vertices = tuple(getattr(edge, "Vertexes", ()) or ())
    if len(vertices) != 2:
        raise ValueError("A Drawing break line must have exactly two endpoints.")
    first = vertices[0].Point
    second = vertices[1].Point
    components = tuple(
        float(getattr(second, name)) - float(getattr(first, name))
        for name in ("x", "y", "z")
    )
    chord = sum(value * value for value in components) ** 0.5
    edge_length = float(getattr(edge, "Length", 0.0))
    if (
        chord <= 1.0e-9
        or abs(edge_length - chord) > max(1.0e-7, chord * 1.0e-7)
    ):
        raise ValueError("A Drawing break definition must use straight nonzero lines.")
    return tuple(round(value / chord, 12) for value in components)


def drawing_break_state(obj: Any) -> dict[str, Any]:
    """Return one exact structurally valid TechDraw break-definition state."""

    source = drawing_source_state(obj)
    shape = obj.Shape
    edges = tuple(getattr(shape, "Edges", ()) or ())
    is_sketch = _is_derived(obj, "Sketcher::SketchObject")
    if is_sketch:
        if len(edges) != 2:
            raise ValueError(
                "A sketch break definition must contain exactly two straight parallel lines."
            )
        directions = tuple(_straight_edge_direction(edge) for edge in edges)
        dot = abs(sum(left * right for left, right in zip(*directions, strict=True)))
        if abs(dot - 1.0) > 1.0e-7:
            raise ValueError("A sketch break definition's two lines must be parallel.")
        kind = "two_line_sketch"
    elif str(getattr(shape, "ShapeType", "") or "") == "Edge" and len(edges) == 1:
        directions = (_straight_edge_direction(edges[0]),)
        kind = "single_edge"
    else:
        raise ValueError(
            "A Drawing break definition must be one edge or a sketch with two straight parallel lines."
        )
    definition = {
        "object_name": source["object_name"],
        "label": source["label"],
        "type_id": source["type_id"],
        "kind": kind,
        "shape_state_sha256": source["state_sha256"],
        "line_count": len(edges),
        "line_directions": [list(direction) for direction in directions],
    }
    definition["state_sha256"] = _digest(
        {
            "object_name": definition["object_name"],
            "type_id": definition["type_id"],
            "kind": kind,
            "shape_state_sha256": source["state_sha256"],
        }
    )
    return definition


def _edge_count(view: Any, method_name: str) -> int | None:
    method = getattr(view, method_name, None)
    if not callable(method):
        return None
    try:
        shape = method()
        return len(tuple(getattr(shape, "Edges", ()) or ()))
    except Exception:
        return None


def _projection_counts(view: Any) -> tuple[int | None, int | None]:
    reader = getattr(view, "getPrecomputedProjection", None)
    if callable(reader):
        try:
            snapshot = reader()
            edges = getattr(snapshot.get("edges"), "Edges", ())
            visibility = tuple(snapshot.get("edge_visibility") or ())
            if len(edges) == len(visibility):
                visible = sum(bool(value) for value in visibility)
                return visible, len(visibility) - visible
        except Exception:
            pass
    return (
        _edge_count(view, "getVisibleEdges"),
        _edge_count(view, "getHiddenEdges"),
    )


def _parent_page(view: Any) -> Any | None:
    finder = getattr(view, "findParentPage", None)
    if callable(finder):
        try:
            page = finder()
            if is_drawing_page(page):
                return page
        except Exception:
            pass
    document = getattr(view, "Document", None)
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        if is_drawing_page(obj) and view in tuple(getattr(obj, "Views", ()) or ()):
            return obj
    return None


def drawing_view_state(view: Any) -> dict[str, Any]:
    if not is_part_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    sources = tuple(getattr(view, "Source", ()) or ())
    source_states = [drawing_source_state(source) for source in sources]
    page = _parent_page(view)
    visible_edge_count, hidden_edge_count = _projection_counts(view)
    settings = {
        "page_name": str(getattr(page, "Name", "") or "") if page else None,
        "source_states": [
            {
                "object_name": state["object_name"],
                "state_sha256": state["state_sha256"],
            }
            for state in source_states
        ],
        "direction": _vector(view.Direction),
        "x_direction": _vector(view.XDirection),
        "x_mm": round(float(view.X), 9),
        "y_mm": round(float(view.Y), 9),
        "scale_type": str(view.ScaleType),
        "scale": round(float(view.Scale), 12),
        "line_visibility": {
            name: bool(getattr(view, name))
            for name in (
                "SmoothVisible",
                "SeamVisible",
                "IsoVisible",
                "HardHidden",
                "SmoothHidden",
                "SeamHidden",
                "IsoHidden",
            )
        },
        "visible_edge_count": visible_edge_count,
        "hidden_edge_count": hidden_edge_count,
    }
    if is_broken_drawing_view(view):
        settings["breaks"] = [
            {
                "object_name": state["object_name"],
                "state_sha256": state["state_sha256"],
                "kind": state["kind"],
            }
            for state in (
                drawing_break_state(item)
                for item in tuple(getattr(view, "Breaks", ()) or ())
            )
        ]
        settings["gap_mm"] = round(float(view.Gap), 9)
    if is_section_drawing_view(view):
        base = getattr(view, "BaseView", None)
        base_state = drawing_view_state(base) if is_part_drawing_view(base) else None
        section_face_count = None
        complex_section = is_complex_section_drawing_view(view)
        reader = getattr(
            view,
            (
                "getPrecomputedComplexSection"
                if complex_section
                else "getPrecomputedSection"
            ),
            None,
        )
        if callable(reader):
            try:
                section_face_count = len(
                    tuple(reader()["section_faces"].Faces)
                )
            except Exception:
                pass
        settings["section"] = {
            "base_view": (
                {
                    "object_name": base_state["object_name"],
                    "state_sha256": base_state["state_sha256"],
                }
                if base_state is not None
                else None
            ),
            "origin_mm": _vector(view.SectionOrigin),
            "normal": _vector(view.SectionNormal),
            "symbol": str(view.SectionSymbol),
            "rotation_degrees": round(float(view.Rotation), 9),
            "direction_mode": str(view.SectionDirection),
            "cut_surface_display": str(view.CutSurfaceDisplay),
            "fuse_before_cut": bool(view.FuseBeforeCut),
            "trim_after_cut": bool(view.TrimAfterCut),
            "use_previous_cut": bool(view.UsePreviousCut),
            "section_face_count": section_face_count,
        }
        if complex_section:
            profile = getattr(view, "CuttingToolWireObject", None)
            profile_state = None
            if profile is not None:
                try:
                    profile_state = drawing_source_state(profile)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
            settings["section"]["complex"] = {
                "profile": (
                    {
                        "object_name": profile_state["object_name"],
                        "state_sha256": profile_state["state_sha256"],
                    }
                    if profile_state is not None
                    else None
                ),
                "projection_strategy": str(view.ProjectionStrategy),
            }
    if is_detail_drawing_view(view):
        base = getattr(view, "BaseView", None)
        base_state = drawing_view_state(base) if is_part_drawing_view(base) else None
        detail_counts = None
        reader = getattr(view, "getPrecomputedDetail", None)
        if callable(reader):
            try:
                detail_shape = reader()["detail_shape"]
                detail_counts = {
                    "solids": len(tuple(detail_shape.Solids)),
                    "faces": len(tuple(detail_shape.Faces)),
                    "edges": len(tuple(detail_shape.Edges)),
                }
            except Exception:
                pass
        settings["detail"] = {
            "base_view": (
                {
                    "object_name": base_state["object_name"],
                    "state_sha256": base_state["state_sha256"],
                }
                if base_state is not None
                else None
            ),
            "anchor_mm": _vector(view.AnchorPoint)[:2],
            "radius_mm": round(float(view.Radius), 9),
            "rotation_degrees": round(float(view.Rotation), 9),
            "reference": str(view.Reference),
            "show_matting": bool(view.ShowMatting),
            "show_highlight": bool(view.ShowHighlight),
            "detail_topology": detail_counts,
        }
    result = {
        "object_name": str(getattr(view, "Name", "") or ""),
        "label": str(getattr(view, "Label", "") or ""),
        "type_id": str(getattr(view, "TypeId", "") or ""),
        **settings,
    }
    result["state_sha256"] = _digest(
        {
            "object_name": result["object_name"],
            "type_id": result["type_id"],
            **settings,
        }
    )
    return result
