# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointArguments as joint_arguments
import VibeCADNativeAssemblyJointIntent as joint_intent
import VibeCADNativeAssemblyMotionJointRuntime as runtime_module
import VibeCADNativeAssemblyRegularJoint as regular_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeAssemblyFixedJoint import (
    FixedJointSpec,
    NativeAssemblyFixedJointError,
    preflight_fixed_joint,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointConnectors import (
    JointConnectorSpec,
    NativeAssemblyJointConnectorError,
    ResolvedJointConnector,
    validate_connector_paths,
)
from VibeCADNativeAssemblyJointGraph import NativeAssemblyJointGraphError
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyJointSchema import (
    assembly_coupling_capability_definition,
    assembly_joint_capability_definition,
    assembly_relation_capability_definition,
)
from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeTargets import NativeObjectRef
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import RibbonSurface


def _fixed_surface() -> RibbonSurface:
    return RibbonSurface.from_manifest(
        {
            "schema_version": 1,
            "surface_id": "assemble",
            "groups": [
                {
                    "label": "Joints",
                    "actions": [
                        {
                            "command_id": "Assembly_CreateJointFixed",
                            "kind": "command",
                            "label": "Fixed Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _placement_mapping(x: float = 0.0) -> dict[str, object]:
    return {
        "origin_mm": {"x": x, "y": 0.0, "z": 0.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


def _connector_mapping(name: str, path: str) -> dict[str, object]:
    return {
        "component": {"object_name": name},
        "element_path": path,
        "anchor_path": path,
        "offset": _placement_mapping(),
        "expected_component_placement": _placement_mapping(),
    }


def test_joint_intent_resolves_a_published_interface_endpoint(monkeypatch) -> None:
    placement = SimpleNamespace(
        Base=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        Rotation=SimpleNamespace(
            Axis=SimpleNamespace(x=0.0, y=0.0, z=1.0),
            Angle=0.0,
        ),
    )
    component = SimpleNamespace(Name="Carrier", Placement=placement)
    monkeypatch.setattr(joint_intent, "resolve_object", lambda _doc, _ref: component)
    monkeypatch.setattr(
        joint_intent.reference_contracts,
        "resolve_component_interface",
        lambda _source, name: {
            "interface_name": name,
            "selection": {"type": "query"},
            "subelements": ["Face21"],
            "geometry": [{"geometry_type": "cylinder"}],
            "connector_frame": None,
        },
    )

    result = joint_intent._connector(
        object(),
        "document-uid",
        {"component": "Carrier", "interface": "Planet1Axis"},
        "first",
    )

    assert result["component"] == {"object_name": "Carrier"}
    assert result["element_path"] == "Face21"
    assert result["anchor_path"] == "Face21"
    assert result["offset"] == _placement_mapping()


def test_fixed_joint_schema_and_action_mapping_are_exact_and_bounded() -> None:
    definition = assembly_joint_capability_definition()
    variant = definition.variants[0]
    published = provider_visible_native_schema(
        definition.provider_schema(("create",))
    )
    schema = published["parameters"]["oneOf"][0]

    assert definition.name == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert definition.preserve_operation_branches is False
    assert variant.operation == "create"
    assert variant.action_ids == frozenset(
        {
            "Assembly_CreateJointFixed",
            "Assembly_CreateJointRevolute",
            "Assembly_CreateJointCylindrical",
            "Assembly_CreateJointSlider",
            "Assembly_CreateJointBall",
        }
    )
    assert variant.surface_ids == frozenset({"assemble"})
    assert set(schema["required"]) == {
        "joint_type",
        "first",
        "second",
    }
    assert set(schema["properties"]) == {
        "joint_type",
        "first",
        "second",
        "label",
        "reverse",
        "limits",
    }
    assert schema["properties"]["joint_type"]["enum"] == [
        "fixed",
        "revolute",
        "cylindrical",
        "slider",
        "ball",
    ]
    assert "operation" not in schema["properties"]
    assert "angle_degrees" not in schema["properties"]
    assert "distance_mm" not in schema["properties"]
    assert "first_revolute_joint" not in schema["properties"]
    assert "components" not in schema["properties"]
    endpoint = schema["properties"]["first"]
    assert endpoint["type"] == "object"
    assert endpoint["required"] == ["component", "connector_type", "connector"]
    assert "oneOf" not in endpoint
    assert "anchor" not in endpoint["properties"]
    assert set(endpoint["properties"]) == {
        "component",
        "connector_type",
        "connector",
        "offset",
    }
    assert endpoint["properties"]["connector_type"]["enum"] == [
        "element",
        "interface",
    ]
    element_pattern = endpoint["properties"]["connector"]["pattern"]
    assert re.fullmatch(element_pattern, "Origin")
    assert re.fullmatch(element_pattern, "origin")
    assert re.fullmatch(element_pattern, "Planet1Axis")
    assert "Face|Edge|Vertex" in endpoint["properties"]["connector"]["pattern"]
    assert schema["additionalProperties"] is False

    plan = classify_native_surface(_fixed_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create"
    assert plan.transaction_behavior == "document"
    registry = build_native_capability_registry()
    assert registry.definition(ASSEMBLY_JOINT_CAPABILITY_NAME) is not None
    assert registry.implementation(ASSEMBLY_JOINT_CAPABILITY_NAME) is not None
    assert assembly_relation_capability_definition().name == "assembly.relation"
    assert assembly_coupling_capability_definition().name == "assembly.coupling"
    assert registry.definition("assembly.relation") is not None
    assert registry.definition("assembly.coupling") is not None
    assert registry.implementation("assembly.relation") is not None
    assert registry.implementation("assembly.coupling") is not None


@pytest.mark.parametrize(
    ("element_path", "anchor_path"),
    [
        ("", ""),
        ("Face6", "Face6"),
        ("Edge2", "Vertex3"),
        ("Body.Pad.Face1", "Body.Pad.Edge2"),
        ("Body.DatumPlane.", "Body.DatumPlane."),
    ],
)
def test_connector_paths_accept_exact_component_relative_forms(
    element_path: str,
    anchor_path: str,
) -> None:
    validate_connector_paths(element_path, anchor_path)


@pytest.mark.parametrize(
    ("element_path", "anchor_path"),
    [
        ("Body.Pad.Face1", "Body.Other.Face1"),
        ("", "Face1"),
        ("Vertex1", "Vertex2"),
        ("Edge1", "Face1"),
        ("Face0", "Face0"),
        ("Body..Face1", "Body..Face1"),
        ("Face1?", "Face1?"),
    ],
)
def test_connector_paths_reject_ambiguous_or_invalid_forms(
    element_path: str,
    anchor_path: str,
) -> None:
    with pytest.raises(NativeAssemblyJointConnectorError):
        validate_connector_paths(element_path, anchor_path)


class _Document:
    Uid = "fixed-document"
    Name = "FixedDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-fixed-unit")
    context = NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "assemble",
        edit_or_task_active=lambda: False,
    )
    return NativeAssemblyJointRuntime(context), state, document


def test_fixed_runtime_routes_complete_exact_spec_before_transaction(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        joint_arguments,
        "joint_placement",
        lambda value, field, _error_type: (field, value),
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_fixed_joint",
        lambda target_document, spec: captured.update(
            preflight_document=target_document,
            spec=spec,
        ),
    )

    def run_immediate(context, **kwargs):
        captured.update(context=context, **kwargs)
        return {"routed": True}

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    expanded = {
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("ComponentA", "Face6"),
        "second": _connector_mapping("ComponentB", "Body.Pad.Face1"),
        "label": "  Base Fixed Joint  ",
        "reverse": True,
        "expected_component_count": 3,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }
    monkeypatch.setattr(
        "VibeCADNativeAssemblyJointRuntime.expand_joint_intent",
        lambda target_document, document_uid, operation, values: (
            captured.update(
                intent_document=target_document,
                intent_document_uid=document_uid,
                intent_operation=operation,
                intent_values=values,
            )
            or expanded
        ),
    )
    arguments = {
        "operation": "create",
        "joint_type": "fixed",
        "first": {
            "component": "ComponentA",
            "connector_type": "element",
            "connector": "Face6",
        },
        "second": {
            "component": "ComponentB",
            "connector_type": "element",
            "connector": "Body.Pad.Face1",
        },
        "label": "Base Fixed Joint",
        "reverse": True,
    }

    result = runtime.mutate_joint(
        arguments,
        ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
    )

    assert result == {"routed": True}
    assert captured["intent_document"] is document
    assert captured["intent_document_uid"] == document.Uid
    assert captured["intent_operation"] == "create_fixed"
    assert captured["intent_values"] == {
        "first": {"component": "ComponentA", "element": "Face6"},
        "second": {"component": "ComponentB", "element": "Body.Pad.Face1"},
        "label": "Base Fixed Joint",
        "reverse": True,
    }
    spec = captured["spec"]
    assert isinstance(spec, FixedJointSpec)
    assert spec.assembly_ref.object_name == "Assembly"
    assert spec.first.component_ref.object_name == "ComponentA"
    assert spec.first.element_path == "Face6"
    assert spec.second.component_ref.object_name == "ComponentB"
    assert spec.second.element_path == "Body.Pad.Face1"
    assert spec.label == "Base Fixed Joint"
    assert spec.reverse is True
    assert spec.expected_component_count == 3
    assert spec.expected_grounded_count == 1
    assert spec.expected_joint_count == 0
    assert spec.expected_solve_on_creation is True
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == "Create Native Assembly Fixed Joint"


def _fixed_spec(document_uid: str = "fixed-document") -> FixedJointSpec:
    placement = object()
    return FixedJointSpec(
        assembly_ref=NativeObjectRef(document_uid, "Assembly"),
        first=JointConnectorSpec(
            NativeObjectRef(document_uid, "ComponentA"),
            "Face1",
            "Face1",
            placement,
            placement,
        ),
        second=JointConnectorSpec(
            NativeObjectRef(document_uid, "ComponentB"),
            "Face1",
            "Face1",
            placement,
            placement,
        ),
        label="Fixed Joint",
        reverse=False,
        expected_component_count=2,
        expected_grounded_count=0,
        expected_joint_count=1,
        expected_solve_on_creation=True,
    )


def _preflight_shell(monkeypatch):
    document = SimpleNamespace(Uid="fixed-document")
    assembly = SimpleNamespace(Document=document)
    joint_group = object()
    component_a = object()
    component_b = object()
    existing = SimpleNamespace(
        JointType="Fixed",
        Reference1=[component_a, ["Face1", "Face1"]],
        Reference2=[component_b, ["Face1", "Face1"]],
    )
    monkeypatch.setattr(regular_module, "resolve_object", lambda *_args, **_kwargs: assembly)
    monkeypatch.setattr(regular_module, "timeline_active", lambda _obj: True)
    monkeypatch.setattr(regular_module, "object_is_valid", lambda _obj: True)
    monkeypatch.setattr(
        regular_module,
        "assembly_components",
        lambda _assembly: (component_a, component_b),
    )
    monkeypatch.setattr(regular_module, "require_joint_group", lambda _assembly: joint_group)
    monkeypatch.setattr(regular_module, "active_regular_joints", lambda _group: (existing,))
    monkeypatch.setattr(regular_module, "active_grounded_joints", lambda _group: ())
    monkeypatch.setattr(regular_module, "_validate_regular_graph", lambda *_args: None)
    monkeypatch.setattr(regular_module, "_validate_grounded_graph", lambda *_args: None)

    def resolve_connector(_document, _assembly, connector_spec):
        component = (
            component_a
            if connector_spec.component_ref.object_name == "ComponentA"
            else component_b
        )
        return ResolvedJointConnector(
            connector_spec,
            component,
            [component, [connector_spec.element_path, connector_spec.anchor_path]],
            component,
            None,
            None,
            object(),
        )

    monkeypatch.setattr(regular_module, "resolve_joint_connector", resolve_connector)
    return document, assembly


def test_fixed_preflight_rejects_duplicate_pair_without_mutation(monkeypatch) -> None:
    document, assembly = _preflight_shell(monkeypatch)

    with pytest.raises(NativeAssemblyFixedJointError, match="already have"):
        preflight_fixed_joint(
            document,
            _fixed_spec(),
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: {"objects": []},
            preference_reader=lambda: True,
        )


def test_fixed_preflight_normalizes_joint_graph_failure(monkeypatch) -> None:
    document, assembly = _preflight_shell(monkeypatch)
    monkeypatch.setattr(
        regular_module,
        "require_joint_group",
        lambda _assembly: (_ for _ in ()).throw(
            NativeAssemblyJointGraphError("malformed joint graph")
        ),
    )

    with pytest.raises(NativeAssemblyFixedJointError, match="malformed joint graph"):
        preflight_fixed_joint(
            document,
            _fixed_spec(),
            active_reader=lambda _document: assembly,
            preference_reader=lambda: True,
        )


def test_regular_joint_postcondition_reports_suppression_exactly(monkeypatch) -> None:
    spec = SimpleNamespace(joint_type="Revolute", label="Planet Joint")
    joint = SimpleNamespace(
        JointType="Revolute",
        Label="Planet Joint",
        Suppressed=True,
        VibeCADTimelineRole="operation",
    )
    monkeypatch.setattr(regular_module, "timeline_active", lambda _joint: True)

    with pytest.raises(
        regular_module.NativeAssemblyRegularJointError,
        match="suppressed during the native solve",
    ):
        regular_module._verify_joint_identity(
            spec,
            joint,
            expected_label="Planet Joint",
            proxy_checker=lambda _joint: True,
            view_checker=lambda _joint: True,
        )


def test_regular_joint_accepts_the_label_assigned_by_freecad(monkeypatch) -> None:
    spec = SimpleNamespace(joint_type="Revolute", label="Revolute Joint")
    joint = SimpleNamespace(
        JointType="Revolute",
        Label="Revolute Joint001",
        Suppressed=False,
        VibeCADTimelineRole="operation",
    )
    monkeypatch.setattr(regular_module, "timeline_active", lambda _joint: True)

    regular_module._verify_joint_identity(
        spec,
        joint,
        expected_label="Revolute Joint001",
        proxy_checker=lambda _joint: True,
        view_checker=lambda _joint: True,
    )
