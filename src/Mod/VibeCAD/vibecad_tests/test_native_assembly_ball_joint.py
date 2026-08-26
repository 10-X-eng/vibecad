# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyBallJoint as ball_module
import VibeCADNativeAssemblyMotionJointRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyBallJoint import (
    BallJointSpec,
    _regular_spec,
    verify_ball_joint,
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
                            "command_id": "Assembly_CreateJointBall",
                            "kind": "command",
                            "label": "Ball Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec() -> BallJointSpec:
    fixed = _fixed_spec()
    return BallJointSpec(
        assembly_ref=fixed.assembly_ref,
        first=fixed.first,
        second=fixed.second,
        label="Spherical Pivot",
        expected_component_count=2,
        expected_grounded_count=0,
        expected_joint_count=0,
        expected_solve_on_creation=True,
    )


def test_ball_spec_maps_only_the_real_native_joint_contract() -> None:
    regular = _regular_spec(_spec())

    assert regular.joint_type == "Ball"
    assert regular.type_index == 4
    assert regular.reverse is False
    assert regular.properties == ()


def test_ball_result_omits_internal_reverse_and_empty_properties(monkeypatch) -> None:
    monkeypatch.setattr(
        ball_module,
        "verify_regular_joint",
        lambda *_args, **_kwargs: {
            "joint_type": "Ball",
            "reverse": False,
            "properties": {},
            "joint_count": 1,
        },
    )

    result = verify_ball_joint(object(), NativeMutationDraft(value={}))

    assert result == {"joint_type": "Ball", "joint_count": 1}


class _Document:
    Uid = "ball-document"
    Name = "BallDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-ball-unit")
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
        "operation": "create_ball",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Base", "Vertex1"),
        "second": _connector_mapping("Arm", "Body.Pad.Vertex2"),
        "label": "  Arm Pivot  ",
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }


@pytest.mark.parametrize("extra", [{"reverse": False}, {"limits": {}}, {"distance": 1.0}])
def test_ball_runtime_rejects_inapplicable_fields_before_guard(
    monkeypatch,
    extra,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_ball_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = _arguments()
    arguments.update(extra)

    with pytest.raises(NativeArgumentError):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
