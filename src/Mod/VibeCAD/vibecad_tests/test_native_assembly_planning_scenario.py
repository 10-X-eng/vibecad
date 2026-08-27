# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

from tool_impl.assembly_planning import AssemblyPlanningError, propose_joints
import VibeCADAssemblyPlanning as public_planning
import VibeCADNativeAssemblyPlanningScenario as scenario_reader


class Obj(SimpleNamespace):
    __hash__ = object.__hash__


def _identified(kind: str, **values):
    return Obj(
        VibeCADAssemblyPersistentIdentity=str(uuid.uuid4()),
        VibeCADAssemblyIdentityKind=kind,
        VibeCADAssemblyIdentitySchema="vibecad-assembly-identity-v1",
        **values,
    )


def _fixture(monkeypatch, *, coupling_parameters=None, ground_first=False):
    document = Obj(Objects=[])
    assembly = _identified(
        "assembly", Name="Assembly", ID=1, Document=document, Group=[]
    )
    first_lcs = _identified("interface", Name="MountA")
    second_lcs = _identified("interface", Name="MountB")
    first = _identified(
        "occurrence", Name="First", Document=document, Group=[first_lcs]
    )
    second = _identified(
        "occurrence", Name="Second", Document=document, Group=[second_lcs]
    )
    joint = _identified(
        "joint",
        Name="Joint",
        JointType="Revolute",
        Reference1=[first, ["MountA", "MountA"]],
        Reference2=[second, ["MountB", "MountB"]],
    )
    joint_group = Obj(Group=[joint])
    document.Objects = [assembly, first, second, first_lcs, second_lcs, joint]
    monkeypatch.setattr(scenario_reader, "assembly_components", lambda value: (first, second))
    monkeypatch.setattr(scenario_reader, "require_joint_group", lambda value: joint_group)
    monkeypatch.setattr(scenario_reader, "active_regular_joints", lambda value: tuple(value.Group))
    monkeypatch.setattr(
        scenario_reader,
        "active_grounded_joints",
        lambda value: (
            (Obj(ObjectToGround=first),) if ground_first else ()
        ),
    )

    definitions = {
        first: ("MountA", "shaft"),
        second: ("MountB", "bore"),
    }

    def interfaces(component):
        name, kind = definitions[component]
        return {
            name: {
                "selection": {"type": "frame", "native_lcs": name},
                "connector": {
                    "kind": kind,
                    "allowed_joints": [
                        "revolute",
                        *(
                            list(coupling_parameters["values"])
                            if component is second and coupling_parameters else []
                        ),
                    ],
                    "compatibility": "mount-01",
                    **(
                        {"coupling_parameters": coupling_parameters}
                        if component is second and coupling_parameters else {}
                    ),
                },
                "resolved": {
                    "geometry_binding": {"status": "current"},
                },
            }
        }

    monkeypatch.setattr(scenario_reader.reference_contracts, "native_interface_definitions", interfaces)
    return document, assembly, joint


def test_reads_normalized_live_scenario_and_proposals(monkeypatch) -> None:
    document, assembly, _joint = _fixture(monkeypatch)
    guard_calls = []
    result = scenario_reader.read_live_planning_scenario(
        document,
        guard=lambda: guard_calls.append(True),
        active_reader=lambda value: assembly,
    )

    assert result["scenario_id"].startswith("assembly:")
    assert len(result["occurrences"]) == 2
    assert len(result["interfaces"]) == 2
    assert result["joints"][0]["joint_kind"] == "revolute"
    assert result["extraction"] == {
        "schema": "vibecad-live-assembly-scenario-extraction-v1",
        "source": "human-active-native-assembly",
        "mutation_performed": False,
        "included_joint_count": 1,
        "omitted_joint_count": 0,
        "omitted_joints": [],
        "omitted_joints_truncated": False,
        "coupling_declarations_extracted": False,
    }
    assert len(guard_calls) == 2
    assert propose_joints(result)["mutation_performed"] is False


