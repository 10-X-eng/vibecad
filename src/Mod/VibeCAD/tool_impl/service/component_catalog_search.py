# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only discovery of authored components reusable by an Assembly."""

from __future__ import annotations

from typing import Any

TOOL_SPEC = {
    "name": "component_catalog.search",
    "description": (
        "Find exact reusable component definitions in open documents and saved "
        "FreeCAD files beside the active Assembly. Pass a returned reference to "
        "api.component; never recreate geometry that already exists."
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
                "description": (
                    "Literal words that must appear anywhere in the document, "
                    "object, label, type, part number, or description."
                ),
            },
            "document_path": {
                "type": "string",
                "maxLength": 2048,
                "description": (
                    "Optional exact .FCStd path below the Assembly document "
                    "directory, using forward slashes."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum deterministic matches to return.",
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
) -> dict[str, Any]:
    from VibeCADComponentCatalog import search_captured_component_catalog

    return search_captured_component_catalog(
        captured,
        query,
        document_path=document_path,
        limit=limit,
    )


def run(
    service: Any,
    query: str = "",
    document_path: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    try:
        return {
            "ok": True,
            **complete(
                capture(service),
                query=query,
                document_path=document_path,
                limit=limit,
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "retry_same_call": False,
        }
