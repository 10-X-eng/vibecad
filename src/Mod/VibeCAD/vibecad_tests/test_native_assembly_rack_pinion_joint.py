# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
import sys
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointArguments as joint_arguments
import VibeCADNativeAssemblyRelationJointRuntime as runtime_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeAssemblyRackPinionJoint import (
    NativeAssemblyRackPinionJointError,
    RackPinionJointSpec,
    _regular_spec,
    _validate_dependencies,
    pitch_radius_mm,
    rack_pinion_dependency_summary,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeTargets import NativeObjectRef
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import RibbonSurface

from vibecad_tests.test_native_assembly_fixed_joint import (
    _connector_mapping,
    _fixed_spec,
)


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
                            "command_id": "Assembly_CreateJointRackPinion",
                            "kind": "command",
                            "label": "Rack and Pinion Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(radius: float = 20.0) -> RackPinionJointSpec:
    fixed = _fixed_spec()
    return RackPinionJointSpec(
        assembly_ref=fixed.assembly_ref,
        rack_connector=fixed.first,
        pinion_connector=fixed.second,
        rack_slider_joint_ref=NativeObjectRef("fixed-document", "RackSlider"),
        pinion_revolute_joint_ref=NativeObjectRef(
            "fixed-document", "PinionRevolute"
        ),
        label="Rack-Pinion Coupling",
        pitch_radius_mm=radius,
        expected_component_count=3,
        expected_grounded_count=1,
        expected_joint_count=2,
        expected_solve_on_creation=True,
    )


def test_rack_pinion_schema_and_action_mapping_are_exact() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "create_rack_pinion"
    )
    schema = definition.provider_schema(("create_rack_pinion",))["parameters"][
        "oneOf"
    ][0]

    assert variant.action_ids == frozenset({"Assembly_CreateJointRackPinion"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "rack_connector",
        "pinion_connector",
        "rack_slider_joint",
        "pinion_revolute_joint",
        "label",
        "pitch_radius_mm",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    }
    assert schema["properties"]["pitch_radius_mm"]["oneOf"] == [
        {"type": "number", "minimum": -1_000_000.0, "maximum": -1.0e-7},
        {"type": "number", "minimum": 1.0e-7, "maximum": 1_000_000.0},
    ]
    assert not {"first", "second", "reverse", "angle", "limits"} & set(
        schema["properties"]
    )
    assert schema["additionalProperties"] is False
    plan = classify_native_surface(_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_rack_pinion"
    assert plan.transaction_behavior == "document"


@pytest.mark.parametrize(
    "value",
    [True, 0.0, 1.0e-8, -1.0e-8, math.nan, math.inf, -math.inf, "radius"],
)
def test_pitch_radius_rejects_zero_unsafe_or_nonfinite_values(value) -> None:
    with pytest.raises(NativeAssemblyRackPinionJointError, match="pitch_radius_mm"):
        pitch_radius_mm(value)


@pytest.mark.parametrize("value", [-1_000_000.0, -20.0, 1.0e-7, 20, 1_000_000.0])
def test_pitch_radius_accepts_complete_bounded_signed_range(value) -> None:
    assert pitch_radius_mm(value) == float(value)


def test_rack_pinion_spec_maps_real_type_property_and_ratio_direction() -> None:
    regular = _regular_spec(_spec(-20.0))

    assert regular.joint_type == "RackPinion"
    assert regular.type_index == 9
    assert regular.reverse is False
    assert [(item.name, item.value) for item in regular.properties] == [
        ("Distance", -20.0)
    ]


class _Axis:
    def __init__(self, xyz: tuple[float, float, float]) -> None:
        self.xyz = xyz

    def dot(self, other: _Axis) -> float:
        return sum(left * right for left, right in zip(self.xyz, other.xyz))


class _Rotation:
    def __init__(self, z_axis: tuple[float, float, float]) -> None:
        self.z_axis = _Axis(z_axis)

    def multVec(self, _vector) -> _Axis:
        return self.z_axis


class _Node(SimpleNamespace):
    __hash__ = object.__hash__


def _install_axis_modules(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(Vector=lambda *_coordinates: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "UtilsAssembly",
        SimpleNamespace(getJcsGlobalPlc=lambda placement, _reference: placement),
    )


def _dependency_fixture(monkeypatch):
    _install_axis_modules(monkeypatch)
    spec = _spec()
    document = _Node(Uid="fixed-document")
    rack = _Node(Name="ComponentA", Document=document)
    pinion = _Node(Name="ComponentB", Document=document)
    base = _Node(Name="Base", Document=document)
    slider = _Node(
        Name="RackSlider",
        Document=document,
        TypeId="App::FeaturePython",
        JointType="Slider",
        Reference1=[base, ["Face1", "Face1"]],
        Reference2=[rack, ["Face1", "Face1"]],
        Offset1=object(),
        Offset2=spec.rack_connector.offset,
        Placement1=SimpleNamespace(Rotation=_Rotation((1.0, 0.0, 0.0))),
        Placement2=SimpleNamespace(Rotation=_Rotation((1.0, 0.0, 0.0))),
    )
    revolute = _Node(
        Name="PinionRevolute",
        Document=document,
        TypeId="App::FeaturePython",
        JointType="Revolute",
        Reference1=[base, ["Face1", "Face1"]],
        Reference2=[pinion, ["Face1", "Face1"]],
        Offset1=object(),
        Offset2=spec.pinion_connector.offset,
        Placement1=SimpleNamespace(Rotation=_Rotation((0.0, 0.0, 1.0))),
        Placement2=SimpleNamespace(Rotation=_Rotation((0.0, 0.0, 1.0))),
    )
    prepared = _Node(
        regular_joints_before=(slider, revolute),
        grounded_joints_before=(),
        first=_Node(component=rack),
        second=_Node(component=pinion),
    )
    return document, rack, pinion, slider, revolute, prepared, spec


def test_dependencies_require_exact_reused_perpendicular_joint_frames(
    monkeypatch,
) -> None:
    (
        _document,
        _rack,
        _pinion,
        slider,
        revolute,
        prepared,
        spec,
    ) = _dependency_fixture(monkeypatch)

    assert _validate_dependencies(prepared, spec, slider, revolute) == (2, 2)

    revolute.Placement2.Rotation = _Rotation((1.0, 0.0, 0.0))
    with pytest.raises(NativeAssemblyRackPinionJointError, match="perpendicular"):
        _validate_dependencies(prepared, spec, slider, revolute)


def test_dependency_summary_derives_exact_persisted_joint_identities(monkeypatch) -> None:
    document, rack, pinion, slider, revolute, _prepared, spec = _dependency_fixture(
        monkeypatch
    )
    coupling = _Node(
        Name="RackPinion",
        Document=document,
        JointType="RackPinion",
        Reference1=[rack, ["Face1", "Face1"]],
        Reference2=[pinion, ["Face1", "Face1"]],
        Offset1=spec.rack_connector.offset,
        Offset2=spec.pinion_connector.offset,
    )

    summary = rack_pinion_dependency_summary(
        coupling,
        (slider, revolute, coupling),
    )

    assert summary == {
        "rack_slider_joint": {
            "document_uid": "fixed-document",
            "object_name": "RackSlider",
            "type_id": "App::FeaturePython",
        },
        "pinion_revolute_joint": {
            "document_uid": "fixed-document",
            "object_name": "PinionRevolute",
            "type_id": "App::FeaturePython",
        },
        "axes_perpendicular": True,
    }


class _Document:
    Uid = "rack-pinion-document"
    Name = "RackPinionDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-rack-pinion-unit")
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
        "operation": "create_rack_pinion",
        "assembly": {"object_name": "Assembly"},
        "rack_connector": _connector_mapping("Rack", "Face6"),
        "pinion_connector": _connector_mapping("Pinion", "Body.Pad.Face1"),
        "rack_slider_joint": {"object_name": "RackSlider"},
        "pinion_revolute_joint": {"object_name": "PinionRevolute"},
        "label": "  Rack-Pinion Coupling  ",
        "pitch_radius_mm": -20.0,
        "expected_component_count": 3,
        "expected_grounded_count": 1,
        "expected_joint_count": 2,
        "expected_solve_on_creation": True,
    }


def test_rack_pinion_runtime_routes_complete_exact_spec_before_transaction(
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
        "preflight_rack_pinion_joint",
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
    assert isinstance(spec, RackPinionJointSpec)
    assert spec.assembly_ref.object_name == "Assembly"
    assert spec.rack_connector.component_ref.object_name == "Rack"
    assert spec.pinion_connector.component_ref.object_name == "Pinion"
    assert spec.rack_slider_joint_ref.object_name == "RackSlider"
    assert spec.pinion_revolute_joint_ref.object_name == "PinionRevolute"
    assert spec.label == "Rack-Pinion Coupling"
    assert spec.pitch_radius_mm == -20.0
    assert spec.expected_component_count == 3
    assert spec.expected_grounded_count == 1
    assert spec.expected_joint_count == 2
    assert spec.expected_solve_on_creation is True
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == (
        "Create Native Assembly Rack-and-Pinion Joint"
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"first": {}},
        {"second": {}},
        {"reverse": True},
        {"angle": 30.0},
        {"limits": {}},
    ],
)
def test_rack_pinion_runtime_rejects_inapplicable_fields_before_guard(
    monkeypatch,
    extra,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_rack_pinion_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = _arguments()
    arguments.update(extra)

    with pytest.raises(NativeArgumentError):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
