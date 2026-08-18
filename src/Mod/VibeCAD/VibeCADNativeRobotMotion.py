# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Robot home edits and bounded preview-only trajectory simulation."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeRobotState import (
    NativeRobotStateError,
    RobotSetupState,
    capture_robot_setup_state,
    same_robot_setup_state,
)
from VibeCADNativeRobotTrajectoryState import (
    NativeRobotTrajectoryStateError,
    RobotTrajectoryState,
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


MAX_SIMULATION_SAMPLES = 64
MAX_SIMULATION_TIME_S = 1.0e12


class NativeRobotMotionError(NativeMutationError):
    """An exact Robot home or simulation operation could not be completed."""

    def __init__(self, message: str) -> None:
        super().__init__("NATIVE_ROBOT_MOTION_FAILED", message)


@dataclass(frozen=True, slots=True)
class RobotHomeSpec:
    operation: str
    robot_ref: NativeObjectRef
    expected_setup_state_sha256: str
    expected_robot_state_sha256: str


@dataclass(frozen=True, slots=True)
class RobotSimulationSpec:
    robot_ref: NativeObjectRef
    trajectory_ref: NativeObjectRef
    sample_times_s: tuple[float, ...]
    expected_setup_state_sha256: str
    expected_robot_state_sha256: str
    expected_trajectory_setup_state_sha256: str
    expected_trajectory_state_sha256: str


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any | None
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class _DocumentBoundary:
    objects: tuple[Any, ...]
    selection: Mapping[str, Any]
    timeline: _TimelineState
    undo_count: int


@dataclass(frozen=True, slots=True)
class PreparedRobotHome:
    spec: RobotHomeSpec
    robot: Any
    setup_state: RobotSetupState
    robot_index: int
    boundary: _DocumentBoundary
    expected_home_degrees: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PreparedRobotSimulation:
    spec: RobotSimulationSpec
    robot: Any
    trajectory: Any
    setup_state: RobotSetupState
    robot_index: int
    trajectory_state: RobotTrajectoryState
    trajectory_index: int
    boundary: _DocumentBoundary


def _digest(value: Any, field: str) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeRobotMotionError(f"{field} must be one lowercase SHA-256 digest.")
    return result


def _reference(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeRobotMotionError(f"The Robot {field} target is invalid.")
    name = str(value["object_name"] or "")
    if not name:
        raise NativeRobotMotionError(f"The Robot {field} target is empty.")
    return NativeObjectRef(document_uid, name)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeRobotMotionError(f"Robot {field} must be a finite number.")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= MAX_SIMULATION_TIME_S:
        raise NativeRobotMotionError(f"Robot {field} is outside its supported range.")
    return 0.0 if result == 0.0 else result


def _float32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", float(value)))[0]


def prepare_robot_home_spec(
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> RobotHomeSpec:
    if operation not in {"set_home_pos", "restore_home_pos"}:
        raise NativeRobotMotionError("The requested Robot home operation is invalid.")
    expected = {
        "robot",
        "expected_setup_state_sha256",
        "expected_robot_state_sha256",
    }
    if not isinstance(values, Mapping) or set(values) != expected:
        raise NativeRobotMotionError("Robot home fields are incorrect.")
    return RobotHomeSpec(
        operation,
        _reference(document_uid, values["robot"], "object"),
        _digest(
            values["expected_setup_state_sha256"],
            "expected_setup_state_sha256",
        ),
        _digest(
            values["expected_robot_state_sha256"],
            "expected_robot_state_sha256",
        ),
    )


def prepare_robot_simulation_spec(
    document_uid: str,
    values: Mapping[str, Any],
) -> RobotSimulationSpec:
    expected = {
        "robot",
        "trajectory",
        "sample_times_s",
        "expected_setup_state_sha256",
        "expected_robot_state_sha256",
        "expected_trajectory_setup_state_sha256",
        "expected_trajectory_state_sha256",
    }
    if not isinstance(values, Mapping) or set(values) != expected:
        raise NativeRobotMotionError("Robot simulation fields are incorrect.")
    raw_times = values["sample_times_s"]
    if (
        not isinstance(raw_times, list)
        or not 1 <= len(raw_times) <= MAX_SIMULATION_SAMPLES
    ):
        raise NativeRobotMotionError(
            f"Robot simulation requires 1 to {MAX_SIMULATION_SAMPLES} sample times."
        )
    times = tuple(_number(value, "simulation time") for value in raw_times)
    if any(right <= left for left, right in zip(times, times[1:])):
        raise NativeRobotMotionError(
            "Robot simulation sample times must be strictly increasing."
        )
    canonical_times = tuple(_float32(value) for value in times)
    if any(right <= left for left, right in zip(canonical_times, canonical_times[1:])):
        raise NativeRobotMotionError(
            "Robot simulation times collapse after the shipped float32 conversion."
        )
    return RobotSimulationSpec(
        _reference(document_uid, values["robot"], "object"),
        _reference(document_uid, values["trajectory"], "trajectory"),
        canonical_times,
        _digest(
            values["expected_setup_state_sha256"],
            "expected_setup_state_sha256",
        ),
        _digest(
            values["expected_robot_state_sha256"],
            "expected_robot_state_sha256",
        ),
        _digest(
            values["expected_trajectory_setup_state_sha256"],
            "expected_trajectory_setup_state_sha256",
        ),
        _digest(
            values["expected_trajectory_state_sha256"],
            "expected_trajectory_state_sha256",
        ),
    )


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return _TimelineState(None, (), ())
    if str(getattr(timeline, "TypeId", "") or "") != "App::DocumentTimeline":
        raise NativeRobotMotionError("The active document History is malformed.")
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    if len(operations) != len(visibility):
        raise NativeRobotMotionError("The active document History is malformed.")
    return _TimelineState(timeline, operations, visibility)


def _document_boundary(document: Any) -> _DocumentBoundary:
    if _transaction_open(document):
        raise NativeRobotMotionError(
            "Finish or cancel the open transaction before changing Robot motion."
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeRobotMotionError(
            "Wait for the active document recompute before changing Robot motion."
        )
    return _DocumentBoundary(
        tuple(document.Objects),
        read_current_selection(document),
        _timeline_state(document),
        int(getattr(document, "UndoCount", 0) or 0),
    )


def _require_boundary(
    document: Any,
    boundary: _DocumentBoundary,
    *,
    check_undo: bool = True,
) -> None:
    if (
        tuple(document.Objects) != boundary.objects
        or read_current_selection(document) != boundary.selection
        or _timeline_state(document) != boundary.timeline
        or (
            check_undo
            and int(getattr(document, "UndoCount", 0) or 0) != boundary.undo_count
        )
    ):
        raise NativeRobotMotionError("The document changed during Robot motion.")


def _capture_setup(document: Any) -> RobotSetupState:
    try:
        return capture_robot_setup_state(document)
    except NativeRobotStateError as exc:
        raise NativeRobotMotionError(str(exc)) from exc


def _capture_trajectories(document: Any) -> RobotTrajectoryState:
    try:
        return capture_robot_trajectory_state(document)
    except NativeRobotTrajectoryStateError as exc:
        raise NativeRobotMotionError(str(exc)) from exc


def _exact_record(
    values: tuple[Any, ...],
    records: tuple[Any, ...],
    target: Any,
    expected_digest: str,
    label: str,
) -> int:
    try:
        index = values.index(target)
    except ValueError as exc:
        raise NativeRobotMotionError(
            f"The exact Robot {label} target is absent from current state."
        ) from exc
    if records[index].state_sha256 != expected_digest:
        raise NativeRobotMotionError(
            f"The exact Robot {label} target changed; read current state and retry."
        )
    return index


def preflight_robot_home(
    document: Any,
    spec: RobotHomeSpec,
) -> PreparedRobotHome:
    if not isinstance(spec, RobotHomeSpec):
        raise TypeError("spec must be a RobotHomeSpec")
    boundary = _document_boundary(document)
    robot = resolve_object(
        document,
        spec.robot_ref,
        expected_types=("Robot::RobotObject",),
    )
    setup = _capture_setup(document)
    if setup.state_sha256 != spec.expected_setup_state_sha256:
        raise NativeRobotMotionError(
            "The Robot setup changed; read current Assemble state and retry."
        )
    index = _exact_record(
        setup.robots,
        setup.records,
        robot,
        spec.expected_robot_state_sha256,
        "object",
    )
    record = setup.records[index]
    if spec.operation == "set_home_pos":
        expected_home = tuple(_float32(value) for value in record.data["axes_degrees"])
    else:
        expected_home = tuple(record.data["home_degrees"])
        if len(expected_home) != 6:
            raise NativeRobotMotionError(
                "Set a six-axis home position for the exact Robot first."
            )
    return PreparedRobotHome(
        spec,
        robot,
        setup,
        index,
        boundary,
        expected_home,
    )


def preflight_robot_simulation(
    document: Any,
    spec: RobotSimulationSpec,
) -> PreparedRobotSimulation:
    if not isinstance(spec, RobotSimulationSpec):
        raise TypeError("spec must be a RobotSimulationSpec")
    boundary = _document_boundary(document)
    robot = resolve_object(
        document,
        spec.robot_ref,
        expected_types=("Robot::RobotObject",),
    )
    trajectory = resolve_object(document, spec.trajectory_ref)
    setup = _capture_setup(document)
    trajectories = _capture_trajectories(document)
    if setup.state_sha256 != spec.expected_setup_state_sha256:
        raise NativeRobotMotionError(
            "The Robot setup changed; inspect it and retry."
        )
    if trajectories.state_sha256 != spec.expected_trajectory_setup_state_sha256:
        raise NativeRobotMotionError(
            "The trajectory setup changed; inspect it and retry."
        )
    robot_index = _exact_record(
        setup.robots,
        setup.records,
        robot,
        spec.expected_robot_state_sha256,
        "object",
    )
    trajectory_index = _exact_record(
        trajectories.trajectories,
        trajectories.records,
        trajectory,
        spec.expected_trajectory_state_sha256,
        "trajectory",
    )
    record = trajectories.records[trajectory_index]
    duration = float(record.data["duration_seconds"])
    if len(record.waypoints) < 2 or duration <= 0.0:
        raise NativeRobotMotionError(
            "Robot simulation requires at least two waypoints and positive duration."
        )
    if spec.sample_times_s[-1] > _float32(duration):
        raise NativeRobotMotionError(
            "A Robot simulation sample time exceeds trajectory duration."
        )
    return PreparedRobotSimulation(
        spec,
        robot,
        trajectory,
        setup,
        robot_index,
        trajectories,
        trajectory_index,
        boundary,
    )


def robot_home_is_noop(prepared: PreparedRobotHome) -> bool:
    if not isinstance(prepared, PreparedRobotHome):
        raise TypeError("prepared must be a PreparedRobotHome")
    record = prepared.setup_state.records[prepared.robot_index]
    current = (
        tuple(record.data["home_degrees"])
        if prepared.spec.operation == "set_home_pos"
        else tuple(record.data["axes_degrees"])
    )
    return current == prepared.expected_home_degrees


def mutate_robot_home(
    document: Any,
    *,
    prepared: PreparedRobotHome,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedRobotHome):
        raise TypeError("prepared must be a PreparedRobotHome")
    _require_boundary(document, prepared.boundary, check_undo=False)
    current = _capture_setup(document)
    if not same_robot_setup_state(prepared.setup_state, current):
        raise NativeRobotMotionError("The Robot setup changed before home mutation.")
    if prepared.spec.operation == "set_home_pos":
        prepared.robot.Home = list(prepared.expected_home_degrees)
    else:
        for axis, value in enumerate(prepared.expected_home_degrees, start=1):
            setattr(prepared.robot, f"Axis{axis}", value)
    return NativeMutationDraft(
        value=prepared,
        recompute_targets=(prepared.robot,),
        changed=(object_identity(prepared.robot),),
    )


def _home_result(
    prepared: PreparedRobotHome,
    current: RobotSetupState,
    *,
    changed: bool,
) -> dict[str, Any]:
    before = prepared.setup_state.records[prepared.robot_index]
    after = current.records[prepared.robot_index]
    return {
        "operation": prepared.spec.operation,
        "robot": object_reference(prepared.robot),
        "changed": changed,
        "previous_axes_degrees": list(before.data["axes_degrees"]),
        "axes_degrees": list(after.data["axes_degrees"]),
        "previous_home_degrees": list(before.data["home_degrees"]),
        "home_degrees": list(after.data["home_degrees"]),
        "robot_state_sha256": after.state_sha256,
        "setup_state_sha256": current.state_sha256,
    }


def verify_robot_home(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value
    if not isinstance(prepared, PreparedRobotHome):
        raise TypeError("Robot home mutation returned an invalid draft.")
    _require_boundary(document, prepared.boundary, check_undo=False)
    current = _capture_setup(document)
    if current.robots != prepared.setup_state.robots:
        raise NativeRobotMotionError("Robot home mutation changed Robot identities.")
    for index, (before, after) in enumerate(
        zip(prepared.setup_state.records, current.records, strict=True)
    ):
        before_data = dict(before.data)
        after_data = dict(after.data)
        if index == prepared.robot_index:
            allowed = (
                {"home_degrees"}
                if prepared.spec.operation == "set_home_pos"
                else {"axes_degrees", "tcp"}
            )
            for field in allowed:
                before_data.pop(field, None)
                after_data.pop(field, None)
        if before_data != after_data:
            raise NativeRobotMotionError("Robot home mutation changed unrelated state.")
    record = current.records[prepared.robot_index]
    observed = (
        tuple(record.data["home_degrees"])
        if prepared.spec.operation == "set_home_pos"
        else tuple(record.data["axes_degrees"])
    )
    if observed != prepared.expected_home_degrees:
        raise NativeRobotMotionError(
            "The Robot did not retain the requested home state."
        )
    return _home_result(prepared, current, changed=True)


def verify_robot_home_noop(
    document: Any,
    prepared: PreparedRobotHome,
) -> dict[str, Any]:
    _require_boundary(document, prepared.boundary)
    current = _capture_setup(document)
    if not same_robot_setup_state(prepared.setup_state, current):
        raise NativeRobotMotionError("Robot state changed during verified no-op.")
    return _home_result(prepared, current, changed=False)


def _preview_samples(prepared: PreparedRobotSimulation) -> list[dict[str, Any]]:
    try:
        import Robot

        raw_samples = Robot.previewTrajectorySamples(
            prepared.robot,
            prepared.trajectory,
            list(prepared.spec.sample_times_s),
        )
    except (
        AttributeError,
        ImportError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        raise NativeRobotMotionError(
            f"Robot simulation could not calculate its preview: {exc}"
        ) from exc
    if not isinstance(raw_samples, list) or len(raw_samples) != len(
        prepared.spec.sample_times_s
    ):
        raise NativeRobotMotionError(
            "Robot simulation returned an invalid number of preview samples."
        )

    result = []
    expected_fields = {
        "time_s",
        "axes_degrees",
        "tcp",
        "path_target",
        "velocity_mm_per_s",
    }
    for expected_time, raw in zip(
        prepared.spec.sample_times_s,
        raw_samples,
        strict=True,
    ):
        if not isinstance(raw, Mapping) or set(raw) != expected_fields:
            raise NativeRobotMotionError(
                "Robot simulation returned a malformed preview sample."
            )
        try:
            time_s = float(raw["time_s"])
            raw_axes = raw["axes_degrees"]
            velocity = float(raw["velocity_mm_per_s"])
            axes = [float(value) for value in raw_axes]
            tcp = robot_placement_summary(raw["tcp"], "simulation TCP")
            path_target = robot_placement_summary(
                raw["path_target"],
                "simulation target",
            )
        except (
            NativeRobotTrajectoryStateError,
            OverflowError,
            TypeError,
            ValueError,
        ) as exc:
            raise NativeRobotMotionError(
                "Robot simulation returned a malformed preview sample."
            ) from exc
        if (
            time_s != expected_time
            or not isinstance(raw_axes, list)
            or len(raw_axes) != 6
            or len(axes) != 6
        ):
            raise NativeRobotMotionError(
                "Robot simulation returned a mismatched preview sample."
            )
        if not all(math.isfinite(value) for value in (*axes, velocity)):
            raise NativeRobotMotionError(
                "Robot simulation returned non-finite motion state."
            )
        result.append(
            {
                "time_s": time_s,
                "axes_degrees": axes,
                "tcp": tcp,
                "path_target": path_target,
                "velocity_mm_per_s": velocity,
            }
        )
    return result


def evaluate_robot_simulation(
    document: Any,
    prepared: PreparedRobotSimulation,
) -> dict[str, Any]:
    if not isinstance(prepared, PreparedRobotSimulation):
        raise TypeError("prepared must be a PreparedRobotSimulation")
    _require_boundary(document, prepared.boundary)
    if not same_robot_setup_state(prepared.setup_state, _capture_setup(document)):
        raise NativeRobotMotionError("Robot state changed before simulation.")
    if not same_robot_trajectory_state(
        prepared.trajectory_state,
        _capture_trajectories(document),
    ):
        raise NativeRobotMotionError("Trajectory state changed before simulation.")
    samples = _preview_samples(prepared)
    _require_boundary(document, prepared.boundary)
    current_setup = _capture_setup(document)
    current_trajectories = _capture_trajectories(document)
    if not same_robot_setup_state(prepared.setup_state, current_setup):
        raise NativeRobotMotionError("Robot simulation changed durable Robot state.")
    if not same_robot_trajectory_state(
        prepared.trajectory_state,
        current_trajectories,
    ):
        raise NativeRobotMotionError(
            "Robot simulation changed durable trajectory state."
        )
    record = prepared.trajectory_state.records[prepared.trajectory_index]
    return {
        "operation": "simulate",
        "robot": object_reference(prepared.robot),
        "trajectory": object_reference(prepared.trajectory),
        "changed": False,
        "preview_only": True,
        "duration_s": record.data["duration_seconds"],
        "length_mm": record.data["length_mm"],
        "samples": samples,
        "robot_state_sha256": current_setup.records[prepared.robot_index].state_sha256,
        "setup_state_sha256": current_setup.state_sha256,
        "trajectory_state_sha256": current_trajectories.records[
            prepared.trajectory_index
        ].state_sha256,
        "trajectory_setup_state_sha256": current_trajectories.state_sha256,
    }
