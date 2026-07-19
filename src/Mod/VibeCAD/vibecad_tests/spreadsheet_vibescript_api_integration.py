# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD production gate for the Spreadsheet VibeScript domain."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from vibescript_domain_api import create_domain_api  # noqa: E402
import vibescript_spreadsheet_worker as spreadsheet_worker  # noqa: E402
from vibescript_spreadsheet_worker import (  # noqa: E402
    SpreadsheetCandidateError,
    validate_and_build_spreadsheets,
)
from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    SpreadsheetDomainAdapter,
    accept_candidate,
    complete_inspection,
    execute_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    _spreadsheet_document_snapshot,
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
)


EXPORTS = ("sheet", "cell", "range_style")
OUTPUT_TYPES = ("sheet",)
EXPECTED_OUTPUTS = [{"name": "Parameters", "type": "sheet"}]


def _api():
    return create_domain_api("spreadsheet", EXPORTS, OUTPUT_TYPES)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected validation failure containing {fragment!r}.")


def _exercise_source_api() -> None:
    import inspect

    api = _api()
    assert api.exported_names == EXPORTS
    assert not hasattr(api, "output")
    for name in EXPORTS:
        signature = str(inspect.signature(getattr(api, name)))
        assert "*args" not in signature
        assert "**" not in signature
        assert inspect.getdoc(getattr(api, name))

    first = api.cell(
        "a1",
        10,
        unit="mm",
        alias="length",
        style="italic|bold",
        alignment="right vcenter",
        foreground=[0.1, 0.2, 0.3],
    )
    formula = api.cell("B1", expression="=length * 2", display_unit="cm")
    style = api.range_style("b2:a1", background=[0.9, 0.8, 0.7])
    sheet = api.sheet(
        [first, formula],
        range_styles=[style],
        column_widths={"b": 90, "a": 120},
        row_heights={2: 35, "1": 30},
        label="Parameters",
    )
    payload = sheet.to_payload()
    assert payload["arguments"][0][0]["arguments"] == ["A1"]
    assert payload["arguments"][0][0]["properties"]["style"] == ["bold", "italic"]
    assert payload["properties"]["range_styles"][0]["arguments"] == ["A1:B2"]
    assert list(payload["properties"]["column_widths"]) == ["A", "B"]
    assert list(payload["properties"]["row_heights"]) == ["1", "2"]
    merged = api.sheet(
        [api.cell("C1", "Merged title")],
        merged_ranges=["d2:c1"],
    ).to_payload()
    assert merged["properties"]["merged_ranges"] == ["C1:D2"]
    legacy = copy.deepcopy(payload)
    legacy["properties"].pop("merged_ranges")
    migrated = spreadsheet_worker.validate_spreadsheet_definition(
        legacy,
        context="legacy Spreadsheet definition",
    )
    assert migrated["properties"]["merged_ranges"] == []
    try:
        first.properties["alias"] = "changed"
    except TypeError:
        pass
    else:
        raise AssertionError("Spreadsheet graph values must be immutable.")

    _expect_error("A1 through ZZ16384", lambda: api.cell("AAA1", 1))
    _expect_error("A1 through ZZ16384", lambda: api.cell("A16385", 1))
    _expect_error("expression=", lambda: api.cell("A1", "=A2"))
    _expect_error("mutually exclusive", lambda: api.cell("A1", 2, expression="A2"))
    _expect_error("numeric literal", lambda: api.cell("A1", "two", unit="mm"))
    _expect_error("cell address", lambda: api.cell("A1", 2, alias="B2"))
    _expect_error("horizontal", lambda: api.cell("A1", 2, alignment="left|right"))
    _expect_error("inclusive range 0-1", lambda: api.cell("A1", 2, foreground=[1.2, 0, 0]))
    _expect_error("at least one", lambda: api.range_style("A1:B2"))
    _expect_error(
        "at most 10000",
        lambda: api.range_style("A1:ZZ16384", style="bold"),
    )
    _expect_error("duplicates cell address", lambda: api.sheet([api.cell("A1"), api.cell("a1")]))
    _expect_error(
        "duplicates alias",
        lambda: api.sheet([api.cell("A1", alias="Length"), api.cell("A2", alias="length")]),
    )
    _expect_error("unknown fields", lambda: api.sheet({"A1": {"value": 1, "magic": True}}))
    _expect_error("at least two cells", lambda: api.sheet([api.cell("A1")], merged_ranges=["A1"]))
    _expect_error(
        "overlaps",
        lambda: api.sheet([api.cell("A1")], merged_ranges=["A1:B2", "B2:C3"]),
    )
    _expect_error(
        "non-anchor",
        lambda: api.sheet([api.cell("B1", "data")], merged_ranges=["A1:B1"]),
    )

    pack = get_vibescript_pack("SpreadsheetWorkbench")
    assert pack is not None
    description = SpreadsheetDomainAdapter(pack).describe_api()
    assert description["api_contract"] == "vibecad-vibescript-spreadsheet-api-v1"
    assert [item["name"] for item in description["runtime_exports"]] == list(EXPORTS)
    assert "*args" not in json.dumps(description["runtime_exports"])
    assert description["native_limits"]["address_range"] == "A1:ZZ16384"
    assert "Spreadsheet::Sheet" in description["evaluation_model"]
    assert description["operation_selection"]["shared_rectangular_formatting"] == (
        "api.range_style"
    )
    assert "single best form" in description["redundancy_contract"][
        "no_set_cell_alias"
    ]
    assert "optional final layout state" in description["redundancy_contract"][
        "merge_is_sheet_state"
    ]
    assert "stable result names" in description["composition_contract"][
        "construction_order"
    ][0]
    assert "working_revision" in description["model_verification_contract"]["success"]
    assert "cannot switch workbench" in description["workbench_handoffs"]["rule"]
    assert len(json.dumps(description, sort_keys=True)) < 32_768
    assert description["recommended_patterns"]

    try:
        api.cell("A1", "=B1")
    except ValueError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "cell"
        assert exc.details["parameter"] == "value"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("A disguised formula did not return structured source guidance.")


