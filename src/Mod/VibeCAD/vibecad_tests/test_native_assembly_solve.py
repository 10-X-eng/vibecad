# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeAssemblySolve import (
    AssemblySolveSpec,
    NativeAssemblySolveError,
    apply_assembly_solve,
    preflight_assembly_solve,
    verify_assembly_solve,
)
from VibeCADNativeAssemblySolveState import (
    MAX_SOLVER_PLACEMENT_OBJECTS,
    NativeAssemblySolveStateError,
    capture_assembly_solver_state,
)
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeMutation import NativeMutationRunner
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeTargets import NativeObjectRef


_DERIVATIONS = {
    "Assembly::AssemblyObject": {"App::Part", "App::GeoFeature"},
    "Assembly::JointGroup": {"App::DocumentObjectGroup"},
    "App::Link": {"App::Link", "App::GeoFeature"},
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
        self._placement_read_only = False
        self._valid = True

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected or expected in _DERIVATIONS.get(
            self.TypeId,
            set(),
        )

    def isValid(self) -> bool:
        return self._valid

    def getPropertyStatus(self, name: str):
        if name == "Placement" and self._placement_read_only:
            return ["ReadOnly"]
        return []

    def getLinkedObject(self):
        return getattr(self, "LinkedObject", None)


class _Document:
    Uid = "assembly-solve-document"
    Name = "AssemblySolveDocument"

    def __init__(self) -> None:
        self.Objects = []
        self.next_id = 1
        self.recompute_count = 0

    def add(self, name: str, type_id: str) -> _Object:
        obj = _Object(self, name, type_id)
        self.Objects.append(obj)
        return obj

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def recompute(self):
        self.recompute_count += 1
        return True


def _fixture(*, grounded: bool = True):
    document = _Document()
    source1 = document.add("Source1", "Part::Box")
    source2 = document.add("Source2", "Part::Box")
    assembly = document.add("Assembly", "Assembly::AssemblyObject")
    assembly.Type = "Assembly"
    group = document.add("Joints", "Assembly::JointGroup")
    assembly.Group.append(group)
    fixed = document.add("FixedJoint", "App::FeaturePython")
    fixed.PropertiesList = ["JointType", "Reference1", "Reference2"]
    fixed.JointType = "Fixed"
    group.Group.append(fixed)
    first = document.add("FirstComponent", "App::Link")
    first.LinkedObject = source1
    first.ElementCount = 0
    first.Placement = _Placement(0.0)
    first.PropertiesList = ["Placement"]
    second = document.add("SecondComponent", "App::Link")
    second.LinkedObject = source2
    second.ElementCount = 0
    second.Placement = _Placement(25.0)
    second.PropertiesList = ["Placement"]
    assembly.Group.extend((first, second))
    fixed.Reference1 = [first, ["Face1", "Face1"]]
    fixed.Reference2 = [second, ["Face1", "Face1"]]
    if grounded:
        ground = document.add("GroundedJoint", "App::FeaturePython")
        ground.PropertiesList = ["ObjectToGround"]
        ground.ObjectToGround = first
        ground.VibeCADTimelineRole = "operation"
        group.Group.insert(0, ground)
        first._placement_read_only = True
    else:
        ground = None
    assembly._diagnostics = {
        "solver_status": 0,
        "solver_message": "",
        "remaining_degrees_of_freedom": 0,
        "has_conflicts": False,
        "has_redundancies": False,
        "has_partial_redundancies": False,
        "has_malformed_constraints": False,
        "conflicting_joints": [],
        "redundant_joints": [],
        "partially_redundant_joints": [],
        "malformed_joints": [],
        "grounded_components": [],
        "joints": [
            {
                "joint": fixed.Name,
                "status": "satisfied",
                "constraint_count": 6,
                "redundant_constraint_count": 0,
                "removed_degrees_of_freedom": 6,
                "maximum_absolute_residual": 0.0,
                "constraints": [],
            }
        ],
        "residual_tolerance": 1.0e-6,
    }
    assembly.getSolverDiagnostics = lambda: dict(assembly._diagnostics)
    return document, assembly, group, ground, first, second


@pytest.fixture(autouse=True)
def _active_timeline(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "UtilsAssembly",
        SimpleNamespace(isTimelineOperationActive=lambda _obj: True),
    )


def _selection(document):
    return {
        "document_uid": document.Uid,
        "selected_count": 0,
        "items": [],
    }


def _active_reader(assembly):
    def read(_document):
        return assembly

    return read


def _spec(document, assembly, *, grounded_count: int = 1) -> AssemblySolveSpec:
    state = capture_assembly_solver_state(assembly)
    return AssemblySolveSpec(
        NativeObjectRef(document.Uid, assembly.Name),
        state.state_sha256,
        2,
        grounded_count,
        1,
    )


def test_solve_schema_requires_only_the_active_assembly() -> None:
    definition = assembly_structure_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}
    solve = variants["solve_assembly"]
    schema = definition.provider_schema(("solve_assembly",))["parameters"]["oneOf"][0]

    assert solve.action_ids == frozenset({"Assembly_SolveAssembly"})
    assert solve.surface_ids == frozenset({"assemble"})
    assert solve.exact_target_type == "HumanActiveAssemblyAndExactSolverState"
    assert solve.transaction_behavior == "document"
    assert set(schema["required"]) == {"assembly"}
    assert not {
        "expected_solver_state_sha256",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
    } & set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_solver_state_fingerprints_every_bounded_placement_and_lock() -> None:
    _document, assembly, _group, _ground, first, second = _fixture()
    initial = capture_assembly_solver_state(assembly)

    assert [record.obj for record in initial.records] == [first, second]
    assert initial.summary()["placement_object_count"] == 2
    second.Placement = _Placement(30.0)
    moved = capture_assembly_solver_state(assembly)
    assert moved.state_sha256 != initial.state_sha256
    second.Placement = _Placement(25.0)
    second._placement_read_only = True
    locked = capture_assembly_solver_state(assembly)
    assert locked.state_sha256 != initial.state_sha256


