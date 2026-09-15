# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native cache preparation remains a guarded asynchronous read."""

from concurrent.futures import Future
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADNativeSheetMetalInspectRuntime import NativeSheetMetalInspectRuntime


@pytest.fixture
def case(monkeypatch):
    class PreparedSheetState:
        pass
    document = SimpleNamespace(Uid="sheet-doc")
    sheet = SimpleNamespace(Name="EditableSheet", Document=document,
                            Proxy=PreparedSheetState(), TypeId="Part::FeaturePython")
    document.getObject = lambda name: sheet if name == sheet.Name else None
    context = NativeRuntimeContext(service=None, document=document, state=NativeDocumentStateStore(),
        undo_ledger=NativeAssistantUndoLedger(), reauthorize_turn=Mock(),
        active_document=lambda: document, active_surface_id=lambda: "sheet_metal",
        edit_or_task_active=lambda: False)
    revision = SimpleNamespace(summary=lambda: {"document_uid": document.Uid,
        "object_name": sheet.Name, "structural_revision": 0, "input_hash": "saved-hash"})
    shared = SimpleNamespace(capture_revision=Mock(return_value=revision))
    run = SimpleNamespace(future=Future())
    start = Mock(return_value=run)
    geometry = Mock()
    monkeypatch.setitem(sys.modules, "SheetMetalEditable", SimpleNamespace(
        PreparedSheetState=PreparedSheetState, get_state_geometry=geometry))
    monkeypatch.setitem(sys.modules, "SheetMetalHistoryOperations", shared)
    monkeypatch.setitem(sys.modules, "SheetMetalPreparation", SimpleNamespace(start_preparation=start))
    monkeypatch.setitem(sys.modules, "SheetMetalPresentation", SimpleNamespace(_gui_thread=Mock()))
    return SimpleNamespace(runtime=NativeSheetMetalInspectRuntime(context), context=context,
        sheet=sheet, revision=revision, shared=shared, run=run, start=start, geometry=geometry,
        arguments={"operation": "prepare", "target": {"document_uid": document.Uid, "object_name": sheet.Name}})


def test_preparation_returns_a_future_and_exact_revision_without_a_mutation_receipt(case):
    result = case.runtime.inspect_async(case.arguments)
    assert isinstance(result, Future) and not result.done()
    case.start.assert_called_once_with(case.sheet, expected_revision=case.revision)
    case.run.future.set_result({"object_name": case.sheet.Name, "input_hash": "saved-hash"})
    payload = result.result()
    assert payload["prepared"] is True
    assert payload["target"] == case.arguments["target"]
    assert payload["revision"] == case.revision.summary()
    assert "receipt" not in payload
    assert case.context.state.current_revision("sheet-doc") == 0
    case.geometry.assert_called_once_with(case.sheet)


def test_existing_inspections_keep_their_synchronous_result(case):
    case.runtime.inspect = Mock(return_value={"items": []})
    arguments = {"operation": "list_sheets"}
    assert case.runtime.inspect_async(arguments) == {"items": []}
    case.runtime.inspect.assert_called_once_with(arguments)
    case.start.assert_not_called()


def test_preparation_on_modeling_explains_the_next_turn_edit_tools(case):
    from dataclasses import replace
    from VibeCADProvider import _provider_visible_tool_result
    case.runtime._context = replace(case.context, active_surface_id=lambda: "model")
    result = case.runtime.inspect_async(case.arguments)
    case.run.future.set_result({"object_name": case.sheet.Name, "input_hash": "saved-hash"})
    payload = _provider_visible_tool_result(result.result(), tool_name="sheet_metal.inspect")
    assert "workspace.switch" in payload["edit_guidance"]
    assert "sheet_metal" in payload["edit_guidance"]
    assert "next turn" in payload["edit_guidance"]


def test_cancelled_native_result_cancels_cache_publication(case):
    result = case.runtime.inspect_async(case.arguments)
    assert result.cancel()
    assert case.run.future.cancelled()


@pytest.mark.parametrize("failure", ("authority", "revision", "worker", "fingerprint"))
def test_completion_rejects_stale_or_failed_preparation(case, failure):
    result = case.runtime.inspect_async(case.arguments)
    if failure == "authority":
        case.context.reauthorize_turn.side_effect = RuntimeError("turn ended")
    elif failure == "revision":
        case.shared.capture_revision.return_value = SimpleNamespace(summary=lambda: {"changed": True})
    if failure == "worker":
        case.run.future.set_exception(RuntimeError("invalid sheet"))
    else:
        case.run.future.set_result({"object_name": case.sheet.Name,
            "input_hash": "different-hash" if failure == "fingerprint" else "saved-hash"})
    with pytest.raises(RuntimeError):
        result.result()


@pytest.mark.parametrize("target", ({"object_name": "EditableSheet"},
    {"document_uid": "foreign-doc", "object_name": "EditableSheet"},
    {"document_uid": "sheet-doc", "object_name": "Missing"}))
def test_bad_targets_never_start_preparation(case, target):
    with pytest.raises(RuntimeError):
        case.runtime.inspect_async({**case.arguments, "target": target})
    case.start.assert_not_called()
