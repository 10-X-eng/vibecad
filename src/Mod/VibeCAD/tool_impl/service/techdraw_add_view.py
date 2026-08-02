# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native TechDraw projected view of exact 3D objects."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_DIRECTION_VECTORS = {
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    "rear": ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
    "isometric": ((1.0, -1.0, 1.0), (0.707107, 0.707107, 0.0)),
}


TOOL_SPEC = {
    "name": "techdraw.add_view",
    "description": (
        "Create one native TechDraw projected view of exact named 3D objects "
        "on an exact drawing page. The view is a live projection: it updates "
        "when the source objects change. The result reports the view's "
        "projected edge and vertex names (Edge0, Vertex1, ...) used by "
        "techdraw.add_dimension."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "TechDrawWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "page_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the drawing page from techdraw.list_pages."
                ),
            },
            "source_object_names": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "Exact internal name of one 3D object to project.",
                },
                "minItems": 1,
                "maxItems": 20,
                "description": (
                    "Exact internal names of the 3D objects to project into "
                    "this view; they are projected together as one image."
                ),
            },
            "view_direction": {
                "type": "string",
                "enum": sorted(_DIRECTION_VECTORS),
                "description": (
                    "Standard direction to view the model from: front looks "
                    "along -Y, rear along +Y, left along -X, right along +X, "
                    "top along +Z, bottom along -Z, isometric from the "
                    "(1,-1,1) corner."
                ),
            },
            "x_mm": {
                "type": "number",
                "description": (
                    "Horizontal position of the view center on the page in mm, "
                    "measured from the page's bottom-left corner (A4 landscape "
                    "is 297 wide x 210 high)."
                ),
            },
            "y_mm": {
                "type": "number",
                "description": (
                    "Vertical position of the view center on the page in mm, "
                    "measured from the page's bottom-left corner."
                ),
            },
            "scale": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Drawing scale for this view; 1 is full size, 0.5 is half "
                    "size, 2 doubles the model."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new view, e.g. 'FrontView'.",
            },
        },
        "required": [
            "page_name",
            "source_object_names",
            "view_direction",
            "x_mm",
            "y_mm",
            "scale",
            "label",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    page_name: str,
    source_object_names: list[str],
    view_direction: str,
    x_mm: float,
    y_mm: float,
    scale: float,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    vectors = _DIRECTION_VECTORS.get(str(view_direction or ""))
    if vectors is None:
        return _invalid(
            "view_direction must be one of: " + ", ".join(sorted(_DIRECTION_VECTORS))
        )
    if float(scale) <= 0:
        return _invalid("scale must be positive.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    page = doc.getObject(str(page_name or "").strip())
    if page is None or getattr(page, "TypeId", "") != "TechDraw::DrawPage":
        return _invalid(
            f"Drawing page not found by exact internal name: {page_name}. "
            "Call techdraw.list_pages for exact names."
        )
    if not isinstance(source_object_names, list) or not source_object_names:
        return _invalid("source_object_names must be a non-empty array.")
    sources: list[str] = []
    for raw_name in source_object_names:
        name = str(raw_name or "").strip()
        source = doc.getObject(name) if name else None
        if source is None:
            return _invalid(
                f"Source object not found by exact internal name: {raw_name}"
            )
        shape = getattr(source, "Shape", None)
        if shape is None or shape.isNull():
            return _invalid(f"Source object has no shape geometry: {name}")
        if name in sources:
            return _invalid(
                f"source_object_names contains the duplicate object {name!r}."
            )
        sources.append(name)
    page_width = float(page.PageWidth)
    page_height = float(page.PageHeight)
    if not (0.0 <= float(x_mm) <= page_width and 0.0 <= float(y_mm) <= page_height):
        return _invalid(
            "The requested view center lies outside the drawing page.",
            requested_position_mm={"x": float(x_mm), "y": float(y_mm)},
            page_dimensions_mm={"width": page_width, "height": page_height},
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target_page = active.getObject(page.Name)
        if target_page is None:
            raise RuntimeError("The drawing page no longer exists.")
        view = active.addObject("TechDraw::DrawViewPart", "View")
        view.Label = clean_label
        target_page.addView(view)
        view.Source = [active.getObject(name) for name in sources]
        direction, x_direction = vectors
        view.Direction = App.Vector(*direction)
        view.XDirection = App.Vector(*x_direction)
        view.ScaleType = "Custom"
        view.Scale = float(scale)
        view.X = float(x_mm)
        view.Y = float(y_mm)
        active.recompute()
        projection = _projected_element_inventory(view)
        actual_sources = [obj.Name for obj in list(getattr(view, "Source", []) or [])]
        page_views = [obj.Name for obj in list(getattr(target_page, "Views", []) or [])]
        projection_bounds = _projection_bounds(projection)
        page_bounds = _page_bounds(
            projection_bounds, float(view.X), float(view.Y)
        )
        state = list(getattr(view, "State", []) or [])
        return {
            "document": active.Name,
            "page": target_page.Name,
            "view": view.Name,
            "view_label": view.Label,
            "requested_sources": sources,
            "actual_sources": actual_sources,
            "page_views": page_views,
            "page_membership": view.Name in page_views,
            "requested_view_direction": view_direction,
            "actual_direction": _vector(view.Direction),
            "actual_x_direction": _vector(view.XDirection),
            "requested_scale": float(scale),
            "actual_scale": float(view.Scale),
            "requested_position_mm": {"x": float(x_mm), "y": float(y_mm)},
            "actual_position_mm": {"x": float(view.X), "y": float(view.Y)},
            "page_dimensions_mm": {
                "width": float(target_page.PageWidth),
                "height": float(target_page.PageHeight),
            },
            "projection": projection,
            "projection_bounds_view_mm": projection_bounds,
            "projection_bounds_page_mm": page_bounds,
            "feature_state": state,
            "up_to_date": "Up-to-date" in state,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        projection = result.get("projection") or {}
        page_bounds = result.get("projection_bounds_page_mm") or {}
        dimensions = result.get("page_dimensions_mm") or {}
        actual_position = result.get("actual_position_mm") or {}
        expected_direction, expected_x_direction = vectors
        checks = [
            {
                "name": "page_membership",
                "ok": result.get("page_membership") is True,
                "page_views": result.get("page_views"),
            },
            {
                "name": "exact_source_links",
                "ok": result.get("actual_sources") == sources,
                "requested": sources,
                "actual": result.get("actual_sources"),
            },
            {
                "name": "projection_computed",
                "ok": projection.get("ok") is True
                and int(projection.get("edge_count", 0)) > 0
                and result.get("up_to_date") is True,
                "projection_error": projection.get("error"),
                "edge_count": projection.get("edge_count"),
                "vertex_count": projection.get("vertex_count"),
                "feature_state": result.get("feature_state"),
            },
            {
                "name": "direction_readback",
                "ok": _vector_close(result.get("actual_direction"), expected_direction)
                and _vector_close(
                    result.get("actual_x_direction"), expected_x_direction
                ),
                "actual_direction": result.get("actual_direction"),
                "actual_x_direction": result.get("actual_x_direction"),
            },
            {
                "name": "scale_and_position_readback",
                "ok": abs(float(result.get("actual_scale", 0.0)) - float(scale))
                <= 1.0e-9
                and abs(float(actual_position.get("x", 0.0)) - float(x_mm))
                <= 1.0e-9
                and abs(float(actual_position.get("y", 0.0)) - float(y_mm))
                <= 1.0e-9,
                "actual_scale": result.get("actual_scale"),
                "actual_position_mm": actual_position,
            },
            {
                "name": "projection_within_page",
                "ok": _bounds_within_page(page_bounds, dimensions),
                "projection_bounds_page_mm": page_bounds,
                "page_dimensions_mm": dimensions,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Add TechDraw view: {clean_label}",
        create,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "add_view", **result},
        next_action=(
            "Add dimensions with techdraw.add_dimension, or capture a "
            "screenshot to inspect the page."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _projected_element_inventory(view: Any) -> dict[str, Any]:
    try:
        raw = view.getProjectedElementDescriptors()
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "edges": [],
            "vertices": [],
            "edge_count": 0,
            "vertex_count": 0,
            "mapping_summary": {},
        }
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "error": "Native projected-element diagnostics returned a non-object value.",
            "edges": [],
            "vertices": [],
            "edge_count": 0,
            "vertex_count": 0,
            "mapping_summary": {},
        }
    edges = [item for item in list(raw.get("edges") or []) if isinstance(item, dict)]
    vertices = [
        item for item in list(raw.get("vertices") or []) if isinstance(item, dict)
    ]
    mapping_summary: dict[str, int] = {}
    for item in edges + vertices:
        mapping = item.get("source_mapping") or {}
        status = str(mapping.get("status") or "missing")
        mapping_summary[status] = mapping_summary.get(status, 0) + 1
    return {
        "ok": True,
        "coordinate_space": raw.get("coordinate_space"),
        "view_scale": raw.get("view_scale"),
        "edges": edges,
        "vertices": vertices,
        "edge_count": len(edges),
        "vertex_count": len(vertices),
        "mapping_summary": mapping_summary,
    }


def _projection_bounds(projection: dict[str, Any]) -> dict[str, float] | None:
    coordinates: list[tuple[float, float]] = []
    for edge in list(projection.get("edges") or []):
        bounds = edge.get("bounds_2d") or {}
        try:
            coordinates.extend(
                [
                    (float(bounds["min_x"]), float(bounds["min_y"])),
                    (float(bounds["max_x"]), float(bounds["max_y"])),
                ]
            )
        except (KeyError, TypeError, ValueError):
            continue
    for vertex in list(projection.get("vertices") or []):
        point = vertex.get("point_2d") or {}
        try:
            coordinates.append((float(point["x"]), float(point["y"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not coordinates:
        return None
    xs = [point[0] for point in coordinates]
    ys = [point[1] for point in coordinates]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }


def _page_bounds(
    projection_bounds: dict[str, float] | None, x_mm: float, y_mm: float
) -> dict[str, float] | None:
    if not projection_bounds:
        return None
    return {
        "min_x": x_mm + float(projection_bounds["min_x"]),
        "min_y": y_mm + float(projection_bounds["min_y"]),
        "max_x": x_mm + float(projection_bounds["max_x"]),
        "max_y": y_mm + float(projection_bounds["max_y"]),
        "width": float(projection_bounds["width"]),
        "height": float(projection_bounds["height"]),
    }


def _bounds_within_page(
    bounds: dict[str, Any] | None, dimensions: dict[str, Any]
) -> bool:
    if not bounds:
        return False
    try:
        return (
            float(bounds["min_x"]) >= -1.0e-6
            and float(bounds["min_y"]) >= -1.0e-6
            and float(bounds["max_x"]) <= float(dimensions["width"]) + 1.0e-6
            and float(bounds["max_y"]) <= float(dimensions["height"]) + 1.0e-6
        )
    except (KeyError, TypeError, ValueError):
        return False


def _vector(value: Any) -> dict[str, float]:
    return {
        "x": float(getattr(value, "x", 0.0)),
        "y": float(getattr(value, "y", 0.0)),
        "z": float(getattr(value, "z", 0.0)),
    }


def _vector_close(actual: Any, expected: tuple[float, float, float]) -> bool:
    if not isinstance(actual, dict):
        return False
    return all(
        abs(float(actual.get(axis, 0.0)) - float(target)) <= 1.0e-6
        for axis, target in zip(("x", "y", "z"), expected)
    )
