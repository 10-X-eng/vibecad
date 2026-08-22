# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyDistanceJoint as distance_module
from VibeCADNativeAssemblyDistanceJoint import (
    DISTANCE_MODES,
    DistanceJointSpec,
    NativeAssemblyDistanceJointError,
    _mode_and_swap,
    _regular_spec,
    preflight_distance_joint,
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
                            "command_id": "Assembly_CreateJointDistance",
                            "kind": "command",
                            "label": "Distance Joint",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def _spec(**changes) -> DistanceJointSpec:
    fixed = _fixed_spec()
    values = {
        "assembly_ref": fixed.assembly_ref,
        "first": fixed.first,
        "second": fixed.second,
        "label": "Arm Distance",
        "reverse": False,
        "distance_mm": 12.5,
        "expected_distance_mode": "point_plane",
        "expected_component_count": 2,
        "expected_grounded_count": 0,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }
    values.update(changes)
    return DistanceJointSpec(**values)


def test_distance_spec_maps_only_the_real_distance_property() -> None:
    regular = _regular_spec(_spec(reverse=True, distance_mm=-15.0))

    assert regular.joint_type == "Distance"
    assert regular.type_index == 5
    assert regular.reverse is True
    assert {item.name: item.value for item in regular.properties} == {
        "Distance": -15.0
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"distance_mm": -1_000_000.01},
        {"distance_mm": 1_000_000.01},
        {"distance_mm": float("nan")},
        {"distance_mm": float("inf")},
        {"distance_mm": True},
        {"expected_distance_mode": "guessed"},
    ],
)
def test_distance_spec_rejects_invalid_values(changes) -> None:
    with pytest.raises(NativeAssemblyDistanceJointError):
        _regular_spec(_spec(**changes))


@pytest.mark.parametrize(
    ("first", "second", "mode", "swap"),
    [
        (("point", "point"), ("point", "point"), "point_point", False),
        (("edge", "line"), ("edge", "line"), "line_line", False),
        (("edge", "line"), ("edge", "circle"), "line_circle", False),
        (("edge", "circle"), ("edge", "line"), "line_circle", True),
        (("edge", "circle"), ("edge", "circle"), "circle_circle", False),
        (("point", "point"), ("face", "plane"), "point_plane", True),
        (("face", "sphere"), ("point", "point"), "point_sphere", False),
        (("edge", "line"), ("face", "cylinder"), "line_cylinder", True),
        (("face", "cone"), ("edge", "curve"), "curve_cone", False),
        (("point", "point"), ("edge", "line"), "point_line", True),
        (("edge", "circle"), ("point", "point"), "point_curve", False),
        (("edge", "curve"), ("edge", "line"), "other", True),
        (("face", "surface"), ("face", "plane"), "other", True),
        (("other", "other"), ("face", "plane"), "other", False),
    ],
)
def test_distance_mode_matches_cpp_canonicalization(first, second, mode, swap) -> None:
    assert _mode_and_swap(first, second) == (mode, swap)


def test_all_face_pair_modes_and_reverse_orders_are_exact() -> None:
    kinds = ("plane", "cylinder", "cone", "torus", "sphere")
    observed = set()
    for first_index, first_kind in enumerate(kinds):
        for second_kind in kinds[first_index:]:
            expected = f"{first_kind}_{second_kind}"
            observed.add(expected)
            assert _mode_and_swap(
                ("face", first_kind),
                ("face", second_kind),
            ) == (expected, False)
            if first_kind != second_kind:
                assert _mode_and_swap(
                    ("face", second_kind),
                    ("face", first_kind),
                ) == (expected, True)
    assert observed <= DISTANCE_MODES


def test_preflight_canonicalizes_reference_and_offset_ownership(monkeypatch) -> None:
    first_resolved = SimpleNamespace(selected_element=SimpleNamespace(ShapeType="Vertex"))
    second_resolved = SimpleNamespace(
        selected_element=SimpleNamespace(
            ShapeType="Face",
            Surface=SimpleNamespace(TypeId="Part::GeomPlane"),
        )
    )
    regular = SimpleNamespace(first=first_resolved, second=second_resolved)
    monkeypatch.setattr(
        distance_module,
        "preflight_regular_joint",
        lambda *_args, **_kwargs: regular,
    )
    spec = _spec(expected_distance_mode="point_plane")

    prepared = preflight_distance_joint(object(), spec)

    assert prepared.distance_mode == "point_plane"
    assert prepared.canonical_spec.first is spec.second
    assert prepared.canonical_spec.second is spec.first


def test_preflight_rejects_changed_geometry_mode(monkeypatch) -> None:
    point = SimpleNamespace(selected_element=SimpleNamespace(ShapeType="Vertex"))
    regular = SimpleNamespace(first=point, second=point)
    monkeypatch.setattr(
        distance_module,
        "preflight_regular_joint",
        lambda *_args, **_kwargs: regular,
    )

    with pytest.raises(NativeAssemblyDistanceJointError, match="geometry mode changed"):
        preflight_distance_joint(object(), _spec(expected_distance_mode="point_plane"))


class _Document:
    Uid = "distance-document"
    Name = "DistanceDocument"


def _runtime() -> tuple[NativeAssemblyJointRuntime, NativeDocumentStateStore, _Document]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-distance-unit")
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
        "operation": "create_distance",
        "assembly": {"object_name": "Assembly"},
        "first": _connector_mapping("Arm", "Vertex1"),
        "second": _connector_mapping("Base", "Face6"),
        "label": "  Arm Height  ",
        "reverse": True,
        "distance_mm": 18.0,
        "expected_distance_mode": "point_plane",
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 0,
        "expected_solve_on_creation": True,
    }
