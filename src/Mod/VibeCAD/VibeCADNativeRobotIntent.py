# SPDX-License-Identifier: LGPL-2.1-or-later

"""Capture Robot preconditions behind small provider requests."""

from __future__ import annotations

from typing import Any, Mapping, Type

from VibeCADNativeRobotDefaultsState import (
    NativeRobotDefaultsStateError,
    capture_robot_waypoint_defaults,
)
from VibeCADNativeRobotMotion import NativeRobotMotionError
from VibeCADNativeRobotSetup import NativeRobotSetupError
from VibeCADNativeRobotState import (
    NativeRobotStateError,
    capture_robot_setup_state,
)
from VibeCADNativeRobotToolState import (
    NativeRobotToolStateError,
    capture_robot_tool_shape_record,
)
from VibeCADNativeRobotTrajectory import NativeRobotTrajectoryError
from VibeCADNativeRobotTrajectoryState import (
    NativeRobotTrajectoryStateError,
    capture_robot_trajectory_state,
)


_RobotError = Type[
    NativeRobotSetupError | NativeRobotTrajectoryError | NativeRobotMotionError
]


def _object_name(value: Any, field: str, error: _RobotError) -> str:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise error(f"{field} must identify one object.")
    name = str(value.get("object_name") or "")
    if not name:
        raise error(f"{field} must identify one object.")
    return name


def _record(
    document: Any,
    value: Any,
    objects: tuple[Any, ...],
    records: tuple[Any, ...],
    field: str,
    error: _RobotError,
) -> Any:
    name = _object_name(value, field, error)
    target = document.getObject(name)
    if target is None:
        raise error(f"{field} does not exist.")
    try:
        index = objects.index(target)
    except ValueError as exc:
        raise error(f"{field} is not available in current Robot state.") from exc
    return records[index]


def _setup(document: Any, error: _RobotError) -> Any:
    try:
        return capture_robot_setup_state(document)
    except NativeRobotStateError as exc:
        raise error(str(exc)) from exc


def _trajectories(document: Any, error: _RobotError) -> Any:
    try:
        return capture_robot_trajectory_state(document)
    except NativeRobotTrajectoryStateError as exc:
        raise error(str(exc)) from exc


def _defaults(error: _RobotError) -> Any:
    try:
        return capture_robot_waypoint_defaults()
    except NativeRobotDefaultsStateError as exc:
        raise error(str(exc)) from exc


def _tool_shape(document: Any, value: Any, error: _RobotError) -> Any:
    name = _object_name(value, "tool_shape", error)
    target = document.getObject(name)
    if target is None:
        raise error("tool_shape does not exist.")
    try:
        return capture_robot_tool_shape_record(target)
    except NativeRobotToolStateError as exc:
        raise error(str(exc)) from exc


