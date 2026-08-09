# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Parameters ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_SHEETS = 24
MAX_ALIASES = 48


def _sheet_summary(sheet: Any) -> dict[str, Any]:
    result = concise_object(sheet)
    try:
        cells = sorted(str(value) for value in list(sheet.getNonEmptyCells()) or [])
    except Exception:
        cells = []
    aliases = []
    get_alias = getattr(sheet, "getAlias", None)
    if callable(get_alias):
        for cell in cells:
            try:
                alias = str(get_alias(cell) or "").strip()
            except Exception:
                continue
            if alias:
                aliases.append({"cell": cell, "alias": alias[:160]})
                if len(aliases) >= MAX_ALIASES:
                    break
    result["non_empty_cell_count"] = len(cells)
    if aliases:
        result["aliases"] = aliases
    return result


def build_parameters_snapshot(document: Any) -> dict[str, Any]:
    sheets = objects_of_type(document, "Spreadsheet::Sheet")
    expression_objects = sum(
        1
        for obj in list(getattr(document, "Objects", []) or [])
        if bool(getattr(obj, "ExpressionEngine", None))
    )
    return {
        "kind": "parameters",
        "counts": {
            "spreadsheets": len(sheets),
            "objects_with_expressions": expression_objects,
        },
        "spreadsheets": [_sheet_summary(value) for value in sheets[:MAX_SHEETS]],
    }
