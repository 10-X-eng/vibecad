# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Parameters ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeParametersState import (
    NativeParametersStateError,
    parameter_sheet_summary,
)
from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_SHEETS = 24


def _sheet_summary(sheet: Any) -> dict[str, Any]:
    try:
        return parameter_sheet_summary(sheet)
    except NativeParametersStateError as exc:
        result = concise_object(sheet)
        result["state_error"] = str(exc)
        result["state_error_code"] = exc.error_code
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
