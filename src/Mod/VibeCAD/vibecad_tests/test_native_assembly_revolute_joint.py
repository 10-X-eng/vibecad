# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyRevoluteJoint import (
    NativeAssemblyRevoluteJointError,
    RevoluteJointSpec,
    _regular_spec,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import RibbonSurface

from vibecad_tests.test_native_assembly_fixed_joint import (
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
