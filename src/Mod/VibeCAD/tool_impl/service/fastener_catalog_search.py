# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only access to the bundled standard-fastener catalog."""

from __future__ import annotations

from typing import Any


TOOL_SPEC = {
    "name": "fastener_catalog.search",
    "description": (
        "Find an exact standard fastener and copy-ready api.fastener arguments. "
        "Unavailable sizes are never substituted."
    ),
    "contextual": False,
    "requires_document": False,
    "safety": "READ",
    "workbench": None,
    "edit_modes": ["none", "sketch"],
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 256,
                "description": (
                    "Catalog search words."
                ),
            },
            "family": {
                "type": "string",
                "maxLength": 128,
                "description": (
                    "Exact family, such as Screw or Nut."
                ),
            },
            "standard": {
                "type": "string",
                "maxLength": 128,
                "description": (
                    "Exact standard, such as ISO4762."
                ),
            },
            "nominal_thread": {
                "type": "string",
                "maxLength": 128,
                "description": (
                    "Exact thread token, such as M6."
                ),
            },
            "length_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Length in mm; requires nominal_thread."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum results.",
            },
        },
        "additionalProperties": False,
    },
}


def run(
    _service: Any,
    query: str = "",
    family: str | None = None,
    standard: str | None = None,
    nominal_thread: str | None = None,
    length_mm: float | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    try:
        from VibeCADFasteners import FastenerCatalogError, search_catalog

        return {
            "ok": True,
            **search_catalog(
                query,
                family=family,
                standard=standard,
                nominal_thread=nominal_thread,
                length_mm=length_mm,
                limit=limit,
            ),
        }
    except FastenerCatalogError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "retry_same_call": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"The bundled fastener catalog could not load: {exc}",
            "retry_same_call": False,
        }
