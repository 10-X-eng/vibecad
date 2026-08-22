# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import replace

import pytest

import VibeCADNativeAssemblySimulation as simulation_module
import VibeCADNativeAssemblyStructureRuntime as runtime_module
from VibeCADNativeAssemblySimulation import (
    AssemblySimulationCreateSpec,
    AssemblySimulationMotionSpec,
    NativeAssemblySimulationError,
    create_assembly_simulation,
    preflight_create_assembly_simulation,
)
from VibeCADNativeAssemblySimulationState import (
    AssemblySimulationJoint,
    AssemblySimulationState,
)
from VibeCADNativeAssemblySolveState import AssemblySolverState
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeTargets import NativeObjectRef


class _Object:
    def __init__(self, document, name: str, type_id: str) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.ID = len(document.objects) + 1
        document.objects[name] = self


class _Document:
    Uid = "native-assembly-simulation-document"

    def __init__(self) -> None:
        self.objects = {}

    def getObject(self, name: str):
        return self.objects.get(name)


def _fixture() -> tuple[_Document, _Object, _Object, AssemblySimulationState]:
    document = _Document()
    assembly = _Object(document, "Assembly", "Assembly::AssemblyObject")
    joint_group = _Object(document, "Joints", "Assembly::JointGroup")
    first = _Object(document, "First", "App::Link")
    second = _Object(document, "Second", "App::Link")
    ground = _Object(document, "Ground", "App::FeaturePython")
    joint = _Object(document, "Hinge", "App::FeaturePython")
    joint.JointType = "Revolute"
    eligible = AssemblySimulationJoint(
        obj=joint,
        joint_type="Revolute",
        supported_motion_types=("angular",),
        record={"object_name": joint.Name},
    )
    state = AssemblySimulationState(
        assembly=assembly,
        joint_group=joint_group,
        components=(first, second),
        grounded_joints=(ground,),
        regular_joints=(joint,),
        eligible_joints=(eligible,),
        solver_state=AssemblySolverState((), "b" * 64),
        simulation_group=None,
        simulations=(),
        simulation_records=(),
        state_sha256="a" * 64,
    )
    return document, assembly, joint, state


def _spec(
    document: _Document, assembly: _Object, joint: _Object
) -> AssemblySimulationCreateSpec:
    return AssemblySimulationCreateSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        label="Hinge cycle",
        time_start_seconds=0.0,
        time_end_seconds=2.0,
        output_time_step_seconds=0.05,
        global_error_tolerance=1.0e-6,
        frames_per_second=30,
        motions=(
            AssemblySimulationMotionSpec(
                joint_ref=NativeObjectRef(document.Uid, joint.Name),
                motion_type="angular",
                formula="  initialValue + pi/2*time  ",
            ),
        ),
        expected_simulation_state_sha256="a" * 64,
        expected_component_count=2,
        expected_grounded_count=1,
        expected_eligible_joint_count=1,
        expected_simulation_count=0,
    )


def _patch_state(monkeypatch, state: AssemblySimulationState) -> None:
    monkeypatch.setattr(simulation_module, "_timeline_active", lambda _obj: True)
    monkeypatch.setattr(
        simulation_module,
        "capture_assembly_simulation_state",
        lambda _assembly: state,
    )


