# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native folded/flat controls retain exact targeting and presentation semantics."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


def test_view_is_registered_as_presentation_and_requires_an_exact_sheet():
    registry = build_native_capability_registry()
    definition = registry.definition("sheet_metal.view")
    assert definition.primary_classification == "view"
    assert definition.name in registry.shared_definition_names
    assert callable(registry.implementation(definition.name).handler)
    assert {item.operation for item in definition.variants} == {"set_representation"}
    variant = definition.variants[0]
    assert variant.transaction_behavior == "presentation"
    assert variant.background_required is False
    assert variant.surface_ids == frozenset({"model", "sheet_metal"})
    validator = Draft202012Validator(definition.provider_schema(("set_representation",))["parameters"])
    valid = {"operation": "set_representation", "representation": "flat",
             "object_name": "SheetCut"}
    assert not list(validator.iter_errors(valid))
    assert not list(validator.iter_errors({**valid, "representation": "folded"}))
    for invalid in ({**valid, "representation": "unfold"}, {**valid, "recompute": True},
                    {**valid, "object_name": "Sheet label"},
                    {**valid, "document_uid": "other"},
                    {key: value for key, value in valid.items() if key != "object_name"}):
        assert list(validator.iter_errors(invalid))


@pytest.fixture
def view_case(monkeypatch):
    from VibeCADNativeSheetMetalViewRuntime import NativeSheetMetalViewRuntime
    class PreparedSheetState:
        pass
    document = SimpleNamespace(Uid="document")
    displayed = SimpleNamespace(Name="EditableSheet", Document=document, Visibility=True,
        ViewObject=SimpleNamespace(Proxy=SimpleNamespace(current=Mock(return_value="geometry-hash"),
                                                        current_display=Mock(return_value="geometry-hash"),
                                                        mode="folded")))
    sheet = SimpleNamespace(Name="SheetCut", Document=document, Proxy=PreparedSheetState(),
                            TypeId="PartDesign::FeaturePython")
    document.getObject = lambda name: sheet if name == sheet.Name else None
    switch = Mock(side_effect=lambda obj, mode: setattr(displayed.ViewObject.Proxy, "mode", mode))
    shared = SimpleNamespace(capture_revision=Mock(), _display_state=Mock(return_value=displayed),
                             switch=switch)
    monkeypatch.setitem(sys.modules, "SheetMetalEditable", SimpleNamespace(PreparedSheetState=PreparedSheetState))
    monkeypatch.setitem(sys.modules, "SheetMetalHistoryOperations", shared)
    monkeypatch.setitem(sys.modules, "SheetMetalPresentation", SimpleNamespace(_gui_thread=Mock()))
    context = NativeRuntimeContext(service=None, document=document,
        state=NativeDocumentStateStore(), undo_ledger=NativeAssistantUndoLedger(),
        reauthorize_turn=Mock(), active_document=lambda: document,
        active_surface_id=lambda: "model", edit_or_task_active=lambda: False)
    return SimpleNamespace(runtime=NativeSheetMetalViewRuntime(context), context=context,
        sheet=sheet, displayed=displayed, shared=shared,
        arguments={"operation": "set_representation", "representation": "flat",
                   "object_name": sheet.Name})


def test_view_uses_shared_switch_and_reports_the_displayed_predecessor(view_case):
    case = view_case
    result = case.runtime.execute(case.arguments)
    case.shared.switch.assert_called_once_with(case.sheet, "flat")
    assert result["target"] == {"document_uid": "document", "object_name": case.sheet.Name}
    assert result["display_state"] == {"document_uid": "document", "object_name": "EditableSheet"}
    assert result["representation"] == "flat"
    assert result["visibility"] is True
    assert result["input_hash"] == "geometry-hash"
    assert result["structural_revision"] == 0
    assert case.context.state.current_revision("document") == 0


def test_view_reports_a_hidden_predecessor_without_changing_visibility(view_case):
    case = view_case
    case.displayed.Visibility = False
    result = case.runtime.execute(case.arguments)
    assert result["visibility"] is False
    assert case.displayed.Visibility is False


def test_view_accepts_current_saved_display_without_an_edit_mapping(view_case):
    case = view_case
    case.displayed.ViewObject.Proxy.current.side_effect = RuntimeError("mapping not prepared after restore")
    assert case.runtime.execute(case.arguments)["input_hash"] == "geometry-hash"
    case.displayed.ViewObject.Proxy.current.assert_not_called()
    case.displayed.ViewObject.Proxy.current_display.assert_called_once_with()


def test_source_target_error_explains_how_to_obtain_a_shared_sheet(view_case):
    from VibeCADNativeTargets import NativeTargetError
    from VibeCADProvider import _provider_visible_tool_result

    case = view_case
    case.sheet.Proxy = object()
    with pytest.raises(NativeTargetError) as caught:
        case.runtime.execute(case.arguments)
    failure = caught.value.failure()
    assert failure["error_code"] == "NATIVE_TARGET_INVALID"
    assert failure["exact_target"] == {
        "document_uid": "document", "object_name": case.sheet.Name}
    assert failure["actual_type"] == case.sheet.TypeId
    # A provider must receive the same repair, rather than repeatedly trying
    # folded/flat on a source or creating a separate, unrelated flat part.
    visible = _provider_visible_tool_result({**failure, "ok": False,
        "_vibecad_native_result": True}, tool_name="sheet_metal.view")
    repair = visible["repair"]
    assert "list_sheets" in repair
    assert "from_source" in repair
    assert "object_name" in repair
    assert "reference_face" in repair
    assert "source_geometry" in repair
    assert "returned" in repair
    case.shared.capture_revision.assert_not_called()
    case.shared._display_state.assert_not_called()
    case.shared.switch.assert_not_called()
    assert case.context.state.current_revision("document") == 0


@pytest.mark.parametrize("field,value", [("representation", "unfold"), ("representation", True),
    ("object_name", "MissingSheet"), ("object_name", "Sheet label"), ("object_name", True),
    ("document_uid", "other")])
def test_view_rejects_bad_arguments_without_switching(view_case, field, value):
    with pytest.raises(RuntimeError):
        view_case.runtime.execute({**view_case.arguments, field: value})
    view_case.shared.switch.assert_not_called()


@pytest.mark.parametrize("failure", ["authority", "history", "prepared", "type"])
def test_view_rejects_unavailable_states_before_changing_presentation(view_case, failure):
    case = view_case
    if failure == "authority":
        case.context.reauthorize_turn.side_effect = RuntimeError("turn ended")
    elif failure == "history":
        case.shared.capture_revision.side_effect = RuntimeError("future History state")
    elif failure == "prepared":
        case.displayed.ViewObject.Proxy.current.side_effect = RuntimeError("stale cached geometry")
        case.displayed.ViewObject.Proxy.current_display.side_effect = RuntimeError("stale cached geometry")
    else:
        case.sheet.Proxy = object()
    with pytest.raises(RuntimeError):
        case.runtime.execute(case.arguments)
    case.shared.switch.assert_not_called()
