# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointArguments as joint_arguments
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
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
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


def test_fixed_joint_schema_and_action_mapping_are_exact_and_bounded() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "create_fixed"
    )
    schema = definition.provider_schema(("create_fixed",))["parameters"]["oneOf"][0]

    assert definition.name == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert variant.action_ids == frozenset({"Assembly_CreateJointFixed"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "first",
        "second",
        "label",
        "reverse",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    }
    assert schema["properties"]["expected_joint_count"]["maximum"] == 256
    assert schema["properties"]["first"]["additionalProperties"] is False
    assert schema["properties"]["first"]["properties"]["element_path"][
        "maxLength"
    ] == 512
    assert schema["additionalProperties"] is False

    plan = classify_native_surface(_fixed_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_fixed"
    assert plan.transaction_behavior == "document"
    registry = build_native_capability_registry()
    assert registry.definition(ASSEMBLY_JOINT_CAPABILITY_NAME) is not None
    assert registry.implementation(ASSEMBLY_JOINT_CAPABILITY_NAME) is not None


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
    arguments = {
        "operation": "create_fixed",
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

    result = runtime.mutate_joint(
        arguments,
        ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
    )

    assert result == {"routed": True}
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
