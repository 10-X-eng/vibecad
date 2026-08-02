# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only discovery of exact native material cards."""

from __future__ import annotations

from typing import Any


TOOL_SPEC = {
    "name": "material_catalog.search",
    "description": (
        "Find exact FreeCAD material cards and copy-ready api.material arguments. "
        "Search matches any substring in names, UUIDs, libraries, tags, property "
        "names, and common values; required properties filter out unusable cards."
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
                    "Words or partial strings that must all occur somewhere on the card; "
                    "empty lists the first cards."
                ),
            },
            "require_physical_properties": {
                "type": "array",
                "maxItems": 64,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 128},
                "description": (
                    "Exact physical-property names the selected card must contain, such "
                    "as Density, YoungsModulus, or PoissonRatio."
                ),
            },
            "require_appearance_properties": {
                "type": "array",
                "maxItems": 64,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 128},
                "description": (
                    "Exact appearance-property names the selected card must contain."
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


def run(
    _service: Any,
    query: str = "",
    require_physical_properties: list[str] | None = None,
    require_appearance_properties: list[str] | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    try:
        from vibescript_material_worker import search_material_catalog

        return {
            "ok": True,
            **search_material_catalog(
                query,
                require_physical_properties=require_physical_properties or (),
                require_appearance_properties=require_appearance_properties or (),
                limit=limit,
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"The native material catalog could not be searched: {exc}",
            "retry_same_call": False,
        }

