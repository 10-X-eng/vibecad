# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read every used cell of one named spreadsheet."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


MAX_CELLS_RETURNED = 500


TOOL_SPEC = {
    "name": "spreadsheet.read_sheet",
    "description": (
        "Read every used cell of one named spreadsheet: raw content, evaluated "
        "value, and alias. Use these exact cells, formulas, and aliases when "
        "reading or editing the owning Spreadsheet VibeScript source."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "SpreadsheetWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "sheet_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the Spreadsheet::Sheet object to read."
                ),
            },
            "cursor": {
                "type": "integer",
                "minimum": 0,
                "description": "Zero-based offset into the naturally sorted used-cell list.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_CELLS_RETURNED,
                "description": "Maximum cells to return in this page.",
            },
        },
        "required": ["sheet_name", "cursor", "limit"],
        "additionalProperties": False,
    },
}


def run(service: Any, sheet_name: str, cursor: int, limit: int) -> dict[str, Any]:
    clean_name = str(sheet_name or "").strip()
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    sheet = doc.getObject(clean_name) if clean_name else None
    if sheet is None:
        return _invalid(
            f"Spreadsheet not found by exact internal name: {sheet_name}",
            candidates=[
                {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
                for obj in list(getattr(doc, "Objects", []) or [])
                if domain_runtime.is_spreadsheet(obj)
            ],
        )
    if not domain_runtime.is_spreadsheet(sheet):
        return _invalid(
            f"Object is not a spreadsheet (Spreadsheet::Sheet): {clean_name}"
        )
    try:
        used_cells = sorted(
            (str(address).upper() for address in list(sheet.getUsedCells())),
            key=_cell_sort_key,
        )
    except AttributeError:
        return _invalid(
            "This FreeCAD build does not expose Sheet.getUsedCells; cannot "
            "enumerate cells.",
        )
    except Exception as exc:
        return _invalid(f"Could not enumerate spreadsheet cells: {exc}")
    start = int(cursor)
    page_limit = int(limit)
    if start > len(used_cells):
        return _invalid(
            "cursor is past the used-cell list.",
            cursor=start,
            cell_count=len(used_cells),
            valid_cursor_range=[0, len(used_cells)],
        )
    end = min(start + page_limit, len(used_cells))
    page = used_cells[start:end]
    truncated = end < len(used_cells)
    cells: list[dict[str, Any]] = []
    for index, address in enumerate(page, start=start):
        record: dict[str, Any] = {"index": index, "cell": str(address), "field_errors": []}
        try:
            record["content"] = str(sheet.getContents(address))
        except Exception as exc:
            record["field_errors"].append({"field": "content", "error": str(exc)})
        try:
            record["value"] = domain_runtime.spreadsheet_display_value(
                sheet.get(address)
            )
        except Exception as exc:
            record["field_errors"].append({"field": "value", "error": str(exc)})
        try:
            alias = sheet.getAlias(address)
            if alias:
                record["alias"] = str(alias)
        except Exception as exc:
            record["field_errors"].append({"field": "alias", "error": str(exc)})
        record["status"] = "ok" if not record["field_errors"] else "partial"
        cells.append(record)
    field_failures = [
        {"cell": record["cell"], "errors": record["field_errors"]}
        for record in cells
        if record["field_errors"]
    ]
    complete = not field_failures and not truncated
    result: dict[str, Any] = {
        "ok": not field_failures,
        "status": "complete" if complete else "partial",
        "complete": complete,
        "document": doc.Name,
        "sheet": sheet.Name,
        "sheet_label": sheet.Label,
        "cell_count": len(used_cells),
        "returned_range": {"start": start, "end_exclusive": end},
        "returned_count": len(cells),
        "field_failures": field_failures,
        "unsupported_native_methods": [],
        "cells": cells,
        "truncated": truncated,
        "next_cursor": end if truncated else None,
    }
    if field_failures:
        result["error"] = (
            f"{len(field_failures)} returned cell(s) could not provide every promised field."
        )
        result["retry_same_call"] = False
    return result


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _cell_sort_key(address: str) -> tuple[int, int]:
    column = 0
    index = 0
    while index < len(address) and address[index].isalpha():
        column = column * 26 + (ord(address[index]) - ord("A") + 1)
        index += 1
    return (int(address[index:]), column)
