# SPDX-License-Identifier: LGPL-2.1-or-later

"""Task-free probe-map parsing and Z-correction path generation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path as FilePath
from typing import Any

import FreeCAD
import Path


MAX_PROBE_BYTES = 16 * 1024 * 1024
MAX_PROBE_POINTS = 100_000
MAX_PROBE_AXIS_VALUES = 2_048
MAX_PROBE_LINE_CHARACTERS = 4_096
MAX_Z_CORRECT_INPUT_COMMANDS = 50_000
MAX_Z_CORRECT_OUTPUT_COMMANDS = 500_000
MAX_Z_CORRECT_COORDINATE_MM = 1_000_000.0
_ROTARY_AXES = frozenset({"A", "B", "C", "U", "V", "W"})


@dataclass(frozen=True, slots=True)
class ProbeGrid:
    rows: tuple[tuple[tuple[float, float, float], ...], ...]
    point_count: int
    x_count: int
    y_count: int
    x_min_mm: float
    x_max_mm: float
    y_min_mm: float
    y_max_mm: float
    z_min_mm: float
    z_max_mm: float
    skipped_line_count: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class FrozenPathCommand:
    name: str
    parameters: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class FrozenToolpath:
    commands: tuple[FrozenPathCommand, ...]
    center_mm: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ZCorrectionDefinition:
    arc_maximum_deflection_mm: float
    line_maximum_segment_length_mm: float


@dataclass(frozen=True, slots=True)
class GeneratedZCorrection:
    path: Any
    input_command_count: int
    output_command_count: int
    corrected_source_move_count: int
    generated_linear_move_count: int
    linearized_arc_count: int
    probe_offset_min_mm: float
    probe_offset_max_mm: float
    probe_bounds_xy_mm: tuple[float, float, float, float]


def _probe_number(token: str, line_number: int) -> float:
    cleaned = token.strip().strip("[]()")
    if "," in cleaned and "." not in cleaned and cleaned.count(",") == 1:
        cleaned = cleaned.replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"Probe line {line_number} contains a nonnumeric XYZ value"
        ) from exc
    if not math.isfinite(value) or abs(value) > MAX_Z_CORRECT_COORDINATE_MM:
        raise ValueError(
            f"Probe line {line_number} contains a nonfinite or out-of-range XYZ value"
        )
    return value


def parse_probe_bytes(value: bytes, *, strict: bool = True) -> ProbeGrid:
    """Parse a bounded LinuxCNC-style XYZ probe log into one rectangular grid."""

    if not isinstance(value, bytes):
        raise TypeError("probe content must be bytes")
    if len(value) > MAX_PROBE_BYTES:
        raise ValueError(
            f"Probe content exceeds the {MAX_PROBE_BYTES}-byte safety limit"
        )
    digest = hashlib.sha256(value).hexdigest()
    try:
        text = value.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Probe content must be UTF-8 text") from exc
    if "\x00" in text:
        raise ValueError("Probe content contains a NUL byte")

    points: list[tuple[float, float, float]] = []
    skipped: list[int] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        if len(raw) > MAX_PROBE_LINE_CHARACTERS:
            raise ValueError(
                f"Probe line {line_number} exceeds {MAX_PROBE_LINE_CHARACTERS} characters"
            )
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        for marker in ("#", ";"):
            if marker in line:
                line = line.split(marker, 1)[0].strip()
        tokens = line.split()
        if len(tokens) < 3:
            if strict:
                raise ValueError(
                    f"Probe line {line_number} must contain at least X Y Z"
                )
            skipped.append(line_number)
            continue
        try:
            point = tuple(
                _probe_number(tokens[index], line_number) for index in range(3)
            )
        except ValueError:
            if strict:
                raise
            skipped.append(line_number)
            continue
        points.append(point)
        if len(points) > MAX_PROBE_POINTS:
            raise ValueError(
                f"Probe content exceeds the {MAX_PROBE_POINTS}-point safety limit"
            )

    if len(points) < 4:
        raise ValueError("Probe content requires at least four usable XYZ points")
    if len(set((x, y) for x, y, _z in points)) != len(points):
        raise ValueError("Probe content contains duplicate XY locations")
    x_values = tuple(sorted({x for x, _y, _z in points}))
    y_values = tuple(sorted({y for _x, y, _z in points}))
    if (
        len(x_values) < 2
        or len(y_values) < 2
        or len(x_values) > MAX_PROBE_AXIS_VALUES
        or len(y_values) > MAX_PROBE_AXIS_VALUES
    ):
        raise ValueError(
            "Probe content requires 2 to "
            f"{MAX_PROBE_AXIS_VALUES} distinct X and Y coordinates"
        )
    if len(points) != len(x_values) * len(y_values):
        raise ValueError(
            "Probe content must form one complete rectangular XY grid; "
            f"expected {len(x_values) * len(y_values)} points but found {len(points)}"
        )
    by_xy = {(x, y): z for x, y, z in points}
    rows = tuple(
        tuple((x, y, by_xy[(x, y)]) for x in x_values) for y in y_values
    )
    z_values = tuple(z for _x, _y, z in points)
    return ProbeGrid(
        rows=rows,
        point_count=len(points),
        x_count=len(x_values),
        y_count=len(y_values),
        x_min_mm=x_values[0],
        x_max_mm=x_values[-1],
        y_min_mm=y_values[0],
        y_max_mm=y_values[-1],
        z_min_mm=min(z_values),
        z_max_mm=max(z_values),
        skipped_line_count=len(skipped),
        content_sha256=digest,
    )


def read_probe_file(path: str, *, strict: bool = False) -> ProbeGrid:
    """Read a human-selected probe file for the shipped task editor."""

    file_path = FilePath(str(path))
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise ValueError("Probe file is not available") from exc
    if size > MAX_PROBE_BYTES:
        raise ValueError(
            f"Probe file exceeds the {MAX_PROBE_BYTES}-byte safety limit"
        )
    try:
        content = file_path.read_bytes()
    except OSError as exc:
        raise ValueError("Probe file could not be read") from exc
    return parse_probe_bytes(content, strict=strict)


def build_interpolation_surface(grid: ProbeGrid):
    """Build one detached B-spline surface for a validated probe grid."""

    if not isinstance(grid, ProbeGrid):
        raise TypeError("grid must be a ProbeGrid")
    import Part

    poles = [
        [FreeCAD.Vector(x, y, z) for x, y, z in row] for row in grid.rows
    ]
    surface = Part.BSplineSurface()
    try:
        surface.interpolate(poles)
        shape = surface.toShape()
    except Exception as exc:
        raise ValueError("Probe grid could not produce an interpolation surface") from exc
    if shape.isNull() or not shape.Faces:
        raise ValueError("Probe grid produced no usable interpolation face")
    return shape


def _frozen_parameter(value: Any, command_name: str, parameter_name: str) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(
                f"Toolpath command {command_name} parameter {parameter_name} is nonfinite"
            )
        return result
    raise ValueError(
        f"Toolpath command {command_name} parameter {parameter_name} cannot be "
        "detached safely"
    )


def freeze_toolpath(path: Any, *, maximum_commands: int | None = None) -> FrozenToolpath:
    """Detach a command stream and rotary center from a live Path value."""

    commands = tuple(getattr(path, "Commands", ()) or ())
    if maximum_commands is not None and len(commands) > maximum_commands:
        raise ValueError(
            f"Z correction input has {len(commands)} commands; the safety limit is "
            f"{maximum_commands}"
        )
    frozen_commands = []
    for command in commands:
        name = str(command.Name)
        parameters = tuple(
            (
                str(parameter_name),
                _frozen_parameter(value, name, str(parameter_name)),
            )
            for parameter_name, value in dict(command.Parameters).items()
        )
        frozen_commands.append(FrozenPathCommand(name, parameters))
    frozen = tuple(frozen_commands)
    center = tuple(float(value) for value in path.Center)
    if len(center) != 3 or not all(math.isfinite(value) for value in center):
        raise ValueError("Toolpath rotary center is invalid")
    return FrozenToolpath(frozen, center)


def _thaw_command(command: FrozenPathCommand):
    return Path.Command(command.name, dict(command.parameters))


def _arc_point_count(edge: Any, maximum_deflection: float) -> int:
    radius = float(getattr(edge.Curve, "Radius", 0.0) or 0.0)
    if not math.isfinite(radius) or radius <= 0.0:
        raise ValueError("Z correction encountered a zero-radius cutting arc")
    sweep = float(edge.Length) / radius
    if maximum_deflection >= radius:
        step = math.pi
    else:
        cosine = max(-1.0, min(1.0, 1.0 - maximum_deflection / radius))
        step = 2.0 * math.acos(cosine)
    if not math.isfinite(step) or step <= 0.0:
        raise ValueError("Z correction arc deflection is too small for this radius")
    return max(2, int(math.ceil(sweep / step)) + 1)


def _line_point_count(edge: Any, maximum_length: float) -> int:
    return max(2, int(math.ceil(float(edge.Length) / maximum_length)) + 1)


def _surface_z(surface: Any, x: float, y: float, z_min: float, z_max: float) -> float:
    import Part

    span = max(1.0, abs(z_min), abs(z_max), abs(z_max - z_min))
    line = Part.Line(
        FreeCAD.Vector(x, y, z_max + 2.0 * span),
        FreeCAD.Vector(x, y, z_min - 2.0 * span),
    )
    points, _parameters = line.intersectCS(surface)
    values = [float(point.Z) for point in points if math.isfinite(float(point.Z))]
    if not values:
        raise ValueError(
            f"Probe surface has no correction value at ({x:.6g}, {y:.6g})"
        )
    return min(values, key=lambda value: abs(value - (z_min + z_max) / 2.0))


def validate_definition(definition: ZCorrectionDefinition) -> ZCorrectionDefinition:
    """Return one finite, physically bounded interpolation definition."""

    if not isinstance(definition, ZCorrectionDefinition):
        raise TypeError("definition must be a ZCorrectionDefinition")
    arc = float(definition.arc_maximum_deflection_mm)
    segment = float(definition.line_maximum_segment_length_mm)
    if not math.isfinite(arc) or not 0.001 <= arc <= 1_000_000.0:
        raise ValueError(
            "Z correction arc maximum deflection must be between 0.001 and 1,000,000 mm"
        )
    if not math.isfinite(segment) or not 0.001 <= segment <= 1_000_000.0:
        raise ValueError(
            "Z correction line maximum segment length must be between 0.001 and 1,000,000 mm"
        )
    return ZCorrectionDefinition(arc, segment)


def generate_corrected_path(
    source: FrozenToolpath,
    interpolation_shape: Any,
    definition: ZCorrectionDefinition,
    *,
    maximum_output_commands: int = MAX_Z_CORRECT_OUTPUT_COMMANDS,
) -> GeneratedZCorrection:
    """Generate a fully detached, bounded Z-corrected linear toolpath."""

    if not isinstance(source, FrozenToolpath):
        raise TypeError("source must be a FrozenToolpath")
    definition = validate_definition(definition)
    if (
        type(maximum_output_commands) is not int
        or maximum_output_commands < 1
        or maximum_output_commands > MAX_Z_CORRECT_OUTPUT_COMMANDS
    ):
        raise ValueError(
            "Z correction output command limit must be between 1 and "
            f"{MAX_Z_CORRECT_OUTPUT_COMMANDS}"
        )
    if interpolation_shape is None or interpolation_shape.isNull():
        raise ValueError("Z correction requires a nonempty interpolation surface")
    try:
        face = interpolation_shape.toNurbs().Faces[0]
        surface = face.Surface
    except Exception as exc:
        raise ValueError("Z correction interpolation surface is invalid") from exc
    bounds = face.BoundBox
    x_min, x_max = float(bounds.XMin), float(bounds.XMax)
    y_min, y_max = float(bounds.YMin), float(bounds.YMax)
    z_min, z_max = float(bounds.ZMin), float(bounds.ZMax)
    tolerance = max(float(Path.Geom.Tolerance), 1.0e-9)

    output = []
    current = {"X": 0.0, "Y": 0.0, "Z": 0.0, "F": 0.0}
    corrected_moves = 0
    generated_moves = 0
    linearized_arcs = 0
    offsets: list[float] = []

    def append(command: Any) -> None:
        if len(output) >= maximum_output_commands:
            raise ValueError(
                "Z correction exceeds the output command safety limit of "
                f"{maximum_output_commands}"
            )
        output.append(command)

    for frozen in source.commands:
        command = _thaw_command(frozen)
        parameters = dict(command.Parameters)
        name = str(command.Name)
        if name == "G91":
            raise ValueError("Z correction does not accept relative-coordinate G91 paths")
        if name not in Path.Geom.CmdMoveMill:
            append(command)
            current.update(parameters)
            continue
        if _ROTARY_AXES.intersection(parameters):
            raise ValueError(
                "Z correction accepts only three-axis XYZ cutting moves; rotary cutting "
                "coordinates were found"
            )
        start = FreeCAD.Vector(current["X"], current["Y"], current["Z"])
        edge = Path.Geom.edgeForCmd(command, start)
        if edge is None:
            if not {"X", "Y", "Z"}.intersection(parameters):
                append(command)
                current.update(parameters)
                continue
            raise ValueError(f"Z correction could not resolve cutting command {name}")
        if name in Path.Geom.CmdMoveArc:
            point_count = _arc_point_count(
                edge,
                definition.arc_maximum_deflection_mm,
            )
            linearized_arcs += 1
        else:
            point_count = _line_point_count(
                edge,
                definition.line_maximum_segment_length_mm,
            )
        if len(output) + point_count > maximum_output_commands:
            raise ValueError(
                "Z correction exceeds the output command safety limit of "
                f"{maximum_output_commands}"
            )
        points = edge.discretize(Number=point_count)
        if len(points) != point_count:
            raise ValueError("Z correction discretization returned an unexpected point count")
        for point in points:
            if (
                point.x < x_min - tolerance
                or point.x > x_max + tolerance
                or point.y < y_min - tolerance
                or point.y > y_max + tolerance
            ):
                raise ValueError(
                    "Cutting path point "
                    f"({point.x:.6g}, {point.y:.6g}) lies outside probe bounds "
                    f"X[{x_min:.6g}, {x_max:.6g}] Y[{y_min:.6g}, {y_max:.6g}]"
                )
            offset = _surface_z(surface, point.x, point.y, z_min, z_max)
            corrected = {
                "X": point.x,
                "Y": point.y,
                "Z": point.z + offset,
            }
            if "F" in parameters:
                corrected["F"] = parameters["F"]
            append(Path.Command("G1", corrected))
            offsets.append(offset)
            generated_moves += 1
        current.update(parameters)
        corrected_moves += 1

    if not offsets:
        raise ValueError("Z correction found no cutting motion to correct")
    result = Path.Path(output)
    result.Center = FreeCAD.Vector(*source.center_mm)
    return GeneratedZCorrection(
        path=result,
        input_command_count=len(source.commands),
        output_command_count=len(output),
        corrected_source_move_count=corrected_moves,
        generated_linear_move_count=generated_moves,
        linearized_arc_count=linearized_arcs,
        probe_offset_min_mm=min(offsets),
        probe_offset_max_mm=max(offsets),
        probe_bounds_xy_mm=(x_min, y_min, x_max, y_max),
    )
