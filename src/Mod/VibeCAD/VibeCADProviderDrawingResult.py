# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise provider projection for exact Drawing inspection results."""

from __future__ import annotations

from typing import Any, Mapping


_LAYOUT_ISSUES = frozenset(
    {"clipped_items", "items_outside_drawing_area", "item_collisions"}
)


def _item(
    value: Mapping[str, Any],
    *,
    collision: bool = False,
) -> dict[str, Any]:
    item = {"object_name": str(value.get("object_name") or "")}
    label_position = value.get("label_position_on_page_mm")
    if isinstance(label_position, Mapping):
        item["label_position_on_page_mm"] = label_position
    if not collision or not isinstance(label_position, Mapping):
        bounds = value.get("bounds_mm")
        if isinstance(bounds, Mapping):
            item["bounds_mm"] = bounds
    if (
        value.get("type_id") == "TechDraw::DrawProjGroupItem"
        and value.get("parent_object_name")
    ):
        item["placement_target"] = {
            "object_name": str(value["parent_object_name"])
        }
    return item


def _item_section(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"count": 0, "items": [], "truncated": False}
    items = [
        _item(item)
        for item in value.get("items", ())
        if isinstance(item, Mapping)
    ]
    return {
        "count": int(value.get("count", len(items))),
        "items": items,
        "truncated": bool(value.get("truncated", False)),
    }


def _collisions(result: Mapping[str, Any]) -> dict[str, Any]:
    collision_state = result.get("collisions")
    pairs = (
        tuple(collision_state.get("pairs", ()))
        if isinstance(collision_state, Mapping)
        else ()
    )
    peers: dict[str, set[str]] = {}
    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        first = str(pair.get("first_object_name") or "")
        second = str(pair.get("second_object_name") or "")
        if not first or not second or first == second:
            continue
        peers.setdefault(first, set()).add(second)
        peers.setdefault(second, set()).add(first)
    items = {
        str(item.get("object_name") or ""): item
        for item in result.get("items", ())
        if isinstance(item, Mapping) and str(item.get("object_name") or "")
    }
    objects = []
    for name in sorted(peers):
        item = items.get(name)
        compact = (
            _item(item, collision=True)
            if isinstance(item, Mapping)
            else {"object_name": name}
        )
        compact["collides_with"] = sorted(peers[name])
        objects.append(compact)
    return {
        "count": len(objects),
        "objects": objects,
        "truncated": bool(collision_state.get("truncated", False))
        if isinstance(collision_state, Mapping)
        else False,
    }


def provider_visible_drawing_readiness(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the verdict and exact geometry needed to repair the page."""

    if result.get("ok") is not True or "ready" not in result:
        return dict(result)
    issues = [str(value) for value in result.get("issues", ())]
    visible: dict[str, Any] = {
        **({"ok": result["ok"]} if "ok" in result else {}),
        "page": result.get("page", {}),
        "ready": bool(result.get("ready", False)),
        "issues": issues,
    }
    if visible["ready"]:
        return visible
    issue_set = frozenset(issues)
    if issue_set & _LAYOUT_ISSUES:
        if "drawing_bounds_mm" in result:
            visible["drawing_bounds_mm"] = result["drawing_bounds_mm"]
        visible["rendered_item_count"] = int(result.get("rendered_item_count", 0))
    if "clipped_items" in issue_set:
        visible["clipping"] = _item_section(result.get("clipping"))
    if "items_outside_drawing_area" in issue_set:
        visible["outside_drawing_area"] = _item_section(
            result.get("outside_drawing_area")
        )
    if "item_collisions" in issue_set:
        visible["collisions"] = _collisions(result)
    if "duplicate_scene_items" in issue_set:
        visible["duplicate_scene_items"] = result.get("duplicate_scene_items", {})
    if "invalid_references" in issue_set:
        visible["references"] = result.get("references", {})
    if "duplicate_dimensions" in issue_set:
        visible["duplicate_dimensions"] = result.get("duplicate_dimensions", {})
    if "unit_system_missing" in issue_set:
        visible["units"] = result.get("units", {})
    if "page_update_error" in issue_set:
        visible["update_status"] = result.get("update_status", {})
    if "no_rendered_content" in issue_set:
        visible["rendered_item_count"] = int(result.get("rendered_item_count", 0))
    return visible


__all__ = ["provider_visible_drawing_readiness"]
