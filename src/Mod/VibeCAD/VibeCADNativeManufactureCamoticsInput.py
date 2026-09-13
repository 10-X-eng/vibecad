# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact main-thread input boundary for optional CAMotics operations."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import shutil
import stat
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureSimulation import (
    FrozenCommand,
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
from VibeCADNativeTargets import read_current_selection


_REQUEST_FIELDS = frozenset({"kind", "resolution"})
_REQUEST_KINDS = frozenset({"read_result", "launch"})
_RESOLUTIONS = frozenset({"low", "medium", "high"})
_SHAPE_MAP = {
    "ballend": "Ballnose",
    "endmill": "Cylindrical",
    "taperedballnose": "Ballnose",
    "v-bit": "Conical",
    "chamfer": "Snubnose",
}
_REQUIRED_SIMULATION_METHODS = (
    "set_metric",
    "set_resolution",
    "set_workpiece",
    "set_tool",
    "compute_path",
    "is_running",
    "wait",
    "get_path",
    "start",
    "interrupt",
    "get_surface",
)


@dataclass(frozen=True, slots=True)
class CamoticsTool:
    number: int
    shape: str
    length_mm: float
    diameter_mm: float
    label: str


@dataclass(frozen=True, slots=True)
class CamoticsExecutableIdentity:
    path: Path = field(repr=False, compare=False)
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class CamoticsPresentationState:
    selection: Any = field(repr=False)
    visibility: tuple[tuple[Any, bool], ...] = field(repr=False)
    undo_count: int
    redo_count: int
    transaction_id: int
    gui_modified: bool | None


@dataclass(frozen=True, slots=True)
class CamoticsTimelineState:
    timeline: Any = field(repr=False, compare=False)
    operations: tuple[Any, ...] = field(repr=False)
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class FrozenCamoticsRun:
    operation: Any = field(repr=False, compare=False)
    operation_name: str
    expected_state_sha256: str
    placement: Any = field(repr=False, compare=False)
    commands: tuple[FrozenCommand, ...] = field(repr=False)
    tool_number: int


@dataclass(frozen=True, slots=True)
class FrozenCamoticsInput:
    job: Any = field(repr=False, compare=False)
    expected_job_state_sha256: str
    other_job_states: tuple[tuple[Any, str], ...] = field(
        repr=False,
        compare=False,
    )
    job_operations: tuple[Any, ...] = field(repr=False)
    stock: Any = field(repr=False, compare=False)
    objects_before: tuple[Any, ...] = field(repr=False)
    timeline_before: CamoticsTimelineState
    runs: tuple[FrozenCamoticsRun, ...]
    command_count: int
    request_kind: str
    resolution: str
    tools: tuple[CamoticsTool, ...]
    stock_bounds_mm: tuple[tuple[float, float, float], tuple[float, float, float]]
    simulation_factory: Any = field(repr=False, compare=False)
    executable: CamoticsExecutableIdentity | None
    presentation_before: CamoticsPresentationState


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _transaction_id(document: Any) -> int:
    reader = getattr(document, "getBookedTransactionID", None)
    return int(reader() or 0) if callable(reader) else 0


def _transaction_open(document: Any) -> bool:
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or _transaction_id(document) != 0
    )


def _gui_modified(document: Any) -> bool | None:
    try:
        import FreeCADGui as Gui

        gui_document = Gui.getDocument(str(document.Name))
        return None if gui_document is None else bool(gui_document.Modified)
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return None


