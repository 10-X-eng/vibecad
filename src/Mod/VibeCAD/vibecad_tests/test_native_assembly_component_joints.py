# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyDiagnosisRuntime as runtime_module
from VibeCADNativeAssemblyComponentJoints import (
    ComponentJointsSpec,
    NativeAssemblyComponentJointsError,
    capture_component_joint_state,
    preflight_component_joints,
    read_component_joints,
)
from VibeCADNativeAssemblyDiagnosisRuntime import NativeAssemblyDiagnosisRuntime
from VibeCADNativeAssemblyDiagnosisSchema import (
    assembly_diagnosis_capability_definition,
)
from VibeCADNativeTargets import NativeObjectRef
from vibecad_tests.test_native_assembly_conflict_diagnosis import (
    _context,
    _fixture,
    _selection,
)


@pytest.fixture(autouse=True)
def _assembly_helpers(monkeypatch):
    def movable_parts(assembly):
        return list(getattr(assembly, "_movable", ()))

    def is_movable(assembly, component):
        return component in movable_parts(assembly)

    monkeypatch.setitem(
        __import__("sys").modules,
        "UtilsAssembly",
        SimpleNamespace(
            getMovablePartsWithin=movable_parts,
            isMovableAssemblyComponent=is_movable,
            isTimelineOperationActive=lambda _obj: True,
        ),
    )


def _component_fixture():
    document, assembly, group, ground, components, joints = _fixture(
        conflict_count=0
    )
    assembly._movable = list(components)
    assembly.Joints = list(joints)
    return document, assembly, group, ground, components, joints


def _spec(
    document,
    assembly,
    component,
    state,
    *,
    offset: int = 0,
    limit: int = 16,
):
    return ComponentJointsSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        component_ref=NativeObjectRef(document.Uid, component.Name),
        expected_joint_graph_state_sha256=state.state_sha256,
        expected_component_count=len(state.components),
        expected_joint_count=len(state.joints),
        offset=offset,
        limit=limit,
    )