def test_solver_state_refuses_an_unbounded_placement_graph() -> None:
    document, assembly, _group, _ground, _first, _second = _fixture()
    source = document.getObject("Source1")
    for index in range(MAX_SOLVER_PLACEMENT_OBJECTS - 1):
        component = document.add(f"Extra{index}", "App::Link")
        component.LinkedObject = source
        component.ElementCount = 0
        component.Placement = _Placement(float(index))
        component.PropertiesList = ["Placement"]
        assembly.Group.append(component)

    with pytest.raises(NativeAssemblySolveStateError, match="128-object"):
        capture_assembly_solver_state(assembly)


def test_preflight_rejects_stale_counts_and_placement_state() -> None:
    document, assembly, _group, _ground, _first, second = _fixture()
    active = _active_reader(assembly)
    spec = _spec(document, assembly)

    prepared = preflight_assembly_solve(
        document,
        spec,
        active_reader=active,
        selection_reader=_selection,
    )
    assert prepared.assembly is assembly

    second.Placement = _Placement(26.0)
    with pytest.raises(NativeAssemblySolveError, match="placement state changed"):
        preflight_assembly_solve(
            document,
            spec,
            active_reader=active,
            selection_reader=_selection,
        )
    with pytest.raises(NativeAssemblySolveError, match="component count changed"):
        preflight_assembly_solve(
            document,
            AssemblySolveSpec(
                spec.assembly_ref,
                capture_assembly_solver_state(assembly).state_sha256,
                1,
                1,
                1,
            ),
            active_reader=active,
            selection_reader=_selection,
        )


def test_solve_moves_only_the_constrained_component_and_verifies_exact_state() -> None:
    document, assembly, _group, _ground, first, second = _fixture()
    active = _active_reader(assembly)
    assembly.solve = lambda _undo: setattr(second, "Placement", _Placement(10.0)) or 0
    spec = _spec(document, assembly)

    draft = apply_assembly_solve(
        document,
        spec,
        active_reader=active,
        selection_reader=_selection,
    )
    result = verify_assembly_solve(
        document,
        draft,
        active_reader=active,
        selection_reader=_selection,
    )

    assert document.recompute_count == 1
    assert first.Placement.Base.x == 0.0
    assert second.Placement.Base.x == 10.0
    assert tuple(identity.object_name for identity in draft.changed) == (second.Name,)
    assert result["moved_object_count"] == 1
    assert result["placement_changes"][0]["object"]["object_name"] == second.Name
    assert result["placement_changes"][0]["before"]["origin_mm"]["x"] == 25.0
    assert result["placement_changes"][0]["after"]["origin_mm"]["x"] == 10.0
    assert result["grounded_placements_unchanged"] is True
    assert result["solver"]["solver_status"] == 0
    assert result["selection_unchanged"] is True


