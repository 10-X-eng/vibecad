# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyBallJoint as ball_module
import VibeCADNativeAssemblyJointArguments as joint_arguments
import VibeCADNativeAssemblyMotionJointRuntime as runtime_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyBallJoint import (
    BallJointSpec,
    _regular_spec,
    verify_ball_joint,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
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


def test_ball_schema_and_action_mapping_are_exact() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(item for item in definition.variants if item.operation == "create_ball")
    schema = definition.provider_schema(("create_ball",))["parameters"]["oneOf"][0]

    assert variant.action_ids == frozenset({"Assembly_CreateJointBall"})
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
    assert not {"reverse", "rotation", "distance", "limits"} & set(
        schema["properties"]
    )
    assert schema["additionalProperties"] is False
    plan = classify_native_surface(_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_ball"
    assert plan.transaction_behavior == "document"


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


def test_ball_runtime_routes_complete_exact_spec_before_transaction(monkeypatch) -> None:
    runtime, state, document = _runtime()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        joint_arguments,
        "joint_placement",
        lambda value, field, _error_type: (field, value),
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_ball_joint",
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
    assert isinstance(spec, BallJointSpec)
    assert spec.assembly_ref.object_name == "Assembly"
    assert spec.first.component_ref.object_name == "Base"
    assert spec.second.component_ref.object_name == "Arm"
    assert spec.label == "Arm Pivot"
    assert spec.expected_component_count == 2
    assert spec.expected_grounded_count == 1
    assert spec.expected_joint_count == 0
    assert spec.expected_solve_on_creation is True
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == "Create Native Assembly Ball Joint"


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