def expand_robot_setup_intent(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Add exact current setup state to one schema-validated request."""

    del document_uid
    result = dict(values)
    if operation == "create":
        state = _setup(document, NativeRobotSetupError)
        return {
            **result,
            "expected_state_sha256": state.state_sha256,
            "expected_robot_count": len(state.robots),
        }
    if operation == "add_tool_shape":
        state = _setup(document, NativeRobotSetupError)
        robot = _record(
            document,
            values.get("robot"),
            state.robots,
            state.records,
            "robot",
            NativeRobotSetupError,
        )
        tool = _tool_shape(
            document,
            values.get("tool_shape"),
            NativeRobotSetupError,
        )
        return {
            **result,
            "expected_setup_state_sha256": state.state_sha256,
            "expected_robot_state_sha256": robot.state_sha256,
            "expected_tool_shape_state_sha256": tool.state_sha256,
        }
    if operation in {"set_default_orientation", "set_default_values"}:
        return {
            **result,
            "expected_defaults_state_sha256": _defaults(
                NativeRobotSetupError
            ).state_sha256,
        }
    raise NativeRobotSetupError("The requested Robot setup operation is unavailable.")


def _trajectory_record(document: Any, value: Any, state: Any) -> Any:
    return _record(
        document,
        value,
        state.trajectories,
        state.records,
        "trajectory",
        NativeRobotTrajectoryError,
    )


def expand_robot_trajectory_intent(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Add exact current trajectory state to one schema-validated request."""

    del document_uid
    state = _trajectories(document, NativeRobotTrajectoryError)
    result = dict(values)
    if operation == "create_trajectory":
        return {
            **result,
            "expected_state_sha256": state.state_sha256,
            "expected_trajectory_count": len(state.trajectories),
        }
    if operation in {"insert_robot_waypoint", "insert_position_waypoint"}:
        trajectory = _trajectory_record(document, values.get("trajectory"), state)
        result.update(
            expected_trajectory_setup_state_sha256=state.state_sha256,
            expected_trajectory_state_sha256=trajectory.state_sha256,
            expected_defaults_state_sha256=_defaults(
                NativeRobotTrajectoryError
            ).state_sha256,
        )
        if operation == "insert_robot_waypoint":
            setup = _setup(document, NativeRobotTrajectoryError)
            robot = _record(
                document,
                values.get("robot"),
                setup.robots,
                setup.records,
                "robot",
                NativeRobotTrajectoryError,
            )
            result.update(
                expected_robot_setup_state_sha256=setup.state_sha256,
                expected_robot_state_sha256=robot.state_sha256,
            )
        return result
    if operation not in {
        "edge2_trac",
        "trajectory_dress_up",
        "trajectory_compound",
    }:
        raise NativeRobotTrajectoryError(
            "The requested Robot trajectory operation is unavailable."
        )

    mode = str(values.get("mode") or "")
    target = values.get("target")
    if mode == "create":
        result["target"] = None
        target_digest = None
    elif mode == "edit":
        target_digest = _trajectory_record(document, target, state).state_sha256
    else:
        raise NativeRobotTrajectoryError("mode must be create or edit.")
    result.update(
        expected_trajectory_setup_state_sha256=state.state_sha256,
        expected_target_state_sha256=target_digest,
    )
    if operation == "edge2_trac":
        source = _tool_shape(
            document,
            values.get("source"),
            NativeRobotTrajectoryError,
        )
        result["expected_source_state_sha256"] = source.state_sha256
    elif operation == "trajectory_dress_up":
        source = _trajectory_record(document, values.get("source"), state)
        result["expected_source_state_sha256"] = source.state_sha256
    else:
        sources = values.get("sources")
        if not isinstance(sources, list):
            raise NativeRobotTrajectoryError("sources must be a list.")
        result["sources"] = [
            {
                **dict(source),
                "expected_state_sha256": _trajectory_record(
                    document,
                    source.get("trajectory") if isinstance(source, Mapping) else None,
                    state,
                ).state_sha256,
            }
            for source in sources
        ]
    return result


def expand_robot_motion_intent(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Add exact current motion state to one schema-validated request."""

    del document_uid
    if operation not in {"set_home_pos", "restore_home_pos", "simulate"}:
        raise NativeRobotMotionError("The requested Robot motion is unavailable.")
    setup = _setup(document, NativeRobotMotionError)
    robot = _record(
        document,
        values.get("robot"),
        setup.robots,
        setup.records,
        "robot",
        NativeRobotMotionError,
    )
    result = {
        **dict(values),
        "expected_setup_state_sha256": setup.state_sha256,
        "expected_robot_state_sha256": robot.state_sha256,
    }
    if operation == "simulate":
        trajectories = _trajectories(document, NativeRobotMotionError)
        trajectory = _record(
            document,
            values.get("trajectory"),
            trajectories.trajectories,
            trajectories.records,
            "trajectory",
            NativeRobotMotionError,
        )
        result.update(
            expected_trajectory_setup_state_sha256=trajectories.state_sha256,
            expected_trajectory_state_sha256=trajectory.state_sha256,
        )
    return result
