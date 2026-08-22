# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
import sys
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyAngleJoint as angle_module
import VibeCADNativeAssemblyRelationJointRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyAngleJoint import (
    AngleJointSpec,
    NativeAssemblyAngleJointError,
    _regular_spec,
    angle_axes_satisfied,
    angle_solver_relation,
    canonical_angle_degrees,
    measured_axis_angle_degrees,
    verify_angle_joint,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
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
                            "command_id": "Assembly_CreateJointAngle",
                            "kind": "command",
                            "label": "Angle Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(*, solve: bool = True, angle: float = 60.0) -> AngleJointSpec:
    fixed = _fixed_spec()
    return AngleJointSpec(
        assembly_ref=fixed.assembly_ref,
        first=fixed.first,
        second=fixed.second,
        label="Sixty Degree Axes",
        angle_degrees=angle,
        expected_component_count=2,
        expected_grounded_count=1,
        expected_joint_count=0,
        expected_solve_on_creation=solve,
    )


@pytest.mark.parametrize(
    "value",
    [True, -1.0, 180.0001, math.nan, math.inf, -math.inf, "sixty"],
)
def test_angle_value_rejects_noncanonical_or_nonfinite_values(value) -> None:
    with pytest.raises(NativeAssemblyAngleJointError, match="angle_degrees"):
        canonical_angle_degrees(value)


@pytest.mark.parametrize("value", [0, 60.0, 180])
def test_angle_value_accepts_complete_canonical_geometric_range(value) -> None:
    assert canonical_angle_degrees(value) == float(value)


def test_angle_spec_maps_only_the_real_native_joint_contract() -> None:
    regular = _regular_spec(_spec())

    assert regular.joint_type == "Angle"
    assert regular.type_index == 8
    assert regular.reverse is False
    assert [(item.name, item.value) for item in regular.properties] == [
        ("Angle", 60.0)
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
    monkeypatch.setitem(
        sys.modules,
        "Part",
        SimpleNamespace(Precision=SimpleNamespace(confusion=lambda: 1.0e-7)),
    )


def _axis_joint(second_z: tuple[float, float, float]):
    return SimpleNamespace(
        Placement1=SimpleNamespace(Rotation=_Rotation((0.0, 0.0, 1.0))),
        Placement2=SimpleNamespace(Rotation=_Rotation(second_z)),
        Reference1=object(),
        Reference2=object(),
    )


def test_angle_measurement_and_constraint_use_live_global_z_dot_product(
    monkeypatch,
) -> None:
    _install_axis_modules(monkeypatch)
    joint = _axis_joint((math.sqrt(0.75), 0.0, 0.5))

    assert measured_axis_angle_degrees(joint) == pytest.approx(60.0)
    assert angle_axes_satisfied(joint, 60.0) is True
    assert angle_axes_satisfied(joint, 45.0) is False


def test_zero_angle_matches_compiled_parallel_solver_for_both_directions(
    monkeypatch,
) -> None:
    _install_axis_modules(monkeypatch)

    assert angle_axes_satisfied(_axis_joint((0.0, 0.0, 1.0)), 0.0) is True
    assert angle_axes_satisfied(_axis_joint((0.0, 0.0, -1.0)), 0.0) is True
    assert angle_solver_relation(0.0) == "parallel_unsigned"
    assert angle_solver_relation(60.0) == "axis_dot_cosine"


@pytest.mark.parametrize(("solve", "satisfied"), [(True, True), (False, False)])
def test_angle_result_reports_property_and_measured_semantic_state(
    monkeypatch,
    solve: bool,
    satisfied: bool,
) -> None:
    spec = _spec(solve=solve)
    joint = object()
    monkeypatch.setattr(
        angle_module,
        "verify_regular_joint",
        lambda *_args, **_kwargs: {
            "joint_type": "Angle",
            "reverse": False,
            "properties": {"Angle": 60.0},
            "joint_count": 1,
        },
    )
    monkeypatch.setattr(
        angle_module,
        "measured_axis_angle_degrees",
        lambda _joint: 60.0 if satisfied else 20.0,
    )
    monkeypatch.setattr(
        angle_module,
        "angle_axes_satisfied",
        lambda _joint, _expected: satisfied,
    )

    result = verify_angle_joint(
        object(),
        NativeMutationDraft(value={"spec": spec, "joint": joint}),
    )

    assert result == {
        "joint_type": "Angle",
        "joint_count": 1,
        "angle_degrees": 60.0,
        "angle_relation": "axis_dot_cosine",
        "measured_axis_angle_degrees": 60.0 if satisfied else 20.0,
        "angle_satisfied": satisfied,
    }


def test_angle_solved_postcondition_rejects_wrong_axis_angle(monkeypatch) -> None:
    monkeypatch.setattr(
        angle_module,
        "verify_regular_joint",
        lambda *_args, **_kwargs: {
            "reverse": False,
            "properties": {"Angle": 60.0},
        },
    )
    monkeypatch.setattr(
        angle_module,
        "measured_axis_angle_degrees",
        lambda _joint: 20.0,
    )
    monkeypatch.setattr(
        angle_module,
        "angle_axes_satisfied",
        lambda _joint, _expected: False,
    )

    with pytest.raises(NativeAssemblyAngleJointError, match="did not establish"):
        verify_angle_joint(
            object(),
            NativeMutationDraft(value={"spec": _spec(), "joint": object()}),
        )


class _Document:
    Uid = "angle-document"
    Name = "AngleDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-angle-unit")
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
        "operation": "create_angle",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Base", "Face6"),
        "second": _connector_mapping("Arm", "Body.Pad.Face1"),
        "label": "  Sixty Degree Axes  ",
        "angle_degrees": 60.0,
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"reverse": True},
        {"rotation": 90.0},
        {"distance": 1.0},
        {"limits": {}},
    ],
)
def test_angle_runtime_rejects_inapplicable_fields_before_guard(
    monkeypatch,
    extra,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_angle_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = _arguments()
    arguments.update(extra)

    with pytest.raises(NativeArgumentError):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
