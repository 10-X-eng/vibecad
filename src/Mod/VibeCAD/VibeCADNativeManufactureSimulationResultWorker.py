# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached, cancellable voxel execution for retained CAM simulation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Callable, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureSimulationResultInput import (
    FrozenNativeSimulation,
    canonical_simulation_command,
    is_canned_simulation_command,
    is_motion_simulation_command,
    is_non_geometric_simulation_command,
)


@dataclass(frozen=True, slots=True)
class PreparedNativeSimulation:
    frozen: FrozenNativeSimulation
    mesh: Any
    mesh_points: int
    mesh_facets: int
    mesh_bounds_mm: tuple[tuple[float, float, float], tuple[float, float, float]]
    statistics: Mapping[str, Any]
    program_sha256: str
    executed_command_count: int


def _error(message: str, code: str) -> None:
    raise NativeManufactureError(message, error_code=code)


def _cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise NativeBackgroundCancelled()


def _finite_parameters(command: Any, operation_name: str, source_index: int) -> dict[str, Any]:
    try:
        parameters = {
            str(key).upper(): value for key, value in dict(command.Parameters).items()
        }
    except Exception as exc:
        raise NativeManufactureError(
            f"Placed command {source_index} in CAM operation {operation_name!r} "
            "has unreadable parameters.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        ) from exc
    for key, value in parameters.items():
        if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
            continue
        try:
            number = float(getattr(value, "Value", value))
        except (TypeError, ValueError) as exc:
            raise NativeManufactureError(
                f"Placed command {source_index} parameter {key!r} in CAM operation "
                f"{operation_name!r} is not finite.",
                error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            ) from exc
        if not math.isfinite(number):
            _error(
                f"Placed command {source_index} parameter {key!r} in CAM operation "
                f"{operation_name!r} is not finite.",
                "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            )
    return parameters


def _expanded_cycle(
    command: Any,
    *,
    operation_name: str,
    source_index: int,
    include_initial_retract: bool,
) -> tuple[Any, ...]:
    import Path

    parameters = _finite_parameters(command, operation_name, source_index)
    missing = [name for name in ("X", "Y", "Z", "R") if parameters.get(name) is None]
    if missing:
        _error(
            f"Canned cycle {canonical_simulation_command(command.Name)} at command "
            f"{source_index} in CAM operation {operation_name!r} is missing: "
            f"{', '.join(missing)}.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    x = float(parameters["X"])
    y = float(parameters["Y"])
    z = float(parameters["Z"])
    retract = float(parameters["R"])
    result = []
    if include_initial_retract:
        result.append(Path.Command("G0", {"Z": retract}))
    result.extend(
        (
            Path.Command("G0", {"X": x, "Y": y, "Z": retract}),
            Path.Command("G1", {"X": x, "Y": y, "Z": z}),
            Path.Command("G1", {"X": x, "Y": y, "Z": retract}),
        )
    )
    return tuple(result)


def _mesh_bounds(mesh: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    box = mesh.BoundBox
    values = tuple(
        float(getattr(box, name))
        for name in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
    )
    if not bool(box.isValid()) or any(not math.isfinite(value) for value in values):
        _error(
            "Native CAM simulation produced a Mesh with invalid bounds.",
            "NATIVE_MANUFACTURE_SIMULATION_RESULT_INVALID",
        )
    return values[:3], values[3:]


def _validate_statistics(statistics: Mapping[str, Any]) -> None:
    required = {
        "resolution_mm",
        "grid",
        "initial_volume_mm3",
        "removed_volume_mm3",
        "remaining_volume_mm3",
        "modified_cells",
        "cut_commands",
        "rapid_commands",
        "unsupported_commands",
    }
    if not isinstance(statistics, Mapping) or not required.issubset(statistics):
        _error(
            "Native CAM simulation returned incomplete stock-removal statistics.",
            "NATIVE_MANUFACTURE_SIMULATION_RESULT_INVALID",
        )
    if int(statistics.get("unsupported_commands", -1)) != 0:
        _error(
            "Native CAM simulation received an unsupported executable command.",
            "NATIVE_MANUFACTURE_TOOLPATH_UNSUPPORTED",
        )
    for name in (
        "resolution_mm",
        "initial_volume_mm3",
        "removed_volume_mm3",
        "remaining_volume_mm3",
    ):
        try:
            value = float(statistics[name])
        except (TypeError, ValueError) as exc:
            raise NativeManufactureError(
                f"Native CAM simulation returned invalid {name}.",
                error_code="NATIVE_MANUFACTURE_SIMULATION_RESULT_INVALID",
            ) from exc
        if not math.isfinite(value) or value < 0.0:
            _error(
                f"Native CAM simulation returned invalid {name}.",
                "NATIVE_MANUFACTURE_SIMULATION_RESULT_INVALID",
            )


def execute_native_simulation(
    frozen: FrozenNativeSimulation,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedNativeSimulation:
    """Run detached height-field cutting while native calls release the GIL."""

    if not isinstance(frozen, FrozenNativeSimulation):
        raise TypeError("frozen must be a FrozenNativeSimulation")
    try:
        import FreeCAD
        import Path
        import PathScripts.PathUtils as PathUtils
        import PathSimulator

        _cancel(cancelled)
        progress(4, "Initializing detached stock")
        simulator = PathSimulator.PathSim()
        simulator.BeginSimulation(frozen.stock_shape, frozen.stock_resolution_mm)
        _cancel(cancelled)

        digest = hashlib.sha256()
        digest.update(f"quality:{frozen.quality}\n".encode("ascii"))
        digest.update(
            f"stock-resolution:{frozen.stock_resolution_mm:.17g}\n".encode("ascii")
        )
        processed_sources = 0
        executed = 0
        total_sources = max(1, frozen.source_command_count)
        for run_index, run in enumerate(frozen.runs):
            _cancel(cancelled)
            progress(
                8 + int(70 * processed_sources / total_sources),
                f"Simulating operation {run_index + 1} of {len(frozen.runs)}",
            )
            simulator.SetToolShape(run.tool_shape, frozen.tool_resolution_mm)
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
            placed_commands = tuple(placed.Commands)
            if len(placed_commands) != len(run.commands):
                _error(
                    f"CAM operation {run.operation_name!r} changed command count during "
                    "detached placement.",
                    "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
                )
            digest.update(f"operation:{run.operation_name}\n".encode("utf-8"))
            digest.update(f"tool:{run.tool_number}\n".encode("ascii"))
            position = FreeCAD.Placement(
                FreeCAD.Vector(0.0, 0.0, frozen.stock_z_max_mm),
                FreeCAD.Rotation(),
            )
            first_cycle = True
            for source_index, command in enumerate(placed_commands):
                _cancel(cancelled)
                name = canonical_simulation_command(command.Name)
                gcode = str(command.toGCode()).encode("utf-8")
                digest.update(len(gcode).to_bytes(8, "big"))
                digest.update(gcode)
                if is_motion_simulation_command(name):
                    position = simulator.ApplyCommand(position, command)
                    executed += 1
                    first_cycle = True
                elif is_canned_simulation_command(name):
                    for expanded in _expanded_cycle(
                        command,
                        operation_name=run.operation_name,
                        source_index=source_index,
                        include_initial_retract=first_cycle,
                    ):
                        _cancel(cancelled)
                        position = simulator.ApplyCommand(position, expanded)
                        executed += 1
                    first_cycle = False
                elif name == "G80":
                    first_cycle = True
                elif not is_non_geometric_simulation_command(name):
                    _error(
                        f"CAM operation {run.operation_name!r} command {source_index} "
                        f"{name!r} became unsupported during detached execution.",
                        "NATIVE_MANUFACTURE_TOOLPATH_UNSUPPORTED",
                    )
                processed_sources += 1
                if processed_sources % 256 == 0:
                    progress(
                        8 + int(70 * processed_sources / total_sources),
                        f"Simulated {processed_sources} of {frozen.source_command_count} commands",
                    )

        _cancel(cancelled)
        progress(82, "Tessellating retained material")
        result_mesh = simulator.GetCombinedResultMesh()
        _cancel(cancelled)
        mesh_points = int(result_mesh.CountPoints)
        mesh_facets = int(result_mesh.CountFacets)
        if mesh_points < 3 or mesh_facets < 1:
            _error(
                "Native CAM simulation produced an empty material-result Mesh.",
                "NATIVE_MANUFACTURE_SIMULATION_RESULT_INVALID",
            )
        bounds = _mesh_bounds(result_mesh)
        statistics = dict(simulator.GetSimulationStats())
        _validate_statistics(statistics)
        if executed != (
            int(statistics.get("cut_commands", 0))
            + int(statistics.get("rapid_commands", 0))
        ):
            _error(
                "Native CAM simulation command accounting is inconsistent.",
                "NATIVE_MANUFACTURE_SIMULATION_RESULT_INVALID",
            )
        progress(89, "Prepared retained material result")
        return PreparedNativeSimulation(
            frozen=frozen,
            mesh=result_mesh,
            mesh_points=mesh_points,
            mesh_facets=mesh_facets,
            mesh_bounds_mm=bounds,
            statistics=statistics,
            program_sha256=digest.hexdigest(),
            executed_command_count=executed,
        )
    except NativeManufactureError:
        raise
    except NativeBackgroundCancelled:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            f"Detached native CAM simulation failed: {str(exc)[:240]}",
            error_code="NATIVE_MANUFACTURE_SIMULATION_EXECUTION_FAILED",
        ) from exc
