# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointArguments as joint_arguments
import VibeCADNativeAssemblyRelationJointRuntime as runtime_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyGearJoint import (
    GearJointSpec,
    NativeAssemblyGearJointError,
    _regular_spec,
    _validate_dependencies,
    gear_radius_mm,
    gears_dependency_summary,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeTargets import NativeObjectRef
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import RibbonSurface

from vibecad_tests.test_native_assembly_fixed_joint import (
    _connector_mapping,
    _fixed_spec,
)
from vibecad_tests.test_native_assembly_rack_pinion_joint import _Node


def _surface() -> RibbonSurface:
    return RibbonSurface.from_manifest(
        {
            "schema_version": 1,
            "surface_id": "assemble",
            "groups": [
                {
                    "label": "Joints",
                    "actions": [
                        {
                            "command_id": "Assembly_CreateJointGears",
                            "kind": "command",
                            "label": "Gears Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(radius1: float = 20.0, radius2: float = 40.0) -> GearJointSpec:
    fixed = _fixed_spec()
    return GearJointSpec(
        assembly_ref=fixed.assembly_ref,
        first_gear_connector=fixed.first,
        second_gear_connector=fixed.second,
        first_revolute_joint_ref=NativeObjectRef("fixed-document", "FirstRevolute"),
        second_revolute_joint_ref=NativeObjectRef("fixed-document", "SecondRevolute"),
        label="External Gear Coupling",
        radius1_mm=radius1,
        radius2_mm=radius2,
        expected_component_count=3,
        expected_grounded_count=1,
        expected_joint_count=2,
        expected_solve_on_creation=True,
    )


def test_gears_schema_and_live_action_mapping_are_exact() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "create_gears"
    )
    schema = definition.provider_schema(("create_gears",))["parameters"]["oneOf"][0]

    assert variant.action_ids == frozenset({"Assembly_CreateJointGears"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "first_gear_connector",
        "second_gear_connector",
        "first_revolute_joint",
        "second_revolute_joint",
        "label",
        "radius1_mm",
        "radius2_mm",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    }
    assert schema["properties"]["radius1_mm"] == {
        "type": "number",
        "minimum": 1.0e-7,
        "maximum": 1_000_000.0,
    }
    assert schema["properties"]["radius2_mm"] == {
        "type": "number",
        "minimum": 1.0e-7,
        "maximum": 1_000_000.0,
    }
    assert not {
        "first",
        "second",
        "reverse",
        "angle",
        "limits",
        "pitch_radius_mm",
    } & set(schema["properties"])
    assert schema["additionalProperties"] is False
    plan = classify_native_surface(_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_gears"
    assert plan.transaction_behavior == "document"


@pytest.mark.parametrize(
    "value",
    [True, 0.0, -1.0e-7, 1.0e-8, math.nan, math.inf, -math.inf, "radius"],
)
def test_gear_radius_rejects_nonpositive_unsafe_or_nonfinite_values(value) -> None:
    with pytest.raises(NativeAssemblyGearJointError, match="radius1_mm"):
        gear_radius_mm(value, "radius1_mm")


@pytest.mark.parametrize("value", [1.0e-7, 1.0, 20, 1_000_000.0])
def test_gear_radius_accepts_complete_human_positive_range(value) -> None:
    assert gear_radius_mm(value, "radius1_mm") == float(value)


def test_gear_spec_maps_real_type_properties_and_compiled_ratio() -> None:
    regular = _regular_spec(_spec(20.0, 40.0))

    assert regular.joint_type == "Gears"
    assert regular.type_index == 11
    assert regular.reverse is False
    assert [(item.name, item.value) for item in regular.properties] == [
        ("Distance", 20.0),
        ("Distance2", 40.0),
    ]


def _dependency_fixture():
    spec = _spec()
    document = _Node(Uid="fixed-document")
    first_gear = _Node(Name="ComponentA", Document=document)
    second_gear = _Node(Name="ComponentB", Document=document)
    base = _Node(Name="Base", Document=document)
    first_revolute = _Node(
        Name="FirstRevolute",
        Document=document,
        TypeId="App::FeaturePython",
        JointType="Revolute",
        Reference1=[base, ["Face1", "Face1"]],
        Reference2=[first_gear, ["Face1", "Face1"]],
        Offset1=object(),
        Offset2=spec.first_gear_connector.offset,
    )
    second_revolute = _Node(
        Name="SecondRevolute",
        Document=document,
        TypeId="App::FeaturePython",
        JointType="Revolute",
        Reference1=[base, ["Face1", "Face1"]],
        Reference2=[second_gear, ["Face1", "Face1"]],
        Offset1=object(),
        Offset2=spec.second_gear_connector.offset,
    )
    prepared = _Node(
        regular_joints_before=(first_revolute, second_revolute),
        grounded_joints_before=(),
        first=_Node(component=first_gear),
        second=_Node(component=second_gear),
    )
    return (
        document,
        first_gear,
        second_gear,
        first_revolute,
        second_revolute,
        prepared,
        spec,
    )


def test_dependencies_require_two_distinct_exact_reused_revolute_frames() -> None:
    (
        _document,
        _first_gear,
        _second_gear,
        first_revolute,
        second_revolute,
        prepared,
        spec,
    ) = _dependency_fixture()

    assert _validate_dependencies(
        prepared,
        spec,
        first_revolute,
        second_revolute,
    ) == (2, 2)

    with pytest.raises(NativeAssemblyGearJointError, match="distinct exact active"):
        _validate_dependencies(prepared, spec, first_revolute, first_revolute)

    second_revolute.Offset2 = object()
    with pytest.raises(NativeAssemblyGearJointError, match="exactly reuse"):
        _validate_dependencies(
            prepared,
            spec,
            first_revolute,
            second_revolute,
        )


def test_dependencies_reject_grounded_or_cross_constrained_gears() -> None:
    (
        _document,
        first_gear,
        second_gear,
        first_revolute,
        second_revolute,
        prepared,
        spec,
    ) = _dependency_fixture()
    prepared.grounded_joints_before = (_Node(ObjectToGround=first_gear),)
    with pytest.raises(
        NativeAssemblyGearJointError, match="rather than being grounded"
    ):
        _validate_dependencies(
            prepared,
            spec,
            first_revolute,
            second_revolute,
        )

    prepared.grounded_joints_before = ()
    first_revolute.Reference1 = [second_gear, ["Face1", "Face1"]]
    with pytest.raises(NativeAssemblyGearJointError, match="distinct rotating"):
        _validate_dependencies(
            prepared,
            spec,
            first_revolute,
            second_revolute,
        )


def test_dependency_summary_derives_both_persisted_revolute_identities() -> None:
    (
        document,
        first_gear,
        second_gear,
        first_revolute,
        second_revolute,
        _prepared,
        spec,
    ) = _dependency_fixture()
    coupling = _Node(
        Name="GearCoupling",
        Document=document,
        JointType="Gears",
        Reference1=[first_gear, ["Face1", "Face1"]],
        Reference2=[second_gear, ["Face1", "Face1"]],
        Offset1=spec.first_gear_connector.offset,
        Offset2=spec.second_gear_connector.offset,
    )

    summary = gears_dependency_summary(
        coupling,
        (first_revolute, second_revolute, coupling),
    )

    assert summary == {
        "first_revolute_joint": {
            "document_uid": "fixed-document",
            "object_name": "FirstRevolute",
            "type_id": "App::FeaturePython",
        },
        "second_revolute_joint": {
            "document_uid": "fixed-document",
            "object_name": "SecondRevolute",
            "type_id": "App::FeaturePython",
        },
    }


class _Document:
    Uid = "gear-document"
    Name = "GearDocument"


def _runtime() -> tuple[
    NativeAssemblyJointRuntime,
    NativeDocumentStateStore,
    _Document,
]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-gear-unit")
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


def _arguments() -> dict[str, object]:
    return {
        "operation": "create_gears",
        "assembly": {"object_name": "Assembly"},
        "first_gear_connector": _connector_mapping("GearOne", "Face6"),
        "second_gear_connector": _connector_mapping("GearTwo", "Body.Pad.Face1"),
        "first_revolute_joint": {"object_name": "FirstRevolute"},
        "second_revolute_joint": {"object_name": "SecondRevolute"},
        "label": "  External Gear Coupling  ",
        "radius1_mm": 20.0,
        "radius2_mm": 40.0,
        "expected_component_count": 3,
        "expected_grounded_count": 1,
        "expected_joint_count": 2,
        "expected_solve_on_creation": True,
    }


def test_gear_runtime_routes_complete_exact_spec_before_transaction(
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
        "preflight_gear_joint",
        lambda target_document, spec: captured.update(
            preflight_document=target_document,
            spec=spec,
        ),
    )

    def run_immediate(context, **kwargs):
        captured.update(context=context, **kwargs)
        return {"routed": True}

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)

    result = runtime.mutate_joint(
        _arguments(),
        ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
    )

    assert result == {"routed": True}
    spec = captured["spec"]
    assert isinstance(spec, GearJointSpec)
    assert spec.assembly_ref.object_name == "Assembly"
    assert spec.first_gear_connector.component_ref.object_name == "GearOne"
    assert spec.second_gear_connector.component_ref.object_name == "GearTwo"
    assert spec.first_revolute_joint_ref.object_name == "FirstRevolute"
    assert spec.second_revolute_joint_ref.object_name == "SecondRevolute"
    assert spec.label == "External Gear Coupling"
    assert spec.radius1_mm == 20.0
    assert spec.radius2_mm == 40.0
    assert spec.expected_component_count == 3
    assert spec.expected_grounded_count == 1
    assert spec.expected_joint_count == 2
    assert spec.expected_solve_on_creation is True
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == "Create Native Assembly Gears Joint"


@pytest.mark.parametrize(
    "extra",
    [
        {"first": {}},
        {"second": {}},
        {"reverse": True},
        {"angle": 30.0},
        {"limits": {}},
        {"pitch_radius_mm": 20.0},
        {"radius_1_mm": 20.0},
    ],
)
def test_gear_runtime_rejects_inapplicable_or_aliased_fields_before_guard(
    monkeypatch,
    extra,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_gear_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = _arguments()
    arguments.update(extra)

    with pytest.raises(NativeArgumentError):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
