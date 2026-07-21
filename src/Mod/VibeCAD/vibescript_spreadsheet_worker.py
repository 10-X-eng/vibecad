# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native Spreadsheet evaluator for production VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_spreadsheet_api import SpreadsheetDomainAPI


VALIDATION_SCHEMA = "vibecad-vibescript-spreadsheet-validation-v1"
READBACK_SAMPLE_LIMIT = 128
EVALUATED_SAMPLE_LIMIT = 128
_CELL_ADDRESS = re.compile(r"^([A-Z]{1,2})([1-9][0-9]{0,4})$")
_MAX_CELLS = 10_000
_GRAPH_FIELDS = {"domain", "operation", "output_type", "arguments", "properties"}
_CELL_PROPERTIES = {
    "value",
    "expression",
    "alias",
    "unit",
    "display_unit",
    "style",
    "alignment",
    "foreground",
    "background",
}
_RANGE_PROPERTIES = {"area", "style", "alignment", "foreground", "background"}
_SHEET_PROPERTIES = {
    "range_styles",
    "merged_ranges",
    "column_widths",
    "row_heights",
    "label",
}


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded repair instruction for every worker-visible failure stage."""

    stage = str(details.get("stage") or "").strip()
    path = str(details.get("path") or "").strip()
    target = str(details.get("target") or details.get("cell_address") or "").strip()
    if stage == "graph_contract":
        location = f" at {path}" if path else ""
        return (
            f"Rebuild only the Spreadsheet definition{location} with api.cell, "
            "api.range_style, or api.sheet as indicated; do not construct or edit serialized "
            "graph dictionaries directly."
        )
    if stage == "result_contract":
        return (
            "Return the declared result name with an api.sheet value and keep "
            "expected_outputs unchanged."
        )
    if stage == "readback_contract":
        location = f" {target!r}" if target else ""
        return (
            f"Regenerate the bounded Spreadsheet range{location} through api.sheet; "
            "do not bypass address normalization or the 10000-cell batch limit."
        )
    if stage == "native_object_creation":
        return (
            "Keep the Spreadsheet source unchanged and retry only after the isolated "
            "FreeCAD worker can create Spreadsheet::Sheet objects."
        )
    if stage == "native_recompute":
        return (
            "Change only the failing formula or alias dependency: use valid same-sheet "
            "cell/alias references, compatible units, and remove every dependency cycle."
        )
    if stage == "formula_evaluation":
        location = f" {target}" if target else ""
        return (
            f"Correct only formula cell{location}: use existing same-sheet cells or aliases, "
            "compatible units, and a finite acyclic result."
        )
    if target:
        return (
            f"Correct only Spreadsheet target {target!r} for stage {stage or 'native_application'} "
            "and retry the failed working revision; the accepted sheet remains live."
        )
    return (
        "Correct only the reported Spreadsheet definition field and retry the failed working "
        "revision; do not recreate the program or change unrelated cells."
    )


class SpreadsheetCandidateError(RuntimeError):
    """A model-facing Spreadsheet failure with exact corrective details."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        if not str(self.details.get("correction") or "").strip():
            self.details["correction"] = _default_correction(self.details)
        super().__init__(message)


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _payload(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise SpreadsheetCandidateError(
            f"{context} must be a value returned by the active Spreadsheet api.",
            details={"stage": "graph_contract", "path": context},
        )
    if set(payload) != _GRAPH_FIELDS:
        raise SpreadsheetCandidateError(
            f"{context} has malformed Spreadsheet graph fields.",
            details={
                "stage": "graph_contract",
                "path": context,
                "missing": sorted(_GRAPH_FIELDS - set(payload)),
                "unexpected": sorted(set(payload) - _GRAPH_FIELDS),
            },
        )
    if not isinstance(payload.get("arguments"), list) or not isinstance(
        payload.get("properties"), dict
    ):
        raise SpreadsheetCandidateError(
            f"{context} arguments and properties must be serialized containers.",
            details={"stage": "graph_contract", "path": context},
        )
    return payload


def _expect_graph(
    raw: Any,
    *,
    operation: str,
    output_type: str,
    property_names: set[str],
    argument_count: int,
    context: str,
) -> dict[str, Any]:
    payload = _payload(raw, context=context)
    observed = (
        str(payload.get("domain") or ""),
        str(payload.get("operation") or ""),
        str(payload.get("output_type") or ""),
    )
    expected = ("spreadsheet", operation, output_type)
    if observed != expected:
        raise SpreadsheetCandidateError(
            f"{context} must be a Spreadsheet {operation} value, not {observed!r}.",
            details={
                "stage": "graph_contract",
                "path": context,
                "expected": list(expected),
                "observed": list(observed),
            },
        )
    arguments = list(payload["arguments"])
    properties = dict(payload["properties"])
    if len(arguments) != argument_count or set(properties) != property_names:
        raise SpreadsheetCandidateError(
            f"{context} does not match the exact api.{operation} schema.",
            details={
                "stage": "graph_contract",
                "path": context,
                "expected_argument_count": argument_count,
                "received_argument_count": len(arguments),
                "missing_properties": sorted(property_names - set(properties)),
                "unexpected_properties": sorted(set(properties) - property_names),
            },
        )
    return payload


def _first_difference(expected: Any, observed: Any, path: str = "definition") -> str:
    if type(expected) is not type(observed):
        return f"{path} has type {type(observed).__name__}, expected {type(expected).__name__}"
    if isinstance(expected, dict):
        if set(expected) != set(observed):
            return f"{path} has different fields"
        for key in expected:
            if expected[key] != observed[key]:
                return _first_difference(expected[key], observed[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(expected) != len(observed):
            return f"{path} has {len(observed)} items, expected {len(expected)}"
        for index, (left, right) in enumerate(zip(expected, observed)):
            if left != right:
                return _first_difference(left, right, f"{path}[{index}]")
    return f"{path} is not in canonical form"


def validate_spreadsheet_definition(value: Any, *, context: str) -> dict[str, Any]:
    """Reconstruct a definition through the explicit API and require byte-equivalent form."""

    payload = _expect_graph(
        _payload(value, context=context),
        operation="sheet",
        output_type="sheet",
        property_names=_SHEET_PROPERTIES,
        argument_count=1,
        context=context,
    )
    raw_cells = payload["arguments"][0]
    if not isinstance(raw_cells, list):
        raise SpreadsheetCandidateError(
            f"{context}.arguments[0] must be a list of api.cell values.",
            details={"stage": "graph_contract", "path": f"{context}.arguments[0]"},
        )
    properties = dict(payload["properties"])
    raw_ranges = properties["range_styles"]
    if not isinstance(raw_ranges, list):
        raise SpreadsheetCandidateError(
            f"{context}.properties.range_styles must be a list.",
            details={
                "stage": "graph_contract",
                "path": f"{context}.properties.range_styles",
            },
        )
    api = SpreadsheetDomainAPI(("sheet", "cell", "range_style"), ("sheet",))
    cells: list[DomainValue] = []
    for index, raw in enumerate(raw_cells):
        path = f"{context}.cells[{index}]"
        cell = _expect_graph(
            raw,
            operation="cell",
            output_type="cell",
            property_names=_CELL_PROPERTIES,
            argument_count=1,
            context=path,
        )
        args = list(cell["arguments"])
        props = dict(cell["properties"])
        try:
            cells.append(
                api.cell(
                    args[0],
                    props["value"],
                    expression=props["expression"],
                    alias=props["alias"],
                    unit=props["unit"],
                    display_unit=props["display_unit"],
                    style=props["style"],
                    alignment=props["alignment"],
                    foreground=props["foreground"],
                    background=props["background"],
                )
            )
        except (TypeError, ValueError) as exc:
            raise SpreadsheetCandidateError(
                f"{path} is invalid: {exc}",
                details={
                    "stage": "graph_contract",
                    "path": path,
                    "correction": "Construct the cell with api.cell using the reported field contract.",
                },
            ) from exc
    ranges: list[DomainValue] = []
    for index, raw in enumerate(raw_ranges):
        path = f"{context}.range_styles[{index}]"
        style = _expect_graph(
            raw,
            operation="range_style",
            output_type="range_style",
            property_names=_RANGE_PROPERTIES,
            argument_count=1,
            context=path,
        )
        args = list(style["arguments"])
        props = dict(style["properties"])
        try:
            rebuilt = api.range_style(
                args[0],
                style=props["style"],
                alignment=props["alignment"],
                foreground=props["foreground"],
                background=props["background"],
            )
        except (TypeError, ValueError) as exc:
            raise SpreadsheetCandidateError(
                f"{path} is invalid: {exc}",
                details={
                    "stage": "graph_contract",
                    "path": path,
                    "correction": "Construct the range with api.range_style using a bounded A1 range.",
                },
            ) from exc
        if int(rebuilt.properties["area"]) != props["area"]:
            raise SpreadsheetCandidateError(
                f"{path}.area does not match its native cell range.",
                details={"stage": "graph_contract", "path": f"{path}.area"},
            )
        ranges.append(rebuilt)
    try:
        canonical = api.sheet(
            cells,
            range_styles=ranges,
            merged_ranges=properties["merged_ranges"],
            column_widths=properties["column_widths"],
            row_heights=properties["row_heights"],
            label=properties["label"],
        ).to_payload()
    except (TypeError, ValueError) as exc:
        raise SpreadsheetCandidateError(
            f"{context} is invalid: {exc}",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": "Build the batch with api.sheet from unique api.cell values.",
            },
        ) from exc
    if payload != canonical:
        raise SpreadsheetCandidateError(
            f"{context} is not in the canonical Spreadsheet API form: "
            f"{_first_difference(canonical, payload)}.",
            details={"stage": "graph_contract", "path": context},
        )
    return canonical


def definition_sha256(definition: Mapping[str, Any]) -> str:
    """Return the stable digest independently checked by the host validator."""

    return _json_sha256(definition)


def _cell_records(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "address": str(raw["arguments"][0]),
            **dict(raw["properties"]),
        }
        for raw in list(definition["arguments"])[0]
    ]


def _range_records(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "range_address": str(raw["arguments"][0]),
            **dict(raw["properties"]),
        }
        for raw in list(dict(definition["properties"])["range_styles"])
    ]


