# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sheet edit tools keep exact targets and use the native asynchronous adapter."""

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


CASES = {
    "add_circle": {"center": [10, 20, 0], "radius": 3},
    "update_circle": {"operation_id": "hole-a", "radius": 4},
    "add_profile": {"profile": {"document_uid": "document", "object_name": "CutSketch"}},
    "replace_profile": {"operation_id": "slot-a", "profile": {
        "document_uid": "document", "object_name": "CutSketch"}},
    "set_suppressed": {"operation_id": "hole-a", "suppressed": True},
    "remove_operation": {"operation_id": "legacy-hole"},
    "set_material": {"material": "Steel", "k_factor": .35},
    "set_parameters": {"changes": {"thickness": 2, "bend_angle": 90}},
}


@pytest.mark.parametrize("name", ["width", "height", "flange_width"])
def test_base_dimensions_are_optional_positive_native_edits(name):
    definition = build_native_capability_registry().definition("sheet_metal.edit")
    validator = Draft202012Validator(definition.provider_schema(("set_parameters",))["parameters"])
    arguments = {"operation": "set_parameters", "object_name": "EditableSheet",
                 "changes": {name: 35}}
    assert not list(validator.iter_errors(arguments))
    for invalid in (0, -1, True, "35"):
        assert list(validator.iter_errors({**arguments, "changes": {name: invalid}}))


def test_sheet_edit_registration_requires_async_geometry_on_the_sheet_surface():
    registry = build_native_capability_registry()
    definition = registry.definition("sheet_metal.edit")
    assert definition is not None
    assert definition.name in registry.shared_definition_names
    assert definition.primary_classification == "mutation"
    assert {variant.operation for variant in definition.variants} == set(CASES)
    implementation = registry.implementation(definition.name)
    assert callable(implementation.handler)
    assert callable(implementation.async_handler)
    for variant in definition.variants:
        assert variant.surface_ids == frozenset({"sheet_metal"})
        assert variant.transaction_behavior == "background"
        assert variant.background_required
        assert variant.exact_target_type == "ExactSharedSheetState"


@pytest.mark.parametrize("operation", CASES)
def test_edit_schema_accepts_exact_operations_and_rejects_unrelated_fields(operation):
    definition = build_native_capability_registry().definition("sheet_metal.edit")
    validator = Draft202012Validator(definition.provider_schema((operation,))["parameters"])
    valid = {"operation": operation, "object_name": "SheetCut", **CASES[operation]}
    assert not list(validator.iter_errors(valid))
    for invalid in ({**valid, "python": "anything"}, {**valid, "object_name": "a label"},
                    {key: value for key, value in valid.items() if key != "object_name"}):
        assert list(validator.iter_errors(invalid))


@pytest.fixture
def edit_case(monkeypatch):
    from VibeCADNativeSheetMetalEditRuntime import NativeSheetMetalEditRuntime
    class PreparedSheetState:
        pass
    document = SimpleNamespace(Uid="document")
    sheet = SimpleNamespace(Name="SheetCut", Document=document, Proxy=PreparedSheetState(),
                            TypeId="PartDesign::FeaturePython")
    document.getObject = lambda name: sheet if name == sheet.Name else None
    prepared, revision = object(), object()
    shared = SimpleNamespace(capture_revision=Mock(return_value=revision),
                             prepare=Mock(return_value=prepared))
    completion = Future()
    start = Mock(return_value=completion)
    monkeypatch.setitem(sys.modules, "SheetMetalEditable", SimpleNamespace(PreparedSheetState=PreparedSheetState))
    monkeypatch.setitem(sys.modules, "SheetMetalHistoryOperations", shared)
    monkeypatch.setitem(sys.modules, "SheetMetalNativeEdit", SimpleNamespace(start=start))
    monkeypatch.setitem(sys.modules, "SheetMetalPresentation", SimpleNamespace(_gui_thread=Mock()))
    state = NativeDocumentStateStore()
    context = NativeRuntimeContext(service=None, document=document, state=state,
        undo_ledger=NativeAssistantUndoLedger(), reauthorize_turn=Mock(),
        active_document=lambda: document, active_surface_id=lambda: "sheet_metal",
        edit_or_task_active=lambda: False)
    return SimpleNamespace(runtime=NativeSheetMetalEditRuntime(context), context=context,
        ticket=state.begin_call(document.Uid, "sheet_metal.edit"), sheet=sheet,
        shared=shared, prepared=prepared, revision=revision, start=start, completion=completion)


