# SPDX-License-Identifier: LGPL-2.1-or-later
"""Direct RMFG tools share GUI state and preserve exact document authority."""

from concurrent.futures import Future
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


NAME = "sheet_metal.manufacturing"


def test_manufacturing_schema_is_direct_and_does_not_expose_payment_or_credentials():
    definition = build_native_capability_registry().definition(NAME)
    assert definition.primary_classification == "export"
    assert {variant.operation for variant in definition.variants} == {
        "status", "show_panel", "load_materials", "list_materials", "analyze", "set_material", "clear_material",
        "set_quantity", "quote", "refresh", "checkout", "list_jobs", "inspect_job", "resume_job"}
    schema = definition.provider_schema(tuple(item.operation for item in definition.variants))["parameters"]
    validator = Draft202012Validator(schema)
    for arguments in (
        {"operation": "analyze", "object_name": "EditableSheet"},
        {"operation": "load_materials", "object_name": "EditableSheet", "more": False},
        {"operation": "set_material", "object_name": "EditableSheet", "part_id": "part_1", "material_id": "steel_1"},
        {"operation": "set_quantity", "object_name": "EditableSheet", "quantity": 10},
    ):
        assert not list(validator.iter_errors(arguments))
        for field in ("document_uid", "url", "token", "payment", "python"):
            assert list(validator.iter_errors({**arguments, field: "untrusted"}))
    for value in (True, 0, -1, 1.5, "10", 1000001):
        assert list(validator.iter_errors({"operation": "set_quantity", "object_name": "EditableSheet", "quantity": value}))


@pytest.fixture
def case(monkeypatch):
    from VibeCADNativeSheetMetalManufacturingRuntime import NativeSheetMetalManufacturingRuntime
    class PreparedSheetState:
        pass
    document = SimpleNamespace(Uid="document")
    sheet = SimpleNamespace(Name="EditableSheet", Document=document, Proxy=PreparedSheetState(), TypeId="PartDesign::FeaturePython")
    document.getObject = lambda name: sheet if name == sheet.Name else None
    controller = SimpleNamespace(status=Mock(return_value={"busy": False}), set_material=Mock(),
        clear_material=Mock(), set_quantity=Mock(), load_materials=Mock(), analyze=Mock(),
        request_quote=Mock(), refresh=Mock(), checkout=Mock(),
        list_jobs=Mock(), inspect_job=Mock(), resume_job=Mock())
    manufacturing = SimpleNamespace(manufacturing_controller=Mock(return_value=controller),
        show_manufacturing=Mock(), open_checkout=Mock(return_value=True))
    operations = SimpleNamespace(capture_revision=Mock())
    monkeypatch.setitem(sys.modules, "SheetMetalEditable", SimpleNamespace(PreparedSheetState=PreparedSheetState))
    monkeypatch.setitem(sys.modules, "SheetMetalOperations", operations)
    monkeypatch.setitem(sys.modules, "SheetMetalPresentation", SimpleNamespace(_gui_thread=Mock()))
    monkeypatch.setitem(sys.modules, "SheetMetalRMFGManufacturingGui", manufacturing)
    context = NativeRuntimeContext(service=None, document=document, state=NativeDocumentStateStore(),
        undo_ledger=NativeAssistantUndoLedger(), reauthorize_turn=Mock(), active_document=lambda: document,
        active_surface_id=lambda: "sheet_metal", edit_or_task_active=lambda: False)
    return SimpleNamespace(runtime=NativeSheetMetalManufacturingRuntime(context), context=context,
        controller=controller, manufacturing=manufacturing, sheet=sheet, operations=operations)


def invoke(case, operation, **values):
    return case.runtime.execute({"operation": operation, "object_name": "EditableSheet", **values}, asynchronous=True)


def test_native_uses_the_exact_shared_controller_for_local_settings(case):
    result = invoke(case, "set_material", part_id="part_1", material_id="steel_1")
    case.controller.set_material.assert_called_once_with("part_1", "steel_1")
    case.manufacturing.manufacturing_controller.assert_called_once_with(case.sheet)
    assert result["target"] == {"document_uid": "document", "object_name": "EditableSheet"}
    invoke(case, "set_quantity", quantity=10)
    case.controller.set_quantity.assert_called_once_with(10)


def test_blocked_dfm_has_revision_specific_inspection_and_repair_route(case):
    case.controller.status.return_value = {"quote": {"status": "blocked", "findings": [
        {"message": "Insufficient clearance"}]}, "can_checkout": False}
    result = invoke(case, "status")
    route = result["repair_workflow"]
    assert route["inspect"] == {"tool": "sheet_metal.inspect", "arguments": {
        "operation": "read_sheet", "target": result["target"]}}
    assert "sketch.open" in route["message"]
    assert "analyze" in route["message"] and "quote" in route["message"]
    assert "guess" in route["message"]
    assert result["quote"]["findings"] == [{"message": "Insufficient clearance"}]
    case.controller.analyze.assert_not_called()
    case.controller.request_quote.assert_not_called()


@pytest.mark.parametrize("operation,method", [("load_materials", "load_materials"), ("analyze", "analyze"),
    ("quote", "request_quote"), ("refresh", "refresh"), ("checkout", "checkout")])
