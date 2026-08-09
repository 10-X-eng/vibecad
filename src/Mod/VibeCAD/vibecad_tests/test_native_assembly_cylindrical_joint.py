# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointArguments as joint_arguments
import VibeCADNativeAssemblyMotionJointRuntime as runtime_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeAssemblyCylindricalJoint import (
    CylindricalJointSpec,
    NativeAssemblyCylindricalJointError,
    _regular_spec,
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
                            "command_id": "Assembly_CreateJointCylindrical",
                            "kind": "command",
                            "label": "Cylindrical Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(**changes) -> CylindricalJointSpec:
    fixed = _fixed_spec()
    values = {
        "assembly_ref": fixed.assembly_ref,
        "first": fixed.first,
        "second": fixed.second,
        "label": "Guide Cylindrical Joint",
        "reverse": False,
        "length_minimum_enabled": True,
        "length_minimum_mm": -5.0,
        "length_maximum_enabled": True,
        "length_maximum_mm": 20.0,
        "angle_minimum_enabled": True,
        "angle_minimum_degrees": -60.0,
        "angle_maximum_enabled": True,
        "angle_maximum_degrees": 100.0,
        "expected_component_count": 2,
        "expected_grounded_count": 0,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }
    values.update(changes)
    return CylindricalJointSpec(**values)


def test_cylindrical_schema_and_action_mapping_are_exact() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "create_cylindrical"
    )
    schema = definition.provider_schema(("create_cylindrical",))["parameters"][
        "oneOf"
    ][0]

    assert variant.action_ids == frozenset({"Assembly_CreateJointCylindrical"})
    assert variant.surface_ids == frozenset({"assemble"})
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
    limits = schema["properties"]["limits"]
    assert set(limits["required"]) == {"length", "angle"}
    assert limits["additionalProperties"] is False
    length_minimum = limits["properties"]["length"]["properties"]["minimum"]
    assert length_minimum["properties"]["mm"] == {
        "type": "number",
        "minimum": -1_000_000.0,
        "maximum": 1_000_000.0,
    }
    angle_maximum = limits["properties"]["angle"]["properties"]["maximum"]
    assert angle_maximum["properties"]["degrees"] == {
        "type": "number",
        "minimum": -180.0,
        "maximum": 180.0,
    }
    plan = classify_native_surface(_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_cylindrical"
    assert plan.transaction_behavior == "document"


def test_cylindrical_spec_maps_every_native_limit_property() -> None:
    regular = _regular_spec(_spec(reverse=True))

    assert regular.joint_type == "Cylindrical"
    assert regular.type_index == 2
    assert regular.reverse is True
    assert {item.name: item.value for item in regular.properties} == {
        "EnableLengthMin": True,
        "LengthMin": -5.0,
        "EnableLengthMax": True,
        "LengthMax": 20.0,
        "EnableAngleMin": True,
        "AngleMin": -60.0,
        "EnableAngleMax": True,
        "AngleMax": 100.0,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"length_minimum_mm": -1_000_000.01},
        {"length_maximum_mm": 1_000_000.01},
        {"angle_minimum_degrees": -180.01},
        {"angle_maximum_degrees": 180.01},
        {"length_minimum_mm": float("nan")},
        {"angle_maximum_degrees": float("inf")},
        {"length_minimum_mm": 25.0, "length_maximum_mm": 20.0},
        {"angle_minimum_degrees": 101.0, "angle_maximum_degrees": 100.0},
        {"length_minimum_enabled": 1},
        {"angle_maximum_enabled": 0},
    ],
)
def test_cylindrical_spec_rejects_invalid_limit_state(changes) -> None:
    with pytest.raises(NativeAssemblyCylindricalJointError):
        _regular_spec(_spec(**changes))


def test_cylindrical_spec_allows_inactive_bounds_to_cross() -> None:
    regular = _regular_spec(
        _spec(
            length_minimum_enabled=False,
            length_minimum_mm=25.0,
            length_maximum_mm=20.0,
            angle_minimum_enabled=False,
            angle_minimum_degrees=101.0,
            angle_maximum_degrees=100.0,
        )
    )

    assert regular.joint_type == "Cylindrical"


class _Document:
    Uid = "cylindrical-document"
    Name = "CylindricalDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-cylindrical-unit")
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
        "operation": "create_cylindrical",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Base", "Face6"),
        "second": _connector_mapping("Guide", "Body.Pad.Face2"),
        "label": "  Guide Cylindrical  ",
        "reverse": True,
        "limits": {
            "length": {
                "minimum": {"enabled": True, "mm": -8.0},
                "maximum": {"enabled": True, "mm": 24.0},
            },
            "angle": {
                "minimum": {"enabled": True, "degrees": -75.0},
                "maximum": {"enabled": False, "degrees": 110.0},
            },
        },
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }


def test_cylindrical_runtime_routes_complete_exact_spec_before_transaction(
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
        "preflight_cylindrical_joint",
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
    assert isinstance(spec, CylindricalJointSpec)
    assert spec.assembly_ref == NativeObjectRef(document.Uid, "Assembly")
    assert spec.first.component_ref.object_name == "Base"
    assert spec.second.component_ref.object_name == "Guide"
    assert spec.label == "Guide Cylindrical"
    assert spec.reverse is True
    assert spec.length_minimum_enabled is True
    assert spec.length_minimum_mm == -8.0
    assert spec.length_maximum_enabled is True
    assert spec.length_maximum_mm == 24.0
    assert spec.angle_minimum_enabled is True
    assert spec.angle_minimum_degrees == -75.0
    assert spec.angle_maximum_enabled is False
    assert spec.angle_maximum_degrees == 110.0
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == (
        "Create Native Assembly Cylindrical Joint"
    )


@pytest.mark.parametrize(
    "limits",
    [
        {"length": {}, "angle": {}},
        {
            "length": {
                "minimum": {"enabled": True, "mm": 0.0},
                "maximum": {"enabled": True, "mm": 10.0},
            }
        },
        {
            "length": {
                "minimum": {"enabled": True, "mm": 0.0},
                "maximum": {"enabled": True, "mm": 10.0},
            },
            "angle": {
                "minimum": {"enabled": True, "degrees": -30.0},
                "maximum": {"enabled": True, "degrees": 181.0},
            },
        },
    ],
)
def test_cylindrical_runtime_rejects_invalid_limits_before_guard(
    monkeypatch,
    limits,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "preflight_cylindrical_joint",
        lambda *_args: pytest.fail("preflight started"),
    )
    arguments = _arguments()
    arguments["limits"] = limits

    with pytest.raises(NativeAssemblyCylindricalJointError):
        runtime.mutate_joint(
            arguments,
            ticket=state.begin_call(document.Uid, ASSEMBLY_JOINT_CAPABILITY_NAME),
        )
