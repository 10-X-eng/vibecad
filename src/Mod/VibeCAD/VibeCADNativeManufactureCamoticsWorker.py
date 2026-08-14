# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached CAMotics execution and host-owned launch workspace."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeManufactureCamoticsInput import FrozenCamoticsInput
from VibeCADNativeManufactureErrors import NativeManufactureError


MAX_CAMOTICS_PROGRAM_BYTES = 128 * 1024 * 1024
MAX_CAMOTICS_PATH_STEPS = 2_000_000
MAX_CAMOTICS_SURFACE_BYTES = 256 * 1024 * 1024
MAX_CAMOTICS_SURFACE_FACETS = 5_000_000
_STL_HEADER_BYTES = 84
_STL_FACET_BYTES = 50


@dataclass(frozen=True, slots=True)
class CamoticsProgram:
    data: bytes = field(repr=False)
    sha256: str
    command_count: int


@dataclass(frozen=True, slots=True)
class PreparedCamoticsResult:
    frozen: FrozenCamoticsInput
    program_sha256: str
    command_count: int
    path_step_count: int
    duration_seconds: float
    surface_sha256: str
    surface_facets: int
    surface_bounds_mm: tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(slots=True)
class PreparedCamoticsLaunch:
    frozen: FrozenCamoticsInput
    program_sha256: str
    command_count: int
    workspace: tempfile.TemporaryDirectory[str] = field(repr=False)
    project_path: Path = field(repr=False)
    _launched: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def launch(self) -> Mapping[str, Any]:
        executable = self.frozen.executable
        if executable is None:
            raise TypeError("A prepared CAMotics launch has no executable identity")
        with self._lock:
            if self._launched:
                raise NativeManufactureError(
                    "The prepared CAMotics project has already been launched.",
                    error_code="NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_FAILED",
                )
            try:
                process = subprocess.Popen(
                    [str(executable.path), str(self.project_path)],
                    cwd=str(self.project_path.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    shell=False,
                )
            except (OSError, ValueError) as exc:
                raise NativeManufactureError(
                    "The installed CAMotics application could not be launched.",
                    error_code="NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_FAILED",
                ) from exc
            self._launched = True
        workspace = self.workspace

        def reap() -> None:
            try:
                process.wait()
            finally:
                workspace.cleanup()

        reaper = threading.Thread(
            target=reap,
            name=f"VibeCAD-CAMotics-{process.pid}",
            daemon=True,
        )
        try:
            reaper.start()
        except RuntimeError as exc:
            try:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait()
            finally:
                workspace.cleanup()
            raise NativeManufactureError(
                "The CAMotics process could not be supervised safely.",
                error_code="NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_FAILED",
            ) from exc
        return {
            "request": "launch",
            "launched": True,
            "job": str(self.frozen.job.Name),
            "operation_count": len(self.frozen.runs),
            "command_count": self.command_count,
            "resolution": self.frozen.resolution,
            "program_sha256": self.program_sha256,
        }

    def cleanup_if_unlaunched(self) -> None:
        with self._lock:
            launched = self._launched
        if not launched:
            self.workspace.cleanup()


PreparedCamotics = PreparedCamoticsResult | PreparedCamoticsLaunch


def _error(message: str, code: str) -> None:
    raise NativeManufactureError(message, error_code=code)


def _cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise NativeBackgroundCancelled()


def _program(
    frozen: FrozenCamoticsInput,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> CamoticsProgram:
    import Path as PathModule
    import PathScripts.PathUtils as PathUtils

    data = bytearray()

    def append_line(line: str) -> None:
        if "\0" in line or "\r" in line or "\n" in line:
            _error(
                "The detached CAMotics program contains an unsafe control character.",
                "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            )
        encoded = (line + "\n").encode("utf-8")
        if len(data) + len(encoded) > MAX_CAMOTICS_PROGRAM_BYTES:
            _error(
                "The detached CAMotics program exceeds the 128 MiB bound.",
                "NATIVE_MANUFACTURE_SIMULATION_LIMIT",
            )
        data.extend(encoded)

    for header in ("G21", "G90", "G17"):
        append_line(header)
    command_count = 0
    total = max(1, frozen.command_count)
    processed = 0
    for run_index, run in enumerate(frozen.runs):
        _cancel(cancelled)
        append_line(f"T{run.tool_number} M6")
        commands = [
            PathModule.Command(command.name, dict(command.parameters))
            for command in run.commands
        ]
        path = PathModule.Path(commands)
        placed = (
            path
            if run.placement.isIdentity()
            else PathUtils.applyPlacementToPath(run.placement, path)
        )
        placed_commands = tuple(placed.Commands)
        if len(placed_commands) != len(run.commands):
            _error(
                f"CAM operation {run.operation_name!r} changed command count during "
                "detached CAMotics placement.",
                "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            )
        for command in placed_commands:
            _cancel(cancelled)
            line = str(command.toGCode())
            if not line:
                _error(
                    f"CAM operation {run.operation_name!r} produced an empty "
                    "detached G-code command.",
                    "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
                )
            append_line(line)
            command_count += 1
            processed += 1
            if processed % 512 == 0:
                progress(
                    5 + int(15 * processed / total),
                    f"Prepared {processed} of {total} CAM commands",
                )
        progress(
            5 + int(15 * processed / total),
            f"Prepared operation {run_index + 1} of {len(frozen.runs)}",
        )
    append_line("M30")
    if command_count != frozen.command_count:
        _error(
            "The detached CAMotics program did not preserve every frozen CAM command.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    frozen_data = bytes(data)
    return CamoticsProgram(
        frozen_data,
        hashlib.sha256(frozen_data).hexdigest(),
        command_count,
    )


def _wait_task(
    simulation: Any,
    *,
    cancelled: Callable[[], bool],
) -> None:
    while bool(simulation.is_running()):
        if cancelled():
            simulation.interrupt()
            simulation.wait()
            raise NativeBackgroundCancelled()
        time.sleep(0.01)
    simulation.wait()
    _cancel(cancelled)


def _path_facts(value: Any) -> tuple[int, float]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CAMOTICS_PATH_STEPS:
        _error(
            "CAMotics returned an empty or over-limit planned toolpath.",
            "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID",
        )
    duration = 0.0
    for index, step in enumerate(value):
        if not isinstance(step, Mapping) or "time" not in step:
            _error(
                f"CAMotics returned a malformed path step at index {index}.",
                "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID",
            )
        try:
            step_time = float(step["time"])
        except (TypeError, ValueError) as exc:
            raise NativeManufactureError(
                f"CAMotics returned an invalid path time at index {index}.",
                error_code="NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID",
            ) from exc
        if not math.isfinite(step_time) or step_time < 0.0:
            _error(
                f"CAMotics returned an invalid path time at index {index}.",
                "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID",
            )
        duration += step_time
        if not math.isfinite(duration):
            _error(
                "CAMotics returned an over-limit path duration.",
                "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID",
            )
    return len(value), duration


def _surface_facts(
    data: Any,
) -> tuple[
    str,
    int,
    tuple[tuple[float, float, float], tuple[float, float, float]],
]:
    if not isinstance(data, bytes) or not _STL_HEADER_BYTES <= len(data) <= (
        MAX_CAMOTICS_SURFACE_BYTES
    ):
        _error(
            "CAMotics returned an empty or over-limit binary surface.",
            "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID",
        )
    facets = struct.unpack_from("<I", data, 80)[0]
    expected = _STL_HEADER_BYTES + facets * _STL_FACET_BYTES
    if not 1 <= facets <= MAX_CAMOTICS_SURFACE_FACETS or len(data) != expected:
        _error(
            "CAMotics returned a malformed binary STL surface.",
            "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID",
        )
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    stream = io.BytesIO(data)
    stream.seek(_STL_HEADER_BYTES)
    for _index in range(facets):
        record = stream.read(_STL_FACET_BYTES)
        values = struct.unpack("<12fH", record)
        if any(not math.isfinite(float(value)) for value in values[:12]):
            _error(
                "CAMotics returned a non-finite surface coordinate.",
                "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID",
            )
        for offset in (3, 6, 9):
            for axis in range(3):
                coordinate = float(values[offset + axis])
                minimum[axis] = min(minimum[axis], coordinate)
                maximum[axis] = max(maximum[axis], coordinate)
    bounds = tuple(minimum), tuple(maximum)
    return hashlib.sha256(data).hexdigest(), facets, bounds


def _configure_simulation(frozen: FrozenCamoticsInput) -> Any:
    try:
        simulation = frozen.simulation_factory()
        simulation.set_metric(True)
        simulation.set_resolution(frozen.resolution)
        simulation.set_workpiece(
            min=frozen.stock_bounds_mm[0],
            max=frozen.stock_bounds_mm[1],
        )
        for tool in frozen.tools:
            simulation.set_tool(
                tool.number,
                metric=True,
                shape=tool.shape,
                length=tool.length_mm,
                diameter=tool.diameter_mm,
                description=tool.label,
            )
        return simulation
    except Exception as exc:
        raise NativeManufactureError(
            "CAMotics could not initialize the exact stock and tool table.",
            error_code="NATIVE_MANUFACTURE_CAMOTICS_EXECUTION_FAILED",
        ) from exc


def _read_result(
    frozen: FrozenCamoticsInput,
    program: CamoticsProgram,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedCamoticsResult:
    simulation = _configure_simulation(frozen)
    try:
        _cancel(cancelled)
        progress(25, "Planning exact CAMotics program")
        simulation.compute_path(program.data.decode("utf-8"))
        _wait_task(simulation, cancelled=cancelled)
        path_steps, duration = _path_facts(simulation.get_path())
        progress(45, "Running CAMotics stock simulation")
        latest = {"percent": 45}
        callback_lock = threading.Lock()

        def callback(_status: Any, fraction: Any) -> bool:
            if cancelled():
                return False
            try:
                value = float(fraction)
            except (TypeError, ValueError):
                value = 0.0
            with callback_lock:
                latest["percent"] = max(
                    latest["percent"],
                    min(84, 45 + int(39 * max(0.0, min(1.0, value)))),
                )
            return True

        simulation.start(callback=callback, time=duration)
        reported = 45
        while bool(simulation.is_running()):
            if cancelled():
                simulation.interrupt()
                simulation.wait()
                raise NativeBackgroundCancelled()
            with callback_lock:
                current = latest["percent"]
            if current > reported:
                reported = current
                progress(reported, "Running CAMotics stock simulation")
            time.sleep(0.01)
        simulation.wait()
        _cancel(cancelled)
        progress(86, "Reading CAMotics material surface")
        surface_sha256, facets, bounds = _surface_facts(
            simulation.get_surface("binary")
        )
        progress(89, "Prepared bounded CAMotics result")
        return PreparedCamoticsResult(
            frozen=frozen,
            program_sha256=program.sha256,
            command_count=program.command_count,
            path_step_count=path_steps,
            duration_seconds=duration,
            surface_sha256=surface_sha256,
            surface_facets=facets,
            surface_bounds_mm=bounds,
        )
    except (NativeManufactureError, NativeBackgroundCancelled):
        raise
    except Exception as exc:
        raise NativeManufactureError(
            f"Detached CAMotics simulation failed: {str(exc)[:240]}",
            error_code="NATIVE_MANUFACTURE_CAMOTICS_EXECUTION_FAILED",
        ) from exc


def _launch_project(
    frozen: FrozenCamoticsInput,
    program: CamoticsProgram,
) -> PreparedCamoticsLaunch:
    workspace = tempfile.TemporaryDirectory(prefix="vibecad-camotics-")
    root = Path(workspace.name)
    try:
        os.chmod(root, 0o700)
        program_path = root / "program.nc"
        project_path = root / "project.camotics"
        with program_path.open("wb") as output:
            output.write(program.data)
        tools = {
            str(tool.number): {
                "units": "metric",
                "shape": tool.shape.casefold(),
                "length": tool.length_mm,
                "diameter": tool.diameter_mm,
                "description": tool.label,
            }
            for tool in frozen.tools
        }
        project = {
            "units": "metric",
            "resolution-mode": frozen.resolution,
            "resolution": 1,
            "tools": tools,
            "workpiece": {
                "automatic": False,
                "margin": 0,
                "bounds": {
                    "min": list(frozen.stock_bounds_mm[0]),
                    "max": list(frozen.stock_bounds_mm[1]),
                },
            },
            "files": [program_path.name],
        }
        encoded = json.dumps(
            project,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        with project_path.open("wb") as output:
            output.write(encoded)
        os.chmod(program_path, 0o600)
        os.chmod(project_path, 0o600)
        if not program_path.is_file() or not project_path.is_file():
            _error(
                "The host-owned CAMotics launch workspace is incomplete.",
                "NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_FAILED",
            )
        return PreparedCamoticsLaunch(
            frozen=frozen,
            program_sha256=program.sha256,
            command_count=program.command_count,
            workspace=workspace,
            project_path=project_path,
        )
    except Exception:
        workspace.cleanup()
        raise


def prepare_camotics(
    frozen: FrozenCamoticsInput,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedCamotics:
    """Prepare an exact result or launch bundle without touching the document."""

    if not isinstance(frozen, FrozenCamoticsInput):
        raise TypeError("frozen must be a FrozenCamoticsInput")
    _cancel(cancelled)
    progress(4, "Preparing exact CAMotics program")
    program = _program(frozen, cancelled=cancelled, progress=progress)
    _cancel(cancelled)
    if frozen.request_kind == "read_result":
        return _read_result(
            frozen,
            program,
            cancelled=cancelled,
            progress=progress,
        )
    if frozen.request_kind == "launch":
        progress(85, "Building host-owned CAMotics project")
        prepared = _launch_project(frozen, program)
        progress(89, "Prepared CAMotics launch")
        return prepared
    raise TypeError("Unsupported frozen CAMotics request")


def camotics_result_summary(prepared: PreparedCamoticsResult) -> Mapping[str, Any]:
    return {
        "request": "read_result",
        "job": str(prepared.frozen.job.Name),
        "operation_count": len(prepared.frozen.runs),
        "command_count": prepared.command_count,
        "resolution": prepared.frozen.resolution,
        "path_step_count": prepared.path_step_count,
        "duration_seconds": round(prepared.duration_seconds, 6),
        "surface": {
            "facet_count": prepared.surface_facets,
            "bounds_mm": {
                "min": list(prepared.surface_bounds_mm[0]),
                "max": list(prepared.surface_bounds_mm[1]),
            },
            "sha256": prepared.surface_sha256,
        },
        "program_sha256": prepared.program_sha256,
    }


def cleanup_camotics(prepared: PreparedCamotics | None) -> None:
    if isinstance(prepared, PreparedCamoticsLaunch):
        prepared.cleanup_if_unlaunched()
