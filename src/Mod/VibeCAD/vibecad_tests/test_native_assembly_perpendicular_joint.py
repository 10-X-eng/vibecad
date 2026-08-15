# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointArguments as joint_arguments
import VibeCADNativeAssemblyPerpendicularJoint as perpendicular_module
import VibeCADNativeAssemblyRelationJointRuntime as runtime_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeAssemblyPerpendicularJoint import (
    NativeAssemblyPerpendicularJointError,
    PerpendicularJointSpec,
    _regular_spec,
    perpendicular_axes_satisfied,
    verify_perpendicular_joint,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
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
                            "command_id": "Assembly_CreateJointPerpendicular",
                            "kind": "command",
                            "label": "Perpendicular Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(*, solve: bool = True) -> PerpendicularJointSpec:
    fixed = _fixed_spec()
    return PerpendicularJointSpec(
        assembly_ref=fixed.assembly_ref,
        first=fixed.first,
        second=fixed.second,
        label="Perpendicular Axes",
        expected_component_count=2,
        expected_grounded_count=1,
        expected_joint_count=0,
        expected_solve_on_creation=solve,
    )


def test_perpendicular_schema_and_action_mapping_are_exact() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "create_perpendicular"
    )
    schema = definition.provider_schema(("create_perpendicular",))["parameters"][
        "oneOf"
    ][0]

    assert variant.action_ids == frozenset({"Assembly_CreateJointPerpendicular"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "first",
        "second",
        "label",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    }
    assert not {"reverse", "angle", "distance", "limits", "rotation"} & set(
        schema["properties"]
    )
    assert schema["additionalProperties"] is False
    plan = classify_native_surface(_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_perpendicular"
    assert plan.transaction_behavior == "document"


def test_perpendicular_spec_maps_only_the_real_native_joint_contract() -> None:
    regular = _regular_spec(_spec())

    assert regular.joint_type == "Perpendicular"
    assert regular.type_index == 7
    assert regular.reverse is False
    assert regular.properties == ()


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


def test_perpendicular_axis_measure_uses_live_global_z_dot_product(monkeypatch) -> None:
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
    joint = SimpleNamespace(
        Placement1=SimpleNamespace(Rotation=_Rotation((0.0, 0.0, 1.0))),
        Placement2=SimpleNamespace(Rotation=_Rotation((1.0, 0.0, 0.0))),
        Reference1=object(),
        Reference2=object(),
    )
    assert perpendicular_axes_satisfied(joint) is True

    joint.Placement2.Rotation = _Rotation((1.0, 0.0, 1.0e-4))
    assert perpendicular_axes_satisfied(joint) is False


@pytest.mark.parametrize(("solve", "satisfied"), [(True, True), (False, False)])
def test_perpendicular_result_reports_exact_axis_state(
    monkeypatch,
    solve: bool,
    satisfied: bool,
) -> None:
    spec = _spec(solve=solve)
    joint = object()
    monkeypatch.setattr(
        perpendicular_module,
        "verify_regular_joint",
        lambda *_args, **_kwargs: {
            "joint_type": "Perpendicular",
            "reverse": False,
            "properties": {},
            "joint_count": 1,
        },
    )
    monkeypatch.setattr(
        perpendicular_module,
        "perpendicular_axes_satisfied",
        lambda _joint: satisfied,
    )

    result = verify_perpendicular_joint(
        object(),
        NativeMutationDraft(value={"spec": spec, "joint": joint}),
    )

    assert result == {
        "joint_type": "Perpendicular",
        "joint_count": 1,
        "axes_perpendicular": satisfied,
    }


def test_perpendicular_solved_postcondition_rejects_nonperpendicular_axes(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        perpendicular_module,
        "verify_regular_joint",
        lambda *_args, **_kwargs: {"reverse": False, "properties": {}},
    )
    monkeypatch.setattr(
        perpendicular_module,
        "perpendicular_axes_satisfied",
        lambda _joint: False,
    )

    with pytest.raises(NativeAssemblyPerpendicularJointError, match="did not make"):
        verify_perpendicular_joint(
            object(),
            NativeMutationDraft(value={"spec": _spec(), "joint": object()}),
        )


class _Document:
    Uid = "perpendicular-document"
    Name = "PerpendicularDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-perpendicular-unit")
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
        "operation": "create_perpendicular",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Base", "Face6"),
        "second": _connector_mapping("Arm", "Body.Pad.Face1"),
        "label": "  Perpendicular Axes  ",
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }


def test_perpendicular_runtime_routes_complete_exact_spec_before_transaction(
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
        "preflight_perpendicular_joint",
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
    assert isinstance(spec, PerpendicularJointSpec)
    assert spec.assembly_ref.object_name == "Assembly"
    assert spec.first.component_ref.object_name == "Base"
    assert spec.second.component_ref.object_name == "Arm"
    assert spec.label == "Perpendicular Axes"
    assert spec.expected_component_count == 2
    assert spec.expected_grounded_count == 1
    assert spec.expected_joint_count == 0
    assert spec.expected_solve_on_creation is True
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == (
        "Create Native Assembly Perpendicular Joint"
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"reverse": True},
        {"angle": 90.0},
        {"distance": 1.0},
        {"limits": {}},
        {"rotation": 90.0},
    ],
)
def test_perpendicular_runtime_rejects_inapplicable_fields_before_guard(
    monkeypatch,
    extra,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_perpendicular_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = _arguments()
    arguments.update(extra)

    with pytest.raises(NativeArgumentError):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