def test_network_operations_return_a_future_and_reject_synchronous_submission(case, operation, method):
    pending = Future()
    getattr(case.controller, method).return_value = pending
    extra = {"more": False} if operation == "load_materials" else {}
    with pytest.raises(RuntimeError):
        case.runtime.execute({"operation": operation, "object_name": "EditableSheet", **extra})
    getattr(case.controller, method).assert_not_called()
    future = invoke(case, operation, **extra)
    assert isinstance(future, Future) and not future.done()
    pending.set_result({"busy": False})
    assert future.result()["target"]["object_name"] == "EditableSheet"


@pytest.mark.parametrize("operation,values", [("set_quantity", {"quantity": True}),
    ("set_quantity", {"quantity": 0}), ("load_materials", {"more": "yes"}),
    ("set_material", {"part_id": "", "material_id": "steel_1"}),
    ("clear_material", {"part_id": []})])
def test_bad_arguments_are_rejected_before_obtaining_manufacturing_state(case, operation, values):
    with pytest.raises(RuntimeError):
        invoke(case, operation, **values)
    case.manufacturing.manufacturing_controller.assert_not_called()


@pytest.mark.parametrize("failure", ["authority", "history", "type"])
def test_invalid_targets_are_rejected_before_io_or_panels(case, failure):
    if failure == "authority":
        case.context.reauthorize_turn.side_effect = RuntimeError("turn ended")
    elif failure == "history":
        case.operations.capture_revision.side_effect = RuntimeError("future History state")
    else:
        case.sheet.Proxy = object()
    with pytest.raises(RuntimeError):
        invoke(case, "show_panel")
    case.manufacturing.manufacturing_controller.assert_not_called()
    case.manufacturing.show_manufacturing.assert_not_called()


def test_async_completion_reauthorizes_before_opening_checkout(case):
    pending = Future()
    case.controller.checkout.return_value = pending
    future = invoke(case, "checkout")
    case.context.reauthorize_turn.side_effect = RuntimeError("turn ended")
    pending.set_result({"busy": False})
    with pytest.raises(RuntimeError, match="turn ended"):
        future.result()
    case.manufacturing.open_checkout.assert_not_called()


def test_cancelled_native_checkout_does_not_open_a_browser(case):
    pending = Future()
    case.controller.checkout.return_value = pending
    future = invoke(case, "checkout")
    assert future.cancel()
    assert pending.cancelled()
    case.manufacturing.open_checkout.assert_not_called()


def test_export_classification_cannot_absorb_an_external_document_revision(case):
    pending = Future()
    case.controller.request_quote.return_value = pending
    future = invoke(case, "quote")
    case.context.state.note_structural_change("document")
    pending.set_result({"busy": False})
    with pytest.raises(RuntimeError, match="document changed"):
        future.result()
    assert case.context.state.current_revision("document") == 1


def test_large_material_catalog_is_explicitly_paged_without_starting_network_work(case):
    materials = [{"id": f"material_{index}", "material": "Steel", "thickness_mm": 1.6} for index in range(153)]
    case.controller.status.return_value = {"materials": materials, "busy": False}
    first = invoke(case, "status")
    assert first["materials"] == materials[:50]
    assert first["material_count"] == 153
    assert first["next_material_offset"] == 50
    page = invoke(case, "list_materials", offset=150, limit=20)
    assert page["materials"] == materials[150:]
    assert page["next_material_offset"] is None
    case.controller.load_materials.assert_not_called()
    assert case.controller.status.return_value["materials"] == materials


@pytest.mark.parametrize("values", [{"offset": -1, "limit": 10}, {"offset": True, "limit": 10},
                                    {"offset": 0, "limit": 0}, {"offset": 0, "limit": 51}])
def test_material_page_bounds_are_checked_before_obtaining_state(case, values):
    with pytest.raises(RuntimeError):
        invoke(case, "list_materials", **values)
    case.manufacturing.manufacturing_controller.assert_not_called()


@pytest.mark.parametrize("operation,values", [("list_jobs", {"offset": 0, "limit": 20}),
    ("inspect_job", {"job_key": "saved-key"}), ("resume_job", {"job_key": "saved-key"})])
def test_saved_jobs_use_the_same_async_controller_and_document_guard(case, operation, values):
    pending = Future()
    getattr(case.controller, operation).return_value = pending
    result = invoke(case, operation, **values)
    assert isinstance(result, Future)
    if operation == "list_jobs":
        case.controller.list_jobs.assert_called_once_with(offset=0, limit=20)
    else:
        getattr(case.controller, operation).assert_called_once_with("saved-key")
    pending.set_result({})
    assert result.result()["target"]["object_name"] == case.sheet.Name


@pytest.mark.parametrize("operation,values", [("list_jobs", {"offset": 0, "limit": 21}),
    ("inspect_job", {"job_key": ""}), ("resume_job", {"job_key": True})])
def test_saved_job_arguments_are_bounded_before_state_access(case, operation, values):
    with pytest.raises(RuntimeError):
        invoke(case, operation, **values)
    case.manufacturing.manufacturing_controller.assert_not_called()
