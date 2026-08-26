# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import VibeCADNativeRobotIntent as intent


class _Document:
    def __init__(self, *objects) -> None:
        self._objects = {obj.Name: obj for obj in objects}

    def getObject(self, name: str):
        return self._objects.get(name)


def _record(obj, digest: str):
    return SimpleNamespace(
        data={"object": {"object_name": obj.Name}},
        state_sha256=digest,
    )


def _states(monkeypatch):
    robot = SimpleNamespace(Name="Robot")
    trajectory = SimpleNamespace(Name="Trajectory")
    target = SimpleNamespace(Name="Target")
    edge = SimpleNamespace(Name="Profile")
    setup = SimpleNamespace(
        robots=(robot,),
        records=(_record(robot, "r" * 64),),
        state_sha256="s" * 64,
    )
    trajectories = SimpleNamespace(
        trajectories=(trajectory, target),
        records=(
            _record(trajectory, "t" * 64),
            _record(target, "u" * 64),
        ),
        state_sha256="v" * 64,
    )
    defaults = SimpleNamespace(state_sha256="d" * 64)
    edge_state = SimpleNamespace(state_sha256="e" * 64)
    monkeypatch.setattr(intent, "capture_robot_setup_state", lambda _doc: setup)
    monkeypatch.setattr(
        intent,
        "capture_robot_trajectory_state",
        lambda _doc: trajectories,
    )
    monkeypatch.setattr(
        intent,
        "capture_robot_waypoint_defaults",
        lambda: defaults,
    )
    monkeypatch.setattr(
        intent,
        "capture_robot_tool_shape_record",
        lambda obj: edge_state if obj is edge else SimpleNamespace(state_sha256="x" * 64),
    )
    return _Document(robot, trajectory, target, edge), robot, trajectory, target, edge


def test_robot_setup_intent_captures_internal_preconditions(monkeypatch) -> None:
    document, robot, _trajectory, _target, edge = _states(monkeypatch)

    created = intent.expand_robot_setup_intent(
        document,
        "document-uid",
        "create",
        {"label": "Cell Robot"},
    )
    assert created == {
        "label": "Cell Robot",
        "expected_state_sha256": "s" * 64,
        "expected_robot_count": 1,
    }

    attached = intent.expand_robot_setup_intent(
        document,
        "document-uid",
        "add_tool_shape",
        {
            "robot": {"object_name": robot.Name},
            "tool_shape": {"object_name": edge.Name},
        },
    )
    assert attached["expected_setup_state_sha256"] == "s" * 64
    assert attached["expected_robot_state_sha256"] == "r" * 64
    assert attached["expected_tool_shape_state_sha256"] == "e" * 64


def test_robot_trajectory_intent_resolves_every_named_state(monkeypatch) -> None:
    document, robot, trajectory, target, edge = _states(monkeypatch)

    created = intent.expand_robot_trajectory_intent(
        document,
        "document-uid",
        "create_trajectory",
        {"label": "Pick Path"},
    )
    assert created == {
        "label": "Pick Path",
        "expected_state_sha256": "v" * 64,
        "expected_trajectory_count": 2,
    }

    waypoint = intent.expand_robot_trajectory_intent(
        document,
        "document-uid",
        "insert_robot_waypoint",
        {
            "trajectory": {"object_name": trajectory.Name},
            "robot": {"object_name": robot.Name},
        },
    )
    assert waypoint["expected_trajectory_state_sha256"] == "t" * 64
    assert waypoint["expected_robot_state_sha256"] == "r" * 64
    assert waypoint["expected_defaults_state_sha256"] == "d" * 64

    edge_route = intent.expand_robot_trajectory_intent(
        document,
        "document-uid",
        "edge2_trac",
        {
            "mode": "edit",
            "target": {"object_name": target.Name},
            "source": {"object_name": edge.Name},
            "edges": ["Edge1"],
            "segmentation_mm": 2.0,
            "use_rotation": True,
        },
    )
    assert edge_route["expected_target_state_sha256"] == "u" * 64
    assert edge_route["expected_source_state_sha256"] == "e" * 64

    compound = intent.expand_robot_trajectory_intent(
        document,
        "document-uid",
        "trajectory_compound",
        {
            "mode": "create",
            "sources": [{"trajectory": {"object_name": trajectory.Name}}],
        },
    )
    assert compound["target"] is None
    assert compound["expected_target_state_sha256"] is None
    assert compound["sources"] == [
        {
            "trajectory": {"object_name": trajectory.Name},
            "expected_state_sha256": "t" * 64,
        }
    ]


def test_robot_motion_intent_resolves_robot_and_trajectory(monkeypatch) -> None:
    document, robot, trajectory, _target, _edge = _states(monkeypatch)

    home = intent.expand_robot_motion_intent(
        document,
        "document-uid",
        "set_home_pos",
        {"robot": {"object_name": robot.Name}},
    )
    assert home["expected_setup_state_sha256"] == "s" * 64
    assert home["expected_robot_state_sha256"] == "r" * 64

    simulation = intent.expand_robot_motion_intent(
        document,
        "document-uid",
        "simulate",
        {
            "robot": {"object_name": robot.Name},
            "trajectory": {"object_name": trajectory.Name},
            "sample_times_s": [0.0, 0.5],
        },
    )
    assert simulation["expected_trajectory_setup_state_sha256"] == "v" * 64
    assert simulation["expected_trajectory_state_sha256"] == "t" * 64
