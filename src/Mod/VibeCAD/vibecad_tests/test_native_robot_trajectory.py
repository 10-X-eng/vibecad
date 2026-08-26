# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from VibeCADNativeRobotTrajectoryBindings import (
    register_robot_path_feature_capability_implementations,
)
from VibeCADNativeActionManifest import _capability_family, _operation_variant
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    provider_visible_native_schema,
)
from VibeCADNativeRobotTrajectory import (
    NativeRobotTrajectoryError,
    prepare_position_waypoint_spec,
    prepare_robot_waypoint_spec,
    prepare_trajectory_create_spec,
)
from VibeCADNativeRobotTrajectorySchema import (
    ROBOT_EDGE_PATH_CAPABILITY_NAME,
    ROBOT_PATH_MOTION_CAPABILITY_NAME,
    ROBOT_PATH_SEQUENCE_CAPABILITY_NAME,
    ROBOT_TRAJECTORY_CAPABILITY_NAME,
    robot_edge_path_capability_definition,
    robot_path_motion_capability_definition,
    robot_path_sequence_capability_definition,
    register_robot_path_feature_capability_definitions,
    register_robot_trajectory_capability_definition,
    robot_trajectory_capability_definition,
)
from VibeCADNativeRobotTrajectoryState import capture_robot_trajectory_state
from VibeCADNativeRobotTrajectoryRuntime import _expand_path_motion_request
from VibeCADNativeRobotTrajectoryState import (
    MAX_WAYPOINTS_PER_TRAJECTORY,
    NativeRobotTrajectoryStateError,
    TrajectoryStateRecord,
    WaypointStateRecord,
)
import VibeCADNativeRobotTrajectoryState as trajectory_state_module
import VibeCADNativeRobotTrajectory as trajectory_module


def test_robot_trajectory_schema_covers_each_shipped_row_11_37_action() -> None:
    definition = robot_trajectory_capability_definition()

    assert definition.name == ROBOT_TRAJECTORY_CAPABILITY_NAME
    row_variants = definition.variants[:3]
    assert tuple(variant.operation for variant in row_variants) == (
        "create_trajectory",
        "insert_robot_waypoint",
        "insert_position_waypoint",
    )
    assert tuple(variant.action_ids for variant in row_variants) == (
        frozenset({"Robot_CreateTrajectory"}),
        frozenset({"Robot_InsertWaypoint"}),
        frozenset({"Robot_InsertWaypointPreselect"}),
    )
    assert all(
        variant.surface_ids == frozenset({"assemble"})
        and variant.transaction_behavior == "document"
        and variant.background_required is False
        for variant in row_variants
    )
    assert _operation_variant("Robot_CreateTrajectory") == "create_trajectory"
    assert _operation_variant("Robot_InsertWaypoint") == "insert_robot_waypoint"
    assert (
        _operation_variant("Robot_InsertWaypointPreselect")
        == "insert_position_waypoint"
    )
    serialized = repr(
        definition.provider_schema(
            (
                "create_trajectory",
                "insert_robot_waypoint",
                "insert_position_waypoint",
            )
        )
    ).casefold()
    for forbidden in (
        "file_path",
        "directory",
        "runcommand",
        "workbench",
        "preselection",
        "command_id",
    ):
        assert forbidden not in serialized

    registry = NativeCapabilityRegistry()
    register_robot_trajectory_capability_definition(registry)
    assert registry.definition_names == (ROBOT_TRAJECTORY_CAPABILITY_NAME,)


def test_robot_trajectory_provider_contract_has_exact_natural_branches() -> None:
    definition = robot_trajectory_capability_definition()
    published = provider_visible_native_schema(
        definition.provider_schema(
            tuple(variant.operation for variant in definition.variants[:3])
        )
    )
    assert published["description"] == (
        "Create trajectories and add Robot or position waypoints."
    )
    schema = published["parameters"]
    serialized = repr(schema)

    assert "expected_" not in serialized
    assert "sha256" not in serialized.casefold()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["operation"]
    properties = schema["properties"]
    assert properties["operation"]["enum"] == [
        "create_trajectory",
        "insert_robot_waypoint",
        "insert_position_waypoint",
    ]
    operation_map = properties["operation"]["description"]
    assert "create_trajectory=label" in operation_map
    assert "insert_position_waypoint=trajectory,position_mm" in operation_map
    assert properties["label"]["description"] == "Trajectory name."


