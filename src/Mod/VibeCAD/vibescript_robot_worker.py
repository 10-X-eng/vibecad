# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native worker for production Robot VibeScript programs."""

from __future__ import annotations

from array import array
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_robot_api import RobotDomainAPI


VALIDATION_SCHEMA = "vibecad-vibescript-robot-validation-v1"
SIMULATION_SCHEMA = "vibecad-vibescript-robot-samples-f64le-v1"
_EXPORTS = ("robot", "waypoint", "trajectory", "dressup", "simulate")
_OUTPUT_TYPES = ("robot", "trajectory", "dressup", "simulation")
_OPERATION_OUTPUT = {
    "robot": "robot",
    "waypoint": "waypoint",
    "trajectory": "trajectory",
    "dressup": "dressup",
    "simulate": "simulation",
}
_MAX_DEFINITION_BYTES = 1_000_000
_MAX_SIMULATION_SAMPLES = 100_000
_SAMPLE_WIDTH = 15


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded model repair for every Robot failure stage."""

    stage = str(details.get("stage") or "")
    path = str(details.get("path") or "")
    output = str(details.get("output") or "")
    location = f" at {path}" if path else (f" {output!r}" if output else "")
    if stage == "result_contract":
        return (
            "Return exactly the declared expected_outputs names, types, and order. "
            "Replace only the mismatched result entry and keep every declaration unchanged."
        )
    if stage == "definition_contract":
        return (
            f"Rebuild only the malformed value{location} with api.robot, api.waypoint, "
            "api.trajectory, api.dressup, or api.simulate; never construct or mutate "
            "serialized definitions."
        )
    if stage == "source_validation":
        return (
            "Change only the named api argument, preserving exact returned graph values; "
            "use finite millimetre/degree values, a non-zero [x,y,z,w] quaternion, and "
            "positive path speed, acceleration, and sampling values."
        )
    if stage == "native_capability":
        return (
            "Keep the source unchanged and retry only with a FreeCAD build that exposes "
            "RobotObject.setKinematic and the isolated native Robot runtime."
        )
    if stage == "native_robot":
        return (
            "Correct only the reported six-axis kinematic row, joint state, base, or tool "
            "placement and retry the failed revision."
        )
    if stage == "native_readback":
        return (
            "Keep the declared Robot graph intact and correct only the reported joint or "
            "placement value that native readback could not reproduce."
        )
    if stage == "native_trajectory":
        return (
            "Correct only the reported waypoint sequence: keep at least two distinct poses, "
            "use positive incoming-segment speed/acceleration, and avoid an invalid native "
            "continuous blend."
        )
    if stage == "native_dressup":
        return (
            "Reuse the exact returned trajectory and correct only the reported speed, "
            "acceleration, continuity, or offset mode/value before retrying."
        )
    if stage == "native_simulation":
        return (
            "Inspect first_unreachable_samples and change only the responsible waypoint "
            "pose, trajectory/robot base, tool transform, kinematic limits, or IK seed; "
            "set require_reachable=false only when partial diagnostic evidence is intended."
        )
    if stage == "graph_membership":
        return (
            "Return every trajectory consumed by dressup and every robot/path consumed by "
            "simulate under its own declared stable output name, then reuse that exact value."
        )
    if stage == "output_identity":
        return (
            "Remove only the duplicate output definition or make it a deliberately different "
            "robot/path/result; every declared output must have one unique graph identity."
        )
    if stage == "output_evaluation":
        return (
            "Return the missing robot or trajectory prerequisite before its dressup/simulation "
            "and preserve the declared graph order."
        )
    if stage == "simulation_artifact":
        return (
            "Keep the validated Robot graph unchanged and retry only after the isolated worker "
            "can write and authenticate its bounded float64 simulation artifact."
        )
    return (
        "Correct only the reported Robot source value, graph member, native path, or simulation "
        "setting and retry the failed working revision; do not recreate the program."
    )


class RobotCandidateError(RuntimeError):
    """A model-correctable Robot failure with structured diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.details = dict(details or {})
        if not str(self.details.get("correction") or "").strip():
            changes = self.details.get("required_changes")
            correction = (
                next(
                    (str(item).strip() for item in changes if str(item).strip()),
                    "",
                )
                if isinstance(changes, list)
                else ""
            )
            self.details["correction"] = correction or _default_correction(
                self.details
            )
        super().__init__(message)