def test_public_facade_lazily_reaches_live_reader(monkeypatch) -> None:
    document, assembly, _joint = _fixture(monkeypatch)

    result = public_planning.read_live_planning_scenario(
        document, active_reader=lambda value: assembly
    )

    assert result["extraction"]["source"] == "human-active-native-assembly"


def test_projects_explicit_coupling_values_only_for_unique_moving_occurrence(
    monkeypatch,
) -> None:
    declaration = {
        "schema": "vibecad-interface-coupling-parameters-v1",
        "values": {"gears": {"pitch_radius_mm": 18.0}},
    }
    document, assembly, _joint = _fixture(
        monkeypatch,
        coupling_parameters=declaration,
        ground_first=True,
    )

    result = scenario_reader.read_live_planning_scenario(
        document, active_reader=lambda value: assembly
    )

    second_occurrence = next(
        value for value in result["occurrences"] if value["object_name"] == "Second"
    )
    assert result["joints"][0]["moving_occurrence_id"] == second_occurrence["persistent_id"]
    assert result["joints"][0]["coupling_parameters"] == declaration
    assert result["extraction"]["coupling_declarations_extracted"] is True


def test_marks_exact_live_coupling_dependencies_as_already_realized(
    monkeypatch,
) -> None:
    first = _identified("joint", Name="FirstMotion", JointType="Revolute")
    second = _identified("joint", Name="SecondMotion", JointType="Revolute")
    coupling = _identified("joint", Name="ExistingGears", JointType="Gears")
    records = {
        "FirstMotion": {"persistent_id": first.VibeCADAssemblyPersistentIdentity},
        "SecondMotion": {"persistent_id": second.VibeCADAssemblyPersistentIdentity},
    }
    monkeypatch.setattr(
        scenario_reader,
        "gears_dependency_summary",
        lambda joint, active: {
            "first_revolute_joint": {"object_name": "FirstMotion"},
            "second_revolute_joint": {"object_name": "SecondMotion"},
        },
    )

    scenario_reader._mark_realized_couplings(
        (first, second, coupling), records
    )

    assert records["FirstMotion"]["realized_couplings"] == [{
        "coupling_kind": "gears",
        "other_joint_id": second.VibeCADAssemblyPersistentIdentity,
        "coupling_joint_id": coupling.VibeCADAssemblyPersistentIdentity,
    }]


def test_reports_element_joint_as_omitted_without_inventing_interface(monkeypatch) -> None:
    document, assembly, joint = _fixture(monkeypatch)
    joint.Reference1 = [joint.Reference1[0], ["Face6", "Face6"]]

    result = scenario_reader.read_live_planning_scenario(
        document, active_reader=lambda value: assembly
    )

    assert result["joints"] == []
    assert result["extraction"]["omitted_joint_count"] == 1
    assert result["extraction"]["omitted_joints"][0]["reason"].startswith(
        "connector-not-bound"
    )


def test_fails_when_active_assembly_changes_during_read(monkeypatch) -> None:
    document, assembly, _joint = _fixture(monkeypatch)
    replacement = _identified(
        "assembly", Name="Other", ID=2, Document=document, Group=[]
    )
    values = iter((assembly, replacement))

    with pytest.raises(AssemblyPlanningError, match="changed while"):
        scenario_reader.read_live_planning_scenario(
            document, active_reader=lambda value: next(values)
        )


def test_fails_closed_for_missing_interface_identity(monkeypatch) -> None:
    document, assembly, _joint = _fixture(monkeypatch)
    first = scenario_reader.assembly_components(assembly)[0]
    first.Group[0].VibeCADAssemblyPersistentIdentity = ""
    first.Group[0].VibeCADAssemblyIdentityKind = ""
    first.Group[0].VibeCADAssemblyIdentitySchema = ""

    with pytest.raises(AssemblyPlanningError, match="lacks its persisted"):
        scenario_reader.read_live_planning_scenario(
            document, active_reader=lambda value: assembly
        )
