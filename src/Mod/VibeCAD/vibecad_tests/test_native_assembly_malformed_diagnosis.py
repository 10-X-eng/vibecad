# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyDiagnosisRuntime as runtime_module
from VibeCADNativeAssemblyDiagnosisRuntime import NativeAssemblyDiagnosisRuntime
from VibeCADNativeAssemblyDiagnosisSchema import (
    assembly_diagnosis_capability_definition,
)
from VibeCADNativeAssemblyDiagnosisState import (
    NativeAssemblyDiagnosisError,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeAssemblyMalformedDiagnosis import (
    MalformedConstraintsSpec,
    preflight_malformed_constraints,
    read_malformed_constraints,
)
from VibeCADNativeTargets import NativeObjectRef
from vibecad_tests.test_native_assembly_conflict_diagnosis import (
    _context,
    _fixture,
    _selection,
)


@pytest.fixture(autouse=True)
def _active_timeline(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "UtilsAssembly",
        SimpleNamespace(isTimelineOperationActive=lambda _obj: True),
    )


def _malformed_fixture(*, malformed_count: int = 2):
    document, assembly, group, ground, components, joints = _fixture(conflict_count=0)
    joints[0].JointType = "Fixed"
    joints[1].JointType = "Slider"
    malformed_names = [joint.Name for joint in joints[:malformed_count]]
    assembly._diagnostics.update(
        {
            "remaining_degrees_of_freedom": 8,
            "has_malformed_constraints": bool(malformed_names),
            "malformed_joints": malformed_names,
            "joints": [
                item
                for item in assembly._diagnostics["joints"]
                if item["joint"] not in malformed_names
            ],
        }
    )
    assembly.getSolverDiagnostics = lambda: deepcopy(assembly._diagnostics)
    return document, assembly, group, ground, components, joints


def _spec(document, assembly, state, *, offset: int = 0, limit: int = 32):
    return MalformedConstraintsSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        expected_diagnosis_state_sha256=state.state_sha256,
        expected_component_count=len(state.components),
        expected_grounded_count=len(state.grounded_joints),
        expected_joint_count=len(state.regular_joints),
        expected_malformed_count=len(state.malformed_names),
        offset=offset,
        limit=limit,
    )


