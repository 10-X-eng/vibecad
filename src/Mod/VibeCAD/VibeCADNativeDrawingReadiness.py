# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact read-only readiness from the live TechDraw page scene."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionRepair import drawing_dimension_repair_state
from VibeCADNativeDrawingDimensionState import is_drawing_dimension
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingPlacementState import (
    NativeDrawingPlacementStateError,
    drawing_dimension_label_placement_state,
    drawing_view_position_on_page,
)
from VibeCADNativeDrawingState import drawing_page_state, is_svg_template
from VibeCADNativeTargets import resolve_object


MAX_DRAWING_LAYOUT_PAGE_SIZE = 64
MAX_DRAWING_LAYOUT_ISSUES = 64
_LAYOUT_TOLERANCE_MM = 1.0e-6
_EXPORT_BLOCKING_ISSUES = frozenset(
    {
        "page_unavailable_in_history",
        "page_invalid",
        "template_unavailable",
        "open_transaction",
        "page_update_error",
        "no_rendered_content",
        "clipped_items",
        "items_outside_drawing_area",
        "item_collisions",
        "duplicate_scene_items",
        "invalid_references",
        "duplicate_dimensions",
        "unit_system_missing",
    }
)
_BOUNDS_FIELDS = frozenset(
    {"min_x_mm", "min_y_mm", "max_x_mm", "max_y_mm"}
)


