# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded human-authorized CSV input and output for Parameters sheets."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import io
from pathlib import Path
import re
from typing import Any, Mapping

from VibeCADNativeInput import NativeInputArtifact, NativeInputRequest
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeOutput import NativeOutputRequest
from VibeCADNativeParameters import (
    NativeParametersError,
    parameter_cell_retains_input,
    resolve_exact_parameter_sheet,
)
from VibeCADNativeParametersState import (
    MAX_PARAMETER_CELL_CONTENT_CHARACTERS,
    MAX_PARAMETER_CELLS,
    cell_address,
    cell_coordinates,
    normalize_range,
    parameter_sheet_content_state,
    parameter_sheet_identity_state,
    parameter_sheet_summary,
)
from VibeCADNativeTargets import object_identity, read_current_selection


MAX_PARAMETER_CSV_BYTES = 32 * 1024 * 1024
_UNSAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass(frozen=True, slots=True)
class PreparedParametersCsvImport:
    artifact: NativeInputArtifact
    label: str
    rows: tuple[tuple[str, ...], ...]
    cells: tuple[tuple[str, str], ...]
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedParametersCsvExport:
    sheet: Any
    identity_state: Mapping[str, Any]
    content_state: Mapping[str, Any]
    selection_before: Mapping[str, Any]
    objects_before: tuple[Any, ...]
    undo_count_before: int
    transaction_id_before: int
    output_request: NativeOutputRequest
    csv_text: str


