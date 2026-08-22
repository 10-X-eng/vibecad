# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyRelationJointRuntime as runtime_module
import VibeCADNativeAssemblyParallelJoint as parallel_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyParallelJoint import (
    NativeAssemblyParallelJointError,
    ParallelJointSpec,
    _regular_spec,
    verify_parallel_joint,
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
                            "command_id": "Assembly_CreateJointParallel",
                            "kind": "command",
                            "label": "Parallel Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(*, solve: bool = True) -> ParallelJointSpec:
    fixed = _fixed_spec()
    return ParallelJointSpec(
        assembly_ref=fixed.assembly_ref,
        first=fixed.first,
        second=fixed.second,
        label="Parallel Axes",
        reverse=True,
        expected_component_count=2,
        expected_grounded_count=1,
        expected_joint_count=0,
        expected_solve_on_creation=solve,
    )


def test_parallel_spec_maps_only_the_real_native_joint_contract() -> None:
    regular = _regular_spec(_spec())

    assert regular.joint_type == "Parallel"
    assert regular.type_index == 6
    assert regular.reverse is True
    assert regular.properties == ()


@pytest.mark.parametrize(("solve", "satisfied"), [(True, True), (False, False)])
def test_parallel_result_reports_exact_axis_state(
    monkeypatch,
    solve: bool,
    satisfied: bool,
) -> None:
    spec = _spec(solve=solve)
    joint = object()
    monkeypatch.setattr(
        parallel_module,
        "verify_regular_joint",
        lambda *_args, **_kwargs: {
            "joint_type": "Parallel",
            "reverse": True,
            "properties": {},
            "joint_count": 1,
        },
    )
    monkeypatch.setattr(
        parallel_module,
        "parallel_axes_satisfied",
        lambda _joint: satisfied,
    )

    result = verify_parallel_joint(
        object(),
        NativeMutationDraft(value={"spec": spec, "joint": joint}),
    )

    assert result == {
        "joint_type": "Parallel",
        "reverse": True,
        "joint_count": 1,
        "axes_parallel": satisfied,
    }


def test_parallel_solved_postcondition_rejects_nonparallel_axes(monkeypatch) -> None:
    monkeypatch.setattr(
        parallel_module,
        "verify_regular_joint",
        lambda *_args, **_kwargs: {"properties": {}},
    )
    monkeypatch.setattr(
        parallel_module,
        "parallel_axes_satisfied",
        lambda _joint: False,
    )

    with pytest.raises(NativeAssemblyParallelJointError, match="did not make"):
        verify_parallel_joint(
            object(),
            NativeMutationDraft(value={"spec": _spec(), "joint": object()}),
        )


class _Document:
    Uid = "parallel-document"
    Name = "ParallelDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-parallel-unit")
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
        "operation": "create_parallel",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Base", "Face6"),
        "second": _connector_mapping("Arm", "Body.Pad.Face1"),
        "label": "  Parallel Axes  ",
        "reverse": True,
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }


@pytest.mark.parametrize(
    "extra",
    [{"angle": 90.0}, {"distance": 1.0}, {"limits": {}}, {"rotation": 90.0}],
)
def test_parallel_runtime_rejects_inapplicable_fields_before_guard(
    monkeypatch,
    extra,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_parallel_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = _arguments()
    arguments.update(extra)

    with pytest.raises(NativeArgumentError):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