@pytest.mark.parametrize("operation", CASES)
def test_binding_returns_the_shared_geometry_future_without_waiting(edit_case, operation):
    case = edit_case
    arguments = {"operation": operation, "object_name": case.sheet.Name, **CASES[operation]}
    implementation = build_native_capability_registry().implementation("sheet_metal.edit")
    future = implementation.async_handler(SimpleNamespace(
        runtime=case.runtime, ticket=case.ticket, arguments=arguments))
    assert future is case.completion
    assert not future.done()
    case.shared.capture_revision.assert_called_once_with(case.sheet)
    case.shared.prepare.assert_called_once_with(case.sheet,
        {"operation": operation, **CASES[operation]}, expected_revision=case.revision)
    case.start.assert_called_once_with(case.context, case.ticket, case.prepared)


def test_synchronous_binding_rejects_before_changing_the_document(edit_case):
    case = edit_case
    implementation = build_native_capability_registry().implementation("sheet_metal.edit")
    with pytest.raises(RuntimeError, match="asynchronous"):
        implementation.handler(SimpleNamespace(runtime=case.runtime, ticket=case.ticket,
            arguments={"operation": "add_circle", "object_name": case.sheet.Name, **CASES["add_circle"]}))
    case.shared.prepare.assert_not_called()
    case.start.assert_not_called()


def test_folded_center_missing_region_has_an_actionable_repair(edit_case):
    from VibeCADNativeSheetMetalEditRuntime import NativeSheetMetalEditRequestError
    case = edit_case
    with pytest.raises(NativeSheetMetalEditRequestError) as caught:
        case.runtime.execute_async({"operation": "add_circle", "object_name": case.sheet.Name,
            "center": [10, 20, 0], "radius": 3, "representation": "folded"}, ticket=case.ticket)
    failure = caught.value.failure()
    assert failure["parameters_committed"] is False
    assert "list_regions" in failure["repair"]
    assert "region" in failure["repair"]
    case.shared.prepare.assert_not_called()
    case.start.assert_not_called()


def test_region_contract_explains_which_edits_require_it():
    definition = build_native_capability_registry().definition("sheet_metal.edit")
    variant = next(item for item in definition.variants if item.operation == "add_circle")
    description = variant.parameters["properties"]["region"]["description"]
    assert "Required" in description
    assert "folded" in description
    assert "center" in description
    assert "list_regions" in description


@pytest.mark.parametrize("failure", ["missing", "label", "type", "authority", "ticket", "capability"])
def test_bad_target_or_authority_is_rejected_before_preparing_an_edit(edit_case, failure):
    case = edit_case
    name, ticket = case.sheet.Name, case.ticket
    if failure == "missing":
        name = "Missing"
    elif failure == "label":
        name = "a label"
    elif failure == "type":
        case.sheet.Proxy = object()
    elif failure == "authority":
        case.context.reauthorize_turn.side_effect = RuntimeError("turn ended")
    elif failure == "ticket":
        ticket = case.context.state.begin_call("another-document", "sheet_metal.edit")
    else:
        ticket = case.context.state.begin_call("document", "sheet_metal.inspect")
    with pytest.raises(RuntimeError):
        case.runtime.execute_async({"operation": "add_circle", "object_name": name,
                                    **CASES["add_circle"]}, ticket=ticket)
    case.shared.prepare.assert_not_called()
    case.start.assert_not_called()


def test_domain_preflight_error_explains_repair_before_any_parameters_change(edit_case):
    case = edit_case
    message = "The requested sheet cut no longer exists"
    case.shared.prepare.side_effect = ValueError(message)
    with pytest.raises(RuntimeError) as caught:
        case.runtime.execute_async({"operation": "update_circle", "object_name": case.sheet.Name,
                                    **CASES["update_circle"]}, ticket=case.ticket)
    failure = caught.value.failure()
    assert failure["message"] == message
    assert failure["parameters_committed"] is False
    assert "sheet_metal.inspect" in failure["repair"]
    case.start.assert_not_called()