def test_solve_accepts_only_the_native_grounding_repair_side_effect() -> None:
    document, assembly, group, _ground, first, _second = _fixture(grounded=False)
    active = _active_reader(assembly)
    first._placement_read_only = True

    def solve_with_grounding_repair(_undo):
        ground = document.add("GroundedJoint", "App::FeaturePython")
        ground.PropertiesList = ["ObjectToGround"]
        ground.ObjectToGround = first
        ground.VibeCADTimelineRole = "operation"
        group.Group.insert(0, ground)
        return 0

    assembly.solve = solve_with_grounding_repair
    draft = apply_assembly_solve(
        document,
        _spec(document, assembly, grounded_count=0),
        active_reader=active,
        selection_reader=_selection,
    )
    result = verify_assembly_solve(
        document,
        draft,
        active_reader=active,
        selection_reader=_selection,
    )

    assert len(draft.created) == 1
    assert {identity.object_name for identity in draft.changed} == {
        assembly.Name,
        group.Name,
    }
    assert result["grounded_count"] == 1
    assert result["moved_object_count"] == 0
    assert result["grounding_repairs"][0]["component"]["object_name"] == first.Name


def test_native_solver_failure_keeps_its_domain_code_and_diagnostic() -> None:
    document, assembly, _group, _ground, _first, _second = _fixture(grounded=False)
    active = _active_reader(assembly)
    assembly._diagnostics["solver_status"] = -6
    assembly._diagnostics["solver_message"] = (
        "No grounded component is visible to the native solver."
    )
    assembly.solve = lambda _undo: -6
    spec = _spec(document, assembly, grounded_count=0)

    with pytest.raises(NativeAssemblySolveError) as failure:
        apply_assembly_solve(
            document,
            spec,
            active_reader=active,
            selection_reader=_selection,
        )

    assert failure.value.failure()["error_code"] == "NATIVE_ASSEMBLY_SOLVE_FAILED"
    assert "No grounded component" in str(failure.value)
    assert document.recompute_count == 1


def test_solver_failure_aborts_the_owned_transaction_and_restores_placements() -> None:
    document, assembly, _group, _ground, _first, second = _fixture()
    active = _active_reader(assembly)
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ticket = state.begin_call(document.Uid, "assembly.structure")
    original = second.Placement
    assembly._diagnostics["solver_status"] = -1
    assembly._diagnostics["solver_message"] = "Injected native solver failure."

    def fail_after_movement(_undo):
        second.Placement = _Placement(99.0)
        return -1

    assembly.solve = fail_after_movement

    class _Transaction:
        def __init__(self) -> None:
            self.aborted = False

        def commit(self) -> None:
            raise AssertionError("A failed solve must not commit")

        def abort(self) -> None:
            self.aborted = True
            second.Placement = original

    transaction = _Transaction()
    runner = NativeMutationRunner(
        state,
        transaction_factory=lambda _document, _name: transaction,
        document_is_live=lambda _document: True,
    )
    spec = _spec(document, assembly)

    with pytest.raises(NativeAssemblySolveError, match="Injected native"):
        runner.run(
            ticket=ticket,
            document=document,
            transaction_name="Solve Native Assembly",
            reauthorize_turn=lambda: None,
            mutate=lambda target: apply_assembly_solve(
                target,
                spec,
                active_reader=active,
                selection_reader=_selection,
            ),
            verify=lambda target, draft: verify_assembly_solve(
                target,
                draft,
                active_reader=active,
                selection_reader=_selection,
            ),
        )

    assert transaction.aborted is True
    assert second.Placement is original
    assert state.snapshot(document.Uid)["recent_receipts"] == []


def test_verify_refuses_any_post_solve_placement_drift() -> None:
    document, assembly, _group, _ground, _first, second = _fixture()
    active = _active_reader(assembly)
    assembly.solve = lambda _undo: setattr(second, "Placement", _Placement(10.0)) or 0
    draft = apply_assembly_solve(
        document,
        _spec(document, assembly),
        active_reader=active,
        selection_reader=_selection,
    )
    second.Placement = _Placement(11.0)

    with pytest.raises(NativeAssemblySolveError, match="changed after"):
        verify_assembly_solve(
            document,
            draft,
            active_reader=active,
            selection_reader=_selection,
        )
