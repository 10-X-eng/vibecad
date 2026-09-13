# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached preparation and exact presentation for GL CAM simulation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Callable, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import (
    capture_other_job_states,
    job_state,
    operation_active_state,
    operation_state,
    other_job_states_are_current,
    resolve_operation_target,
    resolve_job_target,
)


MAX_SIMULATION_COMMANDS = 1_000_000
MAX_COMMAND_PARAMETERS = 64
MAX_COMMAND_NAME_CHARS = 4_096
MAX_PARAMETER_NAME_CHARS = 128
MAX_PARAMETER_TEXT_CHARS = 4_096
_TARGET_FIELDS = frozenset({"object_name", "expected_state_sha256"})


@dataclass(frozen=True, slots=True)
class FrozenCommand:
    name: str
    parameters: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class FrozenSimulationRun:
    operation: Any
    operation_name: str
    operation_label: str
    expected_state_sha256: str
    placement: Any
    commands: tuple[FrozenCommand, ...]
    tool_shape: Any
    tool_number: int
    diameter_mm: float
    tool_shape_id: str = ""
    tool_parameters: tuple[tuple[str, float], ...] = ()
    cycle_time_seconds: float | None = None
    cycle_time_rapid_fallback: bool = False


@dataclass(frozen=True, slots=True)
class FrozenGlSimulation:
    job: Any
    expected_job_state_sha256: str
    other_job_states: tuple[tuple[Any, str], ...]
    quality: int
    stock_shape: Any
    base_shapes: tuple[Any, ...]
    runs: tuple[FrozenSimulationRun, ...]
    command_count: int