def _fail(message: str, *, stage: str, **details: Any) -> RobotCandidateError:
    return RobotCandidateError(message, details={"stage": stage, **details})


def _encoded(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"A Robot definition is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc
    if len(payload) > _MAX_DEFINITION_BYTES:
        raise _fail(
            f"A Robot definition exceeds {_MAX_DEFINITION_BYTES} JSON bytes.",
            stage="definition_contract",
            json_bytes=len(payload),
        )
    return payload


def _definition_key(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _inflate(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_inflate(item) for item in value)
    if not isinstance(value, Mapping):
        return value
    if set(value) == {
        "domain",
        "operation",
        "output_type",
        "arguments",
        "properties",
    }:
        return DomainValue(
            domain=str(value.get("domain") or ""),
            operation=str(value.get("operation") or ""),
            output_type=str(value.get("output_type") or ""),
            arguments=tuple(_inflate(item) for item in list(value.get("arguments") or [])),
            properties={
                str(name): _inflate(item)
                for name, item in dict(value.get("properties") or {}).items()
            },
        )
    return {str(name): _inflate(item) for name, item in value.items()}


def validate_robot_definition(
    value: Any,
    *,
    expected_output_type: str | None = None,
    require_domain_value: bool = True,
    context: str = "definition",
) -> dict[str, Any]:
    """Replay one untrusted definition through the exact provider API."""

    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif not require_domain_value and isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be returned by the active Robot api.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields or payload.get("domain") != "robot":
        raise _fail(
            f"{context} has malformed Robot definition fields.",
            stage="definition_contract",
            path=context,
        )
    operation = str(payload.get("operation") or "")
    output_type = str(payload.get("output_type") or "")
    if operation not in _OPERATION_OUTPUT or output_type != _OPERATION_OUTPUT[operation]:
        raise _fail(
            f"{context} has unsupported operation/type {operation!r}/{output_type!r}.",
            stage="definition_contract",
            path=context,
        )
    if expected_output_type is not None and output_type != expected_output_type:
        raise _fail(
            f"{context} must publish {expected_output_type!r}, not {output_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if not isinstance(arguments, list) or not isinstance(properties, Mapping):
        raise _fail(
            f"{context} arguments/properties must be an array and object.",
            stage="definition_contract",
            path=context,
        )
    properties = dict(properties)
    api = RobotDomainAPI(_EXPORTS, _OUTPUT_TYPES)
    try:
        if operation == "robot":
            required = {
                "kinematics",
                "axis_positions",
                "base",
                "tool",
                "home",
                "label",
            }
            if arguments or set(properties) != required:
                raise ValueError("robot fields are malformed")
            rebuilt = api.robot(
                kinematics=properties["kinematics"],
                axis_positions=properties["axis_positions"],
                base=properties["base"],
                tool=properties["tool"],
                home=properties["home"],
                label=properties["label"],
            )
        elif operation == "waypoint":
            required = {
                "position",
                "motion",
                "name",
                "velocity",
                "acceleration",
                "continuous",
                "tool",
                "base",
            }
            if arguments or set(properties) != required or properties["motion"] != "LIN":
                raise ValueError("waypoint fields are malformed")
            rebuilt = api.waypoint(
                properties["position"],
                name=properties["name"],
                velocity=properties["velocity"],
                acceleration=properties["acceleration"],
                continuous=properties["continuous"],
                tool=properties["tool"],
                base=properties["base"],
            )
        elif operation == "trajectory":
            if len(arguments) != 1 or set(properties) != {"base", "label"}:
                raise ValueError("trajectory fields are malformed")
            if not isinstance(arguments[0], list):
                raise ValueError("trajectory waypoints must be an array")
            waypoints = [
                _inflate(
                    validate_robot_definition(
                        item,
                        expected_output_type="waypoint",
                        require_domain_value=False,
                        context=f"{context}.arguments[0][{index}]",
                    )
                )
                for index, item in enumerate(arguments[0])
            ]
            rebuilt = api.trajectory(
                waypoints,
                base=properties["base"],
                label=properties["label"],
            )
        elif operation == "dressup":
            required = {
                "speed",
                "acceleration",
                "continuous",
                "offset",
                "offset_mode",
                "label",
            }
            if len(arguments) != 1 or set(properties) != required:
                raise ValueError("dressup fields are malformed")
            source = _inflate(
                validate_robot_definition(
                    arguments[0],
                    expected_output_type="trajectory",
                    require_domain_value=False,
                    context=f"{context}.arguments[0]",
                )
            )
            rebuilt = api.dressup(
                source,
                speed=properties["speed"],
                acceleration=properties["acceleration"],
                continuous=properties["continuous"],
                offset=properties["offset"],
                offset_mode=properties["offset_mode"],
                label=properties["label"],
            )
        else:
            required = {
                "sample_period",
                "maximum_samples",
                "require_reachable",
                "label",
            }
            if len(arguments) != 2 or set(properties) != required:
                raise ValueError("simulate fields are malformed")
            robot = _inflate(
                validate_robot_definition(
                    arguments[0],
                    expected_output_type="robot",
                    require_domain_value=False,
                    context=f"{context}.arguments[0]",
                )
            )
            trajectory = _inflate(
                validate_robot_definition(
                    arguments[1],
                    expected_output_type=str(arguments[1].get("output_type") or ""),
                    require_domain_value=False,
                    context=f"{context}.arguments[1]",
                )
            )
            if trajectory.output_type not in {"trajectory", "dressup"}:
                raise ValueError("simulate trajectory must be a trajectory or dressup")
            rebuilt = api.simulate(
                robot,
                trajectory,
                sample_period=properties["sample_period"],
                maximum_samples=properties["maximum_samples"],
                require_reachable=properties["require_reachable"],
                label=properties["label"],
            )
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"{context} is invalid: {exc}",
            stage="definition_contract",
            path=context,
            operation=operation,
        ) from exc
    canonical = rebuilt.to_payload()
    if canonical != payload:
        raise _fail(
            f"{context} is not the canonical api.{operation} representation.",
            stage="definition_contract",
            path=context,
        )
    _encoded(canonical)
    return canonical


def _native_placement(value: Mapping[str, Sequence[float]]) -> Any:
    import FreeCAD as App

    return App.Placement(
        App.Vector(*(float(item) for item in value["position"])),
        App.Rotation(*(float(item) for item in value["rotation"])),
    )


def _placement_payload(value: Any) -> dict[str, list[float]]:
    return {
        "position": [float(item) for item in value.Base],
        "rotation": [float(item) for item in value.Rotation.Q],
    }


def _kinematic_rows(definition: Mapping[str, Any]) -> list[list[float]]:
    return [
        [
            float(axis["a"]),
            float(axis["alpha"]),
            float(axis["d"]),
            float(axis["theta"]),
            float(axis["rotation_direction"]),
            float(axis["maximum_angle"]),
            float(axis["minimum_angle"]),
            float(axis["maximum_velocity"]),
        ]
        for axis in list(definition["properties"]["kinematics"])
    ]


def _native_waypoint(definition: Mapping[str, Any]) -> Any:
    import Robot

    properties = dict(definition["properties"])
    return Robot.Waypoint(
        _native_placement(properties["position"]),
        type="LIN",
        name=str(properties["name"]),
        vel=float(properties["velocity"]),
        cont=bool(properties["continuous"]),
        tool=int(properties["tool"]),
        base=int(properties["base"]),
        acc=float(properties["acceleration"]),
    )


def _waypoint_payload(value: Any) -> dict[str, Any]:
    return {
        "name": str(value.Name),
        "motion": str(value.Type),
        "position": _placement_payload(value.Pos),
        "velocity": float(value.Velocity),
        "acceleration": float(value.Acceleration),
        "continuous": bool(value.Cont),
        "tool": int(value.Tool),
        "base": int(value.Base),
    }


def _placement_matches(
    observed: Mapping[str, Sequence[float]],
    expected: Mapping[str, Sequence[float]],
) -> bool:
    observed_position = [float(item) for item in observed["position"]]
    expected_position = [float(item) for item in expected["position"]]
    if any(
        not math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)
        for left, right in zip(observed_position, expected_position)
    ):
        return False
    observed_rotation = [float(item) for item in observed["rotation"]]
    expected_rotation = [float(item) for item in expected["rotation"]]
    # Unit quaternions q and -q encode the same orientation.
    dot = sum(
        left * right for left, right in zip(observed_rotation, expected_rotation)
    )
    return math.isclose(abs(dot), 1.0, rel_tol=1.0e-10, abs_tol=1.0e-8)


def _waypoint_matches(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return (
        str(observed["motion"]) == str(expected["motion"])
        and _placement_matches(observed["position"], expected["position"])
        and math.isclose(
            float(observed["velocity"]),
            float(expected["velocity"]),
            rel_tol=1.0e-7,
            abs_tol=1.0e-6,
        )
        and math.isclose(
            float(observed["acceleration"]),
            float(expected["acceleration"]),
            rel_tol=1.0e-7,
            abs_tol=1.0e-6,
        )
        and bool(observed["continuous"]) is bool(expected["continuous"])
        and int(observed["tool"]) == int(expected["tool"])
        and int(observed["base"]) == int(expected["base"])
    )


def _trajectory_data(obj: Any, *, operation: str) -> dict[str, Any]:
    trajectory = obj.Trajectory
    waypoints = [_waypoint_payload(item) for item in trajectory.Waypoints]
    return {
        "schema": VALIDATION_SCHEMA,
        "operation": operation,
        "native_type": str(obj.TypeId),
        "base": _placement_payload(obj.Base),
        "waypoint_count": len(waypoints),
        "waypoints": waypoints,
        "length": float(trajectory.Length),
        "duration": float(trajectory.Duration),
    }


def _build_robot(document: Any, definition: Mapping[str, Any], index: int) -> dict[str, Any]:
    properties = dict(definition["properties"])
    try:
        obj = document.addObject("Robot::RobotObject", f"RobotCandidate{index:03d}")
    except Exception as exc:
        raise _fail(
            f"Native Robot object creation failed: {exc}",
            stage="native_robot",
            exception_type=type(exc).__name__,
        ) from exc
    rows = _kinematic_rows(definition)
    setter = getattr(obj, "setKinematic", None)
    if not callable(setter):
        raise _fail(
            "This FreeCAD build has no in-memory Robot kinematic setter.",
            stage="native_capability",
            required="RobotObject.setKinematic",
        )
    try:
        setter(rows)
        obj.Base = _native_placement(properties["base"])
        obj.Tool = _native_placement(properties["tool"])
        obj.Home = [float(value) for value in properties["home"]]
        for axis, value in enumerate(properties["axis_positions"], start=1):
            setattr(obj, f"Axis{axis}", float(value))
        native = obj.getRobot()
    except Exception as exc:
        raise _fail(
            f"Native Robot kinematic application failed: {exc}",
            stage="native_robot",
            exception_type=type(exc).__name__,
        ) from exc
    observed_axes = [float(getattr(native, f"Axis{axis}")) for axis in range(1, 7)]
    expected_axes = [float(value) for value in properties["axis_positions"]]
    if any(
        not math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)
        for left, right in zip(observed_axes, expected_axes)
    ):
        raise _fail(
            "Native Robot axis state differs from the declared state.",
            stage="native_readback",
            expected=expected_axes,
            observed=observed_axes,
        )
    return {
        "object": obj,
        "data": {
            "schema": VALIDATION_SCHEMA,
            "operation": "robot",
            "native_type": str(obj.TypeId),
            "kinematics": rows,
            "axis_positions": observed_axes,
            "home": [float(value) for value in obj.Home],
            "base": _placement_payload(obj.Base),
            "tool": _placement_payload(obj.Tool),
            "tcp": _placement_payload(obj.Tcp),
            "native_trace": {
                "python_type": type(obj).__name__,
                "robot_copy_type": type(native).__name__,
                "filesystem_kinematics": False,
            },
        },
    }


def _build_trajectory(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    import Robot

    properties = dict(definition["properties"])
    waypoint_definitions = list(definition["arguments"][0])
    try:
        native_waypoints = [_native_waypoint(item) for item in waypoint_definitions]
        trajectory = Robot.Trajectory(native_waypoints)
        obj = document.addObject(
            "Robot::TrajectoryObject", f"TrajectoryCandidate{index:03d}"
        )
        obj.Base = _native_placement(properties["base"])
        obj.Trajectory = trajectory
    except Exception as exc:
        raise _fail(
            f"Native Robot trajectory generation failed: {exc}",
            stage="native_trajectory",
            exception_type=type(exc).__name__,
        ) from exc
    data = _trajectory_data(obj, operation="trajectory")
    if data["waypoint_count"] != len(waypoint_definitions) or data["duration"] <= 0.0:
        raise _fail(
            "Native Robot trajectory did not produce a non-zero path.",
            stage="native_trajectory",
            waypoint_count=data["waypoint_count"],
            duration=data["duration"],
        )
    if not _placement_matches(data["base"], properties["base"]):
        raise _fail(
            "Native Robot trajectory base differs from the declared placement.",
            stage="native_readback",
            output=f"TrajectoryCandidate{index:03d}",
            property="Base",
        )
    name_changes = []
    for waypoint_index, (observed, raw_definition) in enumerate(
        zip(data["waypoints"], waypoint_definitions)
    ):
        expected = dict(raw_definition["properties"])
        if not _waypoint_matches(observed, expected):
            raise _fail(
                f"Native Robot waypoint {waypoint_index} differs from its declaration.",
                stage="native_readback",
                output=f"TrajectoryCandidate{index:03d}",
                waypoint=waypoint_index,
                expected=expected,
                observed=observed,
            )
        requested_name = str(expected["name"])
        native_name = str(observed["name"])
        observed["requested_name"] = requested_name
        observed["name_changed"] = native_name != requested_name
        if native_name != requested_name:
            name_changes.append(
                {
                    "waypoint": waypoint_index,
                    "requested": requested_name,
                    "native": native_name,
                }
            )
    data["waypoint_names_preserved"] = not name_changes
    data["waypoint_name_changes"] = name_changes
    return {"object": obj, "data": data}


def _build_dressup(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    source: Any,
    source_output: str,
) -> dict[str, Any]:
    properties = dict(definition["properties"])
    speed = properties["speed"]
    acceleration = properties["acceleration"]
    continuous = properties["continuous"]
    mode = str(properties["offset_mode"])
    try:
        obj = document.addObject(
            "Robot::TrajectoryDressUpObject", f"DressUpCandidate{index:03d}"
        )
        obj.Source = source
        # FreeCAD's dress-up object does not inherit Source.Base during execute().
        # Preserve the exact path frame explicitly so simulation and publication agree.
        obj.Base = source.Base
        obj.UseSpeed = speed is not None
        if speed is not None:
            obj.Speed = float(speed)
        obj.UseAcceleration = acceleration is not None
        if acceleration is not None:
            obj.Acceleration = float(acceleration)
        obj.ContType = (
            "DontChange"
            if continuous is None
            else ("Continues" if continuous else "Discontinues")
        )
        obj.AddType = {
            "none": "DontChange",
            "use_orientation": "UseOrientation",
            "add_position": "AddPosition",
            "add_orientation": "AddOrintation",
            "add_position_and_orientation": "AddPositionAndOrientation",
        }[mode]
        if properties["offset"] is not None:
            obj.PosAdd = _native_placement(properties["offset"])
        document.recompute()
    except Exception as exc:
        raise _fail(
            f"Native Robot trajectory dress-up failed: {exc}",
            stage="native_dressup",
            source_output=source_output,
            exception_type=type(exc).__name__,
        ) from exc
    if {"Invalid", "Error"} & set(obj.State):
        raise _fail(
            f"Native Robot dress-up failed with state {list(obj.State)!r}.",
            stage="native_dressup",
            source_output=source_output,
        )
    data = _trajectory_data(obj, operation="dressup")
    data.update(
        {
            "source_output": source_output,
            "speed": None if speed is None else float(speed),
            "acceleration": (
                None if acceleration is None else float(acceleration)
            ),
            "continuous": continuous,
            "offset_mode": mode,
            "offset": properties["offset"],
        }
    )
    return {"object": obj, "data": data}


def _sample_times(
    duration: float,
    sample_period: float,
    maximum_samples: int,
) -> tuple[list[float], int]:
    requested = max(2, int(math.ceil(duration / sample_period)) + 1)
    count = min(requested, maximum_samples, _MAX_SIMULATION_SAMPLES)
    return ([duration * index / (count - 1) for index in range(count)], requested)


def _write_simulation(path: Path, rows: Sequence[Sequence[float]]) -> None:
    values = array("d")
    for index, row in enumerate(rows):
        if len(row) != _SAMPLE_WIDTH or any(not math.isfinite(value) for value in row):
            raise _fail(
                f"Simulation sample {index} is malformed.",
                stage="simulation_artifact",
                sample=index,
            )
        values.extend(float(value) for value in row)
    if sys.byteorder != "little":
        values.byteswap()
    try:
        with path.open("wb") as handle:
            values.tofile(handle)
    except OSError as exc:
        raise _fail(
            f"Robot simulation artifact could not be written: {exc}",
            stage="simulation_artifact",
            exception_type=type(exc).__name__,
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _fail(
            f"Robot simulation artifact could not be authenticated: {exc}",
            stage="simulation_artifact",
            exception_type=type(exc).__name__,
        ) from exc
    return digest.hexdigest()


def _simulate(
    definition: Mapping[str, Any],
    robot_record: Mapping[str, Any],
    trajectory_record: Mapping[str, Any],
    *,
    robot_output: str,
    trajectory_output: str,
    root: Path,
    output_index: int,
) -> dict[str, Any]:
    properties = dict(definition["properties"])
    robot_object = robot_record["object"]
    trajectory_object = trajectory_record["object"]
    robot = robot_object.getRobot()
    trajectory = trajectory_object.Trajectory
    duration = float(trajectory.Duration)
    length = float(trajectory.Length)
    if duration <= 0.0 or length <= 0.0:
        raise _fail(
            "Robot simulation requires a non-zero native trajectory.",
            stage="native_simulation",
            duration=duration,
            length=length,
        )
    times, requested_samples = _sample_times(
        duration,
        float(properties["sample_period"]),
        int(properties["maximum_samples"]),
    )
    robot_base_inverse = robot_object.Base.inverse()
    trajectory_base = trajectory_object.Base
    tool_inverse = robot_object.Tool.inverse()
    rows = []
    reachable_axes: list[list[float]] = []
    axes_by_sample: list[list[float] | None] = []
    unreachable_indices = []
    path_targets = []
    world_tool_targets = []
    last_axes = [float(getattr(robot, f"Axis{axis}")) for axis in range(1, 7)]
    for index, sample_time in enumerate(times):
        path_target = trajectory.position(sample_time)
        world_tool_target = trajectory_base * path_target
        target = robot_base_inverse * world_tool_target * tool_inverse
        reachable = True
        try:
            robot.Tcp = target
            last_axes = [
                float(getattr(robot, f"Axis{axis}")) for axis in range(1, 7)
            ]
            reachable_axes.append(list(last_axes))
            axes_by_sample.append(list(last_axes))
        except RuntimeError:
            reachable = False
            unreachable_indices.append(index)
            axes_by_sample.append(None)
        path_targets.append(_placement_payload(path_target))
        world_tool_targets.append(_placement_payload(world_tool_target))
        placement = _placement_payload(target)
        rows.append(
            [
                float(sample_time),
                1.0 if reachable else 0.0,
                *last_axes,
                *placement["position"],
                *placement["rotation"],
            ]
        )
    if properties["require_reachable"] and unreachable_indices:
        raise _fail(
            "The native Robot inverse-kinematics solver could not reach every sample.",
            stage="native_simulation",
            unreachable_count=len(unreachable_indices),
            first_unreachable_samples=unreachable_indices[:64],
        )
    relative = Path("outputs") / f"output-{output_index:03d}-simulation.f64"
    target_path = root / relative
    _write_simulation(target_path, rows)
    expected_bytes = len(rows) * _SAMPLE_WIDTH * 8
    try:
        observed_bytes = target_path.stat().st_size
    except OSError as exc:
        raise _fail(
            f"Robot simulation artifact could not be inspected: {exc}",
            stage="simulation_artifact",
            exception_type=type(exc).__name__,
        ) from exc
    if observed_bytes != expected_bytes:
        raise _fail(
            "Robot simulation artifact has the wrong byte count.",
            stage="simulation_artifact",
            expected_bytes=expected_bytes,
            observed_bytes=observed_bytes,
        )
    if reachable_axes:
        joint_minimum = [min(values[index] for values in reachable_axes) for index in range(6)]
        joint_maximum = [max(values[index] for values in reachable_axes) for index in range(6)]
    else:
        joint_minimum = [0.0] * 6
        joint_maximum = [0.0] * 6
    observed_joint_velocity = [0.0] * 6
    velocity_observation_count = 0
    for left_index, (left, right) in enumerate(
        zip(axes_by_sample, axes_by_sample[1:])
    ):
        if left is None or right is None:
            continue
        delta_time = times[left_index + 1] - times[left_index]
        if delta_time <= 0.0:
            continue
        velocity_observation_count += 1
        for axis in range(6):
            observed_joint_velocity[axis] = max(
                observed_joint_velocity[axis],
                abs(right[axis] - left[axis]) / delta_time,
            )
    kinematics = list(robot_record["data"]["kinematics"])
    joint_velocity_limits = [float(row[7]) for row in kinematics]
    velocity_limit_exceeded = [
        observed > limit + max(1.0e-8, abs(limit) * 1.0e-9)
        for observed, limit in zip(observed_joint_velocity, joint_velocity_limits)
    ]
    joint_limit_margin = []
    for axis in range(6):
        minimum_angle = float(kinematics[axis][6])
        maximum_angle = float(kinematics[axis][5])
        joint_limit_margin.append(
            min(
                min(value[axis] - minimum_angle, maximum_angle - value[axis])
                for value in reachable_axes
            )
            if reachable_axes
            else 0.0
        )
    data = {
        "schema": VALIDATION_SCHEMA,
        "operation": "simulate",
        "robot_output": robot_output,
        "trajectory_output": trajectory_output,
        "duration": duration,
        "length": length,
        "sample_period": float(properties["sample_period"]),
        "requested_sample_count": requested_samples,
        "sample_count": len(rows),
        "samples_limited": len(rows) < requested_samples,
        "reachable_count": len(rows) - len(unreachable_indices),
        "unreachable_count": len(unreachable_indices),
        "first_unreachable_samples": unreachable_indices[:64],
        "unreachable_samples_truncated": len(unreachable_indices) > 64,
        "joint_minimum": joint_minimum,
        "joint_maximum": joint_maximum,
        "joint_limit_margin_minimum": joint_limit_margin,
        "joint_velocity_limit": joint_velocity_limits,
        "sampled_joint_velocity_maximum": observed_joint_velocity,
        "sampled_velocity_observation_count": velocity_observation_count,
        "sampled_velocity_limit_exceeded": velocity_limit_exceeded,
        "start_target": {
            "position": rows[0][8:11],
            "rotation": rows[0][11:15],
        },
        "end_target": {
            "position": rows[-1][8:11],
            "rotation": rows[-1][11:15],
        },
        "start_path_target": path_targets[0],
        "end_path_target": path_targets[-1],
        "start_world_tool_target": world_tool_targets[0],
        "end_world_tool_target": world_tool_targets[-1],
        "coordinate_frames": {
            "path_samples": "trajectory-local tool poses",
            "world_tool_targets": "trajectory.Base * path sample",
            "artifact_targets": (
                "robot.Base.inverse() * world tool target * robot.Tool.inverse(); "
                "the flange pose solved in the robot-base frame"
            ),
            "robot_base": _placement_payload(robot_object.Base),
            "trajectory_base": _placement_payload(trajectory_object.Base),
            "tool": _placement_payload(robot_object.Tool),
        },
        "require_reachable": bool(properties["require_reachable"]),
        "native_trace": {
            "trajectory_engine": "Robot::Trajectory.position",
            "kinematics_engine": "Robot::Robot6Axis.Tcp",
            "simulation_process": "isolated FreeCADCmd",
            "base_and_tool_frames_applied": True,
            "collision_or_dynamics": False,
        },
    }
    return {
        "data": data,
        "artifact_kind": "robot_simulation_f64le",
        "artifact_schema": SIMULATION_SCHEMA,
        "artifact_path": str(relative),
        "artifact_sha256": _sha256_file(target_path),
        "artifact_bytes": expected_bytes,
        "sample_count": len(rows),
        "sample_width": _SAMPLE_WIDTH,
    }


def validate_and_build_robot(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build native Robot objects and run isolated trajectory simulation."""

    expected_names = [str(item["name"]) for item in expected_outputs]
    if list(raw_result) != expected_names:
        raise _fail(
            "Robot result keys must exactly match expected_outputs in declared order.",
            stage="result_contract",
            expected=expected_names,
            received=list(raw_result),
        )
    definitions: dict[str, dict[str, Any]] = {}
    keys: dict[str, str] = {}
    output_by_key: dict[str, tuple[str, str]] = {}
    for expected in expected_outputs:
        name = str(expected["name"])
        definition = validate_robot_definition(
            raw_result[name],
            expected_output_type=str(expected["type"]),
            context=f"result.{name}",
        )
        key = _definition_key(definition)
        if key in output_by_key:
            raise _fail(
                f"Outputs {output_by_key[key][0]!r} and {name!r} return duplicate "
                "Robot definitions.",
                stage="output_identity",
                output=name,
            )
        definitions[name] = definition
        keys[name] = key
        output_by_key[key] = (name, str(expected["type"]))

    records: dict[str, dict[str, Any]] = {}
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        definition = definitions[name]
        if definition["operation"] == "robot":
            records[keys[name]] = _build_robot(document, definition, index)
        elif definition["operation"] == "trajectory":
            records[keys[name]] = _build_trajectory(document, definition, index)

    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        definition = definitions[name]
        if definition["operation"] != "dressup":
            continue
        source_key = _definition_key(definition["arguments"][0])
        source_output = output_by_key.get(source_key)
        source_record = records.get(source_key)
        if (
            source_output is None
            or source_output[1] != "trajectory"
            or source_record is None
        ):
            raise _fail(
                f"Robot dress-up {name!r} must reference a returned trajectory output.",
                stage="graph_membership",
                output=name,
            )
        records[keys[name]] = _build_dressup(
            document,
            definition,
            index,
            source_record["object"],
            source_output[0],
        )

    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        definition = definitions[name]
        if definition["operation"] != "simulate":
            continue
        robot_key = _definition_key(definition["arguments"][0])
        trajectory_key = _definition_key(definition["arguments"][1])
        robot_output = output_by_key.get(robot_key)
        trajectory_output = output_by_key.get(trajectory_key)
        robot_record = records.get(robot_key)
        trajectory_record = records.get(trajectory_key)
        if (
            robot_output is None
            or robot_output[1] != "robot"
            or robot_record is None
        ):
            raise _fail(
                f"Robot simulation {name!r} must reference a returned robot output.",
                stage="graph_membership",
                output=name,
            )
        if (
            trajectory_output is None
            or trajectory_output[1] not in {"trajectory", "dressup"}
            or trajectory_record is None
        ):
            raise _fail(
                f"Robot simulation {name!r} must reference a returned trajectory or dress-up.",
                stage="graph_membership",
                output=name,
            )
        records[keys[name]] = _simulate(
            definition,
            robot_record,
            trajectory_record,
            robot_output=robot_output[0],
            trajectory_output=trajectory_output[0],
            root=root,
            output_index=index,
        )

    outputs = []
    summaries = []
    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        definition = definitions[name]
        record = records.get(keys[name])
        if record is None:
            raise _fail(
                f"Robot output {name!r} was not evaluated.",
                stage="output_evaluation",
                output=name,
            )
        data = dict(record["data"])
        item = {
            "name": name,
            "type": output_type,
            "definition": definition,
            "robot_data": data,
        }
        artifact_sha256 = ""
        if output_type == "simulation":
            for field in (
                "artifact_kind",
                "artifact_schema",
                "artifact_path",
                "artifact_sha256",
                "artifact_bytes",
                "sample_count",
                "sample_width",
            ):
                item[field] = record[field]
            artifact_sha256 = str(record["artifact_sha256"])
        _encoded(data)
        outputs.append(item)
        summaries.append(
            {
                "name": name,
                "type": output_type,
                "operation": str(definition["operation"]),
                "definition_sha256": keys[name],
                "artifact_sha256": artifact_sha256,
                "native_type": str(data.get("native_type") or "App::FeaturePython"),
            }
        )
    validation = {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "outputs": summaries,
    }
    _encoded(validation)
    return outputs, validation
