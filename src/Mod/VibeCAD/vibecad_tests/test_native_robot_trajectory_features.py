# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import dataclass

import pytest

from VibeCADNativeActionManifest import _operation_variant
from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot
from VibeCADNativeRobotTrajectory import NativeRobotTrajectoryError
from VibeCADNativeRobotTrajectoryFeatureSpecs import (
    prepare_compound_trajectory_spec,
    prepare_dress_up_trajectory_spec,
    prepare_edge_trajectory_spec,
)
from VibeCADNativeRobotTrajectorySchema import (
    robot_trajectory_capability_definition,
)
from VibeCADNativeRobotTrajectoryState import capture_robot_trajectory_state


_DIGEST = "a" * 64


def _placement() -> dict:
    return {
        "origin_mm": {"x": 1.0, "y": -2.0, "z": 3.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 25.0,
        },
    }


def _edge_values() -> dict:
    return {
        "mode": "create",
        "target": None,
        "source": {"object_name": "RouteWire"},
        "edges": ["Edge1", "Edge3"],
        "segmentation_mm": 2.5,
        "use_rotation": False,
        "expected_trajectory_setup_state_sha256": _DIGEST,
        "expected_target_state_sha256": None,
        "expected_source_state_sha256": "b" * 64,
    }


def _dress_values() -> dict:
    return {
        "mode": "edit",
        "target": {"object_name": "TrajectoryModifier"},
        "source": {"object_name": "Trajectory"},
        "use_speed": True,
        "speed_mm_per_s": 2400.0,
        "use_acceleration": True,
        "acceleration_mm_per_s2": 3600.0,
        "continuity_mode": "continuous",
        "placement": _placement(),
        "placement_mode": "transform",
        "expected_trajectory_setup_state_sha256": _DIGEST,
        "expected_target_state_sha256": "c" * 64,
        "expected_source_state_sha256": "d" * 64,
    }


def _compound_values() -> dict:
    return {
        "mode": "create",
        "target": None,
        "sources": [
            {
                "trajectory": {"object_name": "Trajectory"},
                "expected_state_sha256": "b" * 64,
            },
            {
                "trajectory": {"object_name": "Trajectory001"},
                "expected_state_sha256": "c" * 64,
            },
        ],
        "expected_trajectory_setup_state_sha256": _DIGEST,
        "expected_target_state_sha256": None,
    }


def test_row_11_38_schema_exactly_covers_both_shipped_robot_surfaces() -> None:
    variants = robot_trajectory_capability_definition().variants[-3:]

    assert tuple(value.operation for value in variants) == (
        "edge2_trac",
        "trajectory_dress_up",
        "trajectory_compound",
    )
    assert tuple(value.action_ids for value in variants) == (
        frozenset({"Robot_Edge2Trac"}),
        frozenset({"Robot_TrajectoryDressUp"}),
        frozenset({"Robot_TrajectoryCompound"}),
    )
    assert all(
        value.surface_ids == frozenset({"assemble", "manufacture"})
        and value.transaction_behavior == "document"
        and value.background_required is False
        for value in variants
    )
    assert _operation_variant("Robot_Edge2Trac") == "edge2_trac"
    assert _operation_variant("Robot_TrajectoryDressUp") == "trajectory_dress_up"
    assert _operation_variant("Robot_TrajectoryCompound") == "trajectory_compound"

    serialized = repr(
        robot_trajectory_capability_definition().provider_schema(
            tuple(value.operation for value in variants)
        )
    ).casefold()
    for forbidden in (
        "file_path",
        "directory",
        "runcommand",
        "workbench",
        "selection",
        "preselection",
        "command_id",
    ):
        assert forbidden not in serialized


def test_feature_specs_are_closed_exact_and_mode_consistent() -> None:
    edge = prepare_edge_trajectory_spec("document-uid", _edge_values())
    assert edge.edges == ("Edge1", "Edge3")
    assert edge.target.target_ref is None

    dress = prepare_dress_up_trajectory_spec("document-uid", _dress_values())
    assert dress.target.target_ref.object_name == "TrajectoryModifier"
    assert dress.placement.rotation_axis == (0.0, 0.0, 1.0)
    assert dress.placement_mode == "transform"

    compound = prepare_compound_trajectory_spec("document-uid", _compound_values())
    assert tuple(source.trajectory_ref.object_name for source in compound.sources) == (
        "Trajectory",
        "Trajectory001",
    )

    malformed = _edge_values()
    malformed["command_id"] = "Robot_Edge2Trac"
    with pytest.raises(NativeRobotTrajectoryError, match="incorrect fields"):
        prepare_edge_trajectory_spec("document-uid", malformed)

    malformed = _edge_values()
    malformed["target"] = {"object_name": "EdgeTrajectory"}
    with pytest.raises(NativeRobotTrajectoryError, match="cannot name"):
        prepare_edge_trajectory_spec("document-uid", malformed)

    malformed = _edge_values()
    malformed["edges"] = ["Edge1", "Edge1"]
    with pytest.raises(NativeRobotTrajectoryError, match="unique"):
        prepare_edge_trajectory_spec("document-uid", malformed)

    malformed = _dress_values()
    malformed["placement"] = _placement()
    malformed["placement"]["rotation"]["axis"] = {
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
    }
    with pytest.raises(NativeRobotTrajectoryError, match="nonzero"):
        prepare_dress_up_trajectory_spec("document-uid", malformed)

    malformed = _compound_values()
    malformed["sources"][1]["trajectory"]["object_name"] = "Trajectory"
    with pytest.raises(NativeRobotTrajectoryError, match="unique"):
        prepare_compound_trajectory_spec("document-uid", malformed)


