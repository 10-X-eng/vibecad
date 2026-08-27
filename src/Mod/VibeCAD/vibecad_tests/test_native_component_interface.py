# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
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
from VibeCADReferenceContracts import (
    INTERFACE_FIT_SCHEMA,
    INTERFACE_JOINT_PARAMETERS_SCHEMA,
)
from VibeCADReferenceContracts import (
    INTERFACE_GEOMETRY_SCHEMA,
    PROP_NATIVE_INTERFACE_GEOMETRY,
    capture_native_interface_geometry,
    native_interface_geometry_currentness,
    semantic_interface_geometry_evidence,
)


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
    assert "fit" not in branch["required"]
    assert branch["properties"]["fit"]["properties"]["schema"]["enum"] == [INTERFACE_FIT_SCHEMA]
    assert set(branch["properties"]["kind"]["enum"]) >= {
        "axis", "bearing_face", "bearing_seat", "bolt_pattern", "bore",
        "electrical_connector", "fixture", "fluid_port", "frame",
        "mounting_pattern", "plane", "planar_mate", "point", "shaft",
        "shaft_seat", "thread", "thread_axis", "tool",
    }
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
    assert prepared.initial_state == ((False, None),) * 8


def test_component_interface_fit_is_optional_versioned_and_separate_from_compatibility() -> None:
    document = _Document()
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    values["fit"] = {
        "schema": INTERFACE_FIT_SCHEMA,
        "fit_class": "clearance",
        "designation": "H7/g6",
        "minimum_clearance_mm": 0.008,
        "maximum_clearance_mm": 0.034,
    }

    spec = prepare_component_interface(document, values).spec

    assert spec.compatibility == "mount-v1"
    assert spec.fit == values["fit"]


def test_component_interface_carries_explicit_versioned_relation_parameters() -> None:
    document = _Document()
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    values["allowed_joints"] = ["distance"]
    values["joint_parameters"] = {
        "schema": INTERFACE_JOINT_PARAMETERS_SCHEMA,
        "values": {"distance": {"distance_mm": 12.5}},
    }

    spec = prepare_component_interface(document, values).spec

    assert spec.joint_parameters == values["joint_parameters"]


def test_component_interface_parameters_must_target_an_allowed_relation() -> None:
    document = _Document()
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    values["joint_parameters"] = {
        "schema": INTERFACE_JOINT_PARAMETERS_SCHEMA,
        "values": {"distance": {"distance_mm": 12.5}},
    }

    with pytest.raises(NativeComponentInterfaceError, match="explicitly allowed"):
        prepare_component_interface(document, values)


@pytest.mark.parametrize(
    "fit, message",
    (
        ({"schema": "old", "fit_class": "clearance"}, "unsupported"),
        ({"schema": INTERFACE_FIT_SCHEMA, "fit_class": "unknown"}, "fit_class"),
        (
            {
                "schema": INTERFACE_FIT_SCHEMA,
                "fit_class": "interference",
                "minimum_clearance_mm": -0.01,
            },
            "together",
        ),
        (
            {
                "schema": INTERFACE_FIT_SCHEMA,
                "fit_class": "clearance",
                "minimum_clearance_mm": 0.1,
                "maximum_clearance_mm": 0.01,
            },
            "reversed",
        ),
    ),
)
def test_component_interface_rejects_invalid_or_partial_fit(fit: dict, message: str) -> None:
    document = _Document()
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    values["fit"] = fit
    with pytest.raises(NativeComponentInterfaceError, match=message):
        prepare_component_interface(document, values)


def test_interface_geometry_binding_is_conservative_and_detects_stale_support() -> None:
    document = _Document()
    source = _Object(document, "SupportedFeature", "PartDesign::Feature")
    source.Shape = SimpleNamespace(exportBrepToString=lambda: "brep-v1")
    document.lcs.AttachmentSupport = [(source, ["Face3"])]
    document.lcs.MapMode = "FlatFace"
    binding = capture_native_interface_geometry(document.lcs)
    setattr(
        document.lcs,
        PROP_NATIVE_INTERFACE_GEOMETRY,
        json.dumps(binding, sort_keys=True, separators=(",", ":")),
    )

    current = native_interface_geometry_currentness(document.lcs)
    assert current["schema"] == INTERFACE_GEOMETRY_SCHEMA
    assert current["status"] == "current"
    assert current["supports"][0]["object_name"] == "SupportedFeature"
    assert current["supports"][0]["subelements"] == ["Face3"]

    source.Shape.exportBrepToString = lambda: "brep-v2"
    assert native_interface_geometry_currentness(document.lcs)["status"] == "stale"


