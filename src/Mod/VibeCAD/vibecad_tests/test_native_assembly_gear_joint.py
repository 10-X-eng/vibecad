# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyRelationJointRuntime as runtime_module
import VibeCADNativeAssemblyRotationCoupling as rotation_coupling
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
        grounded_joints_before=(_Node(ObjectToGround=base),),
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


def test_dependencies_accept_a_revolute_mounted_on_a_moving_support() -> None:
    (
        document,
        _first_gear,
        _second_gear,
        first_revolute,
        second_revolute,
        prepared,
        spec,
    ) = _dependency_fixture()
    base = first_revolute.Reference1[0]
    carrier = _Node(Name="Carrier", Document=document)
    carrier_revolute = _Node(
        Name="CarrierRevolute",
        Document=document,
        JointType="Revolute",
        Reference1=[base, ["Face1", "Face1"]],
        Reference2=[carrier, ["Face1", "Face1"]],
    )
    first_revolute.Reference1 = [carrier, ["Face1", "Face1"]]
    prepared.regular_joints_before = (
        carrier_revolute,
        first_revolute,
        second_revolute,
    )
    prepared.grounded_joints_before = (_Node(ObjectToGround=base),)

    assert _validate_dependencies(
        prepared,
        spec,
        first_revolute,
        second_revolute,
    ) == (2, 2)


def test_dependencies_reject_coincident_global_gear_axes(monkeypatch) -> None:
    (
        _document,
        _first_gear,
        _second_gear,
        first_revolute,
        second_revolute,
        prepared,
        spec,
    ) = _dependency_fixture()
    monkeypatch.setattr(
        rotation_coupling,
        "rotation_coupling_axis_separation_mm",
        lambda _first, _second: 0.0,
    )

    with pytest.raises(NativeAssemblyGearJointError, match="axes are coincident"):
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
