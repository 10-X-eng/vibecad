# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyDiagnosisRuntime as runtime_module
from VibeCADNativeAssemblyConflictDiagnosis import (
    ConflictingConstraintsSpec,
    preflight_conflicting_constraints,
    read_conflicting_constraints,
)
from VibeCADNativeAssemblyDiagnosisRuntime import NativeAssemblyDiagnosisRuntime
from VibeCADNativeAssemblyDiagnosisSchema import (
    assembly_diagnosis_capability_definition,
)
from VibeCADNativeAssemblyDiagnosisState import (
    MAX_ASSEMBLY_JOINTS,
    NativeAssemblyDiagnosisError,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeTargets import NativeObjectRef
from VibeCADNativeUndo import NativeAssistantUndoLedger


_DERIVATIONS = {
    "Assembly::AssemblyObject": {"App::Part", "App::GeoFeature"},
    "Assembly::JointGroup": {"App::DocumentObjectGroup"},
    "App::Link": {"App::GeoFeature"},
    "Part::Box": {"Part::Feature", "App::GeoFeature"},
}


class _Placement:
    def __init__(self, x: float = 0.0) -> None:
        self.Base = SimpleNamespace(x=float(x), y=0.0, z=0.0)
        self.Rotation = SimpleNamespace(
            Axis=SimpleNamespace(x=0.0, y=0.0, z=1.0),
            Angle=0.0,
        )

    def isSame(self, other, tolerance: float) -> bool:
        return (
            isinstance(other, _Placement)
            and abs(self.Base.x - other.Base.x) <= tolerance
        )


class _Object:
    def __init__(self, document, name: str, type_id: str) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.ID = document.next_id
        document.next_id += 1
        self.Group = []
        self.PropertiesList = []
        self.State = []

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected or expected in _DERIVATIONS.get(
            self.TypeId,
            set(),
        )

    def isValid(self) -> bool:
        return True

    def getPropertyStatus(self, _name: str):
        return []

    def getLinkedObject(self):
        return getattr(self, "LinkedObject", None)


class _Document:
    Uid = "assembly-conflict-document"
    Name = "AssemblyConflictDocument"

    def __init__(self) -> None:
        self.Objects = []
        self.next_id = 1

    def add(self, name: str, type_id: str) -> _Object:
        obj = _Object(self, name, type_id)
        self.Objects.append(obj)
        return obj

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)


def _constraint(residual: float) -> dict:
    return {
        "specification": "DistanceConstraintIJ",
        "residual": residual,
        "absolute_residual": abs(residual),
        "redundant": False,
    }


def _fixture(*, conflict_count: int = 3):
    document = _Document()
    sources = [document.add(f"Source{index}", "Part::Box") for index in range(3)]
    assembly = document.add("Assembly", "Assembly::AssemblyObject")
    assembly.Type = "Assembly"
    group = document.add("Joints", "Assembly::JointGroup")
    assembly.Group.append(group)
    components = []
    for index, source in enumerate(sources):
        component = document.add(f"Component{index}", "App::Link")
        component.LinkedObject = source
        component.ElementCount = 0
        component.Placement = _Placement(float(index * 10))
        component.PropertiesList = ["Placement"]
        assembly.Group.append(component)
        components.append(component)
    ground = document.add("GroundedJoint", "App::FeaturePython")
    ground.PropertiesList = ["ObjectToGround"]
    ground.ObjectToGround = components[0]
    group.Group.append(ground)
    joints = []
    for index, (first, second) in enumerate(((0, 1), (1, 2), (0, 2))):
        joint = document.add(f"Distance{index}", "App::FeaturePython")
        joint.PropertiesList = [
            "JointType",
            "Reference1",
            "Reference2",
            "Offset1",
            "Offset2",
            "Distance",
            "Suppressed",
        ]
        joint.JointType = "Distance"
        joint.Suppressed = False
        joint.Distance = SimpleNamespace(Value=float(index + 3))
        joint.Reference1 = [components[first], ["Vertex1", "Vertex1"]]
        joint.Reference2 = [components[second], ["Vertex1", "Vertex1"]]
        joint.Offset1 = _Placement()
        joint.Offset2 = _Placement()
        group.Group.append(joint)
        joints.append(joint)
    conflict_names = [joint.Name for joint in joints[:conflict_count]]
    assembly._diagnostics = {
        "solver_status": -1 if conflict_names else 0,
        "solver_message": "Unable to converge." if conflict_names else "",
        "remaining_degrees_of_freedom": 11,
        "has_conflicts": bool(conflict_names),
        "has_redundancies": False,
        "has_partial_redundancies": False,
        "has_malformed_constraints": False,
        "conflicting_joints": conflict_names,
        "redundant_joints": [],
        "partially_redundant_joints": [],
        "malformed_joints": [],
        "grounded_components": [
            {"joint": ground.Name, "component": components[0].Name}
        ],
        "joints": [
            {
                "joint": joint.Name,
                "status": "conflicting"
                if joint.Name in conflict_names
                else "satisfied",
                "constraint_count": 1,
                "redundant_constraint_count": 0,
                "removed_degrees_of_freedom": 1,
                "maximum_absolute_residual": 0.1 + index * 0.01
                if joint.Name in conflict_names
                else 0.0,
                "constraints": [
                    _constraint(
                        (-1.0 if index % 2 else 1.0) * (0.1 + index * 0.01)
                        if joint.Name in conflict_names
                        else 0.0
                    )
                ],
            }
            for index, joint in enumerate(joints)
        ],
        "residual_tolerance": 1.0e-6,
    }
    assembly.getSolverDiagnostics = lambda: deepcopy(assembly._diagnostics)
    return document, assembly, group, ground, tuple(components), tuple(joints)