def _bounds(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != _BOUNDS_FIELDS:
        raise NativeDrawingError(
            "TechDraw returned malformed rendered bounds.",
            error_code="NATIVE_DRAWING_LAYOUT_INVALID",
        )
    values = {name: float(value[name]) for name in _BOUNDS_FIELDS}
    if any(not math.isfinite(value) for value in values.values()):
        raise NativeDrawingError(
            "The Drawing page contains non-finite rendered bounds.",
            error_code="NATIVE_DRAWING_LAYOUT_INVALID",
        )
    if values["max_x_mm"] < values["min_x_mm"] or values["max_y_mm"] < values["min_y_mm"]:
        raise NativeDrawingError(
            "TechDraw returned inverted rendered bounds.",
            error_code="NATIVE_DRAWING_LAYOUT_INVALID",
        )
    return {name: round(item, 9) for name, item in values.items()}


def _inside_bounds(
    bounds: Mapping[str, float],
    container: Mapping[str, float],
) -> bool:
    tolerance = _LAYOUT_TOLERANCE_MM
    return bool(
        bounds["min_x_mm"] >= float(container["min_x_mm"]) - tolerance
        and bounds["min_y_mm"] >= float(container["min_y_mm"]) - tolerance
        and bounds["max_x_mm"] <= float(container["max_x_mm"]) + tolerance
        and bounds["max_y_mm"] <= float(container["max_y_mm"]) + tolerance
    )


def _rendered_items(
    document: Any,
    page: Any,
    *,
    width_mm: float,
    height_mm: float,
    drawing_bounds: Mapping[str, float],
) -> tuple[list[dict[str, Any]], dict[str, Any], tuple[str, ...]]:
    try:
        import TechDrawGui

        raw = TechDrawGui.inspectPageLayout(page)
    except Exception as exc:
        raise NativeDrawingError(
            "The live TechDraw page layout is unavailable.",
            error_code="NATIVE_DRAWING_LAYOUT_UNAVAILABLE",
        ) from exc
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"items", "collisions", "duplicate_object_names"}
        or not isinstance(raw["items"], list)
        or not isinstance(raw["collisions"], list)
        or not isinstance(raw["duplicate_object_names"], list)
    ):
        raise NativeDrawingError(
            "TechDraw returned malformed page-layout state.",
            error_code="NATIVE_DRAWING_LAYOUT_INVALID",
        )
    ordered = []
    names = set()
    for raw_item in raw["items"]:
        required = {
            "object_name",
            "type_id",
            "parent_object_name",
            "bounds_mm",
        }
        if (
            not isinstance(raw_item, Mapping)
            or not required <= set(raw_item)
            or set(raw_item) - required != (
                {"label_bounds_mm"} if "label_bounds_mm" in raw_item else set()
            )
        ):
            raise NativeDrawingError(
                "TechDraw returned a malformed rendered item.",
                error_code="NATIVE_DRAWING_LAYOUT_INVALID",
            )
        name = str(raw_item["object_name"] or "")
        obj = document.getObject(name) if name else None
        type_id = str(raw_item["type_id"] or "")
        if (
            obj is None
            or str(getattr(obj, "TypeId", "") or "") != type_id
            or name in names
        ):
            raise NativeDrawingError(
                "TechDraw returned an unknown or duplicate rendered item.",
                error_code="NATIVE_DRAWING_LAYOUT_INVALID",
            )
        names.add(name)
        item_bounds = _bounds(raw_item["bounds_mm"])
        rendered_item = {
            "object_name": name,
            "type_id": type_id,
            "parent_object_name": str(raw_item["parent_object_name"] or ""),
            "bounds_mm": item_bounds,
            "within_page": _inside_bounds(
                item_bounds,
                {
                    "min_x_mm": 0.0,
                    "min_y_mm": 0.0,
                    "max_x_mm": width_mm,
                    "max_y_mm": height_mm,
                },
            ),
            "within_drawing_area": _inside_bounds(
                item_bounds,
                drawing_bounds,
            ),
        }
        if is_drawing_dimension(obj):
            if "label_bounds_mm" in raw_item:
                rendered_item["label_bounds_mm"] = _bounds(
                    raw_item["label_bounds_mm"]
                )
            parent_name = rendered_item["parent_object_name"]
            parent = document.getObject(parent_name) if parent_name else None
            try:
                placement = drawing_dimension_label_placement_state(obj)
                origin = drawing_view_position_on_page(parent)
            except (
                AttributeError,
                NativeDrawingPlacementStateError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                raise NativeDrawingError(
                    "A rendered Drawing dimension has no exact page-coordinate placement.",
                    error_code="NATIVE_DRAWING_LAYOUT_INVALID",
                ) from exc
            local = placement["label_position_in_view_mm"]
            rendered_item.update(
                {
                    "label_position_in_view_mm": dict(local),
                    "view_origin_on_page_mm": dict(origin),
                    "label_position_on_page_mm": {
                        "x_mm": round(
                            float(origin["x_mm"]) + float(local["x_mm"]),
                            9,
                        ),
                        "y_mm": round(
                            float(origin["y_mm"]) + float(local["y_mm"]),
                            9,
                        ),
                    },
                }
            )
        elif "label_bounds_mm" in raw_item:
            raise NativeDrawingError(
                "TechDraw returned label bounds for a non-dimension item.",
                error_code="NATIVE_DRAWING_LAYOUT_INVALID",
            )
        ordered.append(rendered_item)
    ordered.sort(key=lambda item: item["object_name"])

    ordered_collisions = []
    for value in raw["collisions"]:
        required = {
            "first_object_name",
            "second_object_name",
            "first_type_id",
            "second_type_id",
            "overlap_bounds_mm",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise NativeDrawingError(
                "TechDraw returned a malformed rendered collision.",
                error_code="NATIVE_DRAWING_LAYOUT_INVALID",
            )
        first = str(value["first_object_name"] or "")
        second = str(value["second_object_name"] or "")
        if first not in names or second not in names or first >= second:
            raise NativeDrawingError(
                "TechDraw returned an invalid rendered collision pair.",
                error_code="NATIVE_DRAWING_LAYOUT_INVALID",
            )
        ordered_collisions.append(
            {
                "first_object_name": first,
                "second_object_name": second,
                "first_type_id": str(value["first_type_id"] or ""),
                "second_type_id": str(value["second_type_id"] or ""),
                "overlap_bounds_mm": _bounds(value["overlap_bounds_mm"]),
            }
        )
    ordered_collisions.sort(
        key=lambda item: (item["first_object_name"], item["second_object_name"])
    )
    duplicate_names = tuple(
        sorted(str(name or "") for name in raw["duplicate_object_names"] if str(name or ""))
    )
    return ordered, {
        "count": len(ordered_collisions),
        "pairs": ordered_collisions[:MAX_DRAWING_LAYOUT_ISSUES],
        "truncated": len(ordered_collisions) > MAX_DRAWING_LAYOUT_ISSUES,
    }, duplicate_names


def _dimension_readiness(page: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    projection_names: dict[str, frozenset[str] | None] = {}
    unresolved = []
    duplicate_groups: dict[tuple[Any, ...], list[str]] = {}
    for obj in tuple(page.Document.Objects):
        if not is_drawing_dimension(obj) or obj.findParentPage() is not page:
            continue
        try:
            state = drawing_dimension_repair_state(
                obj,
                projection_names_by_view=projection_names,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            unresolved.append(
                {
                    "object_name": str(getattr(obj, "Name", "") or ""),
                    "issues": [str(exc)[:256]],
                }
            )
            continue
        if not state["valid"] or state["issues"]:
            unresolved.append(
                {
                    "object_name": state["object_name"],
                    "issues": list(state["issues"]),
                }
            )
            continue
        key = (
            state["repair_kind"],
            tuple(
                (item["object_name"], item["subelement"])
                for item in state["references_2d"]
            ),
        )
        duplicate_groups.setdefault(key, []).append(state["object_name"])
    duplicates = [
        {"object_names": sorted(names)}
        for _key, names in sorted(duplicate_groups.items(), key=lambda item: item[0])
        if len(names) > 1
    ]
    return (
        {
            "count": len(unresolved),
            "items": unresolved[:MAX_DRAWING_LAYOUT_ISSUES],
            "truncated": len(unresolved) > MAX_DRAWING_LAYOUT_ISSUES,
        },
        {
            "count": len(duplicates),
            "groups": duplicates[:MAX_DRAWING_LAYOUT_ISSUES],
            "truncated": len(duplicates) > MAX_DRAWING_LAYOUT_ISSUES,
        },
    )


def drawing_page_readiness(
    document: Any,
    *,
    target: Mapping[str, Any],
    offset: int = 0,
) -> dict[str, Any]:
    """Inspect the exact live scene used by TechDraw page rendering."""

    if (
        not isinstance(target, Mapping)
        or set(target) != {"object_name", "expected_state_sha256"}
    ):
        raise NativeDrawingError(
            "The exact Drawing page target is malformed.",
            error_code="NATIVE_DRAWING_READINESS_PARAMETERS_INVALID",
        )
    if type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise NativeDrawingError(
            "Drawing readiness offset must be an integer from 0 through 1000000.",
            error_code="NATIVE_DRAWING_READINESS_PARAMETERS_INVALID",
        )
    page = resolve_object(
        document,
        {
            "document_uid": str(document.Uid),
            "object_name": str(target["object_name"]),
        },
        expected_types=("TechDraw::DrawPage",),
    )
    page_state = drawing_page_state(page)
    if str(target["expected_state_sha256"]) != page_state["state_sha256"]:
        raise NativeDrawingError(
            "The exact Drawing page changed after it was inspected.",
            error_code="NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    geometry = page_state["template_geometry"]
    width_mm = float(geometry["width_mm"])
    height_mm = float(geometry["height_mm"])
    if width_mm <= 0.0 or height_mm <= 0.0:
        raise NativeDrawingError(
            "The Drawing page has invalid template geometry.",
            error_code="NATIVE_DRAWING_LAYOUT_INVALID",
        )
    raw_drawing_bounds = geometry.get("drawing_bounds_mm")
    drawing_bounds = (
        _bounds(raw_drawing_bounds)
        if isinstance(raw_drawing_bounds, Mapping)
        else {
            "min_x_mm": 0.0,
            "min_y_mm": 0.0,
            "max_x_mm": width_mm,
            "max_y_mm": height_mm,
        }
    )
    if not _inside_bounds(
        drawing_bounds,
        {
            "min_x_mm": 0.0,
            "min_y_mm": 0.0,
            "max_x_mm": width_mm,
            "max_y_mm": height_mm,
        },
    ):
        raise NativeDrawingError(
            "The Drawing template has invalid drawing bounds.",
            error_code="NATIVE_DRAWING_LAYOUT_INVALID",
        )
    items, collisions, duplicate_scene_items = _rendered_items(
        document,
        page,
        width_mm=width_mm,
        height_mm=height_mm,
        drawing_bounds=drawing_bounds,
    )
    clipped = [item for item in items if not item["within_page"]]
    outside_drawing_area = [
        item for item in items if not item["within_drawing_area"]
    ]
    unresolved, duplicate_dimensions = _dimension_readiness(page)
    fields = list(page_state.get("editable_fields") or [])
    empty_fields = sorted(
        str(item["field_name"])
        for item in fields
        if not str(item.get("value") or "").strip()
    )
    unit_fields = [
        item for item in fields if str(item.get("field_name") or "") == "unit_system"
    ]
    unit_value = (
        str(unit_fields[0].get("value") or "").strip() if unit_fields else ""
    )
    units = {
        "supported": bool(unit_fields),
        "declared": bool(unit_value),
        "field_name": "unit_system" if unit_fields else None,
        "value": unit_value if unit_fields else None,
    }
    state_messages = [
        str(value or "").strip()[:256]
        for value in tuple(getattr(page, "State", ()) or ())
        if str(value or "").strip()
    ][:MAX_DRAWING_LAYOUT_ISSUES]
    page_current = "Up-to-date" in state_messages and bool(page.isValid())
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    issues = []
    if callable(checker) and not bool(checker(page)):
        issues.append("page_unavailable_in_history")
    if not bool(page.isValid()):
        issues.append("page_invalid")
    if not is_svg_template(getattr(page, "Template", None)):
        issues.append("template_unavailable")
    if int(document.getBookedTransactionID()) != 0:
        issues.append("open_transaction")
    if not page_current:
        issues.append("page_update_error")
    if not items:
        issues.append("no_rendered_content")
    if clipped:
        issues.append("clipped_items")
    if outside_drawing_area:
        issues.append("items_outside_drawing_area")
    if collisions["count"]:
        issues.append("item_collisions")
    if duplicate_scene_items:
        issues.append("duplicate_scene_items")
    if unresolved["count"]:
        issues.append("invalid_references")
    if duplicate_dimensions["count"]:
        issues.append("duplicate_dimensions")
    if units["supported"] and not units["declared"]:
        issues.append("unit_system_missing")

    stop = min(len(items), offset + MAX_DRAWING_LAYOUT_PAGE_SIZE)
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
        },
        "page_bounds_mm": {
            "min_x_mm": 0.0,
            "min_y_mm": 0.0,
            "max_x_mm": width_mm,
            "max_y_mm": height_mm,
        },
        "drawing_bounds_mm": drawing_bounds,
        "ready": not issues,
        "issues": issues,
        "rendered_item_count": len(items),
        "items": items[offset:stop],
        "offset": offset,
        "next_offset": stop if stop < len(items) else None,
        "clipping": {
            "count": len(clipped),
            "items": clipped[:MAX_DRAWING_LAYOUT_ISSUES],
            "truncated": len(clipped) > MAX_DRAWING_LAYOUT_ISSUES,
        },
        "outside_drawing_area": {
            "count": len(outside_drawing_area),
            "items": outside_drawing_area[:MAX_DRAWING_LAYOUT_ISSUES],
            "truncated": len(outside_drawing_area) > MAX_DRAWING_LAYOUT_ISSUES,
        },
        "collisions": collisions,
        "duplicate_scene_items": {
            "count": len(duplicate_scene_items),
            "object_names": list(
                duplicate_scene_items[:MAX_DRAWING_LAYOUT_ISSUES]
            ),
            "truncated": len(duplicate_scene_items) > MAX_DRAWING_LAYOUT_ISSUES,
        },
        "references": unresolved,
        "duplicate_dimensions": duplicate_dimensions,
        "units": units,
        "template_fields": {
            "count": len(fields),
            "empty_count": len(empty_fields),
            "empty_field_names": empty_fields[:MAX_DRAWING_LAYOUT_ISSUES],
            "truncated": len(empty_fields) > MAX_DRAWING_LAYOUT_ISSUES,
        },
        "update_status": {
            "current": page_current,
            "state_messages": state_messages,
        },
    }


def require_drawing_export_readiness(readiness: Mapping[str, Any]) -> None:
    """Reject only objective page defects before export or printing."""

    if not isinstance(readiness, Mapping) or not isinstance(
        readiness.get("issues"), list
    ):
        raise NativeDrawingError(
            "Drawing export readiness is malformed.",
            error_code="NATIVE_DRAWING_LAYOUT_INVALID",
        )
    blocking = [
        str(issue)
        for issue in readiness["issues"]
        if str(issue) in _EXPORT_BLOCKING_ISSUES
    ]
    if not blocking:
        return
    page = readiness.get("page")
    page_target = (
        {"object_name": str(page.get("object_name") or "")}
        if isinstance(page, Mapping)
        else {}
    )
    raise NativeDrawingError(
        "The Drawing page has objective defects that must be corrected before output.",
        error_code="NATIVE_DRAWING_OUTPUT_NOT_READY",
        repair={
            "tool": "drawing.page_readiness",
            "page": page_target,
            "blocking_issues": blocking,
        },
    )


__all__ = [
    "MAX_DRAWING_LAYOUT_PAGE_SIZE",
    "drawing_page_readiness",
    "require_drawing_export_readiness",
]
