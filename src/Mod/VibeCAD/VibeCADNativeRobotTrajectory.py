# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation and in-place waypoint editing for Robot trajectories."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeRobotDefaultsState import (
    NativeRobotDefaultsStateError,
    RobotWaypointDefaultsState,
    capture_robot_waypoint_defaults,
)
from VibeCADNativeRobotState import (
    NativeRobotStateError,
    RobotSetupState,
    capture_robot_setup_state,
    same_robot_setup_state,
)
from VibeCADNativeRobotTrajectoryState import (
    MAX_TRAJECTORIES,
    NativeRobotTrajectoryStateError,
    RobotTrajectoryState,
    TrajectoryStateRecord,
    capture_robot_trajectory_state,
    robot_placement_summary,
    same_robot_trajectory_state,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


NATIVE_ROBOT_TRAJECTORY_FAILED = "NATIVE_ROBOT_TRAJECTORY_FAILED"
MAX_WAYPOINT_COORDINATE_MM = 1_000_000.0


class NativeRobotTrajectoryError(NativeMutationError):
    """An exact Robot trajectory operation could not complete safely."""

    def __init__(self, message: str) -> None:
        super().__init__(NATIVE_ROBOT_TRAJECTORY_FAILED, message)


@dataclass(frozen=True, slots=True)
class TrajectoryCreateSpec:
    label: str
    expected_state_sha256: str
    expected_trajectory_count: int


@dataclass(frozen=True, slots=True)
class RobotWaypointSpec:
    trajectory_ref: NativeObjectRef
    robot_ref: NativeObjectRef
    expected_trajectory_setup_state_sha256: str
    expected_trajectory_state_sha256: str
    expected_robot_setup_state_sha256: str
    expected_robot_state_sha256: str
    expected_defaults_state_sha256: str


@dataclass(frozen=True, slots=True)
class PositionWaypointSpec:
    trajectory_ref: NativeObjectRef
    position_mm: tuple[float, float, float]
    expected_trajectory_setup_state_sha256: str
    expected_trajectory_state_sha256: str
    expected_defaults_state_sha256: str


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any | None
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class PreparedTrajectoryCreate:
    spec: TrajectoryCreateSpec
    state: RobotTrajectoryState
    objects_before: tuple[Any, ...]
    selection_before: Mapping[str, Any]
    timeline_before: _TimelineState


@dataclass(frozen=True, slots=True)
class PreparedWaypointAppend:
    operation: str
    trajectory: Any
    trajectory_index: int
    trajectory_state: RobotTrajectoryState
    defaults_state: RobotWaypointDefaultsState
    end_placement: Any
    robot: Any | None
    robot_index: int | None
    robot_state: RobotSetupState | None
    objects_before: tuple[Any, ...]
    selection_before: Mapping[str, Any]
    timeline_before: _TimelineState


def _digest(value: Any, field: str) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeRobotTrajectoryError(
            f"{field} must be one lowercase SHA-256 digest."
        )
    return result


def _label(value: Any) -> str:
    if not isinstance(value, str):
        raise NativeRobotTrajectoryError("A trajectory label must be text.")
    result = value.strip()
    if not 1 <= len(result) <= 160 or any(
        ord(character) < 32 or ord(character) == 127 for character in result
    ):
        raise NativeRobotTrajectoryError(
            "A trajectory label must contain 1 through 160 printable characters."
        )
    return result


def _count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TRAJECTORIES:
        raise NativeRobotTrajectoryError(
            "expected_trajectory_count is outside the Native trajectory bound."
        )
    return value


def _reference(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeRobotTrajectoryError(
            f"The exact Robot {field} reference is malformed."
        )
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def _coordinate(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeRobotTrajectoryError(
            f"Waypoint {field} must be one finite coordinate."
        )
    result = float(value)
    if not math.isfinite(result) or abs(result) > MAX_WAYPOINT_COORDINATE_MM:
        raise NativeRobotTrajectoryError(
            f"Waypoint {field} is outside the supported coordinate range."
        )
    return 0.0 if result == 0.0 else result


def prepare_trajectory_create_spec(values: Mapping[str, Any]) -> TrajectoryCreateSpec:
    if not isinstance(values, Mapping) or set(values) != {
        "label",
        "expected_state_sha256",
        "expected_trajectory_count",
    }:
        raise NativeRobotTrajectoryError("Trajectory creation has incorrect fields.")
    return TrajectoryCreateSpec(
        _label(values["label"]),
        _digest(values["expected_state_sha256"], "expected_state_sha256"),
        _count(values["expected_trajectory_count"]),
    )


def prepare_robot_waypoint_spec(
    document_uid: str,
    values: Mapping[str, Any],
) -> RobotWaypointSpec:
    fields = {
        "trajectory",
        "robot",
        "expected_trajectory_setup_state_sha256",
        "expected_trajectory_state_sha256",
        "expected_robot_setup_state_sha256",
        "expected_robot_state_sha256",
        "expected_defaults_state_sha256",
    }
    if not isinstance(values, Mapping) or set(values) != fields:
        raise NativeRobotTrajectoryError(
            "Robot-pose waypoint insertion has incorrect fields."
        )
    return RobotWaypointSpec(
        _reference(document_uid, values["trajectory"], "trajectory"),
        _reference(document_uid, values["robot"], "robot"),
        _digest(
            values["expected_trajectory_setup_state_sha256"],
            "expected_trajectory_setup_state_sha256",
        ),
        _digest(
            values["expected_trajectory_state_sha256"],
            "expected_trajectory_state_sha256",
        ),
        _digest(
            values["expected_robot_setup_state_sha256"],
            "expected_robot_setup_state_sha256",
        ),
        _digest(
            values["expected_robot_state_sha256"],
            "expected_robot_state_sha256",
        ),
        _digest(
            values["expected_defaults_state_sha256"],
            "expected_defaults_state_sha256",
        ),
    )


def prepare_position_waypoint_spec(
    document_uid: str,
    values: Mapping[str, Any],
) -> PositionWaypointSpec:
    fields = {
        "trajectory",
        "position_mm",
        "expected_trajectory_setup_state_sha256",
        "expected_trajectory_state_sha256",
        "expected_defaults_state_sha256",
    }
    if not isinstance(values, Mapping) or set(values) != fields:
        raise NativeRobotTrajectoryError(
            "World-point waypoint insertion has incorrect fields."
        )
    position = values["position_mm"]
    if not isinstance(position, Mapping) or set(position) != {"x", "y", "z"}:
        raise NativeRobotTrajectoryError(
            "Waypoint position_mm must be one exact XYZ point."
        )
    return PositionWaypointSpec(
        _reference(document_uid, values["trajectory"], "trajectory"),
        tuple(_coordinate(position[axis], axis) for axis in ("x", "y", "z")),
        _digest(
            values["expected_trajectory_setup_state_sha256"],
            "expected_trajectory_setup_state_sha256",
        ),
        _digest(
            values["expected_trajectory_state_sha256"],
            "expected_trajectory_state_sha256",
        ),
        _digest(
            values["expected_defaults_state_sha256"],
            "expected_defaults_state_sha256",
        ),
    )


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _require_clean_document(document: Any) -> None:
    if _transaction_open(document):
        raise NativeRobotTrajectoryError(
            "Finish or cancel the open transaction before changing a trajectory."
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeRobotTrajectoryError(
            "Wait for the active document recompute before changing a trajectory."
        )


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return _TimelineState(None, (), ())
    if str(getattr(timeline, "TypeId", "") or "") != "App::DocumentTimeline":
        raise NativeRobotTrajectoryError("The active document History is malformed.")
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    if len(operations) != len(visibility):
        raise NativeRobotTrajectoryError("The active document History is malformed.")
    return _TimelineState(timeline, operations, visibility)


def _capture_trajectories(document: Any) -> RobotTrajectoryState:
    try:
        return capture_robot_trajectory_state(document)
    except NativeRobotTrajectoryStateError as exc:
        raise NativeRobotTrajectoryError(str(exc)) from exc


def _capture_robots(document: Any) -> RobotSetupState:
    try:
        return capture_robot_setup_state(document)
    except NativeRobotStateError as exc:
        raise NativeRobotTrajectoryError(str(exc)) from exc


def _capture_defaults() -> RobotWaypointDefaultsState:
    try:
        return capture_robot_waypoint_defaults()
    except NativeRobotDefaultsStateError as exc:
        raise NativeRobotTrajectoryError(str(exc)) from exc


def preflight_trajectory_create(
    document: Any,
    spec: TrajectoryCreateSpec,
) -> PreparedTrajectoryCreate:
    if not isinstance(spec, TrajectoryCreateSpec):
        raise TypeError("spec must be a TrajectoryCreateSpec")
    _require_clean_document(document)
    state = _capture_trajectories(document)
    if (
        state.state_sha256 != spec.expected_state_sha256
        or len(state.trajectories) != spec.expected_trajectory_count
    ):
        raise NativeRobotTrajectoryError(
            "Trajectory state changed; read current Assemble state and retry."
        )
    return PreparedTrajectoryCreate(
        spec,
        state,
        tuple(document.Objects),
        read_current_selection(document),
        _timeline_state(document),
    )


def _trajectory_target(
    document: Any,
    reference: NativeObjectRef,
    setup_state_sha256: str,
    target_state_sha256: str,
) -> tuple[Any, RobotTrajectoryState, int]:
    trajectory = resolve_object(document, reference)
    state = _capture_trajectories(document)
    if state.state_sha256 != setup_state_sha256:
        raise NativeRobotTrajectoryError(
            "Trajectory state changed; read current Assemble state and retry."
        )
    try:
        index = state.trajectories.index(trajectory)
    except ValueError as exc:
        raise NativeRobotTrajectoryError(
            "The exact trajectory target is absent from current trajectory state."
        ) from exc
    record = state.records[index]
    if record.state_sha256 != target_state_sha256:
        raise NativeRobotTrajectoryError(
            "The exact trajectory changed; read current Assemble state and retry."
        )
    if record.data["suppressed"] or not record.data["valid"]:
        raise NativeRobotTrajectoryError(
            "The exact trajectory target is suppressed or invalid."
        )
    return trajectory, state, index


def preflight_robot_waypoint(
    document: Any,
    spec: RobotWaypointSpec,
) -> PreparedWaypointAppend:
    if not isinstance(spec, RobotWaypointSpec):
        raise TypeError("spec must be a RobotWaypointSpec")
    _require_clean_document(document)
    trajectory, trajectory_state, trajectory_index = _trajectory_target(
        document,
        spec.trajectory_ref,
        spec.expected_trajectory_setup_state_sha256,
        spec.expected_trajectory_state_sha256,
    )
    robot = resolve_object(
        document,
        spec.robot_ref,
        expected_types=("Robot::RobotObject",),
    )
    robot_state = _capture_robots(document)
    if robot_state.state_sha256 != spec.expected_robot_setup_state_sha256:
        raise NativeRobotTrajectoryError(
            "Robot setup state changed; read current Assemble state and retry."
        )
    try:
        robot_index = robot_state.robots.index(robot)
    except ValueError as exc:
        raise NativeRobotTrajectoryError(
            "The exact Robot target is absent from current setup state."
        ) from exc
    robot_record = robot_state.records[robot_index]
    if robot_record.state_sha256 != spec.expected_robot_state_sha256:
        raise NativeRobotTrajectoryError(
            "The exact Robot changed; read current Assemble state and retry."
        )
    if robot_record.data["suppressed"] or not robot_record.data["valid"]:
        raise NativeRobotTrajectoryError("The exact Robot is suppressed or invalid.")
    defaults = _capture_defaults()
    if defaults.state_sha256 != spec.expected_defaults_state_sha256:
        raise NativeRobotTrajectoryError(
            "Robot waypoint defaults changed; read current Assemble state and retry."
        )
    try:
        end_placement = robot.Tcp.multiply(robot.Tool)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotTrajectoryError(
            "The exact Robot returned a malformed TCP/tool pose."
        ) from exc
    robot_placement_summary(end_placement, "Robot waypoint")
    return PreparedWaypointAppend(
        "insert_robot_waypoint",
        trajectory,
        trajectory_index,
        trajectory_state,
        defaults,
        end_placement,
        robot,
        robot_index,
        robot_state,
        tuple(document.Objects),
        read_current_selection(document),
        _timeline_state(document),
    )


def preflight_position_waypoint(
    document: Any,
    spec: PositionWaypointSpec,
) -> PreparedWaypointAppend:
    if not isinstance(spec, PositionWaypointSpec):
        raise TypeError("spec must be a PositionWaypointSpec")
    _require_clean_document(document)
    trajectory, trajectory_state, trajectory_index = _trajectory_target(
        document,
        spec.trajectory_ref,
        spec.expected_trajectory_setup_state_sha256,
        spec.expected_trajectory_state_sha256,
    )
    defaults = _capture_defaults()
    if defaults.state_sha256 != spec.expected_defaults_state_sha256:
        raise NativeRobotTrajectoryError(
            "Robot waypoint defaults changed; read current Assemble state and retry."
        )
    import FreeCAD as App

    orientation = defaults.data["orientation"]
    displacement = orientation["displacement_mm"]
    quaternion = orientation["quaternion_xyzw"]
    end_placement = App.Placement(
        App.Vector(
            *(spec.position_mm[index] + displacement[index] for index in range(3))
        ),
        App.Rotation(*quaternion),
    )
    robot_placement_summary(end_placement, "world-point waypoint")
    return PreparedWaypointAppend(
        "insert_position_waypoint",
        trajectory,
        trajectory_index,
        trajectory_state,
        defaults,
        end_placement,
        None,
        None,
        None,
        tuple(document.Objects),
        read_current_selection(document),
        _timeline_state(document),
    )


def _require_static_boundary(
    document: Any,
    prepared: PreparedWaypointAppend,
) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeRobotTrajectoryError(
            "Document objects changed during waypoint insertion."
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeRobotTrajectoryError(
            "The human selection changed during waypoint insertion."
        )
    if _timeline_state(document) != prepared.timeline_before:
        raise NativeRobotTrajectoryError(
            "Document History changed during waypoint insertion."
        )
    defaults = _capture_defaults()
    if defaults != prepared.defaults_state:
        raise NativeRobotTrajectoryError(
            "Robot waypoint defaults changed during waypoint insertion."
        )
    if prepared.robot_state is not None:
        robots = _capture_robots(document)
        if not same_robot_setup_state(prepared.robot_state, robots):
            raise NativeRobotTrajectoryError(
                "Robot setup state changed during waypoint insertion."
            )


def _require_append_preflight(
    document: Any,
    prepared: PreparedWaypointAppend,
) -> None:
    _require_static_boundary(document, prepared)
    trajectories = _capture_trajectories(document)
    if not same_robot_trajectory_state(prepared.trajectory_state, trajectories):
        raise NativeRobotTrajectoryError(
            "Trajectory state changed before waypoint insertion."
        )


def create_trajectory(
    document: Any,
    *,
    prepared: PreparedTrajectoryCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedTrajectoryCreate):
        raise TypeError("prepared must be a PreparedTrajectoryCreate")
    if (
        tuple(document.Objects) != prepared.objects_before
        or read_current_selection(document) != prepared.selection_before
        or _timeline_state(document) != prepared.timeline_before
        or not same_robot_trajectory_state(
            prepared.state,
            _capture_trajectories(document),
        )
    ):
        raise NativeRobotTrajectoryError(
            "The document changed before trajectory creation."
        )
    import Robot  # noqa: F401 - loads the trajectory document factory

    trajectory = document.addObject("Robot::TrajectoryObject", "Trajectory")
    if trajectory is None or str(getattr(trajectory, "TypeId", "") or "") != (
        "Robot::TrajectoryObject"
    ):
        raise NativeRobotTrajectoryError(
            "The trajectory factory returned the wrong object type."
        )
    trajectory.Label = prepared.spec.label
    document.publishProvisionalTimelineOperationBlock(trajectory, (), ())
    return NativeMutationDraft(
        value={"trajectory": trajectory, "prepared": prepared},
        recompute_targets=(trajectory,),
        created=(object_identity(trajectory),),
    )


def _verify_created_timeline(
    document: Any,
    prepared: PreparedTrajectoryCreate,
    trajectory: Any,
) -> None:
    before = prepared.timeline_before
    after = _timeline_state(document)
    if after.timeline is None or (
        before.timeline is not None and after.timeline is not before.timeline
    ):
        raise NativeRobotTrajectoryError(
            "Trajectory creation changed the History identity."
        )
    if after.operations != (*before.operations, trajectory):
        raise NativeRobotTrajectoryError(
            "The created trajectory is not the exact final History operation."
        )
    if (
        after.visibility[: len(before.visibility)] != before.visibility
        or len(after.visibility) != len(before.visibility) + 1
    ):
        raise NativeRobotTrajectoryError(
            "Trajectory creation changed unrelated History presentation."
        )


def verify_created_trajectory(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    trajectory = draft.value["trajectory"]
    prepared = draft.value["prepared"]
    if (
        document.getObject(str(trajectory.Name)) is not trajectory
        or str(trajectory.TypeId) != "Robot::TrajectoryObject"
        or str(trajectory.Label) != prepared.spec.label
        or not trajectory.isValid()
        or bool(trajectory.Suppressed)
        or str(getattr(trajectory, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(trajectory, "VibeCADTimelineOwner", None) is not None
        or tuple(getattr(trajectory, "VibeCADTimelineReplacedInputs", ()) or ())
    ):
        raise NativeRobotTrajectoryError(
            "The created trajectory failed its exact object postcondition."
        )
    expected_objects = set(prepared.objects_before)
    expected_objects.add(trajectory)
    timeline = document.getObject("VibeCADTimeline")
    if prepared.timeline_before.timeline is None:
        expected_objects.add(timeline)
    if timeline is None or set(document.Objects) != expected_objects:
        raise NativeRobotTrajectoryError(
            "Trajectory creation changed unrelated document objects."
        )
    _verify_created_timeline(document, prepared, trajectory)
    if read_current_selection(document) != prepared.selection_before:
        raise NativeRobotTrajectoryError(
            "Trajectory creation changed the human selection."
        )
    state = _capture_trajectories(document)
    if state.trajectories != (*prepared.state.trajectories, trajectory):
        raise NativeRobotTrajectoryError(
            "Trajectory creation changed existing trajectory identities."
        )
    if tuple(record.state_sha256 for record in state.records[:-1]) != tuple(
        record.state_sha256 for record in prepared.state.records
    ):
        raise NativeRobotTrajectoryError(
            "Trajectory creation changed an existing trajectory."
        )
    record = state.records[-1]
    if record.waypoints or record.data["waypoint_count"] != 0:
        raise NativeRobotTrajectoryError("The created trajectory did not start empty.")
    return {
        "operation": "create_trajectory",
        "trajectory": object_reference(trajectory),
        "label": str(trajectory.Label),
        "trajectory_count": len(state.trajectories),
        "waypoint_count": state.waypoint_count,
        "trajectory_state_sha256": record.state_sha256,
        "trajectory_setup_state_sha256": state.state_sha256,
    }


def append_waypoint(
    document: Any,
    *,
    prepared: PreparedWaypointAppend,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedWaypointAppend):
        raise TypeError("prepared must be a PreparedWaypointAppend")
    _require_append_preflight(document, prepared)
    motion = prepared.defaults_state.data["motion"]
    import Robot

    waypoint = Robot.Waypoint(
        prepared.end_placement,
        type="LIN",
        name="Pt",
        vel=f"{motion['speed_mm_per_s']:.17g} mm/s",
        cont=motion["continuous"],
        acc=f"{motion['acceleration_mm_per_s2']:.17g} mm/s^2",
        tool=1,
    )
    prepared.trajectory.Trajectory = prepared.trajectory.Trajectory.insertWaypoints(
        waypoint
    )
    return NativeMutationDraft(
        value=prepared,
        recompute_targets=(prepared.trajectory,),
        changed=(object_identity(prepared.trajectory),),
    )


def _next_waypoint_name(record: TrajectoryStateRecord) -> str:
    names = tuple(str(item.data["name"]) for item in record.waypoints)
    if "Pt" not in names:
        return "Pt"
    highest = 0
    for name in names:
        suffix = name[2:] if name.startswith("Pt") else ""
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"Pt{highest + 1}"


def _float32(value: float) -> float:
    return struct.unpack("!f", struct.pack("!f", value))[0]


def _verify_target_metadata(
    before: TrajectoryStateRecord,
    after: TrajectoryStateRecord,
) -> None:
    allowed = {
        "waypoint_count",
        "waypoints_state_sha256",
        "length_mm",
        "duration_seconds",
    }
    before_data = {
        key: value for key, value in before.data.items() if key not in allowed
    }
    after_data = {key: value for key, value in after.data.items() if key not in allowed}
    if before_data != after_data:
        raise NativeRobotTrajectoryError(
            "Waypoint insertion changed unrelated trajectory state."
        )


def verify_appended_waypoint(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value
    if not isinstance(prepared, PreparedWaypointAppend):
        raise TypeError("Waypoint mutation returned an invalid draft.")
    _require_static_boundary(document, prepared)
    state = _capture_trajectories(document)
    if state.trajectories != prepared.trajectory_state.trajectories:
        raise NativeRobotTrajectoryError(
            "Waypoint insertion changed trajectory object identities."
        )
    for index, (before, after) in enumerate(
        zip(prepared.trajectory_state.records, state.records, strict=True)
    ):
        if index != prepared.trajectory_index and (
            before.state_sha256 != after.state_sha256
        ):
            raise NativeRobotTrajectoryError(
                "Waypoint insertion changed an unrelated trajectory."
            )
    before = prepared.trajectory_state.records[prepared.trajectory_index]
    after = state.records[prepared.trajectory_index]
    _verify_target_metadata(before, after)
    if len(after.waypoints) != len(before.waypoints) + 1 or tuple(
        record.state_sha256 for record in after.waypoints[:-1]
    ) != tuple(record.state_sha256 for record in before.waypoints):
        raise NativeRobotTrajectoryError(
            "Waypoint insertion did not append exactly one waypoint."
        )
    added = after.waypoints[-1]
    motion = prepared.defaults_state.data["motion"]
    expected = {
        "index": len(before.waypoints),
        "name": _next_waypoint_name(before),
        "type": "LIN",
        "placement": robot_placement_summary(
            prepared.end_placement,
            "expected waypoint",
        ),
        "velocity_mm_per_s": _float32(motion["speed_mm_per_s"]),
        "acceleration_mm_per_s2": _float32(motion["acceleration_mm_per_s2"]),
        "continuous": motion["continuous"],
        "tool": 1,
        "base": 0,
    }
    if dict(added.data) != expected:
        raise NativeRobotTrajectoryError(
            "The appended waypoint does not match the exact requested pose and defaults."
        )
    result = {
        "operation": prepared.operation,
        "trajectory": object_reference(prepared.trajectory),
        "waypoint": added.summary(),
        "waypoint_count": len(after.waypoints),
        "previous_trajectory_state_sha256": before.state_sha256,
        "trajectory_state_sha256": after.state_sha256,
        "trajectory_setup_state_sha256": state.state_sha256,
        "defaults_state_sha256": prepared.defaults_state.state_sha256,
    }
    if prepared.robot is not None:
        result["robot"] = object_reference(prepared.robot)
    return result
