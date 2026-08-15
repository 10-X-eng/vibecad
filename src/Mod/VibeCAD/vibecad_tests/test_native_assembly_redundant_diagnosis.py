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
from VibeCADNativeAssemblyRedundantDiagnosis import (
    RedundantConstraintsSpec,
    preflight_redundant_constraints,
    read_redundant_constraints,
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


def _redundant_fixture(*, redundant_count: int = 3):
    document, assembly, group, ground, components, joints = _fixture(conflict_count=0)
    redundant_names = [joint.Name for joint in joints[:redundant_count]]
    assembly._diagnostics.update(
        {
            "remaining_degrees_of_freedom": 8,
            "has_redundancies": bool(redundant_names),
            "redundant_joints": redundant_names,
        }
    )
    for item in assembly._diagnostics["joints"]:
        redundant = item["joint"] in redundant_names
        item.update(
            {
                "status": "redundant" if redundant else "satisfied",
                "redundant_constraint_count": 1 if redundant else 0,
                "removed_degrees_of_freedom": 0 if redundant else 1,
            }
        )
        constraint = item["constraints"][0]
        constraint["redundant"] = redundant
        if redundant:
            constraint["specification"] = "RedundantConstraintDistanceConstraintIJ"
    assembly.getSolverDiagnostics = lambda: deepcopy(assembly._diagnostics)
    return document, assembly, group, ground, components, joints


def _spec(document, assembly, state, *, offset: int = 0, limit: int = 32):
    return RedundantConstraintsSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        expected_diagnosis_state_sha256=state.state_sha256,
        expected_component_count=len(state.components),
        expected_grounded_count=len(state.grounded_joints),
        expected_joint_count=len(state.regular_joints),
        expected_redundant_count=len(state.redundant_names),
        offset=offset,
        limit=limit,
    )