def _timeline(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _selection(document: Any) -> Mapping[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _transaction_id(document: Any) -> int:
    reader = getattr(document, "getBookedTransactionID", None)
    return int(reader() or 0) if callable(reader) else 0


def parameters_csv_input_request() -> NativeInputRequest:
    return NativeInputRequest(
        purpose="import_parameters_csv",
        title="Import Parameters CSV",
        allowed_suffixes=(".csv",),
        name_filter="CSV spreadsheets (*.csv)",
        maximum_bytes=MAX_PARAMETER_CSV_BYTES,
    )


def _parse_csv(value: bytes) -> tuple[tuple[str, ...], ...]:
    if b"\x00" in value:
        raise NativeParametersError(
            "The selected CSV contains a NUL byte.",
            error_code="NATIVE_PARAMETERS_IMPORT_INVALID",
        )
    try:
        text = value.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise NativeParametersError(
            "The selected CSV must be valid UTF-8 text.",
            error_code="NATIVE_PARAMETERS_IMPORT_INVALID",
        ) from exc
    try:
        parsed = tuple(
            tuple(str(field) for field in row)
            for row in csv.reader(io.StringIO(text, newline=""), strict=True)
        )
    except csv.Error as exc:
        raise NativeParametersError(
            f"The selected CSV is malformed: {str(exc).strip()}",
            error_code="NATIVE_PARAMETERS_IMPORT_INVALID",
        ) from exc
    while parsed and not parsed[-1]:
        parsed = parsed[:-1]
    if not parsed or max((len(row) for row in parsed), default=0) == 0:
        raise NativeParametersError(
            "The selected CSV contains no cells.",
            error_code="NATIVE_PARAMETERS_IMPORT_EMPTY",
        )
    width = max(len(row) for row in parsed)
    if len(parsed) * width > MAX_PARAMETER_CELLS:
        raise NativeParametersError(
            f"CSV import is limited to {MAX_PARAMETER_CELLS} cells.",
            error_code="NATIVE_PARAMETERS_IMPORT_TOO_LARGE",
        )
    if any(
        len(field) > MAX_PARAMETER_CELL_CONTENT_CHARACTERS
        for row in parsed
        for field in row
    ):
        raise NativeParametersError(
            "A CSV cell exceeds the 16384-character content bound.",
            error_code="NATIVE_PARAMETERS_IMPORT_TOO_LARGE",
        )
    return parsed


def prepare_parameters_csv_import(
    authorization: Any,
    request: NativeInputRequest,
    *,
    objects_before: tuple[Any, ...],
    timeline_before: tuple[Any, ...],
    selection_before: Mapping[str, Any],
    cancelled: Any,
    progress: Any,
) -> PreparedParametersCsvImport:
    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()
    progress(10, "Verifying selected Parameters CSV")
    artifact = authorization.claim(request)
    rows = _parse_csv(artifact.read_bytes(maximum_bytes=MAX_PARAMETER_CSV_BYTES))
    cells = tuple(
        (cell_address(row_index, column_index), field)
        for row_index, row in enumerate(rows, 1)
        for column_index, field in enumerate(row, 1)
        if field
    )
    label = Path(artifact.file_name).stem.strip()[:160] or "Imported Parameters"
    progress(85, "Parameters CSV verified")
    return PreparedParametersCsvImport(
        artifact=artifact,
        label=label,
        rows=rows,
        cells=cells,
        objects_before=objects_before,
        timeline_before=timeline_before,
        selection_before=dict(selection_before),
    )


def commit_parameters_csv_import(
    document: Any,
    prepared: PreparedParametersCsvImport,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedParametersCsvImport):
        raise TypeError("prepared must be a PreparedParametersCsvImport")
    if tuple(document.Objects) != prepared.objects_before or _timeline(document) != prepared.timeline_before:
        raise NativeParametersError(
            "The Parameters document changed while the CSV was being prepared.",
            error_code="NATIVE_PARAMETERS_IMPORT_STALE",
        )
    sheet = document.addObject(
        "Spreadsheet::Sheet",
        document.getUniqueObjectName("Spreadsheet"),
    )
    if sheet is None:
        raise NativeParametersError("The imported Parameters sheet could not be created.")
    sheet.Label = prepared.label
    for address, contents in prepared.cells:
        sheet.set(address, contents)
    import SpreadsheetGui

    SpreadsheetGui.publishCreatedSheet(sheet)
    created = tuple(
        object_identity(value)
        for value in document.Objects
        if value not in prepared.objects_before
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "sheet": sheet},
        recompute_targets=(sheet,),
        created=created,
    )


def verify_parameters_csv_import(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value
    prepared = value.get("prepared") if isinstance(value, Mapping) else None
    sheet = value.get("sheet") if isinstance(value, Mapping) else None
    if not isinstance(prepared, PreparedParametersCsvImport) or sheet is None:
        raise NativeParametersError("The imported Parameters sheet identity was lost.")
    new_objects = tuple(item for item in document.Objects if item not in prepared.objects_before)
    if (
        sheet not in new_objects
        or any(
            item is not sheet
            and str(getattr(item, "TypeId", "")) != "App::DocumentTimeline"
            for item in new_objects
        )
        or _timeline(document) != (*prepared.timeline_before, sheet)
        or _selection(document) != prepared.selection_before
        or str(getattr(sheet, "VibeCADTimelineRole", "") or "") != "operation"
    ):
        raise NativeParametersError(
            "CSV import changed objects, History, or selection outside its exact result."
        )
    actual = parameter_sheet_content_state(sheet)
    expected_addresses = tuple(address for address, _contents in prepared.cells)
    actual_addresses = tuple(item["address"] for item in actual["contents"])
    if actual_addresses != expected_addresses or any(
        not parameter_cell_retains_input(
            {
                "contents": sheet.getContents(address),
                "evaluated": sheet.get(address),
            },
            contents,
            formula=contents.startswith("="),
        )
        for address, contents in prepared.cells
    ):
        raise NativeParametersError(
            "The imported Parameters cells do not match the authorized CSV."
        )
    return {
        "operation": "import_csv",
        "sheet": parameter_sheet_summary(sheet),
        "input": prepared.artifact.summary(),
        "dimensions": {
            "rows": len(prepared.rows),
            "columns": max(len(row) for row in prepared.rows),
            "non_empty_cells": len(prepared.cells),
        },
    }


def _safe_csv_name(sheet: Any) -> str:
    label = str(getattr(sheet, "Label", "") or getattr(sheet, "Name", "") or "Parameters")
    stem = _UNSAFE_FILE_NAME.sub("_", label).strip(" ._")[:180] or "Parameters"
    return f"{stem}.csv"


def _csv_text(content_state: Mapping[str, Any]) -> str:
    used_range = content_state["used_range"]
    rows: list[list[str]]
    if used_range is None:
        rows = [[""]]
    else:
        _normalized, addresses = normalize_range(
            used_range,
            maximum_cells=MAX_PARAMETER_CELLS,
        )
        start_row, start_column = cell_coordinates(addresses[0])
        end_row, end_column = cell_coordinates(addresses[-1])
        rows = [
            ["" for _column in range(start_column, end_column + 1)]
            for _row in range(start_row, end_row + 1)
        ]
        for item in content_state["contents"]:
            row, column = cell_coordinates(item["address"])
            rows[row - start_row][column - start_column] = item["contents"]
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(rows)
    return stream.getvalue()


def prepare_parameters_csv_export(
    document: Any,
    sheet_target: Any,
) -> PreparedParametersCsvExport:
    sheet, identity = resolve_exact_parameter_sheet(document, sheet_target)
    content = parameter_sheet_content_state(sheet)
    return PreparedParametersCsvExport(
        sheet=sheet,
        identity_state=identity,
        content_state=content,
        selection_before=_selection(document),
        objects_before=tuple(document.Objects),
        undo_count_before=int(getattr(document, "UndoCount", 0) or 0),
        transaction_id_before=_transaction_id(document),
        output_request=NativeOutputRequest(
            purpose="export_parameters_csv",
            title="Export Parameters CSV",
            suggested_file_name=_safe_csv_name(sheet),
            allowed_suffixes=(".csv",),
            name_filter="CSV spreadsheets (*.csv)",
            maximum_bytes=MAX_PARAMETER_CSV_BYTES,
        ),
        csv_text=_csv_text(content),
    )


def verify_parameters_csv_export_source(
    document: Any,
    prepared: PreparedParametersCsvExport,
) -> None:
    if (
        tuple(document.Objects) != prepared.objects_before
        or _selection(document) != prepared.selection_before
        or int(getattr(document, "UndoCount", 0) or 0) != prepared.undo_count_before
        or _transaction_id(document) != prepared.transaction_id_before
        or parameter_sheet_identity_state(prepared.sheet) != prepared.identity_state
        or parameter_sheet_content_state(prepared.sheet) != prepared.content_state
    ):
        raise NativeParametersError(
            "The Parameters sheet changed while CSV output was being produced.",
            error_code="NATIVE_PARAMETERS_EXPORT_STALE",
        )


def write_parameters_csv(prepared: PreparedParametersCsvExport, path: str) -> None:
    Path(path).write_text(prepared.csv_text, encoding="utf-8", newline="")


def validate_parameters_csv(path: Path, expected_text: str) -> None:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise NativeParametersError("The generated Parameters CSV could not be read.") from exc
    if value != expected_text:
        raise NativeParametersError("The generated Parameters CSV content changed unexpectedly.")


def parameters_csv_export_source_summary(
    prepared: PreparedParametersCsvExport,
) -> dict[str, Any]:
    return {
        "sheet": {
            "object_name": prepared.identity_state["object_name"],
            "state_sha256": prepared.identity_state["state_sha256"],
            "content_state_sha256": prepared.content_state["content_state_sha256"],
        },
        "non_empty_cell_count": prepared.content_state["non_empty_cell_count"],
        "used_range": prepared.content_state["used_range"],
    }
