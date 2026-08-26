# SPDX-License-Identifier: LGPL-2.1-or-later

"""Decision-focused provider view of the durable Drawing snapshot."""

from __future__ import annotations

from typing import Any, Mapping


_SETTINGS = (
    "line_defaults",
    "hatch_defaults",
    "rich_annotation_defaults",
    "weld_symbol_catalog",
    "leader_defaults",
)
_SELECTED_CONTEXT = (
    "selected_break_definitions",
    "selected_draft_sources",
    "selected_clip_groups",
    "selected_clip_views",
    "selected_stack_views",
    "selected_dimensions",
    "selected_balloons",
    "selected_measurement_annotations",
    "selected_rich_annotations",
    "selected_leaders",
    "selected_engineering_symbols",
    "selected_leader_owners",
    "selected_format_targets",
    "selected_projected_geometry",
    "selected_line_attributes",
    "selected_line_lengths",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _target(value: Mapping[str, Any]) -> dict[str, str]:
    name = str(value.get("object_name") or "")
    if not name:
        raise TypeError("Drawing provider targets require an object name")
    return {"object_name": name}


def compact_drawing_source(
    value: Any,
    *,
    selected_names: set[str] | None = None,
) -> dict[str, Any]:
    source = _mapping(value)
    name = str(source.get("object_name") or "")
    result: dict[str, Any] = {
        "source_name": name,
        "source_target": _target(source),
        "type_id": str(source.get("type_id") or ""),
        "shape_type": str(source.get("shape_type") or ""),
        "topology": dict(_mapping(source.get("topology"))),
        "bounds_size_mm": source.get("bounds_size_mm"),
    }
    label = str(source.get("label") or "")
    if label and label != name:
        result["label"] = label
    placement = source.get("placement")
    if isinstance(placement, Mapping):
        result["placement"] = dict(placement)
    if name in (selected_names or set()):
        result["selected"] = True
    return result


def _compact_view(value: Any) -> dict[str, Any]:
    view = _mapping(value)
    name = str(view.get("object_name") or "")
    result: dict[str, Any] = {"view_name": name}
    if name:
        result["view_target"] = {"object_name": name}
    for key, item in view.items():
        if key in {"object_name", "state_sha256"} or item in (None, [], {}):
            continue
        if key == "sources" and isinstance(item, list):
            result["source_names"] = [
                str(source.get("object_name") or "")
                for source in item
                if isinstance(source, Mapping) and source.get("object_name")
            ]
            continue
        result[str(key)] = item
    return result


def _without_state_hashes(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_state_hashes(item)
            for key, item in value.items()
            if not str(key).endswith("sha256")
        }
    if isinstance(value, list):
        return [_without_state_hashes(item) for item in value]
    if isinstance(value, tuple):
        return [_without_state_hashes(item) for item in value]
    return value


def _compact_page(
    value: Any,
    *,
    active_page: Mapping[str, Any],
) -> dict[str, Any]:
    page = _mapping(value)
    name = str(page.get("object_name") or "")
    result: dict[str, Any] = {
        "page_name": name,
        "page_target": _target(page),
    }
    for key in (
        "label",
        "keep_updated",
        "projection_type",
        "scale",
        "view_count",
        "template",
        "template_geometry",
        "editable_field_count",
        "editable_fields_supported",
        "update_status",
        "unresolved_references",
        "unresolved_references_truncated",
        "export_readiness",
        "view_locks",
    ):
        if key in page and page[key] not in (None, [], {}):
            result[key] = page[key]
    result["views"] = [_compact_view(view) for view in list(page.get("views") or ())]
    if name and name == str(active_page.get("object_name") or ""):
        result["active"] = True
        for key in ("presentation", "frame_visibility", "grid_visibility"):
            value = active_page.get(key)
            if value not in (None, [], {}):
                result[key] = value
        editable_fields = active_page.get("editable_fields")
        if editable_fields not in (None, []):
            result["editable_fields"] = editable_fields
    return _without_state_hashes(result)


def compact_drawing_provider_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Remove duplicate Drawing facts while retaining every exact next-call target."""

    if not isinstance(state, Mapping) or state.get("surface_id") != "drawing":
        raise TypeError("state must be one Drawing Native snapshot")
    domain = state.get("domain")
    if not isinstance(domain, Mapping) or domain.get("kind") != "drawing":
        raise TypeError("state must contain one Drawing domain")

    selected_names = {
        str(value.get("object_name") or "")
        for value in list(domain.get("selected_sources") or ())
        if isinstance(value, Mapping) and value.get("object_name")
    }
    sources = [
        compact_drawing_source(value, selected_names=selected_names)
        for value in list(domain.get("sources") or ())
    ]
    active_page = _mapping(domain.get("active_page"))
    pages = [
        _compact_page(value, active_page=active_page)
        for value in list(domain.get("pages") or ())
    ]
    compact_domain: dict[str, Any] = {
        "kind": "drawing",
        "source_count": int(domain.get("source_count", len(sources)) or 0),
        "sources": sources,
        "page_count": int(domain.get("page_count", len(pages)) or 0),
        "page_resolution": str(domain.get("active_page_resolution") or ""),
        "pages": pages,
    }
    if domain.get("sources_truncated") is True:
        compact_domain["sources_truncated"] = True
        compact_domain["source_next_offset"] = int(
            domain.get("source_next_offset", len(sources))
        )
    if domain.get("pages_truncated") is True:
        compact_domain["pages_truncated"] = True

    settings = {
        key: domain[key]
        for key in _SETTINGS
        if domain.get(key) not in (None, [], {})
    }
    if settings:
        compact_domain["settings"] = settings
    context = {
        key: domain[key]
        for key in _SELECTED_CONTEXT
        if domain.get(key) not in (None, [], {})
    }
    for key in _SELECTED_CONTEXT:
        truncated_key = key + "_truncated"
        if domain.get(truncated_key) is True:
            context[truncated_key] = True
    if context:
        compact_domain["context"] = context
    viewport = domain.get("active_3d_viewport")
    if viewport not in (None, [], {}):
        compact_domain["active_3d_viewport"] = viewport

    represented = {
        source["source_name"] for source in sources if source.get("source_name")
    }
    represented.update(page["page_name"] for page in pages if page.get("page_name"))
    for page in pages:
        template = page.get("template")
        if isinstance(template, Mapping) and template.get("object_name"):
            represented.add(str(template["object_name"]))
        represented.update(
            str(view.get("view_name") or "") for view in list(page.get("views") or ())
        )
    working_set = [
        value
        for value in list(state.get("working_set") or ())
        if isinstance(value, Mapping)
        and str(value.get("object_name") or "") not in represented
    ]
    result: dict[str, Any] = {
        "surface_id": "drawing",
        "document": dict(_mapping(state.get("document"))),
        "structural_revision": int(state.get("structural_revision", 0) or 0),
        "domain": compact_domain,
    }
    if working_set:
        result["working_set"] = working_set
    return _without_state_hashes(result)


__all__ = ["compact_drawing_provider_state"]