def test_interface_geometry_binding_never_promotes_an_unbound_frame() -> None:
    document = _Document()
    binding = capture_native_interface_geometry(document.lcs)
    setattr(
        document.lcs,
        PROP_NATIVE_INTERFACE_GEOMETRY,
        json.dumps(binding, sort_keys=True, separators=(",", ":")),
    )
    assert binding["status"] == "unbound"
    assert native_interface_geometry_currentness(document.lcs)["status"] == "unbound"


def test_interface_semantic_geometry_is_extracted_from_exact_bound_subelement() -> None:
    document = _Document()
    source = _Object(document, "CylinderFeature", "PartDesign::Feature")
    cylinder = SimpleNamespace(
        ShapeType="Face",
        Surface=SimpleNamespace(TypeId="Part::GeomCylinder"),
    )
    source.Shape = SimpleNamespace(
        exportBrepToString=lambda: "cylinder-brep",
        getElement=lambda name: cylinder if name == "Face2" else None,
    )
    document.lcs.AttachmentSupport = [(source, ["Face2"])]

    evidence = semantic_interface_geometry_evidence(document.lcs, "axis")

    assert evidence == {
        "kind": "axis",
        "status": "compatible",
        "expected": ["circle", "cylinder", "line"],
        "observed": ["cylinder"],
    }
    binding = capture_native_interface_geometry(document.lcs, kind="axis")
    assert binding["semantic_evidence"] == evidence


def test_component_interface_rejects_explicitly_incompatible_bound_geometry() -> None:
    document = _Document()
    source = _Object(document, "PlaneFeature", "PartDesign::Feature")
    plane = SimpleNamespace(
        ShapeType="Face",
        Surface=SimpleNamespace(TypeId="Part::GeomPlane"),
    )
    source.Shape = SimpleNamespace(getElement=lambda name: plane)
    document.lcs.AttachmentSupport = [(source, ["Face1"])]
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    values["kind"] = "bore"

    with pytest.raises(NativeComponentInterfaceError, match="incompatible"):
        prepare_component_interface(document, values)


@pytest.mark.parametrize(
    "kind",
    (
        "bearing_face", "bearing_seat", "bolt_pattern", "bore",
        "electrical_connector", "fixture", "fluid_port", "mounting_pattern",
        "planar_mate", "shaft", "shaft_seat", "thread", "thread_axis", "tool",
    ),
)
def test_component_interface_accepts_expanded_semantic_taxonomy(kind: str) -> None:
    document = _Document()
    values = {key: value for key, value in _arguments().items() if key != "operation"}
    values["kind"] = kind
    assert prepare_component_interface(document, values).spec.kind == kind


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


def test_component_interface_runtime_accepts_additive_fit_field(monkeypatch) -> None:
    runtime, state, document = _runtime()
    captured = {}
    arguments = _arguments()
    arguments["fit"] = {
        "schema": INTERFACE_FIT_SCHEMA,
        "fit_class": "threaded",
        "designation": "M8x1.25",
    }
    values = {key: value for key, value in arguments.items() if key != "operation"}
    monkeypatch.setattr(
        runtime_module,
        "prepare_component_interface",
        lambda target_document, target_values: (
            object()
            if target_document is document and target_values == values
            else pytest.fail("fit field did not reach exact preflight")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(context=context, **kwargs)
        or {"routed": True},
    )

    assert runtime.publish_interface(
        arguments,
        ticket=state.begin_call(document.Uid, "component.interface"),
    ) == {"routed": True}


def test_component_interface_runtime_accepts_additive_joint_parameters(monkeypatch) -> None:
    runtime, state, document = _runtime()
    captured = {}
    arguments = _arguments()
    arguments["allowed_joints"] = ["distance"]
    arguments["joint_parameters"] = {
        "schema": INTERFACE_JOINT_PARAMETERS_SCHEMA,
        "values": {"distance": {"distance_mm": 4.0}},
    }
    values = {key: value for key, value in arguments.items() if key != "operation"}
    monkeypatch.setattr(
        runtime_module,
        "prepare_component_interface",
        lambda target_document, target_values: (
            object()
            if target_document is document and target_values == values
            else pytest.fail("joint parameters did not reach exact preflight")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(context=context, **kwargs)
        or {"routed": True},
    )

    assert runtime.publish_interface(
        arguments,
        ticket=state.begin_call(document.Uid, "component.interface"),
    ) == {"routed": True}


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
