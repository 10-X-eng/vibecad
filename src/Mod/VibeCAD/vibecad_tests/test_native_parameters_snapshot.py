# SPDX-License-Identifier: LGPL-2.1-or-later
"""Generated spreadsheet subclasses must not abort a Parameters handoff."""

import pytest

from vibecad_tests.test_native_snapshot import _document
from VibeCADNativeParameters import NativeParametersError, resolve_exact_parameter_sheet
from VibeCADNativeParametersSnapshot import build_parameters_snapshot
from VibeCADNativeParametersState import parameter_sheet_identity_state, parameter_sheet_summary


def _bom(document):
    sheet = document.getObject("Parameters")
    sheet.TypeId = "Assembly::BomObject"
    sheet.isDerivedFrom = lambda name: name in ("Assembly::BomObject", "Spreadsheet::Sheet")
    return sheet


def test_parameters_context_reads_generated_bom_without_authorizing_cell_edits():
    document = _document()
    bom = _bom(document)
    result = build_parameters_snapshot(document)
    assert result["counts"]["spreadsheets"] == 1
    summary = result["spreadsheets"][0]
    assert summary["object_name"] == bom.Name
    assert summary["type_id"] == "Assembly::BomObject"
    assert summary["non_empty_cell_count"] == 2
    assert summary["used_range"] == "A1:B2"
    assert summary["parameters_editable"] is False
    assert "Assembly" in summary["edit_guidance"]
    with pytest.raises(NativeParametersError):
        resolve_exact_parameter_sheet(document, {
            "object_name": bom.Name, "expected_state_sha256": "0" * 64})
    with pytest.raises(TypeError, match="one live"):
        parameter_sheet_identity_state(bom)


def test_bad_generated_sheet_contents_are_reported_without_losing_other_sheets():
    document = _document()
    bom = _bom(document)
    def unreadable():
        raise RuntimeError("failed cells")
    bom.getNonEmptyCells = unreadable
    normal = document.add("Other", "Spreadsheet::Sheet")
    normal.getNonEmptyCells = lambda: []
    result = build_parameters_snapshot(document)
    assert result["counts"]["spreadsheets"] == 2
    assert result["spreadsheets"][0]["state_error_code"] == "NATIVE_PARAMETERS_STATE_INVALID"
    assert result["spreadsheets"][1]["non_empty_cell_count"] == 0


def test_ordinary_spreadsheet_summary_and_identity_are_unchanged():
    document = _document()
    sheet = document.getObject("Parameters")
    expected = parameter_sheet_summary(sheet)
    summary = build_parameters_snapshot(document)["spreadsheets"][0]
    assert summary == expected
    resolved, identity = resolve_exact_parameter_sheet(document, {
        "object_name": sheet.Name, "expected_state_sha256": summary["state_sha256"]})
    assert resolved is sheet
    assert identity == parameter_sheet_identity_state(sheet)


def test_other_spreadsheet_subclasses_are_not_mislabeled_as_assembly_boms():
    document = _document()
    sheet = _bom(document)
    sheet.TypeId = "Spreadsheet::SheetPython"
    summary = build_parameters_snapshot(document)["spreadsheets"][0]
    assert summary["type_id"] == "Spreadsheet::SheetPython"
    assert summary["parameters_editable"] is False
    assert "Assembly" not in summary["edit_guidance"]
    assert summary["non_empty_cell_count"] == 2