def test_schema_maps_only_simulation_action_to_one_closed_bounded_graph() -> None:
    definition = assembly_structure_capability_definition()
    variant = next(
        value for value in definition.variants if value.operation == "create_simulation"
    )
    schema = definition.provider_schema(("create_simulation",))["parameters"]["oneOf"][
        0
    ]

    assert variant.action_ids == frozenset({"Assembly_CreateSimulation"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert set(schema["required"]) == {
        "motions",
    }
    assert schema["additionalProperties"] is False
    assert schema["properties"]["label"]["default"] == "Simulation"
    motions = schema["properties"]["motions"]
    assert motions["minItems"] == 1
    assert motions["maxItems"] == 256
    variants = motions["items"]["oneOf"]
    assert len(variants) == 4
    assert {variant["properties"]["motion_type"]["const"] for variant in variants} == {
        "angular",
        "linear",
    }
    assert {name for variant in variants for name in variant["required"]} >= {
        "angular_speed_degrees_per_second",
        "linear_speed_mm_per_second",
        "formula",
    }
    assert all(variant["additionalProperties"] is False for variant in variants)


def test_runtime_parser_preserves_motion_order_and_exact_joint_refs() -> None:
    motions = runtime_module._simulation_motions(
        _Document.Uid,
        [
            {
                "joint": {"object_name": "Hinge"},
                "motion_type": "angular",
                "angular_speed_degrees_per_second": 30.0,
            },
            {
                "joint": {"object_name": "Slide"},
                "motion_type": "linear",
                "linear_speed_mm_per_second": 8.0,
            },
            {
                "joint": {"object_name": "Wave"},
                "motion_type": "angular",
                "formula": "initialValue + sin(time)",
            },
        ],
    )

    assert [motion.joint_ref.object_name for motion in motions] == [
        "Hinge",
        "Slide",
        "Wave",
    ]
    assert [motion.motion_type for motion in motions] == [
        "angular",
        "linear",
        "angular",
    ]
    assert [motion.formula for motion in motions] == [
        "initialValue + (30*pi/180)*time",
        "initialValue + 8*time",
        "initialValue + sin(time)",
    ]

    with pytest.raises(NativeAssemblySimulationError, match="exactly"):
        runtime_module._simulation_motions(
            _Document.Uid,
            [
                {
                    "joint": {"object_name": "Hinge"},
                    "motion_type": "angular",
                    "angular_speed_degrees_per_second": 30.0,
                    "extra": True,
                }
            ],
        )
    with pytest.raises(NativeAssemblySimulationError, match="angular or linear"):
        runtime_module._simulation_motions(
            _Document.Uid,
            [
                {
                    "joint": {"object_name": "Hinge"},
                    "motion_type": "rotary",
                    "formula": "initialValue + time",
                }
            ],
        )


def test_preflight_freezes_exact_state_and_normalizes_formulas(monkeypatch) -> None:
    document, assembly, joint, state = _fixture()
    _patch_state(monkeypatch, state)
    selection = {"items": [{"object_name": joint.Name}]}

    prepared = preflight_create_assembly_simulation(
        document,
        _spec(document, assembly, joint),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selection,
    )

    assert prepared.state is state
    assert prepared.motions[0].joint.obj is joint
    assert prepared.motions[0].spec.formula == "initialValue + pi/2*time"
    assert prepared.planned_output_interval_count == 40
    assert prepared.selection_before == selection


def test_preflight_rejects_stale_state_before_any_factory_mutation(monkeypatch) -> None:
    document, assembly, joint, state = _fixture()
    _patch_state(monkeypatch, state)
    calls = []

    with pytest.raises(NativeAssemblySimulationError, match="state changed"):
        create_assembly_simulation(
            document,
            replace(
                _spec(document, assembly, joint),
                expected_simulation_state_sha256="c" * 64,
            ),
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: {"items": []},
            simulation_factory=lambda _assembly: calls.append(True),
        )

    assert calls == []


def test_preflight_rejects_missing_ground_duplicate_motion_and_wrong_type(
    monkeypatch,
) -> None:
    document, assembly, joint, state = _fixture()
    _patch_state(monkeypatch, replace(state, grounded_joints=()))
    base = _spec(document, assembly, joint)
    with pytest.raises(NativeAssemblySimulationError, match="Ground at least one"):
        preflight_create_assembly_simulation(
            document,
            replace(base, expected_grounded_count=0),
            active_reader=lambda _document: assembly,
        )

    _patch_state(monkeypatch, state)
    repeated = replace(base, motions=(base.motions[0], base.motions[0]))
    with pytest.raises(NativeAssemblySimulationError, match="cannot repeat"):
        preflight_create_assembly_simulation(
            document,
            repeated,
            active_reader=lambda _document: assembly,
        )

    wrong_type = replace(
        base,
        motions=(replace(base.motions[0], motion_type="linear"),),
    )
    with pytest.raises(NativeAssemblySimulationError, match="does not support"):
        preflight_create_assembly_simulation(
            document,
            wrong_type,
            active_reader=lambda _document: assembly,
        )


def test_preflight_bounds_output_work_and_rejects_non_printable_formula(
    monkeypatch,
) -> None:
    document, assembly, joint, state = _fixture()
    _patch_state(monkeypatch, state)
    base = _spec(document, assembly, joint)

    with pytest.raises(NativeAssemblySimulationError, match="output intervals"):
        preflight_create_assembly_simulation(
            document,
            replace(base, time_end_seconds=100.0, output_time_step_seconds=0.001),
            active_reader=lambda _document: assembly,
        )
    with pytest.raises(NativeAssemblySimulationError, match="printable line"):
        preflight_create_assembly_simulation(
            document,
            replace(
                base,
                motions=(replace(base.motions[0], formula="time\n+1"),),
            ),
            active_reader=lambda _document: assembly,
        )