def test_schema_maps_the_live_malformed_action_to_the_shared_exact_read() -> None:
    definition = assembly_diagnosis_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}
    variant = variants["select_malformed_constraints"]
    schema = definition.provider_schema((variant.operation,))["parameters"]["oneOf"][0]

    assert tuple(variants) == (
        "select_conflicting_constraints",
        "select_redundant_constraints",
        "select_partially_redundant_constraints",
        "select_malformed_constraints",
    )
    assert variant.action_ids == frozenset({"Assembly_SelectMalformedConstraints"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.exact_target_type == "HumanActiveAssemblyAndExactSolverDiagnosis"
    assert variant.transaction_behavior == "none"
    assert schema["additionalProperties"] is False
    assert "expected_malformed_count" in schema["required"]
    assert "expected_partially_redundant_count" not in schema["properties"]


def test_state_requires_malformed_joints_to_be_excluded_from_solver_rows() -> None:
    _document, assembly, _group, _ground, _components, joints = _malformed_fixture()
    state = capture_assembly_diagnosis_state(assembly)

    assert state.summary()["malformed_count"] == 2
    assert state.malformed_names == (joints[0].Name, joints[1].Name)
    assert [item.joint.Name for item in state.joint_diagnostics] == [joints[2].Name]

    assembly._diagnostics["malformed_joints"].append(joints[2].Name)
    with pytest.raises(NativeAssemblyDiagnosisError, match="malformed joint"):
        capture_assembly_diagnosis_state(assembly)


def test_malformed_read_is_exact_paginated_actionable_and_non_mutating() -> None:
    document, assembly, _group, _ground, components, joints = _malformed_fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    selected = _selection(document, (components[2].Name,))
    before_objects = tuple(document.Objects)
    before_placements = tuple(component.Placement for component in components)

    first = read_malformed_constraints(
        context,
        _spec(document, assembly, state, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )
    second = read_malformed_constraints(
        context,
        _spec(document, assembly, state, offset=1, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )

    assert first["operation"] == "select_malformed_constraints"
    assert first["diagnosis_state_sha256"] == state.state_sha256
    assert first["solver_scope"] == "most_recent_fixed_bundle_drag"
    assert first["solver_status"] == 0
    assert first["remaining_degrees_of_freedom"] == 8
    assert first["malformed_joint_count"] == 2
    assert first["returned_count"] == 1
    assert first["next_offset"] == 1
    assert first["malformed_joints"][0]["joint"]["object_name"] == joints[0].Name
    fixed = first["malformed_joints"][0]
    assert fixed["diagnostic_status"] == "malformed"
    assert fixed["reason_code"] == "same_solver_part_in_fixed_drag_bundle"
    assert fixed["bundle_role"] == "fixed_bundle_constraint"
    assert fixed["first"]["component"]["object_name"] == components[0].Name
    assert second["malformed_joints"][0]["joint"]["object_name"] == joints[1].Name
    assert second["malformed_joints"][0]["bundle_role"] == "intra_bundle_constraint"
    assert "break the Fixed path" in second["malformed_joints"][0]["recommended_action"]
    assert "next_offset" not in second
    assert tuple(document.Objects) == before_objects
    assert tuple(component.Placement for component in components) == before_placements


def test_malformed_read_rejects_stale_count_offset_and_selection_drift() -> None:
    document, assembly, _group, _ground, components, _joints = _malformed_fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    selected = _selection(document, (components[0].Name,))
    stale = _spec(document, assembly, state)
    stale = MalformedConstraintsSpec(
        stale.assembly_ref,
        stale.expected_diagnosis_state_sha256,
        stale.expected_component_count,
        stale.expected_grounded_count,
        stale.expected_joint_count,
        1,
        stale.offset,
        stale.limit,
    )

    with pytest.raises(NativeAssemblyDiagnosisError, match="counts changed"):
        preflight_malformed_constraints(
            context,
            stale,
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: selected,
        )
    with pytest.raises(NativeAssemblyDiagnosisError, match="offset"):
        preflight_malformed_constraints(
            context,
            _spec(document, assembly, state, offset=2),
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: selected,
        )

    calls = 0

    def changing_selection(_document):
        nonlocal calls
        calls += 1
        return selected if calls == 1 else _selection(document)

    with pytest.raises(NativeAssemblyDiagnosisError, match="selection changed"):
        read_malformed_constraints(
            context,
            _spec(document, assembly, state),
            active_reader=lambda _document: assembly,
            selection_reader=changing_selection,
        )


def test_empty_malformed_read_returns_one_exact_empty_page() -> None:
    document, assembly, _group, _ground, _components, _joints = _malformed_fixture(
        malformed_count=0
    )
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    result = read_malformed_constraints(
        context,
        _spec(document, assembly, state, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda target: _selection(target),
    )

    assert result["malformed_joint_count"] == 0
    assert result["returned_count"] == 0
    assert result["malformed_joints"] == []
    assert "next_offset" not in result


def test_runtime_parses_closed_exact_malformed_arguments(monkeypatch) -> None:
    document, assembly, _group, _ground, _components, _joints = _malformed_fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    runtime = NativeAssemblyDiagnosisRuntime(context)
    captured = []
    monkeypatch.setattr(
        runtime_module,
        "read_malformed_constraints",
        lambda exact_context, spec: (
            captured.append((exact_context, spec)) or {"ok": True}
        ),
    )
    arguments = {
        "operation": "select_malformed_constraints",
        "assembly": {"object_name": assembly.Name},
        "expected_diagnosis_state_sha256": state.state_sha256,
        "expected_component_count": 3,
        "expected_grounded_count": 1,
        "expected_joint_count": 3,
        "expected_malformed_count": 2,
        "offset": 0,
        "limit": 1,
    }

    assert runtime.diagnose(arguments) == {"ok": True}
    assert captured == [(context, _spec(document, assembly, state, limit=1))]
    with pytest.raises(RuntimeError, match="do not match"):
        runtime.diagnose({**arguments, "expected_redundant_count": 0})
