# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only access to the bundled standard-fastener catalog."""

from __future__ import annotations

from typing import Any


TOOL_SPEC = {
    "name": "fastener_catalog.search",
    "description": (
        "Find an exact published standard fastener and its allowed dimensions. "
        "Pass returned constructor values to api.fastener; this tool never "
        "chooses hardware or substitutes a nearby size."
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
                    "Words that must appear in the standard, family, or "
                    "catalog description; empty lists the first standards."
                ),
            },
            "family": {
                "type": "string",
                "maxLength": 128,
                "description": (
                    "Exact catalog family such as Screw, Nut, Washer, Stud, "
                    "or Standoff; omit to search every family."
                ),
            },
            "standard": {
                "type": "string",
                "maxLength": 128,
                "description": (
                    "Exact published standard such as ISO4762; omit when "
                    "discovering standards."
                ),
            },
            "nominal_thread": {
                "type": "string",
                "maxLength": 128,
                "description": (
                    "Exact catalog size/thread token such as M6; omit to "
                    "return allowed tokens."
                ),
            },
            "length_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Requested length in millimeters. nominal_thread is "
                    "required; unavailable lengths return nearest valid values "
                    "without selecting one."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum deterministic results to return.",
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