def test_derived_robot_paths_have_focused_create_and_edit_tools() -> None:
    edge = robot_edge_path_capability_definition()
    motion = robot_path_motion_capability_definition()
    sequence = robot_path_sequence_capability_definition()

    assert (edge.name, motion.name, sequence.name) == (
        ROBOT_EDGE_PATH_CAPABILITY_NAME,
        "robot.set_path_motion",
        ROBOT_PATH_SEQUENCE_CAPABILITY_NAME,
    )
    assert ROBOT_PATH_MOTION_CAPABILITY_NAME == "robot.set_path_motion"
    assert edge.description == "Create or edit Robot paths from Part edges."
    assert motion.description == (
        "Set speed, acceleration, continuity, or placement on a Robot path."
    )
    assert sequence.description == "Create or edit ordered Robot path sequences."
    assert tuple(variant.operation for variant in edge.variants) == (
        "create_path",
        "edit_path",
    )
    assert tuple(variant.operation for variant in motion.variants) == ("set_motion",)
    assert tuple(variant.operation for variant in sequence.variants) == (
        "create_sequence",
        "edit_sequence",
    )

    edge_schema = edge.provider_schema(("create_path", "edit_path"))["parameters"]
    assert edge_schema["required"] == ["operation", "source", "edges"]
    assert edge_schema["properties"]["operation"]["enum"] == [
        "create_path",
        "edit_path",
    ]
    assert edge_schema["properties"]["segmentation_mm"]["default"] == 0.5
    assert edge_schema["properties"]["use_rotation"]["default"] is False
    assert edge_schema["properties"]["target"]["type"] == "object"
    assert "modifier" not in edge_schema["properties"]
    assert edge_schema["properties"]["target"]["description"] == (
        "Existing edge path to edit."
    )
    assert edge_schema["properties"]["source"]["properties"]["object_name"][
        "description"
    ] == "Exact object_name from context."

    create_edge = edge.variants[0].provider_parameters()
    edit_edge = edge.variants[1].provider_parameters()
    assert "target" not in create_edge["properties"]
    assert "target" in edit_edge["required"]
    assert "segmentation_mm" not in create_edge["required"]
    assert "use_rotation" not in create_edge["required"]

    motion_schema = provider_visible_native_schema(
        motion.provider_schema(("set_motion",))
    )["parameters"]["oneOf"][0]
    assert motion_schema["required"] == ["path"]
    assert "operation" not in motion_schema["properties"]
    assert "source" not in motion_schema["properties"]
    assert "modifier" not in motion_schema["properties"]
    assert motion_schema["properties"]["path"]["description"] == (
        "Base Robot path to modify, or existing motion modifier to update."
    )
    assert "speed_mm_per_s" not in motion_schema["properties"]
    assert "use_speed" not in motion_schema["properties"]
    assert "acceleration_mm_per_s2" not in motion_schema["properties"]
    assert "use_acceleration" not in motion_schema["properties"]
    speed_limit = motion_schema["properties"]["speed_limit_mm_per_s"]
    acceleration_limit = motion_schema["properties"][
        "acceleration_limit_mm_per_s2"
    ]
    assert speed_limit["type"] == "number"
    assert acceleration_limit["type"] == "number"
    assert motion_schema["properties"]["remove_speed_limit"]["enum"] == [True]
    assert motion_schema["properties"]["remove_acceleration_limit"]["enum"] == [
        True
    ]
    assert "default" not in speed_limit
    assert "default" not in acceleration_limit
    assert motion_schema["properties"]["continuity_mode"]["default"] == "unchanged"
    assert motion_schema["properties"]["placement_mode"]["default"] == "unchanged"

    create_sequence = sequence.variants[0].provider_parameters()
    assert create_sequence["required"] == ["operation", "sources"]
    sequence_schema = sequence.provider_schema(
        ("create_sequence", "edit_sequence")
    )["parameters"]
    assert sequence_schema["properties"]["target"]["description"] == (
        "Existing path sequence to edit."
    )