def _exercise_isolated_native_batch() -> None:
    import FreeCAD as App

    api = _api()
    cells = [
        api.cell("A1", "Parameter", style="bold"),
        api.cell("B1", "Value", style="bold"),
        api.cell("A2", "Length"),
        api.cell(
            "B2",
            120,
            unit="mm",
            alias="length",
            display_unit="cm",
            foreground=[0.1, 0.2, 0.3],
        ),
        api.cell("A3", "Half"),
        api.cell("B3", expression="length / 2", display_unit="mm"),
        api.cell("A4", True),
        api.cell("B4", 0.125),
        api.cell("C1", "Merged title", style="bold"),
    ]
    value = api.sheet(
        cells,
        range_styles=[
            api.range_style("A1:B1", background=[0.8, 0.9, 1.0], alignment="center"),
            api.range_style("A2:B4", style="italic"),
        ],
        column_widths={"A": 140, "B": 100},
        row_heights={1: 36, 2: 32},
        merged_ranges=["C1:D2"],
        label="Native Parameters",
    )
    document = App.newDocument("SpreadsheetDirectWorker", "Spreadsheet Direct Worker", True, True)
    try:
        outputs, validation = validate_and_build_spreadsheets(
            document,
            {"Parameters": value},
            EXPECTED_OUTPUTS,
        )
        assert validation["schema"] == spreadsheet_worker.VALIDATION_SCHEMA
        assert validation["native_object_count"] == 1
        native = outputs[0]["sheet_validation"]
        assert native["native_type"] == "Spreadsheet::Sheet"
        assert native["cell_count"] == 9
        assert native["formula_count"] == 1
        assert native["alias_count"] == 1
        assert native["range_style_count"] == 2
        assert native["merged_range_count"] == 1
        assert native["merged_ranges"] == [
            {
                "range_address": "C1:D2",
                "anchor": "C1",
                "rows": 2,
                "columns": 2,
            }
        ]
        assert native["native_status"] == "Valid"
        readback = {item["address"]: item for item in native["readback_sample"]}
        assert readback["B2"]["contents"] == "=120 mm"
        evaluated = {item["address"]: item for item in native["evaluated_sample"]}
        assert evaluated["B2"]["type"] == "quantity"
        assert evaluated["B3"]["value"]["value"] == 60.0
        assert evaluated["A4"] == {"address": "A4", "type": "bool", "value": True}
        sheet = document.getObject("CandidateSheet1")
        assert sheet is not None
        assert sheet.getCellMerge("D2") == ("C1", 2, 2)
    finally:
        App.closeDocument(document.Name)

    # Native-only validation catches constraints that a static identifier
    # grammar cannot know, while retaining an exact stage and target.
    reserved = api.sheet([api.cell("A1", 1, alias="mA")])
    document = App.newDocument("SpreadsheetReservedAlias", "Reserved Alias", True, True)
    try:
        try:
            validate_and_build_spreadsheets(
                document,
                {"Parameters": reserved},
                EXPECTED_OUTPUTS,
            )
        except SpreadsheetCandidateError as exc:
            assert exc.details["stage"] == "alias_assignment"
            assert exc.details["target"] == "A1"
            assert "reserved" in str(exc).lower() or "alias" in str(exc).lower()
        else:
            raise AssertionError("A native reserved-unit alias was accepted.")
    finally:
        App.closeDocument(document.Name)

    cyclic = api.sheet(
        [
            api.cell("A1", expression="B1 + 1"),
            api.cell("B1", expression="A1 + 1"),
        ]
    )
    document = App.newDocument("SpreadsheetCycle", "Spreadsheet Cycle", True, True)
    try:
        try:
            validate_and_build_spreadsheets(document, {"Parameters": cyclic}, EXPECTED_OUTPUTS)
        except SpreadsheetCandidateError as exc:
            assert exc.details["stage"] == "native_recompute"
            assert "Invalid" in exc.details["native_state"]
            assert "cyclic" in exc.details["correction"]
        else:
            raise AssertionError("A cyclic native Spreadsheet candidate was accepted.")
    finally:
        App.closeDocument(document.Name)


