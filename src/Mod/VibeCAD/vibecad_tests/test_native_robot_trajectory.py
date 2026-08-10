# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import dataclass

import pytest

from VibeCADNativeActionManifest import _operation_variant
from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeRobotTrajectory import (
    NativeRobotTrajectoryError,
    prepare_position_waypoint_spec,
    prepare_robot_waypoint_spec,
    prepare_trajectory_create_spec,
)
from VibeCADNativeRobotTrajectorySchema import (
    ROBOT_TRAJECTORY_CAPABILITY_NAME,
    register_robot_trajectory_capability_definition,
    robot_trajectory_capability_definition,
)
from VibeCADNativeRobotTrajectoryState import capture_robot_trajectory_state
from VibeCADNativeRobotTrajectoryState import (
    MAX_WAYPOINTS_PER_TRAJECTORY,
    NativeRobotTrajectoryStateError,
    TrajectoryStateRecord,
    WaypointStateRecord,
)
import VibeCADNativeRobotTrajectoryState as trajectory_state_module


def test_robot_trajectory_schema_covers_each_shipped_row_11_37_action() -> None:
    definition = robot_trajectory_capability_definition()

    assert definition.name == ROBOT_TRAJECTORY_CAPABILITY_NAME
    assert tuple(variant.operation for variant in definition.variants) == (
        "create_trajectory",
        "insert_robot_waypoint",
        "insert_position_waypoint",
    )
    assert tuple(variant.action_ids for variant in definition.variants) == (
        frozenset({"Robot_CreateTrajectory"}),
        frozenset({"Robot_InsertWaypoint"}),
        frozenset({"Robot_InsertWaypointPreselect"}),
    )
    assert all(
        variant.surface_ids == frozenset({"assemble"})
        and variant.transaction_behavior == "document"
        and variant.background_required is False
        for variant in definition.variants
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