@dataclass(frozen=True, slots=True)
class PreparedGlSimulation:
    frozen: FrozenGlSimulation
    stock_mesh: bytes
    base_shape: Any | None
    base_mesh: bytes | None
    runs: tuple[Mapping[str, Any], ...]
    program_sha256: str


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _exact_target(value: Any, noun: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != _TARGET_FIELDS:
        _error(
            f"The exact {noun} target must contain object_name and "
            "expected_state_sha256."
        )
    name = str(value["object_name"] or "").strip()
    expected = str(value["expected_state_sha256"] or "").strip()
    if not name or len(expected) != 64:
        _error(f"The exact {noun} target is malformed.")
    return name, expected


def _finite_parameter(value: Any, noun: str) -> Any:
    if isinstance(value, str) and len(value) > MAX_PARAMETER_TEXT_CHARS:
        _error(
            f"{noun} exceeds the simulator's bounded text length.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        _error(
            f"{noun} contains a non-finite number.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    quantity = getattr(value, "Value", None)
    if quantity is not None:
        try:
            number = float(quantity)
        except (TypeError, ValueError):
            number = math.nan
        if math.isfinite(number):
            return number
    _error(
        f"{noun} contains an unsupported parameter value.",
        "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
    )


def _freeze_commands(operation: Any) -> tuple[FrozenCommand, ...]:
    path = getattr(operation, "Path", None)
    commands = tuple(getattr(path, "Commands", ()) or ())
    if not commands:
        _error(
            f"CAM operation {operation.Name!r} has no generated toolpath.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    result = []
    for command_index, command in enumerate(commands):
        name = str(getattr(command, "Name", "") or "").strip()
        if not name:
            _error(
                f"CAM operation {operation.Name!r} has an empty command name at "
                f"index {command_index}.",
                "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            )
        if len(name) > MAX_COMMAND_NAME_CHARS:
            _error(
                f"CAM operation {operation.Name!r} command {command_index} exceeds "
                f"the {MAX_COMMAND_NAME_CHARS}-character command-name limit.",
                "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            )
        raw_value = getattr(command, "Parameters", {}) or {}
        if not isinstance(raw_value, Mapping):
            _error(
                f"CAM operation {operation.Name!r} command {command_index} has "
                "non-object parameters.",
                "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            )
        raw = dict(raw_value)
        if len(raw) > MAX_COMMAND_PARAMETERS:
            _error(
                f"CAM operation {operation.Name!r} command {command_index} exceeds "
                f"the {MAX_COMMAND_PARAMETERS}-parameter limit.",
                "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            )
        parameters = []
        for key, value in raw.items():
            parameter_name = str(key or "").strip()
            if not parameter_name:
                _error(
                    f"CAM operation {operation.Name!r} command {command_index} has "
                    "an empty parameter name.",
                    "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
                )
            if len(parameter_name) > MAX_PARAMETER_NAME_CHARS:
                _error(
                    f"CAM operation {operation.Name!r} command {command_index} has "
                    f"a parameter name exceeding {MAX_PARAMETER_NAME_CHARS} characters.",
                    "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
                )
            parameters.append(
                (
                    parameter_name,
                    _finite_parameter(
                        value,
                        f"CAM command {command_index} parameter {parameter_name!r}",
                    ),
                )
            )
        result.append(FrozenCommand(name, tuple(parameters)))
    return tuple(result)


def freeze_simulation_commands(operation: Any) -> tuple[FrozenCommand, ...]:
    """Freeze one generated path without retaining mutable Command objects."""

    return _freeze_commands(operation)


def _valid_shape(shape: Any) -> bool:
    return bool(
        shape is not None
        and callable(getattr(shape, "isNull", None))
        and not shape.isNull()
        and shape.isValid()
    )


def valid_simulation_shape(shape: Any) -> bool:
    """Return whether a detached simulator input shape is usable."""

    return _valid_shape(shape)


def _operation_target(
    document: Any,
    value: Mapping[str, Any],
) -> tuple[Any, Mapping[str, Any]]:
    _exact_target(value, "CAM operation")
    return resolve_operation_target(document, value)


def resolve_simulation_operation_target(
    document: Any,
    value: Mapping[str, Any],
) -> tuple[Any, Mapping[str, Any]]:
    """Resolve one exact generated CAM operation target."""

    return _operation_target(document, value)


def preflight_gl_simulation(
    document: Any,
    *,
    job: Mapping[str, Any],
    operations: list[Mapping[str, Any]],
    quality: int,
) -> FrozenGlSimulation:
    """Freeze all document-bound inputs without opening simulator UI."""

    if _transaction_open(document):
        _error(
            "GL simulation cannot start while a document transaction is open.",
            "NATIVE_MANUFACTURE_TRANSACTION_CONFLICT",
        )
    if type(quality) is not int or not 1 <= quality <= 10:
        _error("GL simulation quality must be an integer from 1 through 10.")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 64:
        _error("GL simulation requires one through 64 exact operations.")

    exact_job, job_before = resolve_job_target(document, job)
    other_job_states = capture_other_job_states(document, (exact_job,))
    group = tuple(getattr(getattr(exact_job, "Operations", None), "Group", ()) or ())
    group_positions = {id(operation): index for index, operation in enumerate(group)}
    if len(group_positions) != len(group):
        _error(
            "The exact CAM Job has duplicate operation identities.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )

    operation_targets = [_operation_target(document, value) for value in operations]
    operation_objects = tuple(value[0] for value in operation_targets)
    names = tuple(str(value.Name) for value in operation_objects)
    if len(names) != len(set(names)):
        _error("GL simulation operation targets must be distinct.")
    if any(id(operation) not in group_positions for operation in operation_objects):
        _error(
            "Every GL simulation operation must be a direct entry of the exact Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    positions = tuple(group_positions[id(operation)] for operation in operation_objects)
    if positions != tuple(sorted(positions)):
        _error(
            "GL simulation operations must be supplied in their current Job order.",
            repair={"job_order": [str(operation.Name) for operation in group]},
        )

    try:
        import FreeCAD
        import CAMSimulator  # noqa: F401 - load extension before worker execution
        import Path  # noqa: F401 - load command types before worker execution
        import Path.Dressup.Utils as PathDressup
        import PathScripts.PathUtils  # noqa: F401 - load Qt-bearing module on GUI thread
        from Path.Main import SimulatorGLPreparation  # noqa: F401
    except Exception as exc:
        raise NativeManufactureError(
            "The GL CAM simulator preparation modules are unavailable.",
            error_code="NATIVE_MANUFACTURE_GL_SIMULATION_UNAVAILABLE",
        ) from exc

    controllers = tuple(getattr(getattr(exact_job, "Tools", None), "Group", ()) or ())
    runs = []
    command_count = 0
    for (operation, state) in operation_targets:
        if not operation_active_state(operation):
            _error(
                f"CAM operation {operation.Name!r} is inactive.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        commands = _freeze_commands(operation)
        command_count += len(commands)
        if command_count > MAX_SIMULATION_COMMANDS:
            _error(
                f"GL simulation is limited to {MAX_SIMULATION_COMMANDS:,} commands; "
                f"the requested operations contain at least {command_count:,}.",
                "NATIVE_MANUFACTURE_SIMULATION_LIMIT",
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
        if not _valid_shape(tool_shape):
            _error(
                f"CAM operation {operation.Name!r} has no valid ToolBit shape.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        try:
            tool_number = int(getattr(controller, "ToolNumber", 0) or 0)
        except (TypeError, ValueError):
            tool_number = 0
        diameter_value = getattr(getattr(tool, "Diameter", None), "Value", None)
        try:
            diameter = float(diameter_value)
        except (TypeError, ValueError):
            diameter = math.nan
        if tool_number < 1 or not math.isfinite(diameter) or diameter <= 0.0:
            _error(
                f"CAM operation {operation.Name!r} has an invalid tool number or diameter.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
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
            )
        )

    stock_shape = getattr(getattr(exact_job, "Stock", None), "Shape", None)
    if not _valid_shape(stock_shape):
        _error(
            "The exact CAM Job has no valid stock shape.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    base_shapes = []
    for model in tuple(getattr(getattr(exact_job, "Model", None), "OutList", ()) or ()):
        shape = getattr(model, "Shape", None)
        if not _valid_shape(shape):
            _error(
                f"CAM Job model resource {getattr(model, 'Name', '')!r} has no valid shape.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        base_shapes.append(shape.copy())

    if job_state(exact_job).get("state_sha256") != job_before.get("state_sha256"):
        _error(
            "The exact CAM Job changed while simulation inputs were frozen.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if not other_job_states_are_current(document, other_job_states):
        _error(
            "Another CAM setup changed while simulation inputs were frozen.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    for run in runs:
        current = operation_state(run.operation)
        if current.get("state_sha256") != run.expected_state_sha256:
            _error(
                f"CAM operation {run.operation_name!r} changed while simulation inputs "
                "were frozen.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
    return FrozenGlSimulation(
        job=exact_job,
        expected_job_state_sha256=str(job_before["state_sha256"]),
        other_job_states=other_job_states,
        quality=quality,
        stock_shape=stock_shape.copy(),
        base_shapes=tuple(base_shapes),
        runs=tuple(runs),
        command_count=command_count,
    )


def prepare_gl_simulation(
    frozen: FrozenGlSimulation,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedGlSimulation:
    """Tessellate and serialize detached data on the background worker."""

    if not isinstance(frozen, FrozenGlSimulation):
        raise TypeError("frozen must be a FrozenGlSimulation")
    try:
        import CAMSimulator
        import Path
        import Part
        import PathScripts.PathUtils as PathUtils
        from Path.Main import SimulatorGLPreparation

        simulator = CAMSimulator.PathSim()
        progress(8, "Tessellating detached stock")
        stock_mesh = simulator.PrepareShapeMesh(frozen.stock_shape, 1.0)
        if cancelled():
            raise NativeBackgroundCancelled()

        base_shape = None
        base_mesh = None
        if frozen.base_shapes:
            progress(34, "Tessellating detached model")
            base_shape = Part.makeCompound(list(frozen.base_shapes))
            if not _valid_shape(base_shape):
                raise ValueError("The detached CAM model compound is invalid")
            base_mesh = simulator.PrepareShapeMesh(base_shape, 1.0)

        prepared_runs = []
        digest = hashlib.sha256()
        operation_total = len(frozen.runs)
        for run_index, run in enumerate(frozen.runs):
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(
                48 + int(38 * run_index / max(1, operation_total)),
                f"Preparing operation {run_index + 1} of {operation_total}",
            )
            profile = SimulatorGLPreparation.build_tool_profile(run.tool_shape, 0.5)
            commands = [
                Path.Command(command.name, dict(command.parameters))
                for command in run.commands
            ]
            path = Path.Path(commands)
            placed = (
                path
                if run.placement.isIdentity()
                else PathUtils.applyPlacementToPath(run.placement, path)
            )
            gcode = []
            for command_index, command in enumerate(placed.Commands):
                if command_index % 1024 == 0 and cancelled():
                    raise NativeBackgroundCancelled()
                line = str(command.toGCode())
                encoded = line.encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
                gcode.append(line)
            if len(gcode) != len(run.commands):
                raise ValueError("Placed G-code command count changed during preparation")
            prepared_runs.append(
                {
                    "operation_name": run.operation_name,
                    "tool_profile": tuple(float(value) for value in profile),
                    "tool_number": run.tool_number,
                    "diameter_mm": run.diameter_mm,
                    "gcode": tuple(gcode),
                }
            )
        progress(88, "Prepared exact GL simulation")
        return PreparedGlSimulation(
            frozen=frozen,
            stock_mesh=stock_mesh,
            base_shape=base_shape,
            base_mesh=base_mesh,
            runs=tuple(prepared_runs),
            program_sha256=digest.hexdigest(),
        )
    except NativeManufactureError:
        raise
    except NativeBackgroundCancelled:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            f"Detached GL simulation preparation failed: {str(exc)[:240]}",
            error_code="NATIVE_MANUFACTURE_GL_PREPARATION_FAILED",
        ) from exc


def validate_gl_simulation(document: Any, frozen: FrozenGlSimulation) -> None:
    """Prove the exact Job and ordered operations are still current."""

    if frozen.job.Document is not document or document.getObject(frozen.job.Name) is not frozen.job:
        _error(
            "The exact CAM Job no longer belongs to the active document.",
            "NATIVE_MANUFACTURE_TARGET_STALE",
        )
    current_job = job_state(frozen.job)
    if current_job.get("state_sha256") != frozen.expected_job_state_sha256:
        _error(
            "The exact CAM Job changed during background simulation preparation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
            repair={
                "object_name": str(frozen.job.Name),
                "current_state_sha256": current_job.get("state_sha256"),
            },
        )
    if not other_job_states_are_current(document, frozen.other_job_states):
        _error(
            "Another CAM setup changed during background simulation preparation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    group = tuple(getattr(frozen.job.Operations, "Group", ()) or ())
    positions = {id(operation): index for index, operation in enumerate(group)}
    previous = -1
    for run in frozen.runs:
        position = positions.get(id(run.operation), -1)
        current = operation_state(run.operation)
        if position <= previous or current.get("state_sha256") != run.expected_state_sha256:
            _error(
                f"CAM operation {run.operation_name!r} changed during background "
                "simulation preparation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
                repair={
                    "object_name": run.operation_name,
                    "current_state_sha256": current.get("state_sha256"),
                },
            )
        previous = position


def present_gl_simulation(
    document: Any,
    prepared: PreparedGlSimulation,
) -> dict[str, Any]:
    """Open the exact prepared task and GL view on the GUI thread."""

    if not isinstance(prepared, PreparedGlSimulation):
        raise TypeError("prepared must be a PreparedGlSimulation")
    validate_gl_simulation(document, prepared.frozen)
    from VibeCADNativeTargets import read_current_selection

    objects_before = tuple(document.Objects)
    visibility_before = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj in objects_before
        if getattr(obj, "ViewObject", None) is not None
    )
    selection_before = read_current_selection(document)
    timeline = document.getObject("VibeCADTimeline")
    timeline_before = (
        tuple(getattr(timeline, "Operations", ()) or ()),
        tuple(bool(value) for value in getattr(timeline, "VisibilityAtEnd", ()) or ()),
        tuple(bool(value) for value in getattr(timeline, "SuppressionAtEnd", ()) or ()),
        int(getattr(timeline, "Position", 0) or 0),
    )
    undo_before = int(getattr(document, "UndoCount", 0) or 0)
    try:
        from Path.Main.Gui import SimulatorGL

        simulation = SimulatorGL.activate_prepared_simulation(
            job=prepared.frozen.job,
            operations=tuple(run.operation for run in prepared.frozen.runs),
            quality=prepared.frozen.quality,
            stockShape=prepared.frozen.stock_shape,
            stockMesh=prepared.stock_mesh,
            baseShape=prepared.base_shape,
            baseMesh=prepared.base_mesh,
            runs=prepared.runs,
        )
    except Exception as exc:
        raise NativeManufactureError(
            "The prepared GL simulation could not be presented.",
            error_code="NATIVE_MANUFACTURE_GL_PRESENTATION_FAILED",
        ) from exc
    postcondition_ok = bool(
        tuple(document.Objects) == objects_before
        and all(bool(obj.ViewObject.Visibility) is visible for obj, visible in visibility_before)
        and read_current_selection(document) == selection_before
        and int(getattr(document, "UndoCount", 0) or 0) == undo_before
        and (
            tuple(getattr(timeline, "Operations", ()) or ()),
            tuple(bool(value) for value in getattr(timeline, "VisibilityAtEnd", ()) or ()),
            tuple(bool(value) for value in getattr(timeline, "SuppressionAtEnd", ()) or ()),
            int(getattr(timeline, "Position", 0) or 0),
        )
        == timeline_before
    )
    if not postcondition_ok:
        try:
            import FreeCADGui as Gui

            Gui.Control.closeDialog()
        except Exception:
            pass
        raise NativeManufactureError(
            "Opening GL simulation changed document, History, undo, selection, or visibility state.",
            error_code="NATIVE_MANUFACTURE_GL_PRESENTATION_SIDE_EFFECT",
        )
    names = [run.operation_name for run in prepared.frozen.runs]
    simulation_id = str(simulation.nativeSimulationId or "")
    if len(simulation_id) != 32:
        raise NativeManufactureError(
            "The prepared GL simulation has no stable task identity.",
            error_code="NATIVE_MANUFACTURE_GL_PRESENTATION_FAILED",
        )
    return {
        "simulation": {
            "mode": "gl",
            "simulation_id": simulation_id,
            "job": str(prepared.frozen.job.Name),
            "operations": names,
            "operation_count": len(names),
            "command_count": prepared.frozen.command_count,
            "tool_run_count": len(prepared.runs),
            "quality": prepared.frozen.quality,
            "program_sha256": prepared.program_sha256,
            "task_active": SimulatorGL.owns_active_prepared_simulation(document),
            "document_changed": False,
        },
        "next": {
            "tool": "manufacture.close_simulation",
            "simulation_id": simulation_id,
        },
    }


def close_gl_simulation(document: Any, simulation_id: str) -> dict[str, Any]:
    """Close only the exact Native-owned simulation for this document."""

    from Path.Main.Gui import SimulatorGL
    from VibeCADNativeTargets import read_current_selection

    simulation = SimulatorGL.active_prepared_simulation()
    if simulation is None or not SimulatorGL.owns_active_prepared_simulation(document):
        _error(
            "This document has no active Native CAM simulation.",
            "NATIVE_MANUFACTURE_GL_SIMULATION_NOT_ACTIVE",
        )
    observed_id = str(getattr(simulation, "nativeSimulationId", "") or "")
    if str(simulation_id or "") != observed_id:
        _error(
            "The active Native CAM simulation changed after it was opened.",
            "NATIVE_MANUFACTURE_STATE_STALE",
            repair={"current_simulation_id": observed_id},
        )

    objects_before = tuple(document.Objects)
    visibility_before = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj in objects_before
        if getattr(obj, "ViewObject", None) is not None
    )
    selection_before = read_current_selection(document)
    timeline = document.getObject("VibeCADTimeline")
    timeline_before = (
        tuple(getattr(timeline, "Operations", ()) or ()),
        tuple(bool(value) for value in getattr(timeline, "VisibilityAtEnd", ()) or ()),
        tuple(bool(value) for value in getattr(timeline, "SuppressionAtEnd", ()) or ()),
        int(getattr(timeline, "Position", 0) or 0),
    )
    undo_before = int(getattr(document, "UndoCount", 0) or 0)
    simulation.taskForm.reject()
    if (
        SimulatorGL.active_prepared_simulation() is not None
        or tuple(document.Objects) != objects_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in visibility_before
        )
        or read_current_selection(document) != selection_before
        or (
            tuple(getattr(timeline, "Operations", ()) or ()),
            tuple(
                bool(value)
                for value in getattr(timeline, "VisibilityAtEnd", ()) or ()
            ),
            tuple(
                bool(value)
                for value in getattr(timeline, "SuppressionAtEnd", ()) or ()
            ),
            int(getattr(timeline, "Position", 0) or 0),
        )
        != timeline_before
        or int(getattr(document, "UndoCount", 0) or 0) != undo_before
    ):
        raise NativeManufactureError(
            "Closing GL simulation did not restore its exact presentation state.",
            error_code="NATIVE_MANUFACTURE_GL_PRESENTATION_SIDE_EFFECT",
        )
    return {
        "simulation": {
            "mode": "gl",
            "simulation_id": observed_id,
            "closed": True,
            "document_changed": False,
        }
    }
