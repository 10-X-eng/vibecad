# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblySliderJoint import (
    NativeAssemblySliderJointError,
    SliderJointSpec,
    _regular_spec,
)
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
                            "command_id": "Assembly_CreateJointSlider",
                            "kind": "command",
                            "label": "Slider Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(**changes) -> SliderJointSpec:
    fixed = _fixed_spec()
    values = {
        "assembly_ref": fixed.assembly_ref,
        "first": fixed.first,
        "second": fixed.second,
        "label": "Carriage Slider Joint",
        "reverse": False,
        "minimum_enabled": True,
        "minimum_mm": -10.0,
        "maximum_enabled": True,
        "maximum_mm": 30.0,
        "expected_component_count": 2,
        "expected_grounded_count": 0,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }
    values.update(changes)
    return SliderJointSpec(**values)


def test_slider_spec_maps_every_native_limit_property() -> None:
    regular = _regular_spec(_spec(reverse=True))

    assert regular.joint_type == "Slider"
    assert regular.type_index == 3
    assert regular.reverse is True
    assert {item.name: item.value for item in regular.properties} == {
        "EnableLengthMin": True,
        "LengthMin": -10.0,
        "EnableLengthMax": True,
        "LengthMax": 30.0,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"minimum_mm": -1_000_000.01},
        {"maximum_mm": 1_000_000.01},
        {"minimum_mm": float("nan")},
        {"maximum_mm": float("inf")},
        {"minimum_mm": 31.0, "maximum_mm": 30.0},
        {"minimum_enabled": 1},
        {"maximum_enabled": 0},
    ],
)
def test_slider_spec_rejects_invalid_limit_state(changes) -> None:
    with pytest.raises(NativeAssemblySliderJointError):
        _regular_spec(_spec(**changes))


def test_slider_spec_allows_inactive_bounds_to_cross() -> None:
    regular = _regular_spec(
        _spec(
            minimum_enabled=False,
            minimum_mm=31.0,
            maximum_mm=30.0,
        )
    )

    assert regular.joint_type == "Slider"


class _Document:
    Uid = "slider-document"
    Name = "SliderDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-slider-unit")
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
        "operation": "create_slider",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Base", "Face6"),
        "second": _connector_mapping("Carriage", "Body.Pad.Face2"),
        "label": "  Carriage Slider  ",
        "reverse": True,
        "limits": {
            "minimum": {"enabled": True, "mm": -12.0},
            "maximum": {"enabled": False, "mm": 36.0},
        },
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }
