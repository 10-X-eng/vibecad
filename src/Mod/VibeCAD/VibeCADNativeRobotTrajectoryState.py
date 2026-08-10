# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded trajectory and waypoint state for Assemble Native tools."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeRobotState import _finite
from VibeCADNativeTargets import object_reference


MAX_TRAJECTORIES = 256
MAX_VISIBLE_TRAJECTORIES = 16
MAX_WAYPOINTS_PER_TRAJECTORY = 4096
MAX_TOTAL_WAYPOINTS = 16_384
MAX_VISIBLE_WAYPOINTS = 4
_WAYPOINT_TYPES = frozenset({"PTP", "LIN", "CIRC", "WAIT", "UNDEF"})


class NativeRobotTrajectoryStateError(RuntimeError):
    """The active document's trajectory state cannot be represented safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ROBOT_TRAJECTORY_STATE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class WaypointStateRecord:
    data: Mapping[str, Any]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        return {**dict(self.data), "state_sha256": self.state_sha256}


@dataclass(frozen=True, slots=True)
class TrajectoryStateRecord:
    trajectory: Any
    data: Mapping[str, Any]
    waypoints: tuple[WaypointStateRecord, ...]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        waypoint_count = len(self.waypoints)
        if waypoint_count <= MAX_VISIBLE_WAYPOINTS:
            visible = self.waypoints
        else:
            edge_count = MAX_VISIBLE_WAYPOINTS // 2
            visible = (*self.waypoints[:edge_count], *self.waypoints[-edge_count:])
        result = {
            **dict(self.data),
            "waypoints": [record.summary() for record in visible],
            "state_sha256": self.state_sha256,
        }
        if waypoint_count > MAX_VISIBLE_WAYPOINTS:
            result["waypoints_truncated"] = True
        return result


@dataclass(frozen=True, slots=True)
class RobotTrajectoryState:
    trajectories: tuple[Any, ...]
    records: tuple[TrajectoryStateRecord, ...]
    waypoint_count: int
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": True,
            "state_sha256": self.state_sha256,
            "trajectory_count": len(self.trajectories),
            "waypoint_count": self.waypoint_count,
            "trajectories": [
                record.summary() for record in self.records[:MAX_VISIBLE_TRAJECTORIES]
            ],
        }
        if len(self.records) > MAX_VISIBLE_TRAJECTORIES:
            result["trajectories_truncated"] = True
        return result


def robot_placement_summary(value: Any, field: str) -> dict[str, list[float]]:
    try:
        position = value.Base
        quaternion = tuple(value.Rotation.Q)
        result = {
            "position_mm": [
                _finite(position.x, field),
                _finite(position.y, field),
                _finite(position.z, field),
            ],
            "quaternion_xyzw": [_finite(component, field) for component in quaternion],
        }
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotTrajectoryStateError(
            f"A trajectory returned malformed {field} placement."
        ) from exc
    if len(result["quaternion_xyzw"]) != 4:
        raise NativeRobotTrajectoryStateError(
            f"A trajectory returned malformed {field} placement."
        )
    return result


def _bounded_integer(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = 65_535,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NativeRobotTrajectoryStateError(
            f"A waypoint returned malformed {field} state."
        )
    return value


def _waypoint_record(waypoint: Any, index: int) -> WaypointStateRecord:
    try:
        name = str(waypoint.Name)
        waypoint_type = str(waypoint.Type)
        continuous = waypoint.Cont
        velocity = waypoint.Velocity
        acceleration = waypoint.Acceleration
        tool = waypoint.Tool
        base = waypoint.Base
        placement = waypoint.Pos
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotTrajectoryStateError(
            "A trajectory returned a malformed waypoint."
        ) from exc
    if not 1 <= len(name) <= 160 or any(
        ord(character) < 32 or ord(character) == 127 for character in name
    ):
        raise NativeRobotTrajectoryStateError(
            "A trajectory returned a malformed waypoint name."
        )
    if waypoint_type not in _WAYPOINT_TYPES:
        raise NativeRobotTrajectoryStateError(
            "A trajectory returned an unsupported waypoint type."
        )
    if type(continuous) is not bool:
        raise NativeRobotTrajectoryStateError(
            "A waypoint returned malformed continuity state."
        )
    data = {
        "index": index,
        "name": name,
        "type": waypoint_type,
        "placement": robot_placement_summary(placement, "waypoint"),
        "velocity_mm_per_s": _finite(velocity, "waypoint velocity"),
        "acceleration_mm_per_s2": _finite(
            acceleration,
            "waypoint acceleration",
        ),
        "continuous": continuous,
        "tool": _bounded_integer(tool, "tool"),
        "base": _bounded_integer(base, "base"),
    }
    encoded = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return WaypointStateRecord(data, hashlib.sha256(encoded).hexdigest())


def _is_trajectory(obj: Any) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    if callable(checker):
        try:
            return bool(checker("Robot::TrajectoryObject"))
        except (ReferenceError, RuntimeError, TypeError):
            return False
    return str(getattr(obj, "TypeId", "") or "") == "Robot::TrajectoryObject"


def _linked_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    document = getattr(value, "Document", None)
    uid = str(getattr(document, "Uid", "") or "")
    name = str(getattr(value, "Name", "") or "")
    if not uid or not name:
        raise NativeRobotTrajectoryStateError(
            "A trajectory History relationship is not durable."
        )
    return {
        "document_uid": uid,
        "object_name": name,
        "object_id": int(getattr(value, "ID", -1)),
        "type_id": str(getattr(value, "TypeId", "") or ""),
    }


def _trajectory_record(trajectory: Any) -> TrajectoryStateRecord:
    document = getattr(trajectory, "Document", None)
    name = str(getattr(trajectory, "Name", "") or "")
    if not _is_trajectory(trajectory) or document is None or not name:
        raise NativeRobotTrajectoryStateError(
            "A trajectory object is not durably attached."
        )
    try:
        value = trajectory.Trajectory
        raw_waypoints = tuple(value.Waypoints)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotTrajectoryStateError(
            "A trajectory object returned malformed waypoint state."
        ) from exc
    if len(raw_waypoints) > MAX_WAYPOINTS_PER_TRAJECTORY:
        raise NativeRobotTrajectoryStateError(
            "A trajectory exceeds the Native waypoint bound."
        )
    waypoints = tuple(
        _waypoint_record(waypoint, index)
        for index, waypoint in enumerate(raw_waypoints)
    )
    waypoint_digest = hashlib.sha256(
        json.dumps(
            [record.summary() for record in waypoints],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    view = getattr(trajectory, "ViewObject", None)
    data: dict[str, Any] = {
        "object": object_reference(trajectory),
        "object_id": int(getattr(trajectory, "ID", -1)),
        "type_id": str(getattr(trajectory, "TypeId", "") or ""),
        "label": str(getattr(trajectory, "Label", "") or "")[:160],
        "base": robot_placement_summary(trajectory.Base, "base"),
        "waypoint_count": len(waypoints),
        "waypoints_state_sha256": waypoint_digest,
        "length_mm": _finite(value.Length, "length"),
        "duration_seconds": _finite(value.Duration, "duration"),
        "suppressed": bool(getattr(trajectory, "Suppressed", False)),
        "valid": bool(trajectory.isValid()),
        "timeline": {
            "role": str(getattr(trajectory, "VibeCADTimelineRole", "") or ""),
            "owner": _linked_object(getattr(trajectory, "VibeCADTimelineOwner", None)),
            "replaced_inputs": [
                _linked_object(item)
                for item in tuple(
                    getattr(
                        trajectory,
                        "VibeCADTimelineReplacedInputs",
                        (),
                    )
                    or ()
                )
            ],
        },
        "presentation": {
            "visible": None if view is None else bool(view.Visibility),
            "display_mode": (
                None
                if view is None or not hasattr(view, "DisplayMode")
                else str(view.DisplayMode)
            ),
        },
    }
    encoded = json.dumps(
        {
            "trajectory": data,
            "waypoints": [record.summary() for record in waypoints],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return TrajectoryStateRecord(
        trajectory,
        data,
        waypoints,
        hashlib.sha256(encoded).hexdigest(),
    )


def capture_robot_trajectory_state(document: Any) -> RobotTrajectoryState:
    """Capture every live native trajectory without exposing unbounded data."""

    trajectories = tuple(
        obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if _is_trajectory(obj)
    )
    if len(trajectories) > MAX_TRAJECTORIES:
        raise NativeRobotTrajectoryStateError(
            f"The active document exceeds the {MAX_TRAJECTORIES}-trajectory Native bound."
        )
    waypoint_count = 0
    record_values = []
    for trajectory in trajectories:
        record = _trajectory_record(trajectory)
        waypoint_count += len(record.waypoints)
        if waypoint_count > MAX_TOTAL_WAYPOINTS:
            raise NativeRobotTrajectoryStateError(
                "The active document exceeds the "
                f"{MAX_TOTAL_WAYPOINTS}-waypoint Native bound."
            )
        record_values.append(record)
    records = tuple(record_values)
    encoded = json.dumps(
        [
            {
                "object": record.data["object"],
                "object_id": record.data["object_id"],
                "state_sha256": record.state_sha256,
            }
            for record in records
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return RobotTrajectoryState(
        trajectories,
        records,
        waypoint_count,
        hashlib.sha256(encoded).hexdigest(),
    )


def same_robot_trajectory_state(
    first: RobotTrajectoryState,
    second: RobotTrajectoryState,
) -> bool:
    return bool(
        isinstance(first, RobotTrajectoryState)
        and isinstance(second, RobotTrajectoryState)
        and first.trajectories == second.trajectories
        and tuple(record.state_sha256 for record in first.records)
        == tuple(record.state_sha256 for record in second.records)
        and first.state_sha256 == second.state_sha256
    )