def _native_content(cell: Mapping[str, Any]) -> str | None:
    expression = cell.get("expression")
    value = cell.get("value")
    unit = str(cell.get("unit") or "")
    if expression is not None:
        return f"={expression}"
    if value is None:
        return None
    if isinstance(value, bool):
        return "=True" if value else "=False"
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = repr(value)
    else:
        text = str(value)
    return f"={text} {unit}" if unit else text


def _apply(
    stage: str,
    target: str,
    call: Any,
    *arguments: Any,
) -> None:
    try:
        call(*arguments)
    except Exception as exc:
        raise SpreadsheetCandidateError(
            f"Native Spreadsheet rejected {target!r} during {stage}: {exc}",
            details={
                "stage": stage,
                "target": target,
                "native_exception_type": type(exc).__name__,
                "native_error": str(exc),
                "correction": (
                    "Correct the reported cell, alias, formula, unit, format, or dimension; "
                    "the previously accepted sheet remains unchanged."
                ),
            },
        ) from exc


def _populate_sheet_without_recomputing(
    sheet: Any,
    definition: Mapping[str, Any],
    *,
    clear: bool = True,
) -> dict[str, int]:
    """Replay one validated batch without recompute, waits, geometry, or artifact I/O."""

    cells = _cell_records(definition)
    ranges = _range_records(definition)
    properties = dict(definition["properties"])
    if clear:
        _apply("clear_batch", str(getattr(sheet, "Name", "sheet")), sheet.clearAll)
    label = str(properties.get("label") or "")
    if label:
        sheet.Label = label

    # Literal text and unitless numbers cannot depend on aliases. Setting them
    # first makes every alias target exist before formulas are parsed.
    deferred: list[tuple[str, str]] = []
    for cell in cells:
        address = str(cell["address"])
        content = _native_content(cell)
        if content is None:
            continue
        if cell.get("expression") is not None or isinstance(cell.get("value"), bool) or cell.get("unit"):
            deferred.append((address, content))
        else:
            _apply("literal_content", address, sheet.set, address, content)

    for cell in cells:
        alias = str(cell.get("alias") or "")
        if alias:
            _apply("alias_assignment", str(cell["address"]), sheet.setAlias, str(cell["address"]), alias)

    for address, content in deferred:
        _apply("formula_or_quantity_content", address, sheet.set, address, content)

    merged_ranges = [str(value) for value in list(properties["merged_ranges"])]
    for range_address in merged_ranges:
        _apply("merge_cells", range_address, sheet.mergeCells, range_address)

    for style in ranges:
        target = str(style["range_address"])
        if style.get("style") is not None:
            _apply("range_style", target, sheet.setStyle, target, set(style["style"]), "replace")
        if style.get("alignment") is not None:
            _apply(
                "range_alignment",
                target,
                sheet.setAlignment,
                target,
                set(style["alignment"]),
                "replace",
            )
        if style.get("foreground") is not None:
            _apply("range_foreground", target, sheet.setForeground, target, tuple(style["foreground"]))
        if style.get("background") is not None:
            _apply("range_background", target, sheet.setBackground, target, tuple(style["background"]))

    for cell in cells:
        address = str(cell["address"])
        if cell.get("style") is not None:
            _apply("cell_style", address, sheet.setStyle, address, set(cell["style"]), "replace")
        if cell.get("alignment") is not None:
            _apply(
                "cell_alignment",
                address,
                sheet.setAlignment,
                address,
                set(cell["alignment"]),
                "replace",
            )
        if cell.get("foreground") is not None:
            _apply("cell_foreground", address, sheet.setForeground, address, tuple(cell["foreground"]))
        if cell.get("background") is not None:
            _apply("cell_background", address, sheet.setBackground, address, tuple(cell["background"]))
        display_unit = str(cell.get("display_unit") or "")
        if display_unit:
            _apply("display_unit", address, sheet.setDisplayUnit, address, display_unit)

    for column, width in dict(properties["column_widths"]).items():
        _apply("column_width", str(column), sheet.setColumnWidth, str(column), int(width))
    for row, height in dict(properties["row_heights"]).items():
        _apply("row_height", str(row), sheet.setRowHeight, str(row), int(height))
    return {
        "cell_count": len(cells),
        "range_style_count": len(ranges),
        "merged_range_count": len(merged_ranges),
        "column_width_count": len(dict(properties["column_widths"])),
        "row_height_count": len(dict(properties["row_heights"])),
    }


