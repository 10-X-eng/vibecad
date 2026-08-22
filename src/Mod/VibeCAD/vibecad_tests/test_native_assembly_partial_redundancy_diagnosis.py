# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from VibeCADNativeAssemblyDiagnosisState import (
    NativeAssemblyDiagnosisError,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeAssemblyPartialRedundancyDiagnosis import (
    PartiallyRedundantConstraintsSpec,
    preflight_partially_redundant_constraints,
    read_partially_redundant_constraints,
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


def _partial_fixture(*, partial_count: int = 3, overlap_count: int = 2):
    document, assembly, group, ground, components, joints = _fixture(conflict_count=0)
    partial_names = [joint.Name for joint in joints[:partial_count]]
    redundant_names = [joint.Name for joint in joints[:overlap_count]]
    assembly._diagnostics.update(
        {
            "remaining_degrees_of_freedom": 8,
            "has_redundancies": bool(redundant_names),
            "has_partial_redundancies": bool(partial_names),
            "redundant_joints": redundant_names,
            "partially_redundant_joints": partial_names,
        }
    )
    for item in assembly._diagnostics["joints"]:
        partial = item["joint"] in partial_names
        overlap = item["joint"] in redundant_names
        if not partial:
            continue
        item.update(
            {
                "status": "redundant" if overlap else "partially_redundant",
                "constraint_count": 2,
                "redundant_constraint_count": 1,
                "removed_degrees_of_freedom": 1,
            }
        )
        item["constraints"][0]["redundant"] = True
        if overlap:
            item["constraints"][0]["specification"] = (
                "RedundantConstraintDistanceConstraintIJ"
            )
        item["constraints"].append(
            {
                "specification": "DirectionCosineConstraintIzJx",
                "residual": 0.0,
                "absolute_residual": 0.0,
                "redundant": False,
            }
        )
    assembly.getSolverDiagnostics = lambda: deepcopy(assembly._diagnostics)
    return document, assembly, group, ground, components, joints


def _spec(document, assembly, state, *, offset: int = 0, limit: int = 32):
    return PartiallyRedundantConstraintsSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        expected_diagnosis_state_sha256=state.state_sha256,
        expected_component_count=len(state.components),
        expected_grounded_count=len(state.grounded_joints),
        expected_joint_count=len(state.regular_joints),
        expected_partially_redundant_count=len(state.partially_redundant_names),
        offset=offset,
        limit=limit,
    )


def test_state_accepts_native_overlap_and_requires_exact_partial_aggregate() -> None:
    _document, assembly, _group, _ground, _components, _joints = _partial_fixture()
    state = capture_assembly_diagnosis_state(assembly)

    assert state.summary()["partially_redundant_count"] == 3
    assert state.summary()["redundant_count"] == 2
    assert [item.status for item in state.joint_diagnostics] == [
        "redundant",
        "redundant",
        "partially_redundant",
    ]

    first = assembly._diagnostics["joints"][0]
    first["constraints"][1]["redundant"] = True
    first["redundant_constraint_count"] = 2
    first["removed_degrees_of_freedom"] = 0
    with pytest.raises(NativeAssemblyDiagnosisError, match="partially redundant joint"):
        capture_assembly_diagnosis_state(assembly)


def test_partial_read_is_exact_paginated_and_selection_preserving() -> None:
    document, assembly, _group, _ground, components, joints = _partial_fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    selected = _selection(document, (components[1].Name,))
    before_objects = tuple(document.Objects)
    before_placements = tuple(component.Placement for component in components)

    first = read_partially_redundant_constraints(
        context,
        _spec(document, assembly, state, limit=2),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )
    second = read_partially_redundant_constraints(
        context,
        _spec(document, assembly, state, offset=2, limit=2),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )

    assert first["operation"] == "select_partially_redundant_constraints"
    assert first["diagnosis_state_sha256"] == state.state_sha256
    assert first["solver_status"] == 0
    assert first["remaining_degrees_of_freedom"] == 8
    assert first["partially_redundant_joint_count"] == 3
    assert first["returned_count"] == 2
    assert first["next_offset"] == 2
    assert [
        item["joint"]["object_name"]
        for item in first["partially_redundant_joints"]
    ] == [joints[0].Name, joints[1].Name]
    assert [
        item["joint"]["object_name"]
        for item in second["partially_redundant_joints"]
    ] == [joints[2].Name]
    assert first["partially_redundant_joints"][0]["diagnostic_status"] == (
        "redundant"
    )
    assert first["partially_redundant_joints"][0]["also_in_redundant_set"] is True
    assert second["partially_redundant_joints"][0]["diagnostic_status"] == (
        "partially_redundant"
    )
    assert second["partially_redundant_joints"][0]["also_in_redundant_set"] is False
    item = first["partially_redundant_joints"][0]
    assert item["first"]["element_path"] == "Vertex1"
    assert item["constraint_count"] == 2
    assert item["redundant_constraint_count"] == 1
    assert item["removed_degrees_of_freedom"] == 1
    assert "next_offset" not in second
    assert tuple(document.Objects) == before_objects
    assert tuple(component.Placement for component in components) == before_placements


def test_partial_read_rejects_stale_count_offset_and_selection_drift() -> None:
    document, assembly, _group, _ground, components, _joints = _partial_fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    selected = _selection(document, (components[0].Name,))
    stale = _spec(document, assembly, state)
    stale = PartiallyRedundantConstraintsSpec(
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
        preflight_partially_redundant_constraints(
            context,
            stale,
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: selected,
        )
    with pytest.raises(NativeAssemblyDiagnosisError, match="offset"):
        preflight_partially_redundant_constraints(
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
        read_partially_redundant_constraints(
            context,
            _spec(document, assembly, state),
            active_reader=lambda _document: assembly,
            selection_reader=changing_selection,
        )


def test_empty_partial_read_returns_one_exact_empty_page() -> None:
    document, assembly, _group, _ground, _components, _joints = _partial_fixture(
        partial_count=0,
        overlap_count=0,
    )
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    result = read_partially_redundant_constraints(
        context,
        _spec(document, assembly, state, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda target: _selection(target),
    )

    assert result["partially_redundant_joint_count"] == 0
    assert result["returned_count"] == 0
    assert result["partially_redundant_joints"] == []
    assert "next_offset" not in result
