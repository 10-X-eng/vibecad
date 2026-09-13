# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact main-thread input boundary for retained CAM material simulation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureSimulation import (
    FrozenSimulationRun,
    MAX_SIMULATION_COMMANDS,
    freeze_simulation_commands,
    resolve_simulation_operation_target,
    valid_simulation_shape,
)
from VibeCADNativeManufactureState import (
    capture_other_job_states,
    job_state,
    operation_active_state,
    operation_state,
    other_job_states_are_current,
    resolve_job_target,
)


MAX_SIMULATION_OPERATIONS = 64
MAX_SIMULATION_GRID_CELLS = 4_000_000
_MOTION = frozenset({"G0", "G1", "G2", "G3"})
_CANNED_CYCLES = frozenset({"G73", "G81", "G82", "G83"})
_NON_GEOMETRIC = frozenset(
    {
        "F",
        "S",
        "T",
        "G4",
        "G17",
        "G18",
        "G19",
        "G20",
        "G21",
        "G40",
        "G41",
        "G42",
        "G43",
        "G49",
        "G53",
        "G54",
        "G55",
        "G56",
        "G57",
        "G58",
        "G59",
        "G80",
        "G90",
        "G91",
        "G94",
        "G98",
        "G99",
        "M0",
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M7",
        "M8",
        "M9",
        "M30",
    }
)


@dataclass(frozen=True, slots=True)
class SimulationTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class FrozenMachineAxis:
    name: str
    kind: str
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class FrozenNativeSimulation:
    job: Any
    expected_job_state_sha256: str
    other_job_states: tuple[tuple[Any, str], ...]
    job_operations: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    timeline_before: SimulationTimelineState
    quality: int
    accuracy: float
    stock_resolution_mm: float
    tool_resolution_mm: float
    stock_shape: Any
    stock_z_max_mm: float
    protected_model_shapes: tuple[Any, ...]
    machine_name: str
    machine_axes: tuple[FrozenMachineAxis, ...]
    runs: tuple[FrozenSimulationRun, ...]
    source_command_count: int
    executable_command_count: int


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def canonical_simulation_command(name: Any) -> str:
    value = str(name or "").strip().upper()
    if len(value) == 3 and value[0] in {"G", "M"} and value[1] == "0":
        return value[0] + value[2]
    return value


def is_non_geometric_simulation_command(name: str) -> bool:
    return bool(name.startswith("(") or name in _NON_GEOMETRIC)


def is_motion_simulation_command(name: str) -> bool:
    return name in _MOTION


