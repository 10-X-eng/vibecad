# SPDX-License-Identifier: LGPL-2.1-or-later

"""Write a batch of cells (content and aliases) to one named spreadsheet."""

from __future__ import annotations

import re
from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_CELL_PATTERN = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,4}$")
_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MAX_CELLS_PER_CALL = 100


TOOL_SPEC = {
    "name": "spreadsheet.set_cells",
    "description": (
        "Write a batch of cells to one named spreadsheet in a single "
        "transaction: numbers, text, formulas (content starting with '='), and "
        "optional aliases. Aliased cells can be referenced from parametric "
        "expressions in other objects as SheetName.alias. Existing cell content "
        "is overwritten; call spreadsheet.read_sheet first when "
        "unsure."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "SpreadsheetWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "sheet_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the Spreadsheet::Sheet object to write."
                ),
            },
            "cells": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CELLS_PER_CALL,
                "description": (
                    "Batch of cell writes applied in order within one "
                    "transaction. Each cell address may appear at most once."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "cell": {
                            "type": "string",
                            "pattern": "^[A-Za-z]{1,3}[1-9][0-9]{0,4}$",
                            "description": (
                                "Exact cell address, for example 'A1' or 'B12'."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "Cell content as a string: a number ('42'), "
                                "text, or a formula starting with '=' (for "
                                "example '=A1*2')."
                            ),
                        },
                        "alias": {
                            "type": "string",
                            "description": (
                                "Optional alias for this cell so expressions "
                                "elsewhere can reference it as SheetName.alias. "
                                "Must start with a letter or underscore and use "
                                "only letters, digits, and underscores."
                            ),
                        },
                    },
                    "required": ["cell", "content"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["sheet_name", "cells"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    sheet_name: str,
    cells: list[dict[str, Any]],
) -> dict[str, Any]:
    clean_name = str(sheet_name or "").strip()
    doc = service._active_document()
    sheet = doc.getObject(clean_name) if doc is not None and clean_name else None
    if sheet is None:
        return _invalid(
            f"Spreadsheet not found by exact internal name: {sheet_name}",
            candidates=_sheet_candidates(doc),
        )
    if not domain_runtime.is_spreadsheet(sheet):
        return _invalid(
            f"Object is not a spreadsheet (Spreadsheet::Sheet): {clean_name}"
        )
    if not isinstance(cells, list) or not cells:
        return _invalid("cells must contain at least one cell write.")
    if len(cells) > MAX_CELLS_PER_CALL:
        return _invalid(
            f"cells accepts at most {MAX_CELLS_PER_CALL} entries per call; "
            "split the batch."
        )
    writes: list[dict[str, Any]] = []
    seen: set[str] = set()
    requested_aliases: dict[str, str] = {}
    for index, entry in enumerate(cells):
        if not isinstance(entry, dict):
            return _invalid(f"cells[{index}] must be an object.")
        address = str(entry.get("cell") or "").strip().upper()
        if not _CELL_PATTERN.fullmatch(address):
            return _invalid(
                f"cells[{index}].cell is not a valid cell address: "
                f"{entry.get('cell')!r}"
            )
        if address in seen:
            return _invalid(
                f"cells[{index}] repeats cell {address}; each cell may appear "
                "once per call."
            )
        seen.add(address)
        content = entry.get("content")
        if not isinstance(content, str):
            return _invalid(
                f"cells[{index}].content must be a string (use '42' for numbers)."
            )
        alias_value = entry.get("alias")
        alias: str | None = None
        if alias_value is not None:
            alias = str(alias_value).strip()
            if not _ALIAS_PATTERN.fullmatch(alias):
                return _invalid(
                    f"cells[{index}].alias must start with a letter or "
                    "underscore and use only letters, digits, and underscores: "
                    f"{alias!r}"
                )
            if _CELL_PATTERN.fullmatch(alias.upper()):
                return _invalid(
                    f"cells[{index}].alias must not look like a cell address: {alias!r}"
                )
            prior = requested_aliases.get(alias)
            if prior is not None and prior != address:
                return _invalid(
                    f"cells[{index}].alias duplicates alias {alias!r} requested for {prior}.",
                    alias=alias,
                    first_cell=prior,
                    second_cell=address,
                )
            requested_aliases[alias] = address
        if content.startswith("=") and not content[1:].strip():
            return _invalid(
                f"cells[{index}].content starts a formula but has no expression.",
                cell=address,
            )
        writes.append({"index": index, "cell": address, "content": content, "alias": alias})

    alias_inventory = _alias_inventory(sheet)
    if not alias_inventory.get("ok"):
        return _invalid(
            "Existing spreadsheet aliases could not be enumerated; no writes were applied.",
            alias_inventory=alias_inventory,
        )
    collisions = [
        {
            "alias": alias,
            "requested_cell": address,
            "existing_cell": alias_inventory["by_alias"][alias],
        }
        for alias, address in requested_aliases.items()
        if alias in alias_inventory["by_alias"]
        and alias_inventory["by_alias"][alias] != address
    ]
    if collisions:
        return _invalid(
            "One or more requested aliases already belong to different cells; no writes were applied.",
            alias_collisions=collisions,
            alias_inventory=alias_inventory,
        )
    before_entries = [_read_cell_state(sheet, write["cell"]) for write in writes]

    def apply() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target = active.getObject(clean_name)
        if target is None:
            raise RuntimeError("The spreadsheet no longer exists.")
        entries: list[dict[str, Any]] = []
        stopped = False
        for write, before in zip(writes, before_entries):
            record: dict[str, Any] = {
                "index": write["index"],
                "cell": write["cell"],
                "requested_content": write["content"],
                "requested_alias": write["alias"],
                "before": before,
                "alias_applied": False,
                "content_applied": False,
                "status": "not_attempted" if stopped else "applying",
            }
            if stopped:
                entries.append(record)
                continue
            try:
                if write["alias"] is not None:
                    target.setAlias(write["cell"], write["alias"])
                    record["alias_applied"] = True
                target.set(write["cell"], write["content"])
                record["content_applied"] = True
                active.recompute()
            except Exception as exc:
                record["native_error"] = str(exc)
                record["failure_stage"] = (
                    "set_content" if record["alias_applied"] else "set_alias_or_content"
                )
                stopped = True
            record["after"] = _read_cell_state(target, write["cell"])
            record["formula"] = write["content"].startswith("=")
            field_errors = list((record["after"] or {}).get("field_errors") or [])
            actual_content = str((record["after"] or {}).get("content") or "")
            if record["formula"]:
                content_matches = actual_content.lstrip().startswith("=")
                record["native_formula_content"] = actual_content
                if not content_matches:
                    record["formula_parse_error"] = (
                        "FreeCAD did not retain the content as a formula."
                    )
            else:
                content_matches = actual_content == write["content"]
            alias_matches = (
                write["alias"] is None
                or (record["after"] or {}).get("alias") == write["alias"]
            )
            record["status"] = (
                "ok"
                if not record.get("native_error")
                and not field_errors
                and content_matches
                and alias_matches
                else "failed"
            )
            if record["status"] != "ok":
                stopped = True
            entries.append(record)
        successful_prefix = 0
        for record in entries:
            if record["status"] != "ok":
                break
            successful_prefix += 1
        return {
            "document": active.Name,
            "sheet": target.Name,
            "sheet_label": target.Label,
            "requested_count": len(writes),
            "successful_prefix_count": successful_prefix,
            "retained_prefix": entries[:successful_prefix],
            "failed_entry": next(
                (record for record in entries if record["status"] == "failed"),
                None,
            ),
            "unattempted_suffix": [
                record for record in entries if record["status"] == "not_attempted"
            ],
            "entries": entries,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        entries = list(result.get("entries") or [])
        checks = [
            {
                "name": "all_entries_applied_and_evaluated",
                "ok": len(entries) == len(writes)
                and all(entry.get("status") == "ok" for entry in entries),
                "requested_count": len(writes),
                "successful_prefix_count": result.get("successful_prefix_count"),
                "failed_entry": result.get("failed_entry"),
                "unattempted_suffix": result.get("unattempted_suffix"),
            }
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Set spreadsheet cells: {clean_name}",
        apply,
        verifier=verify,
    )
    mutation = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "set_cells", "mutation": mutation},
        next_action=(
            "Check the returned evaluated values; any evaluation_error means "
            "the formula or reference in that cell needs correcting."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _read_cell_state(sheet: Any, address: str) -> dict[str, Any]:
    state: dict[str, Any] = {"cell": address, "field_errors": []}
    try:
        state["content"] = str(sheet.getContents(address))
    except Exception as exc:
        state["field_errors"].append({"field": "content", "error": str(exc)})
    try:
        state["value"] = domain_runtime.spreadsheet_display_value(sheet.get(address))
    except Exception as exc:
        state["field_errors"].append({"field": "value", "error": str(exc)})
    try:
        alias = sheet.getAlias(address)
        state["alias"] = str(alias) if alias else None
    except Exception as exc:
        state["field_errors"].append({"field": "alias", "error": str(exc)})
    return state


def _alias_inventory(sheet: Any) -> dict[str, Any]:
    try:
        used_cells = [str(address) for address in list(sheet.getUsedCells())]
    except Exception as exc:
        return {"ok": False, "error": str(exc), "by_alias": {}}
    by_alias: dict[str, str] = {}
    errors = []
    for address in used_cells:
        try:
            alias = sheet.getAlias(address)
        except Exception as exc:
            errors.append({"cell": address, "error": str(exc)})
            continue
        if alias:
            by_alias[str(alias)] = address
    return {"ok": not errors, "by_alias": by_alias, "errors": errors}


def _sheet_candidates(doc: Any) -> list[dict[str, Any]]:
    if doc is None:
        return []
    return [
        {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
        for obj in list(getattr(doc, "Objects", []) or [])
        if domain_runtime.is_spreadsheet(obj)
    ]