def _presentation_state(document: Any) -> CamoticsPresentationState:
    visibility = []
    for obj in tuple(document.Objects):
        view = getattr(obj, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            visibility.append((obj, bool(view.Visibility)))
    return CamoticsPresentationState(
        selection=read_current_selection(document),
        visibility=tuple(visibility),
        undo_count=int(getattr(document, "UndoCount", 0) or 0),
        redo_count=int(getattr(document, "RedoCount", 0) or 0),
        transaction_id=_transaction_id(document),
        gui_modified=_gui_modified(document),
    )


def _timeline_state(document: Any) -> CamoticsTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        _error(
            "CAMotics requires a valid document History.",
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
            "CAMotics found malformed document History state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return CamoticsTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _usable(document: Any, obj: Any, noun: str) -> None:
    reader = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if not callable(reader) or not bool(reader(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_MANUFACTURE_HISTORY_TARGET_INACTIVE",
        )


def _request(value: Any) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != _REQUEST_FIELDS:
        _error("request must contain exactly kind and resolution.")
    kind = str(value["kind"] or "")
    resolution = str(value["resolution"] or "")
    if kind not in _REQUEST_KINDS:
        _error("request.kind must be read_result or launch.")
    if resolution not in _RESOLUTIONS:
        _error("request.resolution must be low, medium, or high.")
    return kind, resolution


def _simulation_factory() -> Any:
    try:
        import camotics
    except Exception as exc:
        raise NativeManufactureError(
            "The optional CAMotics Python runtime is unavailable.",
            error_code="NATIVE_MANUFACTURE_CAMOTICS_UNAVAILABLE",
        ) from exc
    factory = getattr(camotics, "Simulation", None)
    missing = [
        name
        for name in _REQUIRED_SIMULATION_METHODS
        if not callable(getattr(factory, name, None))
    ]
    if not callable(factory) or missing:
        raise NativeManufactureError(
            "The installed CAMotics Python runtime has an unsupported Simulation API.",
            error_code="NATIVE_MANUFACTURE_CAMOTICS_UNAVAILABLE",
            repair={"missing_methods": missing[:16]},
        )
    return factory


def _executable_identity() -> CamoticsExecutableIdentity:
    candidate = shutil.which("camotics")
    if not candidate:
        _error(
            "The installed CAMotics application is unavailable for launch.",
            "NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_UNAVAILABLE",
        )
    try:
        path = Path(candidate).resolve(strict=True)
        value = path.stat()
    except OSError as exc:
        raise NativeManufactureError(
            "The installed CAMotics application could not be inspected.",
            error_code="NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_UNAVAILABLE",
        ) from exc
    if not stat.S_ISREG(value.st_mode) or not os.access(path, os.X_OK):
        _error(
            "The installed CAMotics application is not an executable regular file.",
            "NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_UNAVAILABLE",
        )
    return CamoticsExecutableIdentity(
        path=path,
        device=int(value.st_dev),
        inode=int(value.st_ino),
        size=int(value.st_size),
        modified_ns=int(value.st_mtime_ns),
    )


def _validate_executable(identity: CamoticsExecutableIdentity) -> None:
    try:
        value = identity.path.stat()
    except OSError as exc:
        raise NativeManufactureError(
            "The installed CAMotics application disappeared before launch.",
            error_code="NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_UNAVAILABLE",
        ) from exc
    current = (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
    )
    expected = (
        identity.device,
        identity.inode,
        identity.size,
        identity.modified_ns,
    )
    if (
        current != expected
        or not stat.S_ISREG(value.st_mode)
        or not os.access(identity.path, os.X_OK)
    ):
        _error(
            "The installed CAMotics application changed before launch.",
            "NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_UNAVAILABLE",
        )


def _positive_quantity(value: Any, noun: str) -> float:
    try:
        result = float(getattr(value, "Value", value))
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            f"The CAMotics {noun} is not a finite positive length.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if not math.isfinite(result) or result <= 0.0:
        _error(
            f"The CAMotics {noun} is not a finite positive length.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return result


def _tool(
    operation: Any,
    controllers: tuple[Any, ...],
) -> tuple[CamoticsTool, int]:
    import Path.Dressup.Utils as PathDressup
    import PathScripts.PathUtils as PathUtils

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
    try:
        number = int(getattr(controller, "ToolNumber", 0) or 0)
    except (TypeError, ValueError):
        number = 0
    if number < 1:
        _error(
            f"CAM operation {operation.Name!r} has an invalid tool number.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    tool = getattr(controller, "Tool", None)
    shape_name = str(PathUtils.getToolShapeName(tool) or "")
    return (
        CamoticsTool(
            number=number,
            shape=_SHAPE_MAP.get(shape_name, "Cylindrical"),
            length_mm=_positive_quantity(getattr(tool, "Length", None), "tool length"),
            diameter_mm=_positive_quantity(
                getattr(tool, "Diameter", None),
                "tool diameter",
            ),
            label=str(getattr(controller, "Label", "") or "")[:160],
        ),
        number,
    )


def _stock_bounds(shape: Any) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
]:
    box = shape.BoundBox
    values = tuple(
        float(getattr(box, name))
        for name in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
    )
    if not bool(box.isValid()) or any(not math.isfinite(value) for value in values):
        _error(
            "The exact CAM stock has invalid bounds for CAMotics.",
            "NATIVE_MANUFACTURE_SIMULATION_STOCK_INVALID",
        )
    return values[:3], values[3:]


def preflight_camotics(
    document: Any,
    *,
    job: Mapping[str, Any],
    operations: list[Mapping[str, Any]],
    request: Mapping[str, Any],
) -> FrozenCamoticsInput:
    """Freeze the exact Job, ordered program, runtime, and presentation state."""

    kind, resolution = _request(request)
    factory = _simulation_factory()
    if _transaction_open(document):
        _error(
            "CAMotics cannot start while a document transaction is open.",
            "NATIVE_MANUFACTURE_TRANSACTION_CONFLICT",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for the active document recompute before using CAMotics.",
            "NATIVE_MANUFACTURE_CAMOTICS_UNAVAILABLE",
        )
    if not isinstance(operations, list) or not 1 <= len(operations) <= 64:
        _error("CAMotics requires one through 64 exact operations.")
    exact_job, job_before = resolve_job_target(document, job)
    other_job_states = capture_other_job_states(document, (exact_job,))
    _usable(document, exact_job, "CAM Job")
    group = tuple(getattr(getattr(exact_job, "Operations", None), "Group", ()) or ())
    positions = {id(operation): index for index, operation in enumerate(group)}
    if len(positions) != len(group):
        _error(
            "The exact CAM Job has duplicate operation identities.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    targets = [
        resolve_simulation_operation_target(document, value)
        for value in operations
    ]
    target_objects = tuple(value[0] for value in targets)
    names = tuple(str(value.Name) for value in target_objects)
    if len(names) != len(set(names)):
        _error("CAMotics operation targets must be distinct.")
    if any(id(operation) not in positions for operation in target_objects):
        _error(
            "Every CAMotics operation must be a direct entry of the exact Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    requested_positions = tuple(positions[id(operation)] for operation in target_objects)
    if requested_positions != tuple(sorted(requested_positions)):
        raise NativeManufactureError(
            "CAMotics operations must be supplied in their current Job order.",
            error_code="NATIVE_ARGUMENTS_INVALID",
            repair={"job_order": [str(operation.Name) for operation in group]},
        )
    try:
        import FreeCAD
        import Path  # noqa: F401 - load command types on the document thread
        import Path.Dressup.Utils  # noqa: F401
        import PathScripts.PathUtils  # noqa: F401
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM toolpath modules required by CAMotics are unavailable.",
            error_code="NATIVE_MANUFACTURE_CAMOTICS_UNAVAILABLE",
        ) from exc
    controllers = tuple(getattr(getattr(exact_job, "Tools", None), "Group", ()) or ())
    tool_by_number: dict[int, CamoticsTool] = {}
    runs = []
    command_count = 0
    for operation, state in targets:
        _usable(document, operation, f"CAM operation {operation.Name!r}")
        if not operation_active_state(operation):
            _error(
                f"CAM operation {operation.Name!r} is inactive.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        commands = freeze_simulation_commands(operation)
        command_count += len(commands)
        if command_count > MAX_SIMULATION_COMMANDS:
            _error(
                f"CAMotics is limited to {MAX_SIMULATION_COMMANDS:,} source commands.",
                "NATIVE_MANUFACTURE_SIMULATION_LIMIT",
            )
        descriptor, tool_number = _tool(operation, controllers)
        previous = tool_by_number.get(tool_number)
        if previous is not None and previous != descriptor:
            _error(
                f"CAM tool number {tool_number} has conflicting definitions.",
                "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
        tool_by_number[tool_number] = descriptor
        runs.append(
            FrozenCamoticsRun(
                operation=operation,
                operation_name=str(operation.Name),
                expected_state_sha256=str(state["state_sha256"]),
                placement=FreeCAD.Placement(operation.Placement),
                commands=commands,
                tool_number=tool_number,
            )
        )
    stock = getattr(exact_job, "Stock", None)
    stock_shape = getattr(stock, "Shape", None)
    if not valid_simulation_shape(stock_shape) or not tuple(stock_shape.Solids):
        _error(
            "The exact CAM Job has no valid solid stock shape.",
            "NATIVE_MANUFACTURE_SIMULATION_STOCK_INVALID",
        )
    _usable(document, stock, "CAM stock")
    if job_state(exact_job).get("state_sha256") != job_before.get("state_sha256"):
        _error(
            "The exact CAM Job changed while CAMotics inputs were frozen.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if not other_job_states_are_current(document, other_job_states):
        _error(
            "Another CAM setup changed while CAMotics inputs were frozen.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    for run in runs:
        if operation_state(run.operation).get("state_sha256") != (
            run.expected_state_sha256
        ):
            _error(
                f"CAM operation {run.operation_name!r} changed while CAMotics "
                "inputs were frozen.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
    executable = _executable_identity() if kind == "launch" else None
    return FrozenCamoticsInput(
        job=exact_job,
        expected_job_state_sha256=str(job_before["state_sha256"]),
        other_job_states=other_job_states,
        job_operations=group,
        stock=stock,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_state(document),
        runs=tuple(runs),
        command_count=command_count,
        request_kind=kind,
        resolution=resolution,
        tools=tuple(tool_by_number[number] for number in sorted(tool_by_number)),
        stock_bounds_mm=_stock_bounds(stock_shape),
        simulation_factory=factory,
        executable=executable,
        presentation_before=_presentation_state(document),
    )


def validate_camotics(document: Any, frozen: FrozenCamoticsInput) -> None:
    """Reject a result or launch if exact document or UI state drifted."""

    if not isinstance(frozen, FrozenCamoticsInput):
        raise TypeError("frozen must be a FrozenCamoticsInput")
    if (
        _transaction_open(document)
        or bool(getattr(document, "Recomputing", False))
        or bool(getattr(document, "RecomputePending", False))
        or tuple(document.Objects) != frozen.objects_before
        or frozen.job.Document is not document
        or document.getObject(str(frozen.job.Name)) is not frozen.job
        or tuple(getattr(frozen.job.Operations, "Group", ()) or ())
        != frozen.job_operations
        or getattr(frozen.job, "Stock", None) is not frozen.stock
        or job_state(frozen.job).get("state_sha256")
        != frozen.expected_job_state_sha256
        or not other_job_states_are_current(document, frozen.other_job_states)
        or _timeline_state(document) != frozen.timeline_before
    ):
        _error(
            "The exact CAM Job, object graph, or History changed during CAMotics work.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    _usable(document, frozen.job, "CAM Job")
    _usable(document, frozen.stock, "CAM stock")
    positions = {
        id(operation): index
        for index, operation in enumerate(frozen.job_operations)
    }
    previous_position = -1
    for run in frozen.runs:
        position = positions.get(id(run.operation), -1)
        current = operation_state(run.operation)
        if (
            position <= previous_position
            or current.get("state_sha256") != run.expected_state_sha256
            or not operation_active_state(run.operation)
        ):
            _error(
                f"CAM operation {run.operation_name!r} changed during CAMotics work.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        _usable(document, run.operation, f"CAM operation {run.operation_name!r}")
        previous_position = position
    if _presentation_state(document) != frozen.presentation_before:
        _error(
            "The document or human presentation state changed during CAMotics work.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if frozen.request_kind == "launch":
        if frozen.executable is None:
            raise TypeError("A CAMotics launch requires its frozen executable identity")
        _validate_executable(frozen.executable)
