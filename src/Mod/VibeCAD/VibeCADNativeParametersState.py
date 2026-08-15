# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state and addresses for the Parameters spreadsheet surface."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping


MAX_PARAMETER_CELLS = 4096
MAX_PARAMETER_RANGE_CELLS = 512
MAX_PARAMETER_CELL_CONTENT_CHARACTERS = 16_384
MAX_PARAMETER_CELL_RESULT_CHARACTERS = 4096
MAX_PARAMETER_ROWS = 1_048_576
MAX_PARAMETER_COLUMNS = 16_384
_CELL = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")
_RANGE = re.compile(
    r"^([A-Z]{1,3}[1-9][0-9]{0,6})(?::([A-Z]{1,3}[1-9][0-9]{0,6}))?$"
)
_FORMULA_CELL_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_])(?:'[^'\r\n]{1,128}'#)?\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})(?![A-Za-z0-9_])"
)


class NativeParametersStateError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "NATIVE_PARAMETERS_STATE_INVALID") -> None:
        super().__init__(str(message).strip())
        self.error_code = str(error_code)

    def failure(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": str(self)}


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _column_number(value: str) -> int:
    result = 0
    for character in value:
        result = result * 26 + ord(character) - ord("A") + 1
    if not 1 <= result <= MAX_PARAMETER_COLUMNS:
        raise NativeParametersStateError("Spreadsheet column is outside A through XFD.")
    return result


def _column_name(value: int) -> str:
    if not 1 <= value <= MAX_PARAMETER_COLUMNS:
        raise NativeParametersStateError("Spreadsheet column is outside A through XFD.")
    result = []
    current = value
    while current:
        current, remainder = divmod(current - 1, 26)
        result.append(chr(ord("A") + remainder))
    return "".join(reversed(result))


def cell_address(row: int, column: int) -> str:
    if type(row) is not int or not 1 <= row <= MAX_PARAMETER_ROWS:
        raise NativeParametersStateError(
            "Spreadsheet row is outside 1 through 1048576.",
            error_code="NATIVE_PARAMETERS_ADDRESS_INVALID",
        )
    return f"{_column_name(column)}{row}"


def normalize_cell_address(value: Any) -> str:
    address = str(value or "").strip().upper()
    match = _CELL.fullmatch(address)
    if match is None:
        raise NativeParametersStateError(
            "Spreadsheet cell addresses must use A1 notation.",
            error_code="NATIVE_PARAMETERS_ADDRESS_INVALID",
        )
    _column_number(match.group(1))
    if not 1 <= int(match.group(2)) <= MAX_PARAMETER_ROWS:
        raise NativeParametersStateError(
            "Spreadsheet row is outside 1 through 1048576.",
            error_code="NATIVE_PARAMETERS_ADDRESS_INVALID",
        )
    return address


def cell_coordinates(address: str) -> tuple[int, int]:
    normalized = normalize_cell_address(address)
    match = _CELL.fullmatch(normalized)
    assert match is not None
    return int(match.group(2)), _column_number(match.group(1))


def normalize_range(value: Any, *, maximum_cells: int = MAX_PARAMETER_RANGE_CELLS) -> tuple[str, tuple[str, ...]]:
    raw = str(value or "").strip().upper()
    match = _RANGE.fullmatch(raw)
    if match is None:
        raise NativeParametersStateError(
            "Spreadsheet ranges must use A1 or A1:B2 notation.",
            error_code="NATIVE_PARAMETERS_RANGE_INVALID",
        )
    first = normalize_cell_address(match.group(1))
    second = normalize_cell_address(match.group(2) or first)
    first_row, first_column = cell_coordinates(first)
    second_row, second_column = cell_coordinates(second)
    row_min, row_max = sorted((first_row, second_row))
    column_min, column_max = sorted((first_column, second_column))
    count = (row_max - row_min + 1) * (column_max - column_min + 1)
    if not 1 <= count <= maximum_cells:
        raise NativeParametersStateError(
            f"Spreadsheet ranges are limited to {maximum_cells} cells per call.",
            error_code="NATIVE_PARAMETERS_RANGE_TOO_LARGE",
        )
    start = f"{_column_name(column_min)}{row_min}"
    end = f"{_column_name(column_max)}{row_max}"
    cells = tuple(
        f"{_column_name(column)}{row}"
        for row in range(row_min, row_max + 1)
        for column in range(column_min, column_max + 1)
    )
    return (start if start == end else f"{start}:{end}"), cells


def envelope_range(addresses: Iterable[str]) -> str:
    coordinates = [cell_coordinates(value) for value in addresses]
    if not coordinates:
        raise NativeParametersStateError("At least one spreadsheet cell is required.")
    rows = [value[0] for value in coordinates]
    columns = [value[1] for value in coordinates]
    start = f"{_column_name(min(columns))}{min(rows)}"
    end = f"{_column_name(max(columns))}{max(rows)}"
    return start if start == end else f"{start}:{end}"


def _text(value: Any, maximum: int, noun: str) -> str:
    result = str(value or "")
    if len(result) > maximum:
        raise NativeParametersStateError(
            f"Spreadsheet {noun} exceeds its {maximum}-character state bound."
        )
    return result


def _evaluated(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        result = value
    elif isinstance(value, float):
        result = value if math.isfinite(value) else str(value)
    else:
        result = str(value)
    if isinstance(result, str):
        return _text(result, MAX_PARAMETER_CELL_RESULT_CHARACTERS, "evaluated value")
    return result


def _optional_call(sheet: Any, method_name: str, address: str, default: Any) -> Any:
    method = getattr(sheet, method_name, None)
    if not callable(method):
        return default
    try:
        value = method(address)
    except Exception:
        return default
    return default if value is None else value


def _color(value: Any) -> list[float] | None:
    try:
        components = tuple(float(item) for item in tuple(value))
    except (TypeError, ValueError):
        return None
    if len(components) not in {3, 4} or any(
        not math.isfinite(item) or not 0.0 <= item <= 1.0 for item in components
    ):
        return None
    return [round(item, 6) for item in components[:3]]


def _formula_references(contents: str) -> list[str]:
    if not contents.startswith("="):
        return []
    result = []
    for match in _FORMULA_CELL_REFERENCE.finditer(contents):
        address = normalize_cell_address(f"{match.group(1)}{match.group(2)}")
        if address not in result:
            result.append(address)
        if len(result) >= 64:
            break
    return result


def parameter_cell_state(sheet: Any, address: Any) -> dict[str, Any]:
    normalized = normalize_cell_address(address)
    try:
        contents = _text(
            sheet.getContents(normalized),
            MAX_PARAMETER_CELL_CONTENT_CHARACTERS,
            "cell contents",
        )
    except Exception as exc:
        raise NativeParametersStateError(
            f"Spreadsheet cell {normalized} could not be read."
        ) from exc
    evaluation_error = None
    try:
        evaluated = _evaluated(sheet.get(normalized)) if contents else None
    except Exception as exc:
        evaluated = None
        evaluation_error = _text(str(exc).strip(), 512, "formula error")
    alias = _text(_optional_call(sheet, "getAlias", normalized, ""), 160, "alias")
    styles = sorted(str(item) for item in (_optional_call(sheet, "getStyle", normalized, set()) or set()))
    alignment = sorted(
        str(item)
        for item in (_optional_call(sheet, "getAlignment", normalized, set()) or set())
    )
    merge = _optional_call(sheet, "getCellMerge", normalized, (normalized, 1, 1))
    try:
        merge_anchor, merge_rows, merge_columns = merge
        merge_state = {
            "anchor": normalize_cell_address(merge_anchor),
            "rows": int(merge_rows),
            "columns": int(merge_columns),
        }
    except Exception as exc:
        raise NativeParametersStateError(
            f"Spreadsheet merge state for {normalized} is malformed."
        ) from exc
    exact = {
        "address": normalized,
        "contents": contents,
        "evaluated": evaluated,
        "alias": alias or None,
        "formula_references": _formula_references(contents),
        "formula_error": evaluation_error,
        "styles": styles,
        "alignment": alignment,
        "display_unit": _text(
            _optional_call(sheet, "getDisplayUnit", normalized, ""),
            128,
            "display unit",
        )
        or None,
        "foreground_rgb": _color(
            _optional_call(sheet, "getForeground", normalized, None)
        ),
        "background_rgb": _color(
            _optional_call(sheet, "getBackground", normalized, None)
        ),
        "merge": merge_state,
    }
    return {**exact, "cell_state_sha256": _digest(exact)}


def parameter_range_state(
    sheet: Any,
    range_value: Any,
    *,
    maximum_cells: int = MAX_PARAMETER_RANGE_CELLS,
) -> dict[str, Any]:
    normalized, addresses = normalize_range(range_value, maximum_cells=maximum_cells)
    cells = [parameter_cell_state(sheet, address) for address in addresses]
    exact = {
        "range": normalized,
        "cell_count": len(cells),
        "cells": cells,
    }
    return {**exact, "range_state_sha256": _digest(exact)}


def parameter_sheet_identity_state(sheet: Any) -> dict[str, Any]:
    document = getattr(sheet, "Document", None)
    if document is None or str(getattr(sheet, "TypeId", "")) != "Spreadsheet::Sheet":
        raise TypeError("sheet must be one live Spreadsheet::Sheet")
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    usable = bool(checker(sheet)) if callable(checker) else True
    exact = {
        "object_name": str(sheet.Name),
        "label": _text(sheet.Label, 160, "label"),
        "type_id": "Spreadsheet::Sheet",
        "timeline_role": str(getattr(sheet, "VibeCADTimelineRole", "") or ""),
        "timeline_owner_name": str(
            getattr(getattr(sheet, "VibeCADTimelineOwner", None), "Name", "") or ""
        )
        or None,
        "timeline_usable": usable,
    }
    return {**exact, "state_sha256": _digest(exact)}


def parameter_sheet_content_state(sheet: Any) -> dict[str, Any]:
    try:
        addresses = sorted(
            (normalize_cell_address(value) for value in tuple(sheet.getNonEmptyCells() or ())),
            key=cell_coordinates,
        )
    except Exception as exc:
        raise NativeParametersStateError("Spreadsheet contents could not be enumerated.") from exc
    if len(addresses) > MAX_PARAMETER_CELLS:
        raise NativeParametersStateError(
            f"Spreadsheet contents exceed the {MAX_PARAMETER_CELLS}-cell exact-state bound.",
            error_code="NATIVE_PARAMETERS_SHEET_TOO_LARGE",
        )
    contents = [
        {
            "address": address,
            "contents": _text(
                sheet.getContents(address),
                MAX_PARAMETER_CELL_CONTENT_CHARACTERS,
                "cell contents",
            ),
        }
        for address in addresses
    ]
    exact = {
        "non_empty_cell_count": len(contents),
        "used_range": envelope_range(addresses) if addresses else None,
        "contents": contents,
    }
    return {**exact, "content_state_sha256": _digest(exact)}


def parameter_sheet_summary(sheet: Any) -> dict[str, Any]:
    identity = parameter_sheet_identity_state(sheet)
    content = parameter_sheet_content_state(sheet)
    aliases = []
    for item in content["contents"]:
        alias = _optional_call(sheet, "getAlias", item["address"], "")
        if alias:
            aliases.append({"cell": item["address"], "alias": _text(alias, 160, "alias")})
            if len(aliases) >= 48:
                break
    return {
        **identity,
        "content_state_sha256": content["content_state_sha256"],
        "non_empty_cell_count": content["non_empty_cell_count"],
        "used_range": content["used_range"],
        "aliases": aliases,
    }