def test_schema_maps_the_live_redundant_action_to_the_shared_exact_read() -> None:
    definition = assembly_diagnosis_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}
    variant = variants["select_redundant_constraints"]
    schema = definition.provider_schema((variant.operation,))["parameters"]["oneOf"][0]

    assert tuple(variants) == (
        "select_conflicting_constraints",
        "select_redundant_constraints",
        "select_partially_redundant_constraints",
        "select_malformed_constraints",
        "select_joints_of_component",
    )
    assert variant.action_ids == frozenset({"Assembly_SelectRedundantConstraints"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.exact_target_type == "HumanActiveAssemblyAndExactSolverDiagnosis"
    assert variant.transaction_behavior == "none"
    assert schema["additionalProperties"] is False
    assert "expected_redundant_count" in schema["required"]
    assert "expected_conflicting_count" not in schema["properties"]


def test_state_matches_native_overlapping_redundancy_categories() -> None:
    document, assembly, _group, _ground, components, _joints = _redundant_fixture()
    state = capture_assembly_diagnosis_state(assembly)
    assert state.summary()["redundant_count"] == 3
    assert all(item.status == "redundant" for item in state.joint_diagnostics)

    first = assembly._diagnostics["joints"][0]
    first["constraints"].append(
        {
            "specification": "DistanceConstraintIJ",
            "residual": 0.0,
            "absolute_residual": 0.0,
            "redundant": False,
        }
    )
    first["constraint_count"] = 2
    assembly._diagnostics["joints"][0]["removed_degrees_of_freedom"] = 1
    assembly._diagnostics["partially_redundant_joints"] = [state.redundant_names[0]]
    assembly._diagnostics["has_partial_redundancies"] = True
    overlapping = capture_assembly_diagnosis_state(assembly)
    assert overlapping.redundant_names[0] == overlapping.partially_redundant_names[0]
    assert overlapping.joint_diagnostics[0].status == "redundant"
    result = read_redundant_constraints(
        _context(document),
        _spec(document, assembly, overlapping),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: _selection(document, (components[0].Name,)),
    )
    assert result["redundant_joints"][0]["redundancy"] == "partial"

    first["constraints"][0]["specification"] = "DistanceConstraintIJ"
    with pytest.raises(NativeAssemblyDiagnosisError, match="redundant joint"):
        capture_assembly_diagnosis_state(assembly)


def test_redundant_read_is_exact_paginated_and_selection_preserving() -> None:
    document, assembly, _group, _ground, components, joints = _redundant_fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    selected = _selection(document, (components[2].Name,))
    before_objects = tuple(document.Objects)
    before_placements = tuple(component.Placement for component in components)

    first = read_redundant_constraints(
        context,
        _spec(document, assembly, state, limit=2),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )
    second = read_redundant_constraints(
        context,
        _spec(document, assembly, state, offset=2, limit=2),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )

    assert first["operation"] == "select_redundant_constraints"
    assert first["diagnosis_state_sha256"] == state.state_sha256
    assert first["solver_status"] == 0
    assert first["remaining_degrees_of_freedom"] == 8
    assert first["redundant_joint_count"] == 3
    assert first["returned_count"] == 2
    assert first["next_offset"] == 2
    assert [item["joint"]["object_name"] for item in first["redundant_joints"]] == [
        joints[0].Name,
        joints[1].Name,
    ]
    assert [item["joint"]["object_name"] for item in second["redundant_joints"]] == [
        joints[2].Name
    ]
    item = first["redundant_joints"][0]
    assert item["first"]["element_path"] == "Vertex1"
    assert item["constraint_count"] == 1
    assert item["redundant_constraint_count"] == 1
    assert item["removed_degrees_of_freedom"] == 0
    assert item["diagnostic_status"] == "redundant"
    assert item["redundancy"] == "complete"
    assert "next_offset" not in second
    assert tuple(document.Objects) == before_objects
    assert tuple(component.Placement for component in components) == before_placements


def test_redundant_read_rejects_stale_count_offset_and_selection_drift() -> None:
    document, assembly, _group, _ground, components, _joints = _redundant_fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    selected = _selection(document, (components[0].Name,))

    stale = _spec(document, assembly, state)
    stale = RedundantConstraintsSpec(
        stale.assembly_ref,
        stale.expected_diagnosis_state_sha256,
        stale.expected_component_count,
        stale.expected_grounded_count,
        stale.expected_joint_count,
        2,
        stale.offset,
        stale.limit,
    )
    with pytest.raises(NativeAssemblyDiagnosisError, match="counts changed"):
        preflight_redundant_constraints(
            context,
            stale,
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: selected,
        )
    with pytest.raises(NativeAssemblyDiagnosisError, match="offset"):
        preflight_redundant_constraints(
            context,
            _spec(document, assembly, state, offset=3),
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: selected,
        )

    calls = 0

    def changing_selection(_document):
        nonlocal calls
        calls += 1
        return selected if calls == 1 else _selection(document)

    with pytest.raises(NativeAssemblyDiagnosisError, match="selection changed"):
        read_redundant_constraints(
            context,
            _spec(document, assembly, state),
            active_reader=lambda _document: assembly,
            selection_reader=changing_selection,
        )


def test_empty_redundant_read_returns_one_exact_empty_page() -> None:
    document, assembly, _group, _ground, _components, _joints = _redundant_fixture(
        redundant_count=0
    )
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    result = read_redundant_constraints(
        context,
        _spec(document, assembly, state, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda target: _selection(target),
    )

    assert result["redundant_joint_count"] == 0
    assert result["returned_count"] == 0
    assert result["redundant_joints"] == []
    assert "next_offset" not in result


def test_runtime_parses_closed_exact_redundant_arguments(monkeypatch) -> None:
    document, assembly, _group, _ground, _components, _joints = _redundant_fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    runtime = NativeAssemblyDiagnosisRuntime(context)
    captured = []
    monkeypatch.setattr(
        runtime_module,
        "read_redundant_constraints",
        lambda exact_context, spec: (
            captured.append((exact_context, spec)) or {"ok": True}
        ),
    )
    arguments = {
        "operation": "select_redundant_constraints",
        "assembly": {"object_name": assembly.Name},
        "expected_diagnosis_state_sha256": state.state_sha256,
        "expected_component_count": 3,
        "expected_grounded_count": 1,
        "expected_joint_count": 3,
        "expected_redundant_count": 3,
        "offset": 0,
        "limit": 2,
    }

    assert runtime.diagnose(arguments) == {"ok": True}
    assert captured == [(context, _spec(document, assembly, state, limit=2))]
    with pytest.raises(RuntimeError, match="do not match"):
        runtime.diagnose({**arguments, "expected_conflicting_count": 3})