class _Service:
    def __init__(self, document: Any, project_root: Path) -> None:
        self.document = document
        self.project_root = project_root

    def _active_document(self):
        return self.document

    @staticmethod
    def active_workbench_name() -> str:
        return "SpreadsheetWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "spreadsheet-production-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.project_root)}


def _input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "wheelbase": {"type": "number", "exclusiveMinimum": 0},
            "enabled": {"type": "boolean"},
            "note": {"type": "string", "maxLength": 128},
        },
        "required": ["wheelbase", "enabled", "note"],
        "additionalProperties": False,
    }


def _program_source() -> str:
    return (
        "cells = [\n"
        " api.cell('A1', 'Robot parameter', style='bold', alignment='center'),\n"
        " api.cell('B1', 'Value', style='bold', alignment='center'),\n"
        " api.cell('A2', 'Wheelbase'),\n"
        " api.cell('B2', inputs['wheelbase'], unit='mm', alias='wheelbase', "
        "display_unit='cm', foreground=[0.1,0.2,0.3], style='bold'),\n"
        " api.cell('A3', 'Half wheelbase'),\n"
        " api.cell('B3', expression='wheelbase / 2', alias='half_wheelbase', "
        "display_unit='mm', alignment='right|vcenter'),\n"
        " api.cell('A4', 'Enabled'),\n"
        " api.cell('B4', inputs['enabled']),\n"
        " api.cell('A5', inputs['note']),\n"
        " api.cell('B5', 0.125),\n"
        " api.cell('C1', 'Robot summary', style='bold'),\n"
        " api.cell('D2', 'Stable layout extent'),\n"
        "]\n"
        "header = api.range_style('A1:B1', background=[0.8,0.9,1.0], "
        "alignment='center|vcenter')\n"
        "body = api.range_style('A2:B5', background=[0.96,0.96,0.96])\n"
        "result = {'Parameters': api.sheet(cells, range_styles=[header, body], "
        "merged_ranges=['C1:D1'], "
        "column_widths={'A':150,'B':110}, row_heights={1:38,2:32}, "
        "label='Robot Parameters')}\n"
    )


def _reconfigured_source() -> str:
    return (
        "cells = [\n"
        " api.cell('A1', 'Robot dimensions', style='bold'),\n"
        " api.cell('A2', 'Wheelbase'),\n"
        " api.cell('B2', inputs['wheelbase'], unit='mm', alias='wheelbase'),\n"
        " api.cell('A3', 'Quarter wheelbase'),\n"
        " api.cell('B3', expression='wheelbase / 4', display_unit='mm'),\n"
        "]\n"
        "title = api.range_style('A1:B1', style='bold', background=[0.7,0.85,1.0])\n"
        "result = {'Parameters': api.sheet(cells, range_styles=[title], "
        "merged_ranges=['A1:B1'], "
        "column_widths={'A':160}, row_heights={1:40}, label='Robot Dimensions')}\n"
    )