def test_derived_robot_human_actions_publish_the_focused_provider_tools() -> None:
    assert _capability_family("assemble", "Trajectory", "Robot_Edge2Trac") == (
        ROBOT_EDGE_PATH_CAPABILITY_NAME
    )
    assert _capability_family(
        "assemble", "Trajectory", "Robot_TrajectoryDressUp"
    ) == ROBOT_PATH_MOTION_CAPABILITY_NAME
    assert _capability_family(
        "assemble", "Trajectory", "Robot_TrajectoryCompound"
    ) == ROBOT_PATH_SEQUENCE_CAPABILITY_NAME
    assert _capability_family(
        "assemble", "Trajectory", "Robot_CreateTrajectory"
    ) == ROBOT_TRAJECTORY_CAPABILITY_NAME
    assert _operation_variant("Robot_Edge2Trac") == "create_path"
    assert _operation_variant("Robot_TrajectoryDressUp") == "set_motion"
    assert _operation_variant("Robot_TrajectoryCompound") == "create_sequence"

    registry = NativeCapabilityRegistry()
    register_robot_path_feature_capability_definitions(registry)
    assert registry.definition_names == tuple(
        sorted(
            (
                ROBOT_EDGE_PATH_CAPABILITY_NAME,
                ROBOT_PATH_MOTION_CAPABILITY_NAME,
                ROBOT_PATH_SEQUENCE_CAPABILITY_NAME,
            )
        )
    )

    implementations = NativeCapabilityRegistry()
    register_robot_path_feature_capability_implementations(implementations)
    assert implementations.implementation_names == registry.definition_names


def test_robot_trajectory_specs_require_closed_exact_state_fields() -> None:
    created = prepare_trajectory_create_spec(
        {
            "label": "Inspection route",
            "expected_state_sha256": "a" * 64,
            "expected_trajectory_count": 3,
        }
    )
    assert created.label == "Inspection route"
    assert created.expected_trajectory_count == 3

    robot = prepare_robot_waypoint_spec(
        "document-uid",
        {
            "trajectory": {"object_name": "Trajectory"},
            "robot": {"object_name": "Robot"},
            "expected_trajectory_setup_state_sha256": "b" * 64,
            "expected_trajectory_state_sha256": "c" * 64,
            "expected_robot_setup_state_sha256": "d" * 64,
            "expected_robot_state_sha256": "e" * 64,
            "expected_defaults_state_sha256": "f" * 64,
        },
    )
    assert robot.trajectory_ref.object_name == "Trajectory"
    assert robot.robot_ref.object_name == "Robot"

    position = prepare_position_waypoint_spec(
        "document-uid",
        {
            "trajectory": {"object_name": "Trajectory"},
            "position_mm": {"x": 12.5, "y": -7.0, "z": -0.0},
            "expected_trajectory_setup_state_sha256": "1" * 64,
            "expected_trajectory_state_sha256": "2" * 64,
            "expected_defaults_state_sha256": "3" * 64,
        },
    )
    assert position.position_mm == (12.5, -7.0, 0.0)

    with pytest.raises(NativeRobotTrajectoryError, match="incorrect fields"):
        prepare_trajectory_create_spec(
            {
                "label": "Route",
                "expected_state_sha256": "a" * 64,
                "expected_trajectory_count": 0,
                "command_id": "Robot_CreateTrajectory",
            }
        )


@dataclass
class _Vector:
    x: float
    y: float
    z: float


class _Rotation:
    def __init__(self, quaternion=(0.0, 0.0, 0.0, 1.0)) -> None:
        self.Q = quaternion


