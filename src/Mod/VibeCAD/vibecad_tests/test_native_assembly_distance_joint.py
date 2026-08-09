# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyDistanceJoint as distance_module
import VibeCADNativeAssemblyJointRuntime as runtime_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeAssemblyDistanceJoint import (
    DISTANCE_MODES,
    DistanceJointSpec,
    NativeAssemblyDistanceJointError,
    _mode_and_swap,
    _regular_spec,
    preflight_distance_joint,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
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


def test_distance_schema_and_action_mapping_are_exact() -> None:
    definition = assembly_joint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "create_distance"
    )
    schema = definition.provider_schema(("create_distance",))["parameters"]["oneOf"][0]

    assert variant.action_ids == frozenset({"Assembly_CreateJointDistance"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "first",
        "second",
        "label",
        "reverse",
        "distance_mm",
        "expected_distance_mode",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    }
    assert schema["properties"]["distance_mm"] == {
        "type": "number",
        "minimum": -1_000_000.0,
        "maximum": 1_000_000.0,
    }
    assert set(schema["properties"]["expected_distance_mode"]["enum"]) == DISTANCE_MODES
    assert schema["additionalProperties"] is False
    plan = classify_native_surface(_surface())[0]
    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "create_distance"
    assert plan.transaction_behavior == "document"


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


def test_distance_runtime_routes_complete_exact_spec_before_transaction(
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
        "preflight_distance_joint",
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
    assert isinstance(spec, DistanceJointSpec)
    assert spec.assembly_ref.object_name == "Assembly"
    assert spec.first.component_ref.object_name == "Arm"
    assert spec.second.component_ref.object_name == "Base"
    assert spec.label == "Arm Height"
    assert spec.reverse is True
    assert spec.distance_mm == 18.0
    assert spec.expected_distance_mode == "point_plane"
    assert captured["preflight_document"] is document
    assert captured["transaction_name"] == "Create Native Assembly Distance Joint"