def is_canned_simulation_command(name: str) -> bool:
    return name in _CANNED_CYCLES


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> SimulationTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        _error(
            "Native CAM simulation requires a valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility)
        or len(operations) != len(suppression)
        or not 0 <= position <= len(operations)
    ):
        _error(
            "Native CAM simulation found malformed document History state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return SimulationTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _usable_at_history_position(document: Any, obj: Any, noun: str) -> None:
    usable = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if not callable(usable) or not bool(usable(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_MANUFACTURE_HISTORY_TARGET_INACTIVE",
        )


def _validate_commands(
    operation_name: str,
    commands: tuple[Any, ...],
) -> int:
    executable = 0
    unsupported: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        name = canonical_simulation_command(command.name)
        if is_motion_simulation_command(name):
            executable += 1
        elif is_canned_simulation_command(name):
            executable += 4
        elif not is_non_geometric_simulation_command(name):
            if len(unsupported) < 16:
                unsupported.append({"index": index, "command": name})
    if unsupported:
        _error(
            f"CAM operation {operation_name!r} contains commands unsupported by "
            "retained native simulation.",
            "NATIVE_MANUFACTURE_TOOLPATH_UNSUPPORTED",
            repair={"unsupported_commands": unsupported},
        )
    return executable


def _tool_sweep_parameters(tool: Any) -> tuple[str, tuple[tuple[str, float], ...]]:
    shape_id = str(getattr(tool, "ShapeID", "") or "").strip()
    names = (
        "Diameter",
        "CuttingEdgeHeight",
        "Length",
        "TipAngle",
        "CuttingEdgeAngle",
        "TipDiameter",
    )
    values = []
    properties = set(str(name) for name in getattr(tool, "PropertiesList", ()) or ())
    for name in names:
        if name not in properties:
            continue
        raw = getattr(tool, name)
        value = float(getattr(raw, "Value", raw))
        if not math.isfinite(value):
            _error(
                f"CAM ToolBit {tool.Name!r} has non-finite {name}.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        values.append((name, value))
    return shape_id, tuple(values)


def _published_cycle_time_seconds(operation: Any) -> float | None:
    value = str(getattr(operation, "CycleTime", "") or "").strip()
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (float(part) for part in parts)
    except (TypeError, ValueError):
        return None
    if (
        any(not math.isfinite(part) or part < 0.0 for part in (hours, minutes, seconds))
        or minutes >= 60.0
        or seconds >= 60.0
    ):
        return None
    total = hours * 3600.0 + minutes * 60.0 + seconds
    return round(total, 6)


def _cycle_time_estimate(operation: Any, controller: Any) -> tuple[float | None, bool]:
    published = _published_cycle_time_seconds(operation)
    if published is not None and published > 0.0:
        return published, False
    try:
        horizontal_feed = float(controller.HorizFeed.Value)
        vertical_feed = float(controller.VertFeed.Value)
        horizontal_rapid = float(controller.HorizRapid.Value)
        vertical_rapid = float(controller.VertRapid.Value)
    except (AttributeError, TypeError, ValueError):
        return None, False
    values = (horizontal_feed, vertical_feed, horizontal_rapid, vertical_rapid)
    if any(not math.isfinite(value) or value < 0.0 for value in values):
        return None, False
    if horizontal_feed <= 0.0 or vertical_feed <= 0.0:
        return None, False
    try:
        seconds = float(
            operation.Path.getCycleTime(
                horizontal_feed,
                vertical_feed,
                horizontal_rapid,
                vertical_rapid,
            )
        )
    except Exception:
        return None, False
    if not math.isfinite(seconds) or seconds < 0.0:
        return None, False
    return round(seconds, 6), horizontal_rapid == 0.0 or vertical_rapid == 0.0


def _simulation_resolutions(stock_shape: Any, quality: int) -> tuple[float, float, float]:
    box = stock_shape.BoundBox
    maximum_xy = max(float(box.XLength), float(box.YLength))
    accuracy = 1.1 - 0.1 * quality
    stock_resolution = 0.01 * accuracy * maximum_xy
    tool_resolution = 0.05 * accuracy
    values = (accuracy, stock_resolution, tool_resolution)
    if maximum_xy <= 0.0 or any(not math.isfinite(value) or value <= 0.0 for value in values):
        _error(
            "The exact CAM stock cannot produce a finite positive simulation resolution.",
            "NATIVE_MANUFACTURE_SIMULATION_STOCK_INVALID",
        )
    grid_x = int(math.ceil(float(box.XLength) / stock_resolution)) + 1
    grid_y = int(math.ceil(float(box.YLength) / stock_resolution)) + 1
    grid_cells = grid_x * grid_y
    if grid_cells > MAX_SIMULATION_GRID_CELLS:
        minimum_resolution = math.sqrt(
            float(box.XLength) * float(box.YLength) / MAX_SIMULATION_GRID_CELLS
        )
        _error(
            "The requested native CAM simulation exceeds the bounded height-field grid.",
            "NATIVE_MANUFACTURE_SIMULATION_LIMIT",
            repair={
                "grid_cells": grid_cells,
                "maximum_grid_cells": MAX_SIMULATION_GRID_CELLS,
                "minimum_resolution_mm": round(minimum_resolution, 9),
            },
        )
    return values


def _machine_travel_configuration(job: Any) -> tuple[str, tuple[FrozenMachineAxis, ...]]:
    machine_name = str(getattr(job, "Machine", "") or "").strip()
    if not machine_name or machine_name == "<any>":
        return "", ()
    try:
        from Machine.models.machine import MachineFactory

        machine = MachineFactory.get_machine(machine_name)
    except Exception as exc:
        raise NativeManufactureError(
            f"CAM machine {machine_name!r} could not be loaded for travel verification.",
            error_code="NATIVE_MANUFACTURE_MACHINE_INVALID",
            repair={"machine": machine_name, "reason": str(exc)[:240]},
        ) from exc

    linear_factor = 25.4 if str(machine.configuration_units) == "imperial" else 1.0
    axes = []
    for kind, configured_axes, factor in (
        ("linear", machine.linear_axes, linear_factor),
        ("rotary", machine.rotary_axes, 1.0),
    ):
        for axis_name, axis in sorted(configured_axes.items()):
            minimum = float(axis.min_limit) * factor
            maximum = float(axis.max_limit) * factor
            if (
                not axis_name
                or not math.isfinite(minimum)
                or not math.isfinite(maximum)
                or minimum >= maximum
            ):
                _error(
                    f"CAM machine {machine_name!r} has invalid {axis_name or kind!r} "
                    "travel limits.",
                    "NATIVE_MANUFACTURE_MACHINE_INVALID",
                )
            axes.append(
                FrozenMachineAxis(
                    name=str(axis_name).upper(),
                    kind=kind,
                    minimum=minimum,
                    maximum=maximum,
                )
            )
    if not axes:
        _error(
            f"CAM machine {machine_name!r} has no configured travel axes.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    return machine_name, tuple(axes)


def preflight_native_simulation(
    document: Any,
    *,
    job: Mapping[str, Any],
    operations: list[Mapping[str, Any]],
    quality: int,
) -> FrozenNativeSimulation:
    """Freeze every document-bound input before worker-thread execution."""

    if _transaction_open(document):
        _error(
            "Finish or cancel the open task before creating a CAM simulation result.",
            "NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for document recompute to finish before creating a CAM simulation result.",
            "NATIVE_MANUFACTURE_RECOMPUTE_ACTIVE",
        )
    if type(quality) is not int or not 1 <= quality <= 10:
        _error("Native CAM simulation quality must be an integer from 1 through 10.")
    if not isinstance(operations, list) or not 1 <= len(operations) <= (
        MAX_SIMULATION_OPERATIONS
    ):
        _error("Native CAM simulation requires one through 64 exact operations.")

    exact_job, job_before = resolve_job_target(document, job)
    other_job_states = capture_other_job_states(document, (exact_job,))
    _usable_at_history_position(document, exact_job, "CAM Job")
    group = tuple(getattr(getattr(exact_job, "Operations", None), "Group", ()) or ())
    positions = {id(operation): index for index, operation in enumerate(group)}
    if len(positions) != len(group):
        _error(
            "The exact CAM Job has duplicate operation identities.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )

    operation_targets = [
        resolve_simulation_operation_target(document, value) for value in operations
    ]
    operation_objects = tuple(value[0] for value in operation_targets)
    names = tuple(str(operation.Name) for operation in operation_objects)
    if len(names) != len(set(names)):
        _error("Native CAM simulation operation targets must be distinct.")
    if any(id(operation) not in positions for operation in operation_objects):
        _error(
            "Every native simulation operation must be a direct entry of the exact Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    requested_positions = tuple(positions[id(operation)] for operation in operation_objects)
    if requested_positions != tuple(sorted(requested_positions)):
        _error(
            "Native CAM simulation operations must be supplied in their current Job order.",
            repair={"job_order": [str(operation.Name) for operation in group]},
        )

    try:
        import FreeCAD
        import Mesh  # noqa: F401 - load extension before worker execution
        import Path  # noqa: F401 - load command types before worker execution
        import Path.Dressup.Utils as PathDressup
        import PathScripts.PathUtils  # noqa: F401 - load Qt-bearing module on GUI thread
        import PathSimulator  # noqa: F401 - load native extension on the GUI thread
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM material simulator is unavailable.",
            error_code="NATIVE_MANUFACTURE_SIMULATION_UNAVAILABLE",
        ) from exc

    controllers = tuple(getattr(getattr(exact_job, "Tools", None), "Group", ()) or ())
    runs = []
    source_count = 0
    executable_count = 0
    for operation, state in operation_targets:
        _usable_at_history_position(document, operation, f"CAM operation {operation.Name!r}")
        if not operation_active_state(operation):
            _error(
                f"CAM operation {operation.Name!r} is inactive.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        commands = freeze_simulation_commands(operation)
        source_count += len(commands)
        executable_count += _validate_commands(str(operation.Name), commands)
        if source_count > MAX_SIMULATION_COMMANDS or executable_count > (
            MAX_SIMULATION_COMMANDS
        ):
            _error(
                f"Native CAM simulation is limited to {MAX_SIMULATION_COMMANDS:,} "
                "source or expanded commands.",
                "NATIVE_MANUFACTURE_SIMULATION_LIMIT",
                repair={
                    "source_command_count": source_count,
                    "expanded_command_count": executable_count,
                    "maximum_command_count": MAX_SIMULATION_COMMANDS,
                },
            )
        try:
            controller = PathDressup.toolController(operation)
        except Exception as exc:
            raise NativeManufactureError(
                f"CAM operation {operation.Name!r} has no readable tool controller.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            ) from exc
        if controller not in controllers:
            _error(
                f"CAM operation {operation.Name!r} has no controller owned by the exact Job.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        tool = getattr(controller, "Tool", None)
        tool_shape = getattr(tool, "Shape", None)
        if not valid_simulation_shape(tool_shape):
            _error(
                f"CAM operation {operation.Name!r} has no valid ToolBit shape.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        try:
            tool_number = int(getattr(controller, "ToolNumber", 0) or 0)
            diameter = float(getattr(getattr(tool, "Diameter", None), "Value", math.nan))
        except (TypeError, ValueError):
            tool_number = 0
            diameter = math.nan
        if tool_number < 1 or not math.isfinite(diameter) or diameter <= 0.0:
            _error(
                f"CAM operation {operation.Name!r} has an invalid tool number or diameter.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        tool_shape_id, tool_parameters = _tool_sweep_parameters(tool)
        cycle_time_seconds, cycle_time_rapid_fallback = _cycle_time_estimate(
            operation,
            controller,
        )
        runs.append(
            FrozenSimulationRun(
                operation=operation,
                operation_name=str(operation.Name),
                operation_label=str(operation.Label),
                expected_state_sha256=str(state["state_sha256"]),
                placement=FreeCAD.Placement(operation.Placement),
                commands=commands,
                tool_shape=tool_shape.copy(),
                tool_number=tool_number,
                diameter_mm=diameter,
                tool_shape_id=tool_shape_id,
                tool_parameters=tool_parameters,
                cycle_time_seconds=cycle_time_seconds,
                cycle_time_rapid_fallback=cycle_time_rapid_fallback,
            )
        )

    if executable_count < 1:
        _error(
            "The requested CAM operations contain no executable simulation motion.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    stock = getattr(exact_job, "Stock", None)
    stock_shape = getattr(stock, "Shape", None)
    if not valid_simulation_shape(stock_shape) or not tuple(stock_shape.Solids):
        _error(
            "The exact CAM Job has no valid solid stock shape.",
            "NATIVE_MANUFACTURE_SIMULATION_STOCK_INVALID",
        )
    _usable_at_history_position(document, stock, "CAM stock")
    protected_model_shapes = tuple(
        getattr(model, "Shape", None).copy()
        for model in tuple(getattr(getattr(exact_job, "Model", None), "Group", ()) or ())
        if valid_simulation_shape(getattr(model, "Shape", None))
    )
    if not protected_model_shapes:
        _error(
            "The exact CAM Job has no valid protected model shape.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    machine_name, machine_axes = _machine_travel_configuration(exact_job)
    accuracy, stock_resolution, tool_resolution = _simulation_resolutions(
        stock_shape,
        quality,
    )

    if job_state(exact_job).get("state_sha256") != job_before.get("state_sha256"):
        _error(
            "The exact CAM Job changed while native simulation inputs were frozen.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if not other_job_states_are_current(document, other_job_states):
        _error(
            "Another CAM setup changed while simulation inputs were frozen.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    for run in runs:
        if operation_state(run.operation).get("state_sha256") != (
            run.expected_state_sha256
        ):
            _error(
                f"CAM operation {run.operation_name!r} changed while simulation inputs "
                "were frozen.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
    return FrozenNativeSimulation(
        job=exact_job,
        expected_job_state_sha256=str(job_before["state_sha256"]),
        other_job_states=other_job_states,
        job_operations=group,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_state(document),
        quality=quality,
        accuracy=accuracy,
        stock_resolution_mm=stock_resolution,
        tool_resolution_mm=tool_resolution,
        stock_shape=stock_shape.copy(),
        stock_z_max_mm=float(stock_shape.BoundBox.ZMax),
        protected_model_shapes=protected_model_shapes,
        machine_name=machine_name,
        machine_axes=machine_axes,
        runs=tuple(runs),
        source_command_count=source_count,
        executable_command_count=executable_count,
    )


def validate_native_simulation(
    document: Any,
    frozen: FrozenNativeSimulation,
) -> None:
    """Reject commit when any exact semantic input or History marker changed."""

    if not isinstance(frozen, FrozenNativeSimulation):
        raise TypeError("frozen must be a FrozenNativeSimulation")
    if (
        tuple(document.Objects) != frozen.objects_before
        or frozen.job.Document is not document
        or document.getObject(str(frozen.job.Name)) is not frozen.job
        or tuple(getattr(frozen.job.Operations, "Group", ()) or ())
        != frozen.job_operations
        or job_state(frozen.job).get("state_sha256")
        != frozen.expected_job_state_sha256
        or not other_job_states_are_current(document, frozen.other_job_states)
        or _timeline_state(document) != frozen.timeline_before
    ):
        _error(
            "The exact CAM Job, object graph, or History changed during background simulation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    _usable_at_history_position(document, frozen.job, "CAM Job")
    previous_position = -1
    positions = {
        id(operation): index for index, operation in enumerate(frozen.job_operations)
    }
    for run in frozen.runs:
        position = positions.get(id(run.operation), -1)
        current = operation_state(run.operation)
        if (
            position <= previous_position
            or current.get("state_sha256") != run.expected_state_sha256
            or not operation_active_state(run.operation)
        ):
            _error(
                f"CAM operation {run.operation_name!r} changed during background simulation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
                repair={
                    "object_name": run.operation_name,
                    "current_state_sha256": current.get("state_sha256"),
                },
            )
        _usable_at_history_position(
            document,
            run.operation,
            f"CAM operation {run.operation_name!r}",
        )
        previous_position = position