def populate_sheet_without_recomputing(
    sheet: Any,
    definition: Mapping[str, Any],
    *,
    clear: bool = True,
) -> dict[str, int]:
    """Replay a candidate batch without recomputing the live document."""

    return _populate_sheet_without_recomputing(sheet, definition, clear=clear)


def restore_sheet_without_recomputing(
    sheet: Any,
    accepted_definition: Mapping[str, Any],
) -> dict[str, int]:
    """Restore a previously accepted batch through an independent rollback entry point."""

    return _populate_sheet_without_recomputing(sheet, accepted_definition, clear=True)


def _column_number(label: str) -> int:
    value = 0
    for character in label:
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def _address_key(address: str) -> tuple[int, int]:
    match = _CELL_ADDRESS.fullmatch(address)
    if match is None:
        return (_MAX_CELLS + 1, _MAX_CELLS + 1)
    return (int(match.group(2)), _column_number(match.group(1)))


def range_addresses(range_address: str) -> list[str]:
    parts = range_address.split(":")
    first = _CELL_ADDRESS.fullmatch(parts[0])
    last = _CELL_ADDRESS.fullmatch(parts[-1])
    if first is None or last is None:
        raise SpreadsheetCandidateError(
            f"Validated range {range_address!r} is malformed.",
            details={"stage": "readback_contract", "target": range_address},
        )
    first_column = _column_number(first.group(1))
    last_column = _column_number(last.group(1))
    first_row = int(first.group(2))
    last_row = int(last.group(2))

    def column_label(number: int) -> str:
        result = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(ord("A") + remainder) + result
        return result

    return [
        f"{column_label(column)}{row}"
        for row in range(first_row, last_row + 1)
        for column in range(first_column, last_column + 1)
    ]