class _Placement:
    def __init__(self, position=(0.0, 0.0, 0.0), quaternion=None) -> None:
        self.Base = _Vector(*position)
        self.Rotation = _Rotation(
            quaternion if quaternion is not None else (0.0, 0.0, 0.0, 1.0)
        )


class _Waypoint:
    def __init__(self, name: str, x: float) -> None:
        self.Name = name
        self.Type = "LIN"
        self.Pos = _Placement((x, 2.0, 3.0))
        self.Velocity = 1000.0
        self.Acceleration = 2000.0
        self.Cont = False
        self.Tool = 1
        self.Base = 0


class _TrajectoryValue:
    def __init__(self, waypoints) -> None:
        self.Waypoints = list(waypoints)
        self.Length = 25.0
        self.Duration = 0.5


class _View:
    Visibility = True
    DisplayMode = "Waypoints"


class _TrajectoryObject:
    TypeId = "Robot::TrajectoryObject"
    Label = "Route"
    Base = _Placement()
    Suppressed = False
    ViewObject = _View()
    VibeCADTimelineRole = "operation"
    VibeCADTimelineOwner = None
    VibeCADTimelineReplacedInputs = ()

    def __init__(self, document, waypoints) -> None:
        self.Document = document
        self.Name = "Trajectory"
        self.ID = 42
        self.Trajectory = _TrajectoryValue(waypoints)

    @staticmethod
    def isDerivedFrom(type_id: str) -> bool:
        return type_id == "Robot::TrajectoryObject"

    @staticmethod
    def isValid() -> bool:
        return True


class _Document:
    Uid = "document-uid"

    def __init__(self) -> None:
        self.Objects = []

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)


class _RobotPath:
    def __init__(self, name: str, type_id: str) -> None:
        self.Name = name
        self.TypeId = type_id

    def isDerivedFrom(self, type_id: str) -> bool:
        return type_id == "Robot::TrajectoryObject"


def test_path_motion_request_uses_one_exact_path_and_preserves_an_edit() -> None:
    document = _Document()
    source = _RobotPath("EdgeTrajectory", "Robot::Edge2TracObject")
    modifier = _RobotPath("TrajectoryModifier", "Robot::TrajectoryDressUpObject")
    modifier.Source = source
    modifier.Speed = 1200.0
    modifier.UseSpeed = True
    modifier.Acceleration = 2400.0
    modifier.UseAcceleration = True
    modifier.ContType = "Continues"
    modifier.PosAdd = _Placement((2.0, 3.0, 4.0))
    modifier.AddType = "DontChange"
    document.Objects = [source, modifier]

    edited = _expand_path_motion_request(
        document,
        {
            "path": {"object_name": "TrajectoryModifier"},
            "speed_limit_mm_per_s": 800.0,
        },
    )
    assert edited["mode"] == "edit"
    assert edited["target"] == {"object_name": "TrajectoryModifier"}
    assert edited["source"] == {"object_name": "EdgeTrajectory"}
    assert edited["speed_mm_per_s"] == 800.0
    assert edited["use_speed"] is True
    assert edited["acceleration_mm_per_s2"] == 2400.0
    assert edited["use_acceleration"] is True
    assert edited["continuity_mode"] == "continuous"
    assert edited["placement"]["origin_mm"] == {"x": 2.0, "y": 3.0, "z": 4.0}
    assert edited["placement_mode"] == "unchanged"

    created = _expand_path_motion_request(
        document,
        {"path": {"object_name": "EdgeTrajectory"}},
    )
    assert created["mode"] == "create"
    assert created["target"] is None
    assert created["source"] == {"object_name": "EdgeTrajectory"}
    assert created["speed_mm_per_s"] == 1000.0
    assert created["use_speed"] is False
    assert created["continuity_mode"] == "unchanged"

    disabled = _expand_path_motion_request(
        document,
        {
            "path": {"object_name": "TrajectoryModifier"},
            "remove_speed_limit": True,
        },
    )
    assert disabled["use_speed"] is False
    assert disabled["speed_mm_per_s"] == 1200.0


