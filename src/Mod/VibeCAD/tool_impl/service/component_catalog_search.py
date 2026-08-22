# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only discovery of authored components reusable by an Assembly."""

from __future__ import annotations

from typing import Any

from VibeCADComponentCatalog import MAX_COMPONENT_SEARCH_RESULTS

TOOL_SPEC = {
    "name": "component_catalog.search",
    "description": (
        "Search reusable components absent from available_components. Use a returned "
        "catalog_key in a component input; follow next_offset until null."
    ),
    "contextual": False,
    "requires_document": True,
    "safety": "READ",
    "workbench": None,
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 256,
                "description": "Metadata search words.",
            },
            "document_path": {
                "type": "string",
                "maxLength": 2048,
                "description": "Project-relative .FCStd path.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_COMPONENT_SEARCH_RESULTS,
                "description": "Requested page size.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Page offset.",
            },
            "detail": {
                "type": "string",
                "enum": ["references", "full"],
                "description": "Compact references or full metadata.",
            },
        },
        "additionalProperties": False,
    },
}


def capture(service: Any) -> dict[str, Any]:
    from VibeCADComponentCatalog import capture_component_catalog

    return capture_component_catalog(service)


def complete(
    captured: dict[str, Any],
    query: str = "",
    document_path: str | None = None,
    limit: int = 25,
    offset: int = 0,
    detail: str = "full",
) -> dict[str, Any]:
    from VibeCADComponentCatalog import (
        search_captured_component_catalog,
        search_prepared_component_catalog,
    )

    if captured.get("schema") == "vibecad-component-catalog-snapshot-v1":
        return search_prepared_component_catalog(
            captured,
            query,
            document_path=document_path,
            limit=limit,
            offset=offset,
            detail=detail,
        )
    return search_captured_component_catalog(
        captured,
        query,
        document_path=document_path,
        limit=limit,
        offset=offset,
        detail=detail,
    )


def prepare(captured: dict[str, Any]) -> dict[str, Any]:
    from VibeCADComponentCatalog import prepare_captured_component_catalog

    return prepare_captured_component_catalog(captured)


def run(
    service: Any,
    query: str = "",
    document_path: str | None = None,
    limit: int = 25,
    offset: int = 0,
    detail: str = "full",
) -> dict[str, Any]:
    try:
        return {
            "ok": True,
            **complete(
                capture(service),
                query=query,
                document_path=document_path,
                limit=limit,
                offset=offset,
                detail=detail,
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "retry_same_call": False,
        }
