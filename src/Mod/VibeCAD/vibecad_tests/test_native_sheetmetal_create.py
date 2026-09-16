# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native source-face creation reaches shared folded/flat geometry without MCP."""

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


def test_from_source_creation_is_bounded_and_registered_for_async_dispatch():
    registry = build_native_capability_registry()
    definition = registry.definition("sheet_metal.create")
    assert definition is not None
    assert definition.name in registry.shared_definition_names
    variant = next(item for item in definition.variants if item.operation == "from_source")
    assert variant.surface_ids == frozenset({"sheet_metal"})
    assert variant.action_ids == frozenset({"SheetMetal_CreateEditable"})
    assert variant.transaction_behavior == "background" and variant.background_required
    assert callable(registry.implementation(definition.name).async_handler)
    validator = Draft202012Validator(definition.provider_schema(("from_source",))["parameters"])
    valid = {"operation": "from_source", "object_name": "BaseBend", "reference_face": "Face1"}
    assert not list(validator.iter_errors(valid))
    for invalid in ({**valid, "reference_face": "Edge1"}, {**valid, "reference_face": "Face0"},
                    {**valid, "object_name": "a label"}, {**valid, "script": "anything"}):
        assert list(validator.iter_errors(invalid))


@pytest.fixture
def creation_case(monkeypatch):
    from VibeCADNativeSheetMetalCreateRuntime import NativeSheetMetalCreateRuntime
    document = SimpleNamespace(Uid="document")
    source = SimpleNamespace(Name="BaseBend", Document=document)
    document.getObject = lambda name: source if name == source.Name else None
    revision = object()
    capture = Mock(return_value=revision)
    future = Future()
    start = Mock(return_value=future)
    monkeypatch.setitem(sys.modules, "SheetMetalOperations", SimpleNamespace(capture_source_revision=capture))
    monkeypatch.setitem(sys.modules, "SheetMetalNativeEdit", SimpleNamespace(start_creation=start))
    monkeypatch.setitem(sys.modules, "SheetMetalPresentation", SimpleNamespace(_gui_thread=Mock()))
    state = NativeDocumentStateStore()
    context = NativeRuntimeContext(service=None, document=document, state=state,
        undo_ledger=NativeAssistantUndoLedger(), reauthorize_turn=Mock(),
        active_document=lambda: document, active_surface_id=lambda: "sheet_metal",
        edit_or_task_active=lambda: False)
    return SimpleNamespace(context=context, runtime=NativeSheetMetalCreateRuntime(context),
        source=source, capture=capture, revision=revision, start=start, future=future,
        ticket=state.begin_call(document.Uid, "sheet_metal.create"),
        arguments={"operation": "from_source", "object_name": source.Name, "reference_face": "Face1"})


def test_from_source_binding_passes_the_exact_face_and_revision_to_native_creation(creation_case):
    case = creation_case
    implementation = build_native_capability_registry().implementation("sheet_metal.create")
    result = implementation.async_handler(SimpleNamespace(runtime=case.runtime, ticket=case.ticket,
                                                           arguments=case.arguments))
    assert result is case.future and not result.done()
    case.capture.assert_called_once_with(case.source)
    case.start.assert_called_once_with(case.context, case.ticket, case.source, "Face1",
                                      expected_revision=case.revision)


@pytest.mark.parametrize("change", ["label", "missing", "face", "authority", "capability"])
def test_invalid_creation_target_or_authority_never_starts_geometry(creation_case, change):
    case = creation_case
    arguments, ticket = dict(case.arguments), case.ticket
    if change in ("label", "missing"):
        arguments["object_name"] = "a label" if change == "label" else "Missing"
    elif change == "face":
        arguments["reference_face"] = "Edge1"
    elif change == "authority":
        case.context.reauthorize_turn.side_effect = RuntimeError("turn ended")
    else:
        ticket = case.context.state.begin_call("document", "sheet_metal.inspect")
    with pytest.raises(RuntimeError):
        case.runtime.execute_async(arguments, ticket=ticket)
    case.capture.assert_not_called()
    case.start.assert_not_called()


def test_synchronous_creation_rejects_before_mutating(creation_case):
    case = creation_case
    with pytest.raises(RuntimeError, match="asynchronous"):
        case.runtime.execute(case.arguments, ticket=case.ticket)
    case.start.assert_not_called()


SOURCE_REQUESTS = (
    {"operation": "base_shape", "shape_type": "L-Shape", "thickness": 1.6,
     "bend_radius": 2, "width": 50, "length": 70, "height": 25,
     "flange_width": 8, "origin": "0,0", "fill_gaps": True},
    {"operation": "base_from_sketch", "object_name": "Profile", "thickness": 1.6,
     "bend_radius": 2, "length": 25, "bend_side": "Inside", "midplane": False, "reverse": False},
    {"operation": "from_solid", "object_name": "Solid", "subelements": ["Face1", "Edge2"],
     "thickness": 1.6, "bend_radius": 2, "invert": False},
)


