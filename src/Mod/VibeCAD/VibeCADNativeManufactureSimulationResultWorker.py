# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached, cancellable voxel execution for retained CAM simulation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from types import SimpleNamespace
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
    stock_shape: Any
    stock_shape_type: str
    stock_solid_count: int
    stock_shape_sha256: str
    verification: Mapping[str, Any]
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


def _tool_proxy(run: Any) -> Any:
    parameters = dict(run.tool_parameters)
    return SimpleNamespace(
        Name=run.operation_name,
        ShapeID=run.tool_shape_id,
        PropertiesList=tuple(parameters),
        **parameters,
    )


def _collision_bounds(shape: Any) -> dict[str, list[float]] | None:
    if shape is None or shape.isNull():
        return None
    box = shape.BoundBox
    values = tuple(
        float(getattr(box, name))
        for name in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
    )
    if not bool(box.isValid()) or any(not math.isfinite(value) for value in values):
        return None
    return {"min": list(values[:3]), "max": list(values[3:])}


def _machine_travel_report(
    frozen: FrozenNativeSimulation,
    path_extents: Mapping[str, tuple[float, float]],
) -> dict[str, Any]:
    axes = []
    violations = []
    all_checked = bool(frozen.machine_axes)
    for configured in frozen.machine_axes:
        limits = {
            "unit": "mm" if configured.kind == "linear" else "degrees",
            "machine_min": round(configured.minimum, 9),
            "machine_max": round(configured.maximum, 9),
            "machine_span": round(configured.maximum - configured.minimum, 9),
        }
        extent = path_extents.get(configured.name)
        if extent is None:
            all_checked = False
            axes.append(
                {
                    "axis": configured.name,
                    "kind": configured.kind,
                    "checked": False,
                    **limits,
                }
            )
            continue
        path_min, path_max = extent
        path_span = path_max - path_min
        within = path_span <= configured.maximum - configured.minimum + 1.0e-9
        record = {
            "axis": configured.name,
            "kind": configured.kind,
            "checked": True,
            "path_min": round(path_min, 9),
            "path_max": round(path_max, 9),
            "path_span": round(path_span, 9),
            **limits,
            "span_within_limits": within,
        }
        axes.append(record)
        if not within:
            violations.append(record)
    return {
        "machine": frozen.machine_name,
        "configured": bool(frozen.machine_name),
        "axis_span_checked": all_checked,
        "fits_axis_spans": (
            not violations if all_checked else (False if violations else None)
        ),
        "position_checked": False,
        "axes": axes,
        "violations": violations,
    }


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
        import Part
        import Path.Main.Simulation as NativeSimulation
        import PathScripts.PathUtils as PathUtils
        import PathSimulator

        _cancel(cancelled)
        progress(4, "Initializing detached stock")
        simulator = PathSimulator.PathSim()
        simulator.BeginSimulation(frozen.stock_shape, frozen.stock_resolution_mm)
        protected_model = Part.makeCompound(list(frozen.protected_model_shapes))
        if protected_model.isNull() or not protected_model.isValid():
            _error(
                "Native CAM verification could not build the protected model.",
                "NATIVE_MANUFACTURE_SIMULATION_RESULT_INVALID",
            )
        _cancel(cancelled)

        digest = hashlib.sha256()
        digest.update(f"quality:{frozen.quality}\n".encode("ascii"))
        digest.update(
            f"stock-resolution:{frozen.stock_resolution_mm:.17g}\n".encode("ascii")
        )
        processed_sources = 0
        executed = 0
        collision_count = 0
        collision_records = []
        rapid_collision_count = 0
        rapid_collision_records = []
        path_extents: dict[str, tuple[float, float]] = {}
        maximum_collision_records = 32
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
            for axis_name, coordinate in (
                ("X", float(position.Base.x)),
                ("Y", float(position.Base.y)),
                ("Z", float(position.Base.z)),
            ):
                previous = path_extents.get(axis_name, (coordinate, coordinate))
                path_extents[axis_name] = (
                    min(previous[0], coordinate),
                    max(previous[1], coordinate),
                )
            first_cycle = True

            def apply(command: Any, source_index: int, *, rapid: bool) -> None:
                nonlocal position, executed, collision_count, rapid_collision_count
                _cancel(cancelled)
                try:
                    path_edge = Path.Geom.edgeForCmd(command, FreeCAD.Vector(position.Base))
                    sweep, _end = NativeSimulation._swept_tool(
                        _tool_proxy(run),
                        command,
                        FreeCAD.Vector(position.Base),
                    )
                except Exception as exc:
                    raise NativeManufactureError(
                        f"CAM operation {run.operation_name!r} command "
                        f"{source_index} could not be verified: {exc}",
                        error_code="NATIVE_MANUFACTURE_VERIFICATION_FAILED",
                    ) from exc
                if path_edge is not None:
                    box = path_edge.BoundBox
                    for axis_name, minimum, maximum in (
                        ("X", float(box.XMin), float(box.XMax)),
                        ("Y", float(box.YMin), float(box.YMax)),
                        ("Z", float(box.ZMin), float(box.ZMax)),
                    ):
                        previous = path_extents.get(axis_name, (minimum, maximum))
                        path_extents[axis_name] = (
                            min(previous[0], minimum),
                            max(previous[1], maximum),
                        )
                if sweep is not None:
                    overlap = sweep.common(protected_model)
                    volume = float(overlap.Volume) if not overlap.isNull() else 0.0
                    if volume > 1.0e-7:
                        collision_count += 1
                        record = {
                            "operation": run.operation_name,
                            "command_index": source_index,
                            "command": canonical_simulation_command(command.Name),
                            "rapid": rapid,
                            "volume_mm3": round(volume, 9),
                            "bounds_mm": _collision_bounds(overlap),
                        }
                        if len(collision_records) < maximum_collision_records:
                            collision_records.append(record)
                        if rapid:
                            rapid_collision_count += 1
                            if len(rapid_collision_records) < maximum_collision_records:
                                rapid_collision_records.append(record)
                position = simulator.ApplyCommand(position, command)
                executed += 1

            for source_index, command in enumerate(placed_commands):
                _cancel(cancelled)
                name = canonical_simulation_command(command.Name)
                gcode = str(command.toGCode()).encode("utf-8")
                digest.update(len(gcode).to_bytes(8, "big"))
                digest.update(gcode)
                if is_motion_simulation_command(name):
                    apply(command, source_index, rapid=name == "G0")
                    first_cycle = True
                elif is_canned_simulation_command(name):
                    for expanded in _expanded_cycle(
                        command,
                        operation_name=run.operation_name,
                        source_index=source_index,
                        include_initial_retract=first_cycle,
                    ):
                        apply(
                            expanded,
                            source_index,
                            rapid=canonical_simulation_command(expanded.Name) == "G0",
                        )
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
        progress(85, "Building solid retained stock")
        stock_shape = simulator.GetRemainingStockShape()
        stock_solids = tuple(getattr(stock_shape, "Solids", ()) or ())
        stock_shape_type = str(getattr(stock_shape, "ShapeType", "") or "")
        if (
            stock_shape is None
            or stock_shape.isNull()
            or not stock_shape.isValid()
            or stock_shape_type not in {"Solid", "CompSolid", "Compound"}
            or not stock_solids
            or any(solid.isNull() or not solid.isValid() for solid in stock_solids)
        ):
            _error(
                "Native CAM simulation produced no valid solid retained stock.",
                "NATIVE_MANUFACTURE_SIMULATION_RESULT_INVALID",
            )
        stock_shape_sha256 = hashlib.sha256(
            stock_shape.exportBrepToString().encode("utf-8")
        ).hexdigest()
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
        cycle_time_operations = [
            {
                "operation": run.operation_name,
                "seconds": run.cycle_time_seconds,
                "rapid_speed_fallback": run.cycle_time_rapid_fallback,
            }
            for run in frozen.runs
            if run.cycle_time_seconds is not None
        ]
        missing_cycle_time = [
            run.operation_name
            for run in frozen.runs
            if run.cycle_time_seconds is None
        ]
        unavailable_checks = [
            "holder_collision",
            "fixture_collision",
            "rapid_clearance_current_stock",
        ]
        machine_travel = _machine_travel_report(frozen, path_extents)
        if not frozen.machine_name:
            unavailable_checks.append("machine_travel")
        else:
            unavailable_checks.append("machine_travel_position")
            if not machine_travel["axis_span_checked"]:
                unavailable_checks.append("machine_travel_axis_span")
        if missing_cycle_time:
            unavailable_checks.append("cycle_time")
        verification = {
            "protected_model": {
                "checked": True,
                "collision": collision_count > 0,
                "collision_command_count": collision_count,
                "collisions": collision_records,
                "collisions_truncated": collision_count > len(collision_records),
            },
            "rapid_clearance": {
                "protected_model_checked": True,
                "protected_model_collision": rapid_collision_count > 0,
                "collision_command_count": rapid_collision_count,
                "collisions": rapid_collision_records,
                "collisions_truncated": rapid_collision_count
                > len(rapid_collision_records),
                "current_stock_checked": False,
            },
            "cycle_time": {
                "complete": not missing_cycle_time,
                "method": "CAM Path estimates from setup feeds and rapids",
                "total_seconds": round(
                    sum(float(value["seconds"]) for value in cycle_time_operations),
                    6,
                ),
                "operations": cycle_time_operations,
                "missing_operations": missing_cycle_time,
            },
            "machine_travel": machine_travel,
            "unavailable_checks": unavailable_checks,
        }
        return PreparedNativeSimulation(
            frozen=frozen,
            mesh=result_mesh,
            mesh_points=mesh_points,
            mesh_facets=mesh_facets,
            mesh_bounds_mm=bounds,
            stock_shape=stock_shape,
            stock_shape_type=stock_shape_type,
            stock_solid_count=len(stock_solids),
            stock_shape_sha256=stock_shape_sha256,
            verification=verification,
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