def test_trajectory_state_is_exact_bounded_and_previews_both_ends() -> None:
    document = _Document()
    trajectory = _TrajectoryObject(
        document,
        [_Waypoint(f"P{index}", float(index)) for index in range(6)],
    )
    document.Objects = [trajectory]

    state = capture_robot_trajectory_state(document)
    summary = state.summary()

    assert summary["trajectory_count"] == 1
    assert summary["waypoint_count"] == 6
    record = summary["trajectories"][0]
    assert record["waypoint_count"] == 6
    assert record["waypoints_truncated"] is True
    assert [item["name"] for item in record["waypoints"]] == [
        "P0",
        "P1",
        "P4",
        "P5",
    ]
    before = state.state_sha256
    trajectory.Trajectory.Waypoints[-1].Pos = _Placement((99.0, 2.0, 3.0))
    after = capture_robot_trajectory_state(document)
    assert after.state_sha256 != before


def test_created_trajectory_result_reports_that_trajectory_waypoint_count(
    monkeypatch,
) -> None:
    document = _Document()
    existing = _TrajectoryObject(document, [_Waypoint("P0", 0.0), _Waypoint("P1", 1.0)])
    created = _TrajectoryObject(document, [])
    created.Name = "Trajectory001"
    created.Label = "Current Pose Path"
    created.ID = 43
    timeline = type("_Timeline", (), {"Name": "VibeCADTimeline"})()
    document.Objects = [existing, created, timeline]

    existing_record = SimpleNamespace(state_sha256="a" * 64)
    created_record = SimpleNamespace(
        state_sha256="b" * 64,
        waypoints=(),
        data={"waypoint_count": 0},
    )
    state = SimpleNamespace(
        trajectories=(existing, created),
        records=(existing_record, created_record),
        waypoint_count=2,
        state_sha256="c" * 64,
    )
    prepared = SimpleNamespace(
        spec=SimpleNamespace(label="Current Pose Path"),
        objects_before=(existing,),
        timeline_before=SimpleNamespace(timeline=None),
        state=SimpleNamespace(
            trajectories=(existing,),
            records=(existing_record,),
        ),
        selection_before=(),
    )
    draft = SimpleNamespace(value={"trajectory": created, "prepared": prepared})

    monkeypatch.setattr(trajectory_module, "_capture_trajectories", lambda _doc: state)
    monkeypatch.setattr(
        trajectory_module,
        "_verify_created_timeline",
        lambda _document, _prepared, _trajectory: None,
    )
    monkeypatch.setattr(trajectory_module, "read_current_selection", lambda _doc: ())

    result = trajectory_module.verify_created_trajectory(document, draft)

    assert result["trajectory_count"] == 2
    assert result["waypoint_count"] == 0


def test_total_waypoint_bound_stops_before_scanning_remaining_trajectories(
    monkeypatch,
) -> None:
    document = _Document()
    document.Objects = [_TrajectoryObject(document, ()) for _index in range(100)]
    for index, trajectory in enumerate(document.Objects):
        trajectory.Name = f"Trajectory{index}"
    calls = []
    waypoint = WaypointStateRecord({}, "a" * 64)

    def record(trajectory):
        calls.append(trajectory)
        return TrajectoryStateRecord(
            trajectory,
            {
                "object": {
                    "document_uid": document.Uid,
                    "object_name": trajectory.Name,
                    "type_id": trajectory.TypeId,
                },
                "object_id": trajectory.ID,
            },
            (waypoint,) * MAX_WAYPOINTS_PER_TRAJECTORY,
            "b" * 64,
        )

    monkeypatch.setattr(trajectory_state_module, "_trajectory_record", record)

    with pytest.raises(NativeRobotTrajectoryStateError, match="waypoint Native bound"):
        capture_robot_trajectory_state(document)

    assert len(calls) == 5