@pytest.mark.parametrize("shape", ["Flat", "L-Shape", "U-Shape", "Tub", "Hat", "Box"])
def test_base_creation_accepts_omitted_return_flange_width(shape):
    definition = build_native_capability_registry().definition("sheet_metal.create")
    validator = Draft202012Validator(definition.provider_schema(("base_shape",))["parameters"])
    arguments = {**SOURCE_REQUESTS[0], "shape_type": shape}
    arguments.pop("flange_width")
    assert not list(validator.iter_errors(arguments))
    variant = next(item for item in definition.variants if item.operation == "base_shape")
    assert variant.parameters["properties"]["flange_width"]["default"] == 8


def test_native_creation_supplies_the_documented_flange_default(creation_case, monkeypatch):
    case = creation_case
    prepare = Mock(return_value=object())
    monkeypatch.setitem(sys.modules, "SheetMetalSourceOperations", SimpleNamespace(
        capture_revision=Mock(return_value=case.revision), prepare=prepare))
    monkeypatch.setattr(sys.modules["SheetMetalNativeEdit"], "start_source_creation",
                        Mock(return_value=case.future), raising=False)
    arguments = dict(SOURCE_REQUESTS[0])
    arguments.pop("flange_width")
    result = case.runtime.execute_async(arguments, ticket=case.ticket)
    assert result is case.future
    prepare.assert_called_once_with(case.context.document,
        {**arguments, "container_name": None, "flange_width": 8}, expected_revision=case.revision)


@pytest.mark.parametrize("arguments", SOURCE_REQUESTS)
def test_upstream_source_variants_have_bounded_async_contracts(arguments):
    definition = build_native_capability_registry().definition("sheet_metal.create")
    variant = next(item for item in definition.variants if item.operation == arguments["operation"])
    assert variant.background_required and variant.transaction_behavior == "background"
    assert variant.surface_ids == frozenset({"sheet_metal"})
    assert variant.exact_target_type
    validator = Draft202012Validator(definition.provider_schema((arguments["operation"],))["parameters"])
    assert not list(validator.iter_errors(arguments))
    for invalid in ({**arguments, "thickness": True}, {**arguments, "thickness": -1},
                    {**arguments, "bend_radius": 1_000_001}, {**arguments, "script": "anything"}):
        assert list(validator.iter_errors(invalid))
    if arguments["operation"] == "from_solid":
        for selected in ([], ["Vertex1"], ["Face0"], ["Face1", "Face1"], ["Face1"]*257):
            assert list(validator.iter_errors({**arguments, "subelements": selected}))


@pytest.mark.parametrize("arguments", SOURCE_REQUESTS)
def test_registered_source_bindings_use_the_locked_document_and_prepared_request(
        creation_case, monkeypatch, arguments):
    case = creation_case
    prepared = object()
    prepare = Mock(return_value=prepared)
    capture = Mock(return_value=case.revision)
    start = Mock(return_value=case.future)
    monkeypatch.setitem(sys.modules, "SheetMetalSourceOperations",
                        SimpleNamespace(capture_revision=capture, prepare=prepare))
    monkeypatch.setattr(sys.modules["SheetMetalNativeEdit"], "start_source_creation", start, raising=False)
    implementation = build_native_capability_registry().implementation("sheet_metal.create")
    result = implementation.async_handler(SimpleNamespace(runtime=case.runtime, ticket=case.ticket,
                                                           arguments=arguments))
    assert result is case.future
    capture.assert_called_once_with(case.context.document)
    expected = dict(arguments)
    if expected["operation"] == "base_shape":
        expected["container_name"] = None
    prepare.assert_called_once_with(case.context.document, expected, expected_revision=case.revision)
    start.assert_called_once_with(case.context, case.ticket, prepared)
    case.start.assert_not_called()


def test_source_validation_failure_reports_no_committed_parameters(creation_case, monkeypatch):
    case = creation_case
    from VibeCADNativeSheetMetalCreateRuntime import NativeSheetMetalCreateRequestError
    monkeypatch.setitem(sys.modules, "SheetMetalSourceOperations", SimpleNamespace(
        capture_revision=Mock(return_value=case.revision),
        prepare=Mock(side_effect=ValueError("Choose an editable sketch"))))
    with pytest.raises(NativeSheetMetalCreateRequestError) as caught:
        case.runtime.execute_async(SOURCE_REQUESTS[1], ticket=case.ticket)
    assert caught.value.failure()["parameters_committed"] is False
    assert "planar sheet face" not in caught.value.failure()["repair"]
    case.start.assert_not_called()