def test_schema_maps_the_remaining_live_diagnose_action_to_one_exact_read() -> None:
    definition = assembly_diagnosis_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}
    variant = variants["select_joints_of_component"]
    schema = definition.provider_schema((variant.operation,))["parameters"]["oneOf"][
        0
    ]

    assert tuple(variants) == (
        "select_conflicting_constraints",
        "select_redundant_constraints",
        "select_partially_redundant_constraints",
        "select_malformed_constraints",
        "select_joints_of_component",
    )
    assert variant.action_ids == frozenset({"Assembly_SelectJointsOfComponent"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert (
        variant.exact_target_type
        == "HumanActiveAssemblyAndExactComponentJointGraph"
    )
    assert variant.transaction_behavior == "none"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "component",
        "expected_joint_graph_state_sha256",
        "expected_component_count",
        "expected_joint_count",
        "offset",
        "limit",
    }
    assert "expected_diagnosis_state_sha256" not in schema["properties"]
    assert schema["properties"]["limit"]["maximum"] == 16


def test_component_joint_state_uses_compiled_order_and_hashes_exact_output() -> None:
    _document, assembly, _group, _ground, components, joints = _component_fixture()
    initial = capture_component_joint_state(assembly)

    assert initial.summary() == {
        "available": True,
        "state_sha256": initial.state_sha256,
        "component_count": 3,
        "joint_count": 3,
    }
    assert initial.components == components
    assert initial.joints == joints
    assert initial.joint_components == (
        (components[0], components[1]),
        (components[1], components[2]),
        (components[0], components[2]),
    )

    joints[0].Label = "Renamed exact joint"
    assert capture_component_joint_state(assembly).state_sha256 != initial.state_sha256
    joints[0].Label = joints[0].Name
    joints[0].Offset1.Base.x = 2.0
    assert capture_component_joint_state(assembly).state_sha256 != initial.state_sha256
    joints[0].Offset1.Base.x = 0.0
    assembly.Joints = [joints[2], joints[0], joints[1]]
    reordered = capture_component_joint_state(assembly)
    assert reordered.state_sha256 != initial.state_sha256
    assert reordered.joints == (joints[2], joints[0], joints[1])


def test_component_joint_state_rejects_duplicate_or_malformed_compiled_rows() -> None:
    _document, assembly, _group, _ground, _components, joints = _component_fixture()
    assembly.Joints.append(joints[0])
    with pytest.raises(NativeAssemblyComponentJointsError, match="duplicates"):
        capture_component_joint_state(assembly)

    assembly.Joints = list(joints)
    joints[0].Suppressed = True
    with pytest.raises(NativeAssemblyComponentJointsError, match="invalid joint"):
        capture_component_joint_state(assembly)

    joints[0].Suppressed = False
    joints[0].Reference1[1][0] = "x" * 513
    with pytest.raises(NativeAssemblyComponentJointsError, match="unbounded"):
        capture_component_joint_state(assembly)


def test_component_joint_read_is_exact_paginated_and_non_mutating() -> None:
    document, assembly, _group, _ground, components, joints = _component_fixture()
    context = _context(document)
    state = capture_component_joint_state(assembly)
    selected = _selection(document, (components[1].Name,))
    before_objects = tuple(document.Objects)
    before_placements = tuple(component.Placement for component in components)

    first = read_component_joints(
        context,
        _spec(document, assembly, components[1], state, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )
    second = read_component_joints(
        context,
        _spec(document, assembly, components[1], state, offset=1, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )

    assert first["operation"] == "select_joints_of_component"
    assert first["joint_graph_state_sha256"] == state.state_sha256
    assert first["component"]["object_name"] == components[1].Name
    assert first["component_count"] == 3
    assert first["joint_count"] == 3
    assert first["component_joint_count"] == 2
    assert first["returned_count"] == 1
    assert first["next_offset"] == 1
    assert first["joints"][0]["joint"]["object_name"] == joints[0].Name
    assert first["joints"][0]["component_side"] == "second"
    assert (
        first["joints"][0]["other_component"]["object_name"]
        == components[0].Name
    )
    assert second["joints"][0]["joint"]["object_name"] == joints[1].Name
    assert second["joints"][0]["component_side"] == "first"
    assert "next_offset" not in second
    assert tuple(document.Objects) == before_objects
    assert tuple(component.Placement for component in components) == before_placements


def test_component_joint_read_handles_one_exact_unconnected_component() -> None:
    document, assembly, _group, _ground, _components, _joints = _component_fixture()
    component = document.add("Unconnected", "App::Link")
    component.Placement = SimpleNamespace()
    assembly._movable.append(component)
    state = capture_component_joint_state(assembly)
    result = read_component_joints(
        _context(document),
        _spec(document, assembly, component, state, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda target: _selection(target),
    )

    assert result["component_joint_count"] == 0
    assert result["returned_count"] == 0
    assert result["joints"] == []
    assert "next_offset" not in result


def test_component_joint_read_rejects_stale_target_count_offset_and_drift() -> None:
    document, assembly, _group, _ground, components, _joints = _component_fixture()
    context = _context(document)
    state = capture_component_joint_state(assembly)
    selected = _selection(document, (components[1].Name,))

    def active(_document):
        return assembly

    stale = _spec(document, assembly, components[1], state)
    stale = ComponentJointsSpec(
        stale.assembly_ref,
        stale.component_ref,
        "0" * 64,
        stale.expected_component_count,
        stale.expected_joint_count,
        stale.offset,
        stale.limit,
    )
    with pytest.raises(NativeAssemblyComponentJointsError, match="graph changed"):
        preflight_component_joints(
            context,
            stale,
            active_reader=active,
            selection_reader=lambda _document: selected,
        )
    wrong_count = ComponentJointsSpec(
        stale.assembly_ref,
        stale.component_ref,
        state.state_sha256,
        2,
        3,
        0,
        16,
    )
    with pytest.raises(NativeAssemblyComponentJointsError, match="counts changed"):
        preflight_component_joints(
            context,
            wrong_count,
            active_reader=active,
            selection_reader=lambda _document: selected,
        )
    with pytest.raises(NativeAssemblyComponentJointsError, match="offset"):
        preflight_component_joints(
            context,
            _spec(document, assembly, components[1], state, offset=2),
            active_reader=active,
            selection_reader=lambda _document: selected,
        )
    with pytest.raises(NativeAssemblyComponentJointsError, match="not one active"):
        preflight_component_joints(
            context,
            _spec(document, assembly, document.getObject("Source0"), state),
            active_reader=active,
            selection_reader=lambda _document: selected,
        )

    calls = 0

    def changing_selection(_document):
        nonlocal calls
        calls += 1
        return selected if calls == 1 else _selection(document)

    with pytest.raises(NativeAssemblyComponentJointsError, match="selection changed"):
        read_component_joints(
            context,
            _spec(document, assembly, components[1], state),
            active_reader=active,
            selection_reader=changing_selection,
        )

    calls = 0

    def drifting_joint(_document):
        nonlocal calls
        calls += 1
        if calls == 2:
            assembly.Joints[0].Label = "Drifted"
        return selected

    with pytest.raises(NativeAssemblyComponentJointsError, match="changed during"):
        read_component_joints(
            context,
            _spec(document, assembly, components[1], state),
            active_reader=active,
            selection_reader=drifting_joint,
        )


def test_runtime_parses_closed_exact_component_joint_arguments(monkeypatch) -> None:
    document, assembly, _group, _ground, components, _joints = _component_fixture()
    context = _context(document)
    state = capture_component_joint_state(assembly)
    runtime = NativeAssemblyDiagnosisRuntime(context)
    captured = []
    monkeypatch.setattr(
        runtime_module,
        "read_component_joints",
        lambda exact_context, spec: (
            captured.append((exact_context, spec)) or {"ok": True}
        ),
    )
    arguments = {
        "operation": "select_joints_of_component",
        "assembly": {"object_name": assembly.Name},
        "component": {"object_name": components[1].Name},
        "expected_joint_graph_state_sha256": state.state_sha256,
        "expected_component_count": 3,
        "expected_joint_count": 3,
        "offset": 0,
        "limit": 1,
    }

    assert runtime.diagnose(arguments) == {"ok": True}
    assert captured == [
        (context, _spec(document, assembly, components[1], state, limit=1))
    ]
    with pytest.raises(RuntimeError, match="do not match"):
        runtime.diagnose({**arguments, "expected_malformed_count": 0})
    with pytest.raises(RuntimeError, match="lowercase SHA-256"):
        runtime.diagnose(
            {**arguments, "expected_joint_graph_state_sha256": "A" * 64}
        )
    with pytest.raises(RuntimeError, match="lowercase SHA-256"):
        runtime.diagnose(
            {**arguments, "expected_joint_graph_state_sha256": 10**63}
        )
