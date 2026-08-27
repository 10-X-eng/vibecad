# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeComponentInterfaceRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeComponentInterface import (
    NativeComponentInterfaceError,
    prepare_component_interface,
)
from VibeCADNativeComponentInterfaceRuntime import NativeComponentInterfaceRuntime
from VibeCADNativeComponentInterfaceSchema import (
    component_interface_capability_definition,
    component_interfaces_capability_definition,
)
from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Object:
    def __init__(self, document, name: str, type_id: str):
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.PropertiesList = []
        self._editor_modes = {}

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected or (
            self.TypeId == "PartDesign::CoordinateSystem"
            and expected == "App::LocalCoordinateSystem"
        )

    def addProperty(self, _type_id, name, _group, _description):
        self.PropertiesList.append(name)

    def setEditorMode(self, name, mode):
        self._editor_modes[name] = mode


class _Document:
    Uid = "document-interface"
    Name = "DocumentInterface"

    def __init__(self):
        self.component = _Object(self, "Bracket", "PartDesign::Body")
        self.lcs = _Object(self, "MountLCS", "PartDesign::CoordinateSystem")
        self.component.Group = [self.lcs]
        self.objects = {
            self.component.Name: self.component,
            self.lcs.Name: self.lcs,
        }

    def getObject(self, name: str):
        return self.objects.get(name)


def _arguments() -> dict[str, object]:
    return {
        "operation": "publish_interface",
        "component": {"object_name": "Bracket"},
        "lcs": {"object_name": "MountLCS"},
        "name": "MountAxis",
        "kind": "axis",
        "allowed_joints": ["revolute", "fixed"],
        "compatibility": "mount-v1",
    }


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("component-interface-unit")
    context = NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "model",
        edit_or_task_active=lambda: False,
    )
    return NativeComponentInterfaceRuntime(context), state, document


def test_component_interface_contract_is_exact_and_ribbon_scoped() -> None:
    definition = component_interface_capability_definition()
    schema = definition.provider_schema(("publish_interface",))
    branch = schema["parameters"]["oneOf"][0]
    variant = definition.variants[0]

    assert definition.name == "component.interface"
    assert definition.description == (
        "Publish an LCS returned by component.interfaces."
    )
    assert definition.primary_classification == "mutation"
    assert variant.action_ids == frozenset({"VibeCAD_PublishInterface"})
    assert variant.surface_ids == frozenset({"model", "assemble"})
    assert variant.exact_target_type == "Component + LocalCoordinateSystem"
    assert variant.background_required is False
    assert set(branch["required"]) == set(_arguments()) - {"operation"}
    assert branch["additionalProperties"] is False
    assert branch["properties"]["component"]["required"] == ["object_name"]
    assert branch["properties"]["lcs"]["required"] == ["object_name"]
    assert branch["properties"]["allowed_joints"]["uniqueItems"] is True
    serialized = repr(schema)
    for forbidden in ("selection", "workbench", "runCommand", "activate"):
        assert forbidden not in serialized


def test_component_interface_discovery_has_one_empty_request() -> None:
    definition = component_interfaces_capability_definition()
    variant = definition.variants[0]
    schema = provider_visible_native_schema(
        definition.provider_schema((variant.operation,))
    )
    parameters = schema["parameters"]["oneOf"][0]

    assert definition.name == "component.interfaces"
    assert definition.description == "Find LCS references and published interfaces."
    assert variant.operation == "find"
    assert variant.surface_ids == frozenset({"model", "assemble"})
    assert parameters == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_component_interface_discovery_runtime_reads_current_document(monkeypatch) -> None:
    runtime, _state, document = _runtime()
    calls = []
    monkeypatch.setattr(
        runtime_module,
        "read_component_interface_targets",
        lambda target_document, *, guard: (
            calls.append((target_document, guard)) or {"targets": []}
        ),
        raising=False,
    )

    result = runtime.interfaces({})

    assert result == {"targets": []}
    assert calls == [(document, runtime._context.guard)]


def test_component_interface_preflight_resolves_and_normalizes_exact_targets() -> None:
    document = _Document()
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    prepared = prepare_component_interface(document, values)

    assert prepared.component_ref.object_name == document.component.Name
    assert prepared.lcs_ref.object_name == document.lcs.Name
    assert prepared.spec.name == "MountAxis"
    assert prepared.spec.kind == "axis"
    assert prepared.spec.allowed_joints == ("revolute", "fixed")
    assert prepared.spec.compatibility == "mount-v1"
    assert prepared.initial_state == ((False, None),) * 5


def test_component_interface_preflight_rejects_vibescript_and_unowned_lcs() -> None:
    document = _Document()
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    document.component.VibeCADVibeScriptProgramId = "program-a"
    with pytest.raises(NativeComponentInterfaceError, match="VibeScript-owned"):
        prepare_component_interface(document, values)

    document.component.VibeCADVibeScriptProgramId = ""
    document.component.Group = []
    with pytest.raises(NativeComponentInterfaceError, match="not a direct resource"):
        prepare_component_interface(document, values)


def test_component_interface_runtime_preflights_before_one_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    captured = {}
    prepared = object()
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    monkeypatch.setattr(
        runtime_module,
        "prepare_component_interface",
        lambda target_document, target_values: (
            prepared
            if target_document is document and target_values == values
            else pytest.fail("wrong component-interface preflight")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "publish_component_interface",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(context=context, **kwargs)
        or {"routed": True},
    )

    result = runtime.publish_interface(
        _arguments(),
        ticket=state.begin_call(document.Uid, "component.interface"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Publish Native Component Interface"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_component_interface


def test_component_interface_runtime_rejects_noisy_arguments_before_preflight(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "prepare_component_interface",
        lambda *_args: pytest.fail("invalid arguments reached preflight"),
    )
    arguments = _arguments()
    arguments["selection"] = []

    with pytest.raises(NativeArgumentError, match="do not match"):
        runtime.publish_interface(
            arguments,
            ticket=state.begin_call(document.Uid, "component.interface"),
        )