def merged_range_specs(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return canonical declarative merge records independently checked against native state."""

    properties = dict(definition["properties"])
    records: list[dict[str, Any]] = []
    for raw in list(properties.get("merged_ranges") or []):
        range_address = str(raw)
        first_raw, last_raw = range_address.split(":", 1)
        first = _CELL_ADDRESS.fullmatch(first_raw)
        last = _CELL_ADDRESS.fullmatch(last_raw)
        if first is None or last is None:
            raise SpreadsheetCandidateError(
                f"Validated merged range {range_address!r} is malformed.",
                details={"stage": "readback_contract", "target": range_address},
            )
        records.append(
            {
                "range_address": range_address,
                "anchor": first_raw,
                "rows": int(last.group(2)) - int(first.group(2)) + 1,
                "columns": _column_number(last.group(1))
                - _column_number(first.group(1))
                + 1,
            }
        )
    return records


def _native_merge_record(
    sheet: Any,
    address: str,
    *,
    range_address: str,
) -> tuple[str, int, int]:
    try:
        raw = sheet.getCellMerge(address)
    except Exception as exc:
        raise SpreadsheetCandidateError(
            f"Native Spreadsheet could not inspect merged range {range_address!r}: {exc}",
            details={
                "stage": "merge_readback",
                "target": range_address,
                "cell_address": address,
                "native_exception_type": type(exc).__name__,
                "native_error": str(exc),
                "correction": (
                    "Use non-overlapping canonical merged_ranges in api.sheet and retry; "
                    "the worker must provide native getCellMerge readback."
                ),
            },
        ) from exc
    if (
        not isinstance(raw, tuple)
        or len(raw) != 3
        or not isinstance(raw[0], str)
        or isinstance(raw[1], bool)
        or type(raw[1]) is not int
        or isinstance(raw[2], bool)
        or type(raw[2]) is not int
        or raw[1] < 1
        or raw[2] < 1
    ):
        raise SpreadsheetCandidateError(
            f"Native Spreadsheet returned malformed merge readback for {range_address!r}.",
            details={
                "stage": "merge_readback",
                "target": range_address,
                "observed": repr(raw)[:512],
            },
        )
    return str(raw[0]), int(raw[1]), int(raw[2])


def merged_range_readback(
    sheet: Any,
    definition: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Require every declared range to exist as the exact native merged span."""

    records = merged_range_specs(definition)
    for record in records:
        range_address = str(record["range_address"])
        expected = (
            str(record["anchor"]),
            int(record["rows"]),
            int(record["columns"]),
        )
        last = range_address.split(":", 1)[1]
        anchor_readback = _native_merge_record(
            sheet,
            str(record["anchor"]),
            range_address=range_address,
        )
        last_readback = _native_merge_record(
            sheet,
            last,
            range_address=range_address,
        )
        if anchor_readback != expected or last_readback != expected:
            raise SpreadsheetCandidateError(
                f"Native Spreadsheet did not retain merged range {range_address!r}.",
                details={
                    "stage": "merge_readback",
                    "target": range_address,
                    "expected": list(expected),
                    "anchor_observed": list(anchor_readback),
                    "last_observed": list(last_readback),
                    "correction": (
                        "Use disjoint merged_ranges with content only in each top-left anchor cell."
                    ),
                },
            )
    return records


def affected_addresses(definition: Mapping[str, Any]) -> list[str]:
    values = {str(cell["address"]) for cell in _cell_records(definition)}
    for style in _range_records(definition):
        values.update(range_addresses(str(style["range_address"])))
        if len(values) > _MAX_CELLS:
            raise SpreadsheetCandidateError(
                f"Spreadsheet batch affects more than {_MAX_CELLS} cells.",
                details={"stage": "readback_contract"},
            )
    return sorted(values, key=_address_key)


def _optional_call(call: Any, address: str, *, default: Any) -> Any:
    try:
        value = call(address)
    except (KeyError, ValueError):
        return default
    return default if value is None else value


def _color_readback(value: Any) -> list[float] | None:
    if value is None:
        return None
    channels = [round(float(channel), 8) for channel in tuple(value)[:4]]
    if len(channels) == 3:
        channels.append(1.0)
    return channels


def _used_range(sheet: Any) -> list[str]:
    raw = sheet.getUsedRange()
    if not isinstance(raw, tuple) or len(raw) != 2:
        return []
    values = [str(item) for item in raw]
    return values if all(_CELL_ADDRESS.fullmatch(item) for item in values) else []


def sheet_readback(sheet: Any, definition: Mapping[str, Any]) -> dict[str, Any]:
    """Capture deterministic assigned state; safe for worker and bounded publication use."""

    addresses = affected_addresses(definition)
    cells: list[dict[str, Any]] = []
    for address in addresses:
        styles = _optional_call(sheet.getStyle, address, default=None)
        alignments = _optional_call(sheet.getAlignment, address, default=None)
        cells.append(
            {
                "address": address,
                "contents": str(_optional_call(sheet.getContents, address, default="")),
                "alias": str(_optional_call(sheet.getAlias, address, default="")),
                "display_unit": str(_optional_call(sheet.getDisplayUnit, address, default="")),
                "style": sorted(str(item) for item in styles) if styles else [],
                "alignment": sorted(str(item) for item in alignments) if alignments else [],
                "foreground": _color_readback(
                    _optional_call(sheet.getForeground, address, default=None)
                ),
                "background": _color_readback(
                    _optional_call(sheet.getBackground, address, default=None)
                ),
            }
        )
    properties = dict(definition["properties"])
    merged_ranges = merged_range_readback(sheet, definition)
    readback = {
        "cells": cells,
        "column_widths": {
            str(column): int(sheet.getColumnWidth(str(column)))
            for column in dict(properties["column_widths"])
        },
        "row_heights": {
            str(row): int(sheet.getRowHeight(str(row)))
            for row in dict(properties["row_heights"])
        },
        "used_range": _used_range(sheet),
        "nonempty_cells": sorted(
            (str(address) for address in list(sheet.getNonEmptyCells() or [])),
            key=_address_key,
        ),
        "merged_ranges": merged_ranges,
    }
    return {
        "sha256": _json_sha256(readback),
        "affected_cell_count": len(cells),
        "used_range": list(readback["used_range"]),
        "nonempty_cell_count": len(readback["nonempty_cells"]),
        "merged_ranges": merged_ranges,
        "sample": cells[:READBACK_SAMPLE_LIMIT],
        "sample_limit": READBACK_SAMPLE_LIMIT,
        "sample_truncated": len(cells) > READBACK_SAMPLE_LIMIT,
    }


def _evaluated_value(sheet: Any, address: str) -> dict[str, Any]:
    try:
        value = sheet.get(address)
    except Exception as exc:
        raise SpreadsheetCandidateError(
            f"Native Spreadsheet could not evaluate cell {address}: {exc}",
            details={
                "stage": "formula_evaluation",
                "cell_address": address,
                "native_exception_type": type(exc).__name__,
                "native_error": str(exc),
                "correction": "Correct the formula, aliases, units, or cyclic references.",
            },
        ) from exc
    if value is None or isinstance(value, (bool, int, str)):
        clean: Any = value
        value_type = type(value).__name__
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise SpreadsheetCandidateError(
                f"Cell {address} evaluated to a non-finite number.",
                details={"stage": "formula_evaluation", "cell_address": address},
            )
        clean = value
        value_type = "float"
    elif hasattr(value, "Value") and hasattr(value, "Unit"):
        numeric = float(value.Value)
        if not math.isfinite(numeric):
            raise SpreadsheetCandidateError(
                f"Cell {address} evaluated to a non-finite quantity.",
                details={"stage": "formula_evaluation", "cell_address": address},
            )
        clean = {"value": numeric, "unit": str(value.Unit)}
        value_type = "quantity"
    else:
        text = str(value)
        if len(text) > 4_096:
            text = text[:4_096]
        clean = text
        value_type = type(value).__name__
    return {"address": address, "type": value_type, "value": clean}


def validate_and_build_spreadsheets(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply, recompute, inspect, and serialize real native Spreadsheet sheets."""

    definitions: list[tuple[str, dict[str, Any]]] = []
    for expected in expected_outputs:
        name = str(expected.get("name") or "")
        definition = validate_spreadsheet_definition(
            raw_result[name], context=f"result[{name!r}]"
        )
        if str(definition["output_type"]) != str(expected.get("type") or ""):
            raise SpreadsheetCandidateError(
                f"Output {name!r} returned {definition['output_type']!r}; expected 'sheet'.",
                details={"stage": "result_contract", "output_name": name},
            )
        definitions.append((name, definition))

    native: list[tuple[str, dict[str, Any], Any, dict[str, int]]] = []
    for index, (name, definition) in enumerate(definitions):
        try:
            sheet = document.addObject("Spreadsheet::Sheet", f"CandidateSheet{index + 1}")
        except Exception as exc:
            raise SpreadsheetCandidateError(
                f"Could not create isolated native sheet for output {name!r}: {exc}",
                details={
                    "stage": "native_object_creation",
                    "output_name": name,
                    "native_exception_type": type(exc).__name__,
                },
            ) from exc
        counts = populate_sheet_without_recomputing(sheet, definition, clear=False)
        native.append((name, definition, sheet, counts))

    try:
        recompute_result = document.recompute()
    except Exception as exc:
        raise SpreadsheetCandidateError(
            f"The isolated Spreadsheet document failed to recompute: {exc}",
            details={
                "stage": "native_recompute",
                "native_exception_type": type(exc).__name__,
                "native_error": str(exc),
                "correction": "Correct invalid or cyclic formulas before regenerating.",
            },
        ) from exc

    outputs: list[dict[str, Any]] = []
    global_outputs: list[dict[str, Any]] = []
    for name, definition, sheet, counts in native:
        state = sorted(str(item) for item in list(getattr(sheet, "State", []) or []))
        status = str(sheet.getStatusString())
        if "Invalid" in state or status != "Valid":
            raise SpreadsheetCandidateError(
                f"Native Spreadsheet rejected output {name!r}: state={state!r}, status={status!r}.",
                details={
                    "stage": "native_recompute",
                    "output_name": name,
                    "native_type": str(getattr(sheet, "TypeId", "")),
                    "native_state": state,
                    "native_status": status,
                    "correction": (
                        "Correct syntax, unit compatibility, missing aliases, external object "
                        "references, or cyclic formulas. Use only same-sheet references."
                    ),
                },
            )
        readback = sheet_readback(sheet, definition)
        cells = _cell_records(definition)
        evaluated_cells = [
            cell
            for cell in cells
            if cell.get("value") is not None or cell.get("expression") is not None
        ]
        evaluated = [
            _evaluated_value(sheet, str(cell["address"]))
            for cell in evaluated_cells[:EVALUATED_SAMPLE_LIMIT]
        ]
        validation = {
            "schema": VALIDATION_SCHEMA,
            "output_name": name,
            "native_type": str(getattr(sheet, "TypeId", "")),
            "definition_sha256": definition_sha256(definition),
            "readback_sha256": str(readback["sha256"]),
            "cell_count": counts["cell_count"],
            "formula_count": sum(cell.get("expression") is not None for cell in cells),
            "quantity_literal_count": sum(bool(cell.get("unit")) for cell in cells),
            "alias_count": sum(bool(cell.get("alias")) for cell in cells),
            "range_style_count": counts["range_style_count"],
            "merged_range_count": counts["merged_range_count"],
            "affected_cell_count": int(readback["affected_cell_count"]),
            "column_width_count": counts["column_width_count"],
            "row_height_count": counts["row_height_count"],
            "used_range": list(readback["used_range"]),
            "nonempty_cell_count": int(readback["nonempty_cell_count"]),
            "merged_ranges": list(readback["merged_ranges"]),
            "native_state": state,
            "native_status": status,
            "recompute_result": bool(recompute_result),
            "readback_sample": list(readback["sample"]),
            "readback_sample_limit": int(readback["sample_limit"]),
            "readback_sample_truncated": bool(readback["sample_truncated"]),
            "evaluated_sample": evaluated,
            "evaluated_sample_limit": EVALUATED_SAMPLE_LIMIT,
            "evaluated_sample_truncated": len(evaluated_cells) > EVALUATED_SAMPLE_LIMIT,
        }
        outputs.append(
            {
                "name": name,
                "type": "sheet",
                "definition": definition,
                "sheet_validation": validation,
            }
        )
        global_outputs.append(
            {
                "name": name,
                "type": "sheet",
                "native_type": validation["native_type"],
                "cell_count": validation["cell_count"],
                "formula_count": validation["formula_count"],
                "alias_count": validation["alias_count"],
                "merged_range_count": validation["merged_range_count"],
                "definition_sha256": validation["definition_sha256"],
                "readback_sha256": validation["readback_sha256"],
            }
        )
    return outputs, {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "native_object_count": len(native),
        "outputs": global_outputs,
    }
