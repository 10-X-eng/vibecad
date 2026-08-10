# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointArguments as joint_arguments
import VibeCADNativeAssemblyRelationJointRuntime as runtime_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeAssemblyScrewJoint import (
    NativeAssemblyScrewJointError,
    ScrewJointSpec,
    _regular_spec,
    _validate_dependencies,
    screw_dependency_summary,
    thread_pitch_mm,
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
from vibecad_tests.test_native_assembly_rack_pinion_joint import (
    _Axis,
    _Node,
    _Rotation,
    _install_axis_modules,
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
                            "command_id": "Assembly_CreateJointScrew",
                            "kind": "command",
                            "label": "Screw Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(pitch: float = -2.0) -> ScrewJointSpec:
    fixed = _fixed_spec()
    return ScrewJointSpec(
        assembly_ref=fixed.assembly_ref,
        slider_connector=fixed.first,
        screw_connector=fixed.second,
        slider_joint_ref=NativeObjectRef("fixed-document", "SliderJoint"),
        screw_revolute_joint_ref=NativeObjectRef(
            "fixed-document", "ScrewRevoluteJoint"
        ),
        label="Lead Screw Coupling",
        thread_pitch_mm=pitch,
        expected_component_count=3,
        expected_grounded_count=1,
        expected_joint_count=2,
        expected_solve_on_creation=True,
    )


def test_screw_schema_and_action_mapping_are_exact() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "create_screw"
    )
    schema = definition.provider_schema(("create_screw",))["parameters"]["oneOf"][0]

    assert variant.action_ids == frozenset({"Assembly_CreateJointScrew"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "slider_connector",
        "screw_connector",
        "slider_joint",
        "screw_revolute_joint",
        "label",
        "thread_pitch_mm",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    }
    assert schema["properties"]["thread_pitch_mm"]["oneOf"] == [
        {"type": "number", "minimum": -1_000_000.0, "maximum": -1.0e-7},
        {"type": "number", "minimum": 1.0e-7, "maximum": 1_000_000.0},
    ]
    assert not {
        "first",
        "second",
        "reverse",
        "angle",
        "limits",
        "radius2_mm",
    } & set(schema["properties"])
    assert schema["additionalProperties"] is False
    plan = classify_native_surface(_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_screw"
    assert plan.transaction_behavior == "document"


@pytest.mark.parametrize(
    "value",
    [True, 0.0, 1.0e-8, -1.0e-8, math.nan, math.inf, -math.inf, "pitch"],
)
def test_thread_pitch_rejects_zero_unsafe_or_nonfinite_values(value) -> None:
    with pytest.raises(NativeAssemblyScrewJointError, match="thread_pitch_mm"):
        thread_pitch_mm(value)


@pytest.mark.parametrize("value", [-1_000_000.0, -2.0, 1.0e-7, 2, 1_000_000.0])
def test_thread_pitch_accepts_complete_bounded_signed_range(value) -> None:
    assert thread_pitch_mm(value) == float(value)


def test_screw_spec_maps_real_type_property_and_compiled_ratio() -> None:
    regular = _regular_spec(_spec(-2.0))

    assert regular.joint_type == "Screw"
    assert regular.type_index == 10
    assert regular.reverse is False
    assert [(item.name, item.value) for item in regular.properties] == [
        ("Distance", -2.0)
    ]


def _dependency_fixture(monkeypatch):
    _install_axis_modules(monkeypatch)
    spec = _spec()
    document = _Node(Uid="fixed-document")
    slider_component = _Node(Name="ComponentA", Document=document)
    screw_component = _Node(Name="ComponentB", Document=document)
    base = _Node(Name="Base", Document=document)
    slider = _Node(
        Name="SliderJoint",
        Document=document,
        TypeId="App::FeaturePython",
        JointType="Slider",
        Reference1=[base, ["Face1", "Face1"]],
        Reference2=[slider_component, ["Face1", "Face1"]],
        Offset1=object(),
        Offset2=spec.slider_connector.offset,
        Placement1=SimpleNamespace(
            Rotation=_Rotation((0.0, 0.0, 1.0)),
            Base=_Axis((0.0, 0.0, 0.0)),
        ),
        Placement2=SimpleNamespace(
            Rotation=_Rotation((0.0, 0.0, 1.0)),
            Base=_Axis((0.0, 0.0, 0.0)),
        ),
    )
    revolute = _Node(
        Name="ScrewRevoluteJoint",
        Document=document,
        TypeId="App::FeaturePython",
        JointType="Revolute",
        Reference1=[base, ["Face1", "Face1"]],
        Reference2=[screw_component, ["Face1", "Face1"]],
        Offset1=object(),
        Offset2=spec.screw_connector.offset,
        Placement1=SimpleNamespace(
            Rotation=_Rotation((0.0, 0.0, 1.0)),
            Base=_Axis((0.0, 0.0, 5.0)),
        ),
        Placement2=SimpleNamespace(
            Rotation=_Rotation((0.0, 0.0, 1.0)),
            Base=_Axis((0.0, 0.0, 5.0)),
        ),
    )
    prepared = _Node(
        regular_joints_before=(slider, revolute),
        grounded_joints_before=(),
        first=_Node(component=slider_component),
        second=_Node(component=screw_component),
    )
    return (
        document,
        slider_component,
        screw_component,
        slider,
        revolute,
        prepared,
        spec,
    )


def test_dependencies_require_exact_reused_directed_collinear_frames(
    monkeypatch,
) -> None:
    (
        _document,
        _slider_component,
        _screw_component,
        slider,
        revolute,
        prepared,
        spec,
    ) = _dependency_fixture(monkeypatch)

    assert _validate_dependencies(prepared, spec, slider, revolute) == (2, 2)

    revolute.Placement2.Base = _Axis((0.01, 0.0, 5.0))
    with pytest.raises(NativeAssemblyScrewJointError, match="collinear"):
        _validate_dependencies(prepared, spec, slider, revolute)
    revolute.Placement2.Base = _Axis((0.0, 0.0, 5.0))
    revolute.Placement2.Rotation = _Rotation((0.0, 0.0, -1.0))
    with pytest.raises(NativeAssemblyScrewJointError, match="directed"):
        _validate_dependencies(prepared, spec, slider, revolute)


def test_dependency_summary_derives_exact_persisted_joint_identities(
    monkeypatch,
) -> None:
    (
        document,
        slider_component,
        screw_component,
        slider,
        revolute,
        _prepared,
        spec,
    ) = _dependency_fixture(monkeypatch)
    coupling = _Node(
        Name="ScrewCoupling",
        Document=document,
        JointType="Screw",
        Reference1=[slider_component, ["Face1", "Face1"]],
        Reference2=[screw_component, ["Face1", "Face1"]],
        Offset1=spec.slider_connector.offset,
        Offset2=spec.screw_connector.offset,
    )

    summary = screw_dependency_summary(coupling, (slider, revolute, coupling))

    assert summary == {
        "slider_joint": {
            "document_uid": "fixed-document",
            "object_name": "SliderJoint",
            "type_id": "App::FeaturePython",
        },
        "screw_revolute_joint": {
            "document_uid": "fixed-document",
            "object_name": "ScrewRevoluteJoint",
            "type_id": "App::FeaturePython",
        },
        "axes_collinear": True,
    }


class _Document:
    Uid = "screw-document"
    Name = "ScrewDocument"


def _runtime() -> tuple[
    NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document
]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-screw-unit")
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
        "operation": "create_screw",
        "assembly": {"object_name": "Assembly"},
        "slider_connector": _connector_mapping("Slider", "Face6"),
        "screw_connector": _connector_mapping("Screw", "Body.Pad.Face1"),
        "slider_joint": {"object_name": "SliderJoint"},
        "screw_revolute_joint": {"object_name": "ScrewRevoluteJoint"},
        "label": "  Lead Screw Coupling  ",
        "thread_pitch_mm": -2.0,
        "expected_component_count": 3,
        "expected_grounded_count": 1,
        "expected_joint_count": 2,
        "expected_solve_on_creation": True,
    }


def test_screw_runtime_routes_complete_exact_spec_before_transaction(
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
        "preflight_screw_joint",
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
    assert isinstance(spec, ScrewJointSpec)
    assert spec.assembly_ref.object_name == "Assembly"
    assert spec.slider_connector.component_ref.object_name == "Slider"
    assert spec.screw_connector.component_ref.object_name == "Screw"
    assert spec.slider_joint_ref.object_name == "SliderJoint"
    assert spec.screw_revolute_joint_ref.object_name == "ScrewRevoluteJoint"
    assert spec.label == "Lead Screw Coupling"
    assert spec.thread_pitch_mm == -2.0
    assert spec.expected_component_count == 3
    assert spec.expected_grounded_count == 1
    assert spec.expected_joint_count == 2
    assert spec.expected_solve_on_creation is True
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == "Create Native Assembly Screw Joint"


@pytest.mark.parametrize(
    "extra",
    [
        {"first": {}},
        {"second": {}},
        {"reverse": True},
        {"angle": 30.0},
        {"limits": {}},
        {"radius2_mm": 4.0},
    ],
)
def test_screw_runtime_rejects_inapplicable_fields_before_guard(
    monkeypatch,
    extra,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_screw_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = _arguments()
    arguments.update(extra)

    with pytest.raises(NativeArgumentError):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