@dataclass
class _Vector:
    x: float
    y: float
    z: float


class _Rotation:
    Q = (0.0, 0.0, 0.0, 1.0)


class _Placement:
    Base = _Vector(0.0, 0.0, 0.0)
    Rotation = _Rotation()


class _TrajectoryValue:
    Waypoints = ()
    Length = 0.0
    Duration = 0.0


class _View:
    Visibility = True
    DisplayMode = "Waypoints"


class _Document:
    Uid = "document-uid"

    def __init__(self) -> None:
        self.Objects = []

    @staticmethod
    def isObjectUsableAtCurrentTimelinePosition(_obj) -> bool:
        return True


class _Object:
    Label = "Object"
    Base = _Placement()
    Suppressed = False
    ViewObject = _View()
    VibeCADTimelineRole = "operation"
    VibeCADTimelineOwner = None
    VibeCADTimelineReplacedInputs = ()

    def __init__(self, document, name: str, type_id: str, object_id: int) -> None:
        self.Document = document
        self.Name = name
        self.TypeId = type_id
        self.ID = object_id
        self.Trajectory = _TrajectoryValue()

    def isDerivedFrom(self, type_id: str) -> bool:
        return bool(
            type_id == "Robot::TrajectoryObject"
            and self.TypeId
            in {
                "Robot::TrajectoryObject",
                "Robot::Edge2TracObject",
                "Robot::TrajectoryDressUpObject",
                "Robot::TrajectoryCompound",
            }
        )

    @staticmethod
    def isValid() -> bool:
        return True


def _record(document, target):
    state = capture_robot_trajectory_state(document)
    return state.records[state.trajectories.index(target)]


def test_feature_state_digest_includes_exact_controls_and_source_order() -> None:
    document = _Document()
    shape = _Object(document, "RouteWire", "Part::Feature", 1)
    first = _Object(document, "Trajectory", "Robot::TrajectoryObject", 2)
    second = _Object(document, "Trajectory001", "Robot::TrajectoryObject", 3)
    edge = _Object(document, "EdgeTrajectory", "Robot::Edge2TracObject", 4)
    edge.Source = (shape, ["Edge1"])
    edge.SegValue = 1.0
    edge.UseRotation = False
    dress = _Object(
        document,
        "TrajectoryModifier",
        "Robot::TrajectoryDressUpObject",
        5,
    )
    dress.Source = first
    dress.Speed = 1000.0
    dress.UseSpeed = False
    dress.Acceleration = 1000.0
    dress.UseAcceleration = False
    dress.ContType = "DontChange"
    dress.PosAdd = _Placement()
    dress.AddType = "DontChange"
    compound = _Object(
        document,
        "TrajectorySequence",
        "Robot::TrajectoryCompound",
        6,
    )
    compound.Source = [first, second]
    document.Objects = [shape, first, second, edge, dress, compound]

    edge_before = _record(document, edge)
    assert edge_before.data["feature"]["kind"] == "edge"
    edge.SegValue = 2.0
    assert _record(document, edge).state_sha256 != edge_before.state_sha256

    dress_before = _record(document, dress)
    assert dress_before.data["feature"]["kind"] == "dress_up"
    dress.UseSpeed = True
    assert _record(document, dress).state_sha256 != dress_before.state_sha256

    compound_before = _record(document, compound)
    assert compound_before.data["feature"]["kind"] == "compound"
    compound.Source = [second, first]
    compound_after = _record(document, compound)
    assert compound_after.state_sha256 != compound_before.state_sha256
    assert [
        source["object_name"] for source in compound_after.data["feature"]["sources"]
    ] == ["Trajectory001", "Trajectory"]


def test_manufacture_snapshot_exposes_frozen_robot_feature_inputs() -> None:
    snapshot = build_manufacture_snapshot(_Document())

    assert snapshot["kind"] == "manufacture"
    assert snapshot["robot_tool_shapes"]["available"] is True
    assert snapshot["robot_trajectories"]["available"] is True
