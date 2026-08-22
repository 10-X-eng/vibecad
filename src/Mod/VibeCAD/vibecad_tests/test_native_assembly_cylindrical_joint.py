# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeAssemblyCylindricalJoint import (
    CylindricalJointSpec,
    NativeAssemblyCylindricalJointError,
    _regular_spec,
)
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
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
