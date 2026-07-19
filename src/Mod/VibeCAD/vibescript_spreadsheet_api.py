# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Spreadsheet VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_CELL_ADDRESS = re.compile(r"^([A-Z]{1,2})([1-9][0-9]{0,4})$")
_COLUMN = re.compile(r"^[A-Z]{1,2}$")
_ROW = re.compile(r"^[1-9][0-9]{0,4}$")
_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MAX_COLUMNS = 702  # A through ZZ, matching App::CellAddress.
_MAX_ROWS = 16_384
_MAX_CELLS = 10_000
_MAX_RANGE_STYLES = 256
_MAX_MERGED_RANGES = 256
_MAX_TEXT_CHARS = 16_384
_MAX_EXPRESSION_CHARS = 8_192
_MAX_UNIT_CHARS = 128
_MAX_CUSTOM_ROW_HEIGHTS = 4_096
_STYLES = ("bold", "italic", "underline")
_ALIGNMENTS = ("left", "center", "right", "top", "vcenter", "bottom")
_HORIZONTAL_ALIGNMENTS = frozenset({"left", "center", "right"})
_VERTICAL_ALIGNMENTS = frozenset({"top", "vcenter", "bottom"})
_MISSING = object()


class SpreadsheetAPIError(ValueError):
    """A source error carrying one exact repair target for the operating model."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        parameter: str,
        reason: str,
    ) -> None:
        self.details = {
            "stage": "source_validation",
            "operation": operation,
            "parameter": parameter,
            "reason": reason,
            "correction": (
                f"Correct api.{operation} parameter {parameter!r}: it {reason}. "
                "Change only the failing source expression, then retry against the "
                "failed working revision."
            ),
        }
        super().__init__(message)


def _error(
    operation: str,
    parameter: str,
    reason: str,
    value: Any = _MISSING,
) -> SpreadsheetAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return SpreadsheetAPIError(
        f"api.{operation}: {parameter} {reason}{suffix}.",
        operation=operation,
        parameter=parameter,
        reason=reason,
    )


def _column_number(label: str) -> int:
    value = 0
    for character in label:
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def _column_label(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _address(operation: str, parameter: str, value: Any) -> tuple[str, int, int]:
    if not isinstance(value, str):
        raise _error(operation, parameter, "must be a cell address from A1 through ZZ16384", value)
    clean = value.strip().upper()
    match = _CELL_ADDRESS.fullmatch(clean)
    if match is None:
        raise _error(operation, parameter, "must be a cell address from A1 through ZZ16384", value)
    column = _column_number(match.group(1))
    row = int(match.group(2))
    if column > _MAX_COLUMNS or row > _MAX_ROWS:
        raise _error(
            operation,
            parameter,
            "must be a cell address from A1 through ZZ16384",
            value,
        )
    return clean, column, row


def _range(
    operation: str,
    value: Any,
    *,
    parameter: str = "range_address",
) -> tuple[str, int]:
    if not isinstance(value, str):
        raise _error(operation, parameter, "must be A1 or A1:B2", value)
    parts = value.strip().split(":")
    if len(parts) not in {1, 2}:
        raise _error(operation, parameter, "must be A1 or A1:B2", value)
    first, first_column, first_row = _address(operation, parameter, parts[0])
    if len(parts) == 1:
        return first, 1
    last, last_column, last_row = _address(operation, parameter, parts[1])
    minimum_column, maximum_column = sorted((first_column, last_column))
    minimum_row, maximum_row = sorted((first_row, last_row))
    start = f"{_column_label(minimum_column)}{minimum_row}"
    end = f"{_column_label(maximum_column)}{maximum_row}"
    area = (maximum_column - minimum_column + 1) * (maximum_row - minimum_row + 1)
    return (start if start == end else f"{start}:{end}"), area


def _range_bounds(range_address: str) -> tuple[int, int, int, int]:
    first, *rest = range_address.split(":")
    last = rest[0] if rest else first
    first_match = _CELL_ADDRESS.fullmatch(first)
    last_match = _CELL_ADDRESS.fullmatch(last)
    if first_match is None or last_match is None:  # pragma: no cover - canonical internal value
        raise RuntimeError("A canonical Spreadsheet range became malformed.")
    return (
        _column_number(first_match.group(1)),
        int(first_match.group(2)),
        _column_number(last_match.group(1)),
        int(last_match.group(2)),
    )


def _merged_ranges(value: Any) -> tuple[tuple[str, ...], int]:
    if not isinstance(value, (list, tuple)):
        raise _error("sheet", "merged_ranges", "must be a sequence of rectangular ranges", value)
    if len(value) > _MAX_MERGED_RANGES:
        raise _error(
            "sheet",
            "merged_ranges",
            f"may contain at most {_MAX_MERGED_RANGES} ranges",
        )
    ranges: list[str] = []
    bounds: list[tuple[int, int, int, int]] = []
    affected = 0
    for index, raw in enumerate(value):
        parameter = f"merged_ranges[{index}]"
        clean, area = _range("sheet", raw, parameter=parameter)
        if area < 2:
            raise _error("sheet", parameter, "must span at least two cells", raw)
        if area > _MAX_CELLS:
            raise _error("sheet", parameter, f"may span at most {_MAX_CELLS} cells", raw)
        current = _range_bounds(clean)
        for prior_index, prior in enumerate(bounds):
            disjoint = (
                current[2] < prior[0]
                or prior[2] < current[0]
                or current[3] < prior[1]
                or prior[3] < current[1]
            )
            if not disjoint:
                raise _error(
                    "sheet",
                    parameter,
                    f"overlaps merged_ranges[{prior_index}] {ranges[prior_index]!r}",
                    raw,
                )
        ranges.append(clean)
        bounds.append(current)
        affected += area
    return tuple(ranges), affected


def _label(operation: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise _error(operation, "label", "must be a string of at most 256 characters", value)
    return value


def _tokens(
    operation: str,
    parameter: str,
    value: Any,
    *,
    allowed: Sequence[str],
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw = [item for item in re.split(r"[|,\s]+", value.strip()) if item]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw = list(value)
    else:
        raise _error(operation, parameter, f"must contain only {', '.join(allowed)}", value)
    normalized: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or item.strip().lower() not in allowed:
            raise _error(
                operation,
                f"{parameter}[{index}]",
                f"must be one of {list(allowed)!r}",
                item,
            )
        clean = item.strip().lower()
        if clean not in normalized:
            normalized.append(clean)
    return tuple(token for token in allowed if token in normalized)


def _style(operation: str, value: Any) -> tuple[str, ...] | None:
    return _tokens(operation, "style", value, allowed=_STYLES)


def _alignment(operation: str, value: Any) -> tuple[str, ...] | None:
    result = _tokens(operation, "alignment", value, allowed=_ALIGNMENTS)
    if result is None:
        return None
    horizontal = _HORIZONTAL_ALIGNMENTS.intersection(result)
    vertical = _VERTICAL_ALIGNMENTS.intersection(result)
    if len(horizontal) > 1:
        raise _error(operation, "alignment", "may contain at most one horizontal alignment", result)
    if len(vertical) > 1:
        raise _error(operation, "alignment", "may contain at most one vertical alignment", result)
    return result


def _color(operation: str, parameter: str, value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "must be RGB [r, g, b] with channels from 0 to 1", value)
    channels: list[float] = []
    for index, channel in enumerate(value):
        if isinstance(channel, bool) or not isinstance(channel, (int, float)):
            raise _error(operation, f"{parameter}[{index}]", "must be a finite number from 0 to 1", channel)
        clean = float(channel)
        if not math.isfinite(clean) or not 0.0 <= clean <= 1.0:
            raise _error(operation, f"{parameter}[{index}]", "must be in the inclusive range 0-1", channel)
        channels.append(clean)
    return tuple(channels)


def _alias(operation: str, value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _error(operation, "alias", "must be an identifier or an empty string", value)
    clean = value.strip()
    if clean and _ALIAS.fullmatch(clean) is None:
        raise _error(
            operation,
            "alias",
            "must start with a letter or underscore and contain at most 64 letters, digits, or underscores",
            value,
        )
    if clean and _CELL_ADDRESS.fullmatch(clean.upper()):
        raise _error(operation, "alias", "cannot be a cell address", value)
    return clean


def _unit(operation: str, parameter: str, value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > _MAX_UNIT_CHARS:
        raise _error(operation, parameter, f"must be a string of at most {_MAX_UNIT_CHARS} characters", value)
    clean = value.strip()
    if any(character in clean for character in "\r\n\0"):
        raise _error(operation, parameter, "cannot contain control characters", value)
    return clean


def _scalar(operation: str, value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            if len(value) > _MAX_TEXT_CHARS:
                raise _error(operation, "value", f"must contain at most {_MAX_TEXT_CHARS} characters")
            if value.startswith("="):
                raise _error(
                    operation,
                    "value",
                    "cannot start with '='; pass the formula with expression= instead",
                    value,
                )
            if "\0" in value:
                raise _error(operation, "value", "cannot contain a null character")
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error(operation, "value", "must be finite", value)
        return value
    raise _error(operation, "value", "must be a JSON scalar or None", value)


def _expression(operation: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _error(operation, "expression", "must be a formula string", value)
    clean = value.strip()
    if clean.startswith("="):
        clean = clean[1:].strip()
    if not clean:
        raise _error(operation, "expression", "cannot be empty", value)
    if len(clean) > _MAX_EXPRESSION_CHARS:
        raise _error(operation, "expression", f"must contain at most {_MAX_EXPRESSION_CHARS} characters")
    if "\0" in clean or "\n" in clean or "\r" in clean:
        raise _error(operation, "expression", "must be one line without null characters")
    return clean


def _integer_dimension(
    operation: str,
    parameter: str,
    value: Any,
    *,
    maximum: int,
) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise _error(operation, parameter, "must be an integer", value)
    if not 1 <= value <= maximum:
        raise _error(operation, parameter, f"must be in the inclusive range 1-{maximum}", value)
    return value


class SpreadsheetDomainAPI:
    """Explicit batch API injected into Spreadsheet VibeScript source."""

    __slots__ = ()

    domain = "spreadsheet"
    exported_names = ("sheet", "cell", "range_style")

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared = tuple(dict.fromkeys(str(item) for item in exports))
        if declared != self.exported_names:
            raise RuntimeError(
                "Spreadsheet pack exports do not match the production runtime contract: "
                f"expected {self.exported_names!r}, received {declared!r}."
            )
        if tuple(dict.fromkeys(str(item) for item in output_types)) != ("sheet",):
            raise RuntimeError("Spreadsheet pack must publish exactly the native sheet output type.")

    @staticmethod
    def _value(operation: str, output_type: str, *arguments: Any, **properties: Any) -> DomainValue:
        return DomainValue(
            domain="spreadsheet",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def cell(
        self,
        address: str,
        value: str | int | float | bool | None = None,
        *,
        expression: str | None = None,
        alias: str = "",
        unit: str = "",
        display_unit: str = "",
        style: str | Sequence[str] | None = None,
        alignment: str | Sequence[str] | None = None,
        foreground: Sequence[float] | None = None,
        background: Sequence[float] | None = None,
    ) -> DomainValue:
        """Define one cell for api.sheet; use expression= for formulas, never value='=...'."""

        clean_address, _column, _row = _address("cell", "address", address)
        clean_value = _scalar("cell", value)
        clean_expression = _expression("cell", expression)
        clean_unit = _unit("cell", "unit", unit)
        clean_display_unit = _unit("cell", "display_unit", display_unit)
        if clean_expression is not None and value is not None:
            raise _error("cell", "value/expression", "are mutually exclusive")
        if clean_unit and (clean_expression is not None or isinstance(clean_value, (str, bool)) or clean_value is None):
            raise _error("cell", "unit", "is allowed only with a numeric literal value", unit)
        return self._value(
            "cell",
            "cell",
            clean_address,
            value=clean_value,
            expression=clean_expression,
            alias=_alias("cell", alias),
            unit=clean_unit,
            display_unit=clean_display_unit,
            style=_style("cell", style),
            alignment=_alignment("cell", alignment),
            foreground=_color("cell", "foreground", foreground),
            background=_color("cell", "background", background),
        )

    def range_style(
        self,
        range_address: str,
        *,
        style: str | Sequence[str] | None = None,
        alignment: str | Sequence[str] | None = None,
        foreground: Sequence[float] | None = None,
        background: Sequence[float] | None = None,
    ) -> DomainValue:
        """Define shared formatting for one bounded range consumed by api.sheet."""

        clean_range, area = _range("range_style", range_address)
        clean_style = _style("range_style", style)
        clean_alignment = _alignment("range_style", alignment)
        clean_foreground = _color("range_style", "foreground", foreground)
        clean_background = _color("range_style", "background", background)
        if all(
            value is None
            for value in (clean_style, clean_alignment, clean_foreground, clean_background)
        ):
            raise _error("range_style", "formatting", "must specify at least one formatting property")
        if area > _MAX_CELLS:
            raise _error(
                "range_style",
                "range_address",
                f"may affect at most {_MAX_CELLS} cells, not {area}",
                range_address,
            )
        return self._value(
            "range_style",
            "range_style",
            clean_range,
            area=area,
            style=clean_style,
            alignment=clean_alignment,
            foreground=clean_foreground,
            background=clean_background,
        )

    def _legacy_cell(self, address: str, raw: Any) -> DomainValue:
        """Normalize the unavailable v2 scaffold's mapping form without weakening validation."""

        if not isinstance(raw, Mapping):
            return self.cell(address, raw)
        allowed = {
            "value",
            "content",
            "expression",
            "alias",
            "unit",
            "display_unit",
            "style",
            "alignment",
            "foreground",
            "background",
        }
        unknown = sorted(str(key) for key in set(raw) - allowed)
        if unknown:
            raise _error("sheet", f"cells[{address!r}]", f"contains unknown fields {unknown!r}")
        if "value" in raw and "content" in raw:
            raise _error("sheet", f"cells[{address!r}]", "cannot contain both value and content")
        return self.cell(
            address,
            raw.get("value", raw.get("content")),
            expression=raw.get("expression"),
            alias=raw.get("alias", ""),
            unit=raw.get("unit", ""),
            display_unit=raw.get("display_unit", ""),
            style=raw.get("style"),
            alignment=raw.get("alignment"),
            foreground=raw.get("foreground"),
            background=raw.get("background"),
        )

    def sheet(
        self,
        cells: Sequence[DomainValue] | Mapping[str, Any],
        *,
        range_styles: Sequence[DomainValue] = (),
        merged_ranges: Sequence[str] = (),
        column_widths: Mapping[str, int] | None = None,
        row_heights: Mapping[str | int, int] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Publish one atomic native sheet from api.cell and api.range_style definitions."""

        if isinstance(cells, Mapping):
            raw_cells = [self._legacy_cell(str(address), raw) for address, raw in cells.items()]
        elif isinstance(cells, (list, tuple)):
            raw_cells = list(cells)
        else:
            raise _error("sheet", "cells", "must be a sequence of api.cell values", cells)
        if len(raw_cells) > _MAX_CELLS:
            raise _error("sheet", "cells", f"may contain at most {_MAX_CELLS} definitions")
        clean_cells: list[DomainValue] = []
        addresses: set[str] = set()
        aliases: dict[str, str] = {}
        for index, value in enumerate(raw_cells):
            if not isinstance(value, DomainValue) or (
                value.domain,
                value.operation,
                value.output_type,
            ) != ("spreadsheet", "cell", "cell"):
                raise _error("sheet", f"cells[{index}]", "must be returned by api.cell", value)
            address = str(value.arguments[0])
            if address in addresses:
                raise _error("sheet", f"cells[{index}]", f"duplicates cell address {address!r}")
            addresses.add(address)
            alias = str(value.properties.get("alias") or "")
            if alias:
                key = alias.casefold()
                if key in aliases:
                    raise _error(
                        "sheet",
                        f"cells[{index}].alias",
                        f"duplicates alias {alias!r} already assigned to {aliases[key]}",
                    )
                aliases[key] = address
            clean_cells.append(value)
        if not isinstance(range_styles, (list, tuple)):
            raise _error("sheet", "range_styles", "must be a sequence of api.range_style values")
        if len(range_styles) > _MAX_RANGE_STYLES:
            raise _error("sheet", "range_styles", f"may contain at most {_MAX_RANGE_STYLES} definitions")
        clean_range_styles: list[DomainValue] = []
        affected = len(clean_cells)
        for index, value in enumerate(range_styles):
            if not isinstance(value, DomainValue) or (
                value.domain,
                value.operation,
                value.output_type,
            ) != ("spreadsheet", "range_style", "range_style"):
                raise _error(
                    "sheet",
                    f"range_styles[{index}]",
                    "must be returned by api.range_style",
                    value,
                )
            affected += int(value.properties["area"])
            if affected > _MAX_CELLS:
                raise _error(
                    "sheet",
                    "cells/range_styles",
                    f"may contain at most {_MAX_CELLS} total cell operations",
                    affected,
                )
            clean_range_styles.append(value)

        clean_merged_ranges, merged_cell_operations = _merged_ranges(merged_ranges)
        affected += merged_cell_operations
        if affected > _MAX_CELLS:
            raise _error(
                "sheet",
                "cells/range_styles/merged_ranges",
                f"may contain at most {_MAX_CELLS} total cell operations",
                affected,
            )
        merge_bounds = [_range_bounds(value) for value in clean_merged_ranges]
        for index, cell in enumerate(clean_cells):
            address = str(cell.arguments[0])
            _clean, column, row = _address("sheet", f"cells[{index}].address", address)
            for merge_index, bounds in enumerate(merge_bounds):
                if bounds[0] <= column <= bounds[2] and bounds[1] <= row <= bounds[3]:
                    anchor = clean_merged_ranges[merge_index].split(":", 1)[0]
                    if address != anchor:
                        raise _error(
                            "sheet",
                            f"cells[{index}]",
                            f"targets non-anchor {address!r} inside merged range "
                            f"{clean_merged_ranges[merge_index]!r}; define content and cell "
                            f"formatting only at anchor {anchor!r}",
                        )

        raw_widths = {} if column_widths is None else column_widths
        if not isinstance(raw_widths, Mapping):
            raise _error("sheet", "column_widths", "must be a mapping from A-ZZ to integer widths")
        clean_widths: dict[str, int] = {}
        for raw_column, raw_width in raw_widths.items():
            if not isinstance(raw_column, str) or _COLUMN.fullmatch(raw_column.strip().upper()) is None:
                raise _error("sheet", "column_widths key", "must be a column from A through ZZ", raw_column)
            column = raw_column.strip().upper()
            if _column_number(column) > _MAX_COLUMNS:
                raise _error("sheet", "column_widths key", "must be a column from A through ZZ", raw_column)
            if column in clean_widths:
                raise _error("sheet", "column_widths", f"duplicates column {column!r}")
            clean_widths[column] = _integer_dimension(
                "sheet", f"column_widths[{column!r}]", raw_width, maximum=10_000
            )
        clean_widths = dict(sorted(clean_widths.items(), key=lambda item: _column_number(item[0])))

        raw_heights = {} if row_heights is None else row_heights
        if not isinstance(raw_heights, Mapping):
            raise _error("sheet", "row_heights", "must be a mapping from row numbers to integer heights")
        if len(raw_heights) > _MAX_CUSTOM_ROW_HEIGHTS:
            raise _error("sheet", "row_heights", f"may contain at most {_MAX_CUSTOM_ROW_HEIGHTS} entries")
        clean_heights: dict[str, int] = {}
        for raw_row, raw_height in raw_heights.items():
            row = str(raw_row).strip()
            if _ROW.fullmatch(row) is None or int(row) > _MAX_ROWS:
                raise _error("sheet", "row_heights key", f"must be a row from 1 through {_MAX_ROWS}", raw_row)
            row = str(int(row))
            if row in clean_heights:
                raise _error("sheet", "row_heights", f"duplicates row {row!r}")
            clean_heights[row] = _integer_dimension(
                "sheet", f"row_heights[{row!r}]", raw_height, maximum=10_000
            )
        clean_heights = dict(sorted(clean_heights.items(), key=lambda item: int(item[0])))
        return self._value(
            "sheet",
            "sheet",
            tuple(clean_cells),
            range_styles=tuple(clean_range_styles),
            merged_ranges=clean_merged_ranges,
            column_widths=clean_widths,
            row_heights=clean_heights,
            label=_label("sheet", label),
        )
