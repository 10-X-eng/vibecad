# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointRuntime as runtime_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeAssemblyRevoluteJoint import (
    NativeAssemblyRevoluteJointError,
    RevoluteJointSpec,
    _regular_spec,
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
                            "command_id": "Assembly_CreateJointRevolute",
                            "kind": "command",
                            "label": "Revolute Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(**changes) -> RevoluteJointSpec:
    fixed = _fixed_spec()
    values = {
        "assembly_ref": fixed.assembly_ref,
        "first": fixed.first,
        "second": fixed.second,
        "label": "Arm Revolute Joint",
        "reverse": False,
        "minimum_enabled": True,
        "minimum_degrees": -45.0,
        "maximum_enabled": True,
        "maximum_degrees": 120.0,
        "expected_component_count": 2,
        "expected_grounded_count": 0,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }
    values.update(changes)
    return RevoluteJointSpec(**values)


def test_revolute_schema_and_action_mapping_are_exact() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "create_revolute"
    )
    schema = definition.provider_schema(("create_revolute",))["parameters"][
        "oneOf"
    ][0]

    assert variant.action_ids == frozenset({"Assembly_CreateJointRevolute"})
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "first",
        "second",
        "label",
        "reverse",
        "limits",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    }
    minimum = schema["properties"]["limits"]["properties"]["minimum"]
    assert minimum["properties"]["degrees"] == {
        "type": "number",
        "minimum": -180.0,
        "maximum": 180.0,
    }
    assert schema["properties"]["limits"]["additionalProperties"] is False
    plan = classify_native_surface(_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_revolute"
    assert plan.transaction_behavior == "document"


def test_revolute_spec_maps_complete_native_limit_properties() -> None:
    regular = _regular_spec(_spec(reverse=True))

    assert regular.joint_type == "Revolute"
    assert regular.type_index == 1
    assert regular.reverse is True
    assert {item.name: item.value for item in regular.properties} == {
        "EnableAngleMin": True,
        "AngleMin": -45.0,
        "EnableAngleMax": True,
        "AngleMax": 120.0,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"minimum_degrees": -180.01},
        {"maximum_degrees": 180.01},
        {"minimum_degrees": float("nan")},
        {"minimum_degrees": 80.0, "maximum_degrees": 40.0},
        {"minimum_enabled": 1},
    ],
)
def test_revolute_spec_rejects_invalid_limit_state(changes) -> None:
    with pytest.raises(NativeAssemblyRevoluteJointError):
        _regular_spec(_spec(**changes))


class _Document:
    Uid = "revolute-document"
    Name = "RevoluteDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-revolute-unit")
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


def test_revolute_runtime_routes_complete_exact_spec_before_transaction(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        runtime_module,
        "_placement",
        lambda value, field, _error_type: (field, value),
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_revolute_joint",
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
        "operation": "create_revolute",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Base", "Face6"),
        "second": _connector_mapping("Arm", "Body.Pad.Face2"),
        "label": "  Arm Pivot  ",
        "reverse": True,
        "limits": {
            "minimum": {"enabled": True, "degrees": -30},
            "maximum": {"enabled": True, "degrees": 135},
        },
        "expected_component_count": 2,
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
    assert isinstance(spec, RevoluteJointSpec)
    assert spec.assembly_ref == NativeObjectRef(document.Uid, "Assembly")
    assert spec.first.component_ref.object_name == "Base"
    assert spec.second.component_ref.object_name == "Arm"
    assert spec.label == "Arm Pivot"
    assert spec.reverse is True
    assert spec.minimum_enabled is True and spec.minimum_degrees == -30.0
    assert spec.maximum_enabled is True and spec.maximum_degrees == 135.0
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == "Create Native Assembly Revolute Joint"


def test_revolute_runtime_rejects_malformed_limits_before_guard(monkeypatch) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_revolute_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = {
        "operation": "create_revolute",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Base", "Face6"),
        "second": _connector_mapping("Arm", "Face6"),
        "label": "Arm Pivot",
        "reverse": False,
        "limits": {"minimum": {"enabled": True, "degrees": -30}},
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }

    with pytest.raises(NativeAssemblyRevoluteJointError, match="minimum and maximum"):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