@pytest.fixture(autouse=True)
def _active_timeline(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "UtilsAssembly",
        SimpleNamespace(isTimelineOperationActive=lambda _obj: True),
    )


def _selection(document, names=()):
    return {
        "document_uid": document.Uid,
        "selected_count": len(names),
        "items": [{"object_name": name} for name in names],
    }


def _context(document) -> NativeRuntimeContext:
    return NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=NativeDocumentStateStore(),
        undo_ledger=NativeAssistantUndoLedger(),
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "assemble",
        edit_or_task_active=lambda: False,
    )


def _spec(document, assembly, state, *, offset: int = 0, limit: int = 32):
    return ConflictingConstraintsSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        expected_diagnosis_state_sha256=state.state_sha256,
        expected_component_count=len(state.components),
        expected_grounded_count=len(state.grounded_joints),
        expected_joint_count=len(state.regular_joints),
        expected_conflicting_count=len(state.conflicting_names),
        offset=offset,
        limit=limit,
    )


def test_schema_maps_only_the_live_conflict_action_to_an_exact_read() -> None:
    definition = assembly_diagnosis_capability_definition()
    variant = definition.variants[0]
    schema = definition.provider_schema(("select_conflicting_constraints",))[
        "parameters"
    ]["oneOf"][0]

    assert definition.name == "assembly.diagnose"
    assert tuple(item.operation for item in definition.variants) == (
        "select_conflicting_constraints",
        "select_redundant_constraints",
        "select_partially_redundant_constraints",
        "select_malformed_constraints",
        "select_joints_of_component",
        "read",
    )
    assert definition.primary_classification == "read"
    assert variant.action_ids == frozenset({"Assembly_SelectConflictingConstraints"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.exact_target_type == "HumanActiveAssemblyAndExactSolverDiagnosis"
    assert variant.transaction_behavior == "none"
    assert schema["additionalProperties"] is False
    assert schema["required"] == []
    assert set(schema["properties"]) == {
        "operation",
        "assembly",
        "expected_diagnosis_state_sha256",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_conflicting_count",
        "offset",
        "limit",
    }
    assert schema["properties"]["limit"]["maximum"] == 100
    registry = build_native_capability_registry()
    assert registry.definition("assembly.diagnose") is not None
    assert registry.implementation("assembly.diagnose") is not None


def test_runtime_accepts_the_legacy_frozen_conflict_request(monkeypatch) -> None:
    document, assembly, _group, _ground, _components, _joints = _fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    runtime = NativeAssemblyDiagnosisRuntime(context)
    captured = []
    monkeypatch.setattr(
        runtime_module,
        "read_conflicting_constraints",
        lambda exact_context, spec: (
            captured.append((exact_context, spec)) or {"ok": True}
        ),
    )
    arguments = {
        "operation": "select_conflicting_constraints",
        "assembly": {"object_name": assembly.Name},
        "expected_diagnosis_state_sha256": state.state_sha256,
        "expected_component_count": len(state.components),
        "expected_grounded_count": len(state.grounded_joints),
        "expected_joint_count": len(state.regular_joints),
        "expected_conflicting_count": len(state.conflicting_names),
        "offset": 0,
        "limit": 32,
    }

    assert runtime.diagnose(arguments) == {"ok": True}
    assert captured == [(context, _spec(document, assembly, state))]

    partial = dict(arguments)
    partial.pop("expected_joint_count")
    with pytest.raises(RuntimeError, match="complete frozen-state contract"):
        runtime.diagnose(partial)


def test_runtime_reads_the_active_assembly_summary_without_a_component(
    monkeypatch,
) -> None:
    document, assembly, _group, _ground, _components, _joints = _fixture()
    context = _context(document)
    runtime = NativeAssemblyDiagnosisRuntime(context)
    monkeypatch.setattr(
        runtime_module,
        "read_active_assembly",
        lambda target_document: assembly,
    )

    result = runtime.diagnose({"operation": "read"})

    assert result["assembly"] == {
        "object_name": assembly.Name,
        "label": assembly.Label,
    }
    assert result["component_count"] == 3
    assert result["joint_count"] == 3


def test_runtime_accepts_a_large_diagnosis_page(monkeypatch) -> None:
    document, assembly, _group, _ground, _components, _joints = _fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    runtime = NativeAssemblyDiagnosisRuntime(context)
    captured = []
    monkeypatch.setattr(
        runtime_module,
        "read_active_assembly",
        lambda target_document: assembly,
    )
    monkeypatch.setattr(
        runtime_module,
        "read_conflicting_constraints",
        lambda exact_context, spec: (
            captured.append((exact_context, spec)) or {"ok": True}
        ),
    )

    result = runtime.diagnose(
        {"operation": "select_conflicting_constraints", "limit": 50}
    )

    assert result == {"ok": True}
    assert captured == [(context, _spec(document, assembly, state, limit=50))]


def test_diagnosis_state_hashes_placements_joint_definitions_and_residuals() -> None:
    _document, assembly, _group, _ground, components, joints = _fixture()
    initial = capture_assembly_diagnosis_state(assembly)

    assert initial.summary() == {
        "available": True,
        "state_sha256": initial.state_sha256,
        "component_count": 3,
        "grounded_count": 1,
        "joint_count": 3,
        "solver_status": -1,
        "remaining_degrees_of_freedom": 11,
        "conflicting_count": 3,
        "redundant_count": 0,
        "partially_redundant_count": 0,
        "malformed_count": 0,
        "residual_tolerance": 1.0e-6,
    }
    components[1].Placement = _Placement(11.0)
    assert (
        capture_assembly_diagnosis_state(assembly).state_sha256 != initial.state_sha256
    )
    components[1].Placement = _Placement(10.0)
    joints[1].Distance.Value = 9.0
    assert (
        capture_assembly_diagnosis_state(assembly).state_sha256 != initial.state_sha256
    )
    joints[1].Distance.Value = 4.0
    assembly._diagnostics["joints"][1]["constraints"][0]["residual"] = -0.2
    assembly._diagnostics["joints"][1]["constraints"][0]["absolute_residual"] = 0.2
    assembly._diagnostics["joints"][1]["maximum_absolute_residual"] = 0.2
    assert (
        capture_assembly_diagnosis_state(assembly).state_sha256 != initial.state_sha256
    )


@pytest.mark.parametrize(
    "corrupt",
    (
        lambda raw: raw.update({"has_conflicts": False}),
        lambda raw: raw["conflicting_joints"].append(raw["conflicting_joints"][0]),
        lambda raw: raw["conflicting_joints"].append("MissingJoint"),
        lambda raw: raw["joints"][0].update({"status": "satisfied"}),
        lambda raw: raw["joints"][0].update({"maximum_absolute_residual": 0.0}),
        lambda raw: raw["joints"][0]["constraints"][0].update(
            {"absolute_residual": 0.2}
        ),
        lambda raw: raw.update(
            {
                "conflicting_joints": [
                    f"Joint{index}" for index in range(MAX_ASSEMBLY_JOINTS + 1)
                ]
            }
        ),
    ),
)
def test_diagnosis_state_rejects_inconsistent_or_unbounded_host_data(corrupt) -> None:
    _document, assembly, _group, _ground, _components, _joints = _fixture()
    corrupt(assembly._diagnostics)

    with pytest.raises(NativeAssemblyDiagnosisError, match="malformed"):
        capture_assembly_diagnosis_state(assembly)


def test_conflict_read_is_exact_paginated_and_selection_preserving() -> None:
    document, assembly, _group, _ground, components, joints = _fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    before_objects = tuple(document.Objects)
    before_placements = tuple(component.Placement for component in components)
    selected = _selection(document, (components[2].Name,))

    first = read_conflicting_constraints(
        context,
        _spec(document, assembly, state, limit=2),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )
    second = read_conflicting_constraints(
        context,
        _spec(document, assembly, state, offset=2, limit=2),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selected,
    )

    assert first["operation"] == "select_conflicting_constraints"
    assert first["diagnosis_state_sha256"] == state.state_sha256
    assert first["conflicting_joint_count"] == 3
    assert first["returned_count"] == 2
    assert first["next_offset"] == 2
    assert [item["joint"]["object_name"] for item in first["conflicting_joints"]] == [
        joints[0].Name,
        joints[1].Name,
    ]
    assert [item["joint"]["object_name"] for item in second["conflicting_joints"]] == [
        joints[2].Name
    ]
    assert "next_offset" not in second
    assert first["conflicting_joints"][0]["first"]["element_path"] == "Vertex1"
    assert first["conflicting_joints"][0]["violating_constraint_count"] == 1
    assert first["conflicting_joints"][0]["maximum_absolute_residual"] == 0.1
    assert tuple(document.Objects) == before_objects
    assert tuple(component.Placement for component in components) == before_placements


def test_conflict_read_rejects_stale_state_counts_offset_and_selection_drift() -> None:
    document, assembly, _group, _ground, components, _joints = _fixture()
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)

    def active(_document):
        return assembly

    selection = _selection(document, (components[0].Name,))

    stale = _spec(document, assembly, state)
    stale = ConflictingConstraintsSpec(
        stale.assembly_ref,
        "0" * 64,
        stale.expected_component_count,
        stale.expected_grounded_count,
        stale.expected_joint_count,
        stale.expected_conflicting_count,
        stale.offset,
        stale.limit,
    )
    with pytest.raises(NativeAssemblyDiagnosisError, match="diagnosis changed"):
        preflight_conflicting_constraints(
            context,
            stale,
            active_reader=active,
            selection_reader=lambda _document: selection,
        )
    with pytest.raises(NativeAssemblyDiagnosisError, match="offset"):
        preflight_conflicting_constraints(
            context,
            _spec(document, assembly, state, offset=3),
            active_reader=active,
            selection_reader=lambda _document: selection,
        )

    calls = 0

    def changing_selection(_document):
        nonlocal calls
        calls += 1
        return selection if calls == 1 else _selection(document)

    with pytest.raises(NativeAssemblyDiagnosisError, match="selection changed"):
        read_conflicting_constraints(
            context,
            _spec(document, assembly, state),
            active_reader=active,
            selection_reader=changing_selection,
        )


def test_empty_conflict_read_returns_one_exact_empty_page() -> None:
    document, assembly, _group, _ground, _components, _joints = _fixture(
        conflict_count=0
    )
    context = _context(document)
    state = capture_assembly_diagnosis_state(assembly)
    result = read_conflicting_constraints(
        context,
        _spec(document, assembly, state, limit=1),
        active_reader=lambda _document: assembly,
        selection_reader=lambda target: _selection(target),
    )

    assert result["solver_status"] == 0
    assert result["conflicting_joint_count"] == 0
    assert result["returned_count"] == 0
    assert result["conflicting_joints"] == []
    assert "next_offset" not in result
    assert "solver_message" not in result
