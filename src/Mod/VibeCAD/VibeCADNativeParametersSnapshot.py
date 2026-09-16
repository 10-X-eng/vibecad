# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Parameters ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeParametersState import (
    NativeParametersStateError,
    parameter_sheet_content_state,
    parameter_sheet_summary,
)
from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_SHEETS = 24


def _sheet_summary(sheet: Any) -> dict[str, Any]:
    try:
        if str(sheet.TypeId) != "Spreadsheet::Sheet":
            # Discovery includes derived sheets, notably Assembly::BomObject.
            # Read their contents without granting generic cell-edit authority
            # over a generated table or misreporting its concrete type.
            content = parameter_sheet_content_state(sheet)
            return {
                **concise_object(sheet),
                "content_state_sha256": content["content_state_sha256"],
                "non_empty_cell_count": content["non_empty_cell_count"],
                "used_range": content["used_range"],
                "parameters_editable": False,
                "edit_guidance": (
                    "This is a generated Assembly bill of materials. Use Assembly BOM tools to update it."
                    if str(sheet.TypeId) == "Assembly::BomObject"
                    else "Use this derived spreadsheet's owning workbench to edit it."
                ),
            }
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