def _base_capture(root: Path, document: Any) -> dict[str, Any]:
    import FreeCAD as App

    pack = get_vibescript_pack("SpreadsheetWorkbench")
    assert pack is not None and pack.production_ready
    surface = resolve_modeling_surface("SpreadsheetWorkbench", "vibescript")
    assert surface.available
    return {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "spreadsheet-production-revision",
        "document_objects": [
            {"name": str(obj.Name), "label": str(obj.Label), "type_id": str(obj.TypeId)}
            for obj in document.Objects
        ],
        "surface": surface.summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _create_capture(base: dict[str, Any]) -> dict[str, Any]:
    return {
        **base,
        "operation": "create_program",
        "tool_name": "vibescript.spreadsheet.create_program",
        "arguments": {
            "program_name": "Production Spreadsheet",
            "source": _program_source(),
            "input_schema": _input_schema(),
            "inputs": {"wheelbase": 120.0, "enabled": True, "note": "Production robot"},
            "expected_outputs": EXPECTED_OUTPUTS,
        },
    }


def _run_candidate(captured: dict[str, Any], service: _Service):
    prepared = prepare_candidate(captured)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


def _assert_live_sheet(document: Any, accepted: MappingLike, expected_wheelbase: float):
    object_name = accepted["live_outputs"]["Parameters"]["object_name"]
    sheet = document.getObject(object_name)
    assert sheet is not None and sheet.TypeId == "Spreadsheet::Sheet"
    assert PROP_PROGRAM_ID in sheet.PropertiesList
    assert "VibeCADSpreadsheetValidation" in sheet.PropertiesList
    assert sheet.getAlias("B2") == "wheelbase"
    assert sheet.getAlias("B3") == "half_wheelbase"
    assert sheet.getContents("B2") == f"={expected_wheelbase:g} mm"
    assert sheet.getContents("B3") == "=wheelbase / 2"
    assert sheet.getDisplayUnit("B2") == "cm"
    assert sheet.getDisplayUnit("B3") == "mm"
    assert sheet.getStyle("B2") == {"bold"}
    assert sheet.getAlignment("B3") == {"right", "vcenter"}
    assert sheet.getColumnWidth("A") == 150
    assert sheet.getColumnWidth("B") == 110
    assert sheet.getRowHeight("1") == 38
    assert sheet.getCellMerge("C1") == ("C1", 1, 2)
    assert sheet.getCellMerge("D1") == ("C1", 1, 2)
    document.recompute()
    assert abs(float(sheet.get("B3").Value) - expected_wheelbase / 2) < 1.0e-9
    return sheet, object_name


MappingLike = dict[str, Any]


def _exercise_lifecycle(root: Path) -> None:
    import FreeCAD as App

    document = App.newDocument("SpreadsheetProductionLifecycle")
    service = _Service(document, root)
    base = _base_capture(root, document)
    create_capture = _create_capture(base)
    prepared, execution, validated, publication, accepted = _run_candidate(
        create_capture, service
    )
    assert execution["spreadsheet_validation"]["native_object_count"] == 1
    assert publication["recompute_deferred"] is True
    assert publication["created_objects"]
    sheet, object_name = _assert_live_sheet(document, accepted, 120.0)
    original_sheet = sheet
    accepted_revision = prepared["revision"]

    inspection = complete_inspection(
        {**create_capture, "program_id": prepared["program_id"], "live_programs": []}
    )
    assert inspection["program"]["accepted_revision"] == accepted_revision

    # The host independently rejects internally inconsistent worker output.
    malformed = copy.deepcopy(execution)
    malformed["outputs"][0]["sheet_validation"]["cell_count"] += 1
    try:
        validate_candidate(prepared, malformed)
    except ValueError as exc:
        assert "cell_count" in str(exc)
    else:
        raise AssertionError("A malformed Spreadsheet worker summary was accepted.")
    false_readback = copy.deepcopy(execution)
    false_readback["outputs"][0]["sheet_validation"]["readback_sample"][0][
        "contents"
    ] = "'different model-facing value"
    try:
        validate_candidate(prepared, false_readback)
    except ValueError as exc:
        assert "contents" in str(exc)
    else:
        raise AssertionError("A false Spreadsheet content readback was accepted.")
    false_merge = copy.deepcopy(execution)
    false_merge["outputs"][0]["sheet_validation"]["merged_ranges"][0][
        "columns"
    ] = 3
    try:
        validate_candidate(prepared, false_merge)
    except ValueError as exc:
        assert "merged-range" in str(exc)
    else:
        raise AssertionError("A false Spreadsheet merge readback was accepted.")
    false_recompute = copy.deepcopy(execution)
    false_recompute["outputs"][0]["sheet_validation"]["recompute_result"] = False
    try:
        validate_candidate(prepared, false_recompute)
    except ValueError as exc:
        assert "recompute state" in str(exc)
    else:
        raise AssertionError("An unsuccessful Spreadsheet recompute was accepted.")
    assert validated["outputs"][0]["sheet_validation"]["native_status"] == "Valid"

    # A cyclic candidate remains inspectable while the accepted object and
    # content stay live and untouched.
    failed_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.spreadsheet.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "replacements": [
                {"old": "expression='wheelbase / 2'", "new": "expression='B3'"}
            ],
        },
    }
    failed_prepared = prepare_candidate(failed_capture)
    failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
    assert failed_execution.get("ok") is False, failed_execution
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    worker_details = failed_execution["observed"]["details"]
    assert worker_details["stage"] == "native_recompute"
    assert "Invalid" in worker_details["native_state"]
    assert failed_execution["domain_failure_stage"] == "native_recompute"
    assert failed_execution["retry"]["required_changes"] == [
        worker_details["correction"]
    ]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert document.getObject(object_name) is original_sheet
    assert original_sheet.getContents("B3") == "=wheelbase / 2"
    failed_inspection = complete_inspection(
        {**failed_capture, "program_id": prepared["program_id"], "live_programs": []}
    )
    assert failed_inspection["program"]["working_revision"] == failed_prepared["revision"]
    assert failed_inspection["program"]["accepted_revision"] == accepted_revision
    assert failed_inspection["program"]["latest_candidate"]["status"] == "failed"

    recovery_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.spreadsheet.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {"old": "expression='B3'", "new": "expression='wheelbase / 2'"}
            ],
        },
    }
    recovered_prepared, _execution, _validated, recovered_publication, accepted = (
        _run_candidate(recovery_capture, service)
    )
    assert recovered_publication["created_objects"] == []
    sheet, recovered_name = _assert_live_sheet(document, accepted, 120.0)
    assert sheet is original_sheet and recovered_name == object_name

    consumer = document.addObject("App::FeaturePython", "SpreadsheetConsumer")
    consumer.addProperty("App::PropertyLink", "SourceSheet", "Native")
    consumer.SourceSheet = sheet
    consumer.addProperty("App::PropertyLength", "DrivenLength", "Native")
    consumer.setExpression("DrivenLength", f"{sheet.Name}.wheelbase")
    document.recompute()
    assert abs(float(consumer.DrivenLength) - 120.0) < 1.0e-9

    update_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.spreadsheet.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": recovered_prepared["revision"],
            "patch": {"wheelbase": 130.0, "enabled": False},
        },
    }
    update_prepared, _execution, _validated, update_publication, accepted = _run_candidate(
        update_capture, service
    )
    assert update_publication["created_objects"] == []
    assert update_publication["downstream_references"]["safe_whole_object_uses"]
    sheet, _ = _assert_live_sheet(document, accepted, 130.0)
    assert sheet is original_sheet and consumer.SourceSheet is original_sheet
    document.recompute()
    assert abs(float(consumer.DrivenLength) - 130.0) < 1.0e-9

    # A user edit outside the accepted batch is detected before mutation. It
    # cannot be silently erased merely because a new candidate is valid.
    sheet.set("Z100", "human edit")
    drift_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.spreadsheet.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": update_prepared["revision"],
            "patch": {"wheelbase": 135.0},
        },
    }
    drift_prepared = prepare_candidate(drift_capture)
    drift_execution = execute_candidate(drift_prepared, cancellation_check=None)
    assert drift_execution.get("ok") is True, drift_execution
    drift_validated = validate_candidate(drift_prepared, drift_execution)
    retain_candidate(drift_prepared, status="validated")
    try:
        publish_candidate(service, drift_prepared, drift_validated)
    except RuntimeError as exc:
        assert "changed outside the accepted VibeScript revision" in str(exc)
    else:
        raise AssertionError("A human-edited live sheet was silently overwritten.")
    retain_candidate(
        drift_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "precondition",
            "error": "live Spreadsheet drift",
        },
    )
    assert sheet.getContents("B2") == "=130 mm"
    assert sheet.getContents("Z100") == "'human edit"
    sheet.clear("Z100")

    # Merged layout is part of the same protected accepted state, not merely
    # decoration that a later candidate may silently replace.
    sheet.splitCell("C1")
    assert sheet.getCellMerge("D1") == ("D1", 1, 1)
    merge_drift_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.spreadsheet.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": drift_prepared["revision"],
            "patch": {"wheelbase": 136.0},
        },
    }
    merge_drift_prepared = prepare_candidate(merge_drift_capture)
    merge_drift_execution = execute_candidate(
        merge_drift_prepared, cancellation_check=None
    )
    assert merge_drift_execution.get("ok") is True, merge_drift_execution
    merge_drift_validated = validate_candidate(
        merge_drift_prepared, merge_drift_execution
    )
    retain_candidate(merge_drift_prepared, status="validated")
    try:
        publish_candidate(service, merge_drift_prepared, merge_drift_validated)
    except RuntimeError as exc:
        assert "did not retain merged range 'C1:D1'" in str(exc), str(exc)
    else:
        raise AssertionError("A human-edited merged range was silently overwritten.")
    retain_candidate(
        merge_drift_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "precondition",
            "error": "live Spreadsheet merge drift",
        },
    )
    sheet.mergeCells("C1:D1")
    assert sheet.getCellMerge("D1") == ("C1", 1, 2)

    # Force failure after the complete live replay. The FreeCAD transaction
    # must restore every internal sheet cell, format, label, and revision.
    rollback_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.spreadsheet.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": merge_drift_prepared["revision"],
            "patch": {"wheelbase": 140.0},
        },
    }
    rollback_prepared = prepare_candidate(rollback_capture)
    rollback_execution = execute_candidate(rollback_prepared, cancellation_check=None)
    assert rollback_execution.get("ok") is True, rollback_execution
    rollback_validated = validate_candidate(rollback_prepared, rollback_execution)
    retain_candidate(rollback_prepared, status="validated")
    previous_revision = str(sheet.VibeCADVibeScriptRevision)
    previous_label = str(sheet.Label)
    previous_contents = str(sheet.getContents("B2"))
    original_populate = spreadsheet_worker.populate_sheet_without_recomputing

    def fail_after_replay(target, definition, *, clear=True):
        original_populate(target, definition, clear=clear)
        target.set("A1", "transaction should restore this")
        raise RuntimeError("injected Spreadsheet publication failure")

    spreadsheet_worker.populate_sheet_without_recomputing = fail_after_replay
    try:
        try:
            publish_candidate(service, rollback_prepared, rollback_validated)
        except RuntimeError as exc:
            assert "injected Spreadsheet publication failure" in str(exc)
        else:
            raise AssertionError("Injected Spreadsheet publication failure did not propagate.")
    finally:
        spreadsheet_worker.populate_sheet_without_recomputing = original_populate
    retain_candidate(
        rollback_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "injected Spreadsheet publication failure",
        },
    )
    assert sheet is document.getObject(object_name)
    assert sheet.getContents("B2") == previous_contents
    assert sheet.Label == previous_label
    assert sheet.VibeCADVibeScriptRevision == previous_revision
    assert sheet.getCellMerge("D1") == ("C1", 1, 2)
    assert consumer.SourceSheet is sheet

    reconfigure_capture = {
        **create_capture,
        "operation": "reconfigure_program",
        "tool_name": "vibescript.spreadsheet.reconfigure_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": rollback_prepared["revision"],
            "source": _reconfigured_source(),
            "input_schema": _input_schema(),
            "inputs": {"wheelbase": 160.0, "enabled": False, "note": "reconfigured"},
            "expected_outputs": EXPECTED_OUTPUTS,
        },
    }
    final_prepared, _execution, _validated, final_publication, accepted = _run_candidate(
        reconfigure_capture, service
    )
    assert final_publication["created_objects"] == []
    assert accepted["live_outputs"]["Parameters"]["object_name"] == object_name
    assert document.getObject(object_name) is original_sheet
    assert sheet.Label == "Robot Dimensions"
    assert sheet.getAlias("B2") == "wheelbase"
    assert sheet.getContents("B3") == "=wheelbase / 4"
    assert sheet.getContents("B5") == ""
    assert sheet.getColumnWidth("A") == 160
    assert sheet.getColumnWidth("B") == 100
    assert sheet.getRowHeight("1") == 40
    assert sheet.getRowHeight("2") == 30
    assert sheet.getCellMerge("A1") == ("A1", 1, 2)
    assert sheet.getCellMerge("B1") == ("A1", 1, 2)
    assert sheet.getCellMerge("C1") == ("C1", 1, 1)
    document.recompute()
    assert abs(float(sheet.get("B3").Value) - 40.0) < 1.0e-9
    assert abs(float(consumer.DrivenLength) - 160.0) < 1.0e-9

    snapshot = _spreadsheet_document_snapshot(document)
    assert snapshot["sheet_count"] == 1
    assert snapshot["sheets"][0]["name"] == object_name
    sampled = {item["address"]: item for item in snapshot["sheets"][0]["cells"]}
    assert sampled["B2"]["alias"] == "wheelbase"
    assert snapshot["sheets"][0]["merged_ranges"] == [
        {
            "range_address": "A1:B1",
            "anchor": "A1",
            "rows": 1,
            "columns": 2,
        }
    ]
    assert (
        snapshot["sheets"][0]["merged_ranges_source"]
        == "accepted_vibescript_validation"
    )
    context = complete_domain_context(domain_context_snapshot(service, "spreadsheet"))
    assert context["document_sheets"]["sheet_count"] == 1
    assert any(
        program.get("program_id") == prepared["program_id"]
        for program in context["programs"]
    )

    consumer.setExpression("DrivenLength", None)
    consumer.SourceSheet = None
    document.removeObject(consumer.Name)
    save_path = root / "spreadsheet-production.FCStd"
    document.saveAs(str(save_path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(save_path))
    assert reopened is not None
    reopened_sheet = reopened.getObject(object_name)
    assert reopened_sheet is not None
    assert reopened_sheet.TypeId == "Spreadsheet::Sheet"
    assert reopened_sheet.getAlias("B2") == "wheelbase"
    assert reopened_sheet.getContents("B3") == "=wheelbase / 4"
    assert reopened_sheet.getCellMerge("B1") == ("A1", 1, 2)
    assert reopened_sheet.VibeCADVibeScriptRevision == final_prepared["revision"]

    service.document = reopened
    delete_capture = {
        **create_capture,
        "operation": "delete_program",
        "tool_name": "vibescript.spreadsheet.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": final_prepared["revision"],
            "reason": "Spreadsheet production integration complete",
        },
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "document_objects": [
            {"name": str(obj.Name), "label": str(obj.Label), "type_id": str(obj.TypeId)}
            for obj in reopened.Objects
        ],
        "surface": resolve_modeling_surface("SpreadsheetWorkbench", "vibescript").summary(),
    }
    prepared_delete = prepare_delete(delete_capture)
    try:
        deletion = delete_live_program(service, prepared_delete)
        result = finish_delete(prepared_delete, deletion)
    except Exception:
        restore_prepared_delete = __import__(
            "VibeCADVibeScriptDomainRuntime", fromlist=["restore_prepared_delete"]
        ).restore_prepared_delete
        restore_prepared_delete(prepared_delete)
        raise
    assert result["ok"] is True and result["artifacts_deleted"] is True
    assert reopened.getObject(object_name) is None
    assert not Path(prepared_delete["program_directory"]).exists()
    App.closeDocument(reopened.Name)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="vibecad-spreadsheet-production-"))
    try:
        _exercise_source_api()
        _exercise_isolated_native_batch()
        _exercise_lifecycle(root)
        print("Spreadsheet VibeScript production integration passed")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
