# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded, task-free CAM Ramp Entry generation shared by human and Native mode."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import math
from typing import Any

import FreeCAD
import Path
import Path.Base.Util as PathUtil
import Path.Dressup.Utils as PathDressup
import PathScripts.PathUtils as PathUtils
from PySide.QtCore import QT_TRANSLATE_NOOP


MAX_RAMP_ENTRY_INPUT_COMMANDS = 50_000
MAX_RAMP_ENTRY_OUTPUT_COMMANDS = 500_000
MAX_RAMP_ENTRY_GENERATION_UNITS = 1_000_000
MAX_RAMP_ENTRY_SCAN_UNITS = 5_000_000
MIN_RAMP_ANGLE_DEGREES = 0.1
MAX_RAMP_ANGLE_DEGREES = 89.9
MAX_ABSOLUTE_START_DEPTH_MM = 1_000_000.0
RAMP_METHODS = frozenset(("RampMethod1", "RampMethod2", "RampMethod3", "Helix"))
_EPSILON = 1.0e-9


@dataclass(frozen=True, slots=True)
class RampFeedRates:
    horizontal_feed: float
    vertical_feed: float
    ramp_feed: float
    horizontal_rapid: float
    vertical_rapid: float


@dataclass(frozen=True, slots=True)
class RampDefinition:
    method: str
    angle_from_vertical_degrees: float
    start_depth_mm: float | None


def _finite(value: Any, noun: str) -> float:
    result = float(getattr(value, "Value", value))
    if not math.isfinite(result):
        raise ValueError(f"{noun} must be finite")
    return result


def normalize_definition(definition: RampDefinition) -> RampDefinition:
    if not isinstance(definition, RampDefinition):
        raise TypeError("Ramp Entry definition must be a RampDefinition")
    method = str(definition.method or "")
    if method not in RAMP_METHODS:
        raise ValueError("Ramp Entry method is not one of the four shipped methods")
    angle = _finite(
        definition.angle_from_vertical_degrees,
        "Ramp Entry angle",
    )
    if not MIN_RAMP_ANGLE_DEGREES <= angle <= MAX_RAMP_ANGLE_DEGREES:
        raise ValueError(
            f"Ramp Entry angle must be between {MIN_RAMP_ANGLE_DEGREES:g} and "
            f"{MAX_RAMP_ANGLE_DEGREES:g} degrees from vertical"
        )
    start_depth = definition.start_depth_mm
    if start_depth is not None:
        start_depth = _finite(start_depth, "Ramp Entry start depth")
        if abs(start_depth) > MAX_ABSOLUTE_START_DEPTH_MM:
            raise ValueError(
                "Ramp Entry start depth exceeds the supported absolute limit"
            )
    return RampDefinition(method, angle, start_depth)


def normalize_feed_rates(feed_rates: RampFeedRates) -> RampFeedRates:
    if not isinstance(feed_rates, RampFeedRates):
        raise TypeError("Ramp Entry feed rates must be RampFeedRates")
    values = {
        field: _finite(getattr(feed_rates, field), f"Ramp Entry {field}")
        for field in RampFeedRates.__dataclass_fields__
    }
    if any(value < 0.0 for value in values.values()):
        raise ValueError("Ramp Entry feed and rapid rates cannot be negative")
    return RampFeedRates(**values)


def feed_rates_from_base(base: Any) -> RampFeedRates:
    controller = PathDressup.toolController(base)
    if controller is None:
        raise ValueError("Ramp Entry requires one exact tool controller")
    return normalize_feed_rates(
        RampFeedRates(
            horizontal_feed=controller.HorizFeed.Value,
            vertical_feed=controller.VertFeed.Value,
            ramp_feed=controller.RampFeed.Value,
            horizontal_rapid=controller.HorizRapid.Value,
            vertical_rapid=controller.VertRapid.Value,
        )
    )


def _copy_command(command: Any) -> Any:
    return Path.Command(str(command.Name), dict(command.Parameters))


def _path_with_job_center(owner: Any, commands=()) -> Any:
    path = Path.Path(list(commands)) if commands else Path.Path()
    job = None
    for candidate in (owner, getattr(owner, "Base", None)):
        if candidate is None:
            continue
        try:
            job = PathUtils.findParentJob(candidate) or PathUtil.timelineParentJob(
                candidate
            )
        except (AttributeError, TypeError):
            job = None
        if job is not None:
            break
    if job is not None:
        path.Center = job.Path.Center
    return path


class AnnotatedGCode:
    """One detached command with explicit modal start and end coordinates."""

    def __init__(self, command: Any, start_point: tuple[float, float, float]):
        self.start_point = tuple(float(value) for value in start_point)
        self.command = _copy_command(command)
        parameters = self.command.Parameters
        self.end_point = (
            _finite(parameters.get("X", self.start_point[0]), "Ramp Entry X"),
            _finite(parameters.get("Y", self.start_point[1]), "Ramp Entry Y"),
            _finite(parameters.get("Z", self.start_point[2]), "Ramp Entry Z"),
        )
        self.is_line = self.command.Name in Path.Geom.CmdMoveStraight
        self.is_arc = self.command.Name in Path.Geom.CmdMoveArc
        self.xy_length: float | None = None
        if self.is_line:
            self.xy_length = math.hypot(
                self.start_point[0] - self.end_point[0],
                self.start_point[1] - self.end_point[1],
            )
        elif self.is_arc:
            self.center_xy = (
                self.start_point[0]
                + _finite(parameters.get("I", 0.0), "Ramp Entry arc I"),
                self.start_point[1]
                + _finite(parameters.get("J", 0.0), "Ramp Entry arc J"),
            )
            self.start_angle = math.atan2(
                self.start_point[1] - self.center_xy[1],
                self.start_point[0] - self.center_xy[0],
            )
            self.end_angle = math.atan2(
                self.end_point[1] - self.center_xy[1],
                self.end_point[0] - self.center_xy[0],
            )
            if (
                self.command.Name in Path.Geom.CmdMoveCCW
                and self.end_angle < self.start_angle
            ):
                self.end_angle += 2.0 * math.pi
            if (
                self.command.Name in Path.Geom.CmdMoveCW
                and self.end_angle > self.start_angle
            ):
                self.end_angle -= 2.0 * math.pi
            self.radius = math.hypot(
                self.start_point[0] - self.center_xy[0],
                self.start_point[1] - self.center_xy[1],
            )
            if self.radius <= _EPSILON:
                raise ValueError("Ramp Entry encountered a zero-radius arc")
            self.xy_length = self.radius * abs(self.end_angle - self.start_angle)

    def clone(
        self,
        z_start: float | None = None,
        z_end: float | None = None,
        reverse: bool = False,
    ) -> AnnotatedGCode:
        z_start = self.start_point[2] if z_start is None else float(z_start)
        z_end = self.end_point[2] if z_end is None else float(z_end)
        other = copy.copy(self)
        parameters = dict(self.command.Parameters)
        command_name = str(self.command.Name)
        other.start_point = (self.start_point[0], self.start_point[1], z_start)
        other.end_point = (self.end_point[0], self.end_point[1], z_end)
        parameters["Z"] = z_end
        if reverse:
            other.start_point, other.end_point = other.end_point, other.start_point
            parameters.update(
                {
                    "X": other.end_point[0],
                    "Y": other.end_point[1],
                    "Z": other.end_point[2],
                }
            )
            if other.is_arc:
                other.start_angle, other.end_angle = other.end_angle, other.start_angle
                command_name = (
                    Path.Geom.CmdMoveCW[0]
                    if self.command.Name in Path.Geom.CmdMoveCCW
                    else Path.Geom.CmdMoveCCW[0]
                )
                parameters.update(
                    {
                        "I": other.center_xy[0] - other.start_point[0],
                        "J": other.center_xy[1] - other.start_point[1],
                    }
                )
        other.command = Path.Command(command_name, parameters)
        return other

    def split(self, split_length: float) -> tuple[AnnotatedGCode, AnnotatedGCode]:
        if not (self.is_line or self.is_arc) or self.xy_length is None:
            raise ValueError("Ramp Entry can split only line and arc motion")
        if self.xy_length <= _EPSILON:
            raise ValueError("Ramp Entry cannot split a zero-length motion")
        length = min(max(float(split_length), 0.0), self.xy_length)
        proportion = length / self.xy_length
        first_parameters = dict(self.command.Parameters)
        second_parameters = dict(self.command.Parameters)
        if self.is_line:
            split_point = tuple(
                self.start_point[index] * (1.0 - proportion)
                + self.end_point[index] * proportion
                for index in range(3)
            )
        else:
            angle = self.start_angle * (1.0 - proportion) + self.end_angle * proportion
            split_point = (
                self.center_xy[0] + self.radius * math.cos(angle),
                self.center_xy[1] + self.radius * math.sin(angle),
                self.start_point[2] * (1.0 - proportion)
                + self.end_point[2] * proportion,
            )
            second_parameters.update(
                {
                    "I": self.center_xy[0] - split_point[0],
                    "J": self.center_xy[1] - split_point[1],
                }
            )
        first_parameters.update(
            {"X": split_point[0], "Y": split_point[1], "Z": split_point[2]}
        )
        return (
            AnnotatedGCode(
                Path.Command(self.command.Name, first_parameters),
                self.start_point,
            ),
            AnnotatedGCode(
                Path.Command(self.command.Name, second_parameters),
                split_point,
            ),
        )


class RampGenerator:
    def __init__(self, definition: RampDefinition, feed_rates: RampFeedRates):
        self.definition = normalize_definition(definition)
        self.feed_rates = normalize_feed_rates(feed_rates)
        self.generation_units = 0
        self.scan_units = 0
        self.ramped_plunges = 0
        self.unchanged_plunges = 0
        self.start_depth_splits = 0
        self.duplicates_removed = 0
        self.plunges_combined = 0

    def _work_append(self, output: list[AnnotatedGCode], edge: AnnotatedGCode) -> None:
        self.generation_units += 1
        if self.generation_units > MAX_RAMP_ENTRY_GENERATION_UNITS:
            raise ValueError(
                "Ramp Entry generation exceeds its "
                f"{MAX_RAMP_ENTRY_GENERATION_UNITS} intermediate-work limit"
            )
        output.append(edge)

    @staticmethod
    def _output_append(
        output: list[AnnotatedGCode],
        edge: AnnotatedGCode,
    ) -> None:
        if len(output) >= MAX_RAMP_ENTRY_OUTPUT_COMMANDS:
            raise ValueError(
                f"Ramp Entry output exceeds its {MAX_RAMP_ENTRY_OUTPUT_COMMANDS} command limit"
            )
        output.append(edge)

    @staticmethod
    def _output_extend(
        output: list[AnnotatedGCode],
        edges: list[AnnotatedGCode],
    ) -> None:
        if len(output) + len(edges) > MAX_RAMP_ENTRY_OUTPUT_COMMANDS:
            raise ValueError(
                f"Ramp Entry output exceeds its {MAX_RAMP_ENTRY_OUTPUT_COMMANDS} command limit"
            )
        output.extend(edges)

    def _annotate(self, commands: tuple[Any, ...]) -> list[AnnotatedGCode]:
        edges: list[AnnotatedGCode] = []
        last_parameters: dict[str, Any] = {}
        for command in commands:
            parameters = dict(command.Parameters)
            if (
                command.Name in Path.Geom.CmdMoveAll
                and edges
                and command.Name == edges[-1].command.Name
                and all(last_parameters.get(key) == value for key, value in parameters.items())
            ):
                self.duplicates_removed += 1
                continue
            start = edges[-1].end_point if edges else (0.0, 0.0, 0.0)
            edge = AnnotatedGCode(command, start)
            last_parameters.update(parameters)
            if (
                edges
                and edge.is_line
                and edges[-1].is_line
                and edge.xy_length is not None
                and edges[-1].xy_length is not None
                and edge.xy_length <= _EPSILON
                and edges[-1].xy_length <= _EPSILON
                and edges[-1].end_point[2] < edges[-1].start_point[2]
                and edge.end_point[2] < edge.start_point[2]
            ):
                edges[-1] = AnnotatedGCode(command, edges[-1].start_point)
                self.plunges_combined += 1
                continue
            edges.append(edge)
        return edges

    @staticmethod
    def _is_plunge(edge: AnnotatedGCode) -> bool:
        return bool(
            (edge.is_line or edge.is_arc)
            and edge.xy_length is not None
            and edge.xy_length < 1.0e-6
            and edge.end_point[2] < edge.start_point[2]
        )

    def _process_start_depth(
        self,
        edge: AnnotatedGCode,
    ) -> tuple[AnnotatedGCode | None, AnnotatedGCode | None]:
        limit = self.definition.start_depth_mm
        if limit is None:
            return None, edge
        z0, z1 = edge.start_point[2], edge.end_point[2]
        if z0 > limit:
            if z1 >= limit or math.isclose(z1, limit, abs_tol=_EPSILON):
                return edge, None
            if not math.isclose(z0, limit, abs_tol=_EPSILON):
                self.start_depth_splits += 1
                return edge.clone(z0, limit), edge.clone(limit, z1)
        return None, edge

    def _method1_parts(
        self,
        ramp_edges: list[AnnotatedGCode],
        start: tuple[float, float, float],
        projection: float,
        angle: float,
    ) -> tuple[list[AnnotatedGCode], list[AnnotatedGCode]]:
        usable_length = sum(edge.xy_length or 0.0 for edge in ramp_edges)
        if usable_length <= _EPSILON:
            raise ValueError("Ramp Entry has no positive-length motion after a plunge")
        ramp: list[AnnotatedGCode] = []
        reset: list[AnnotatedGCode] = []
        reversed_edges: list[AnnotatedGCode] = []
        for edge in ramp_edges:
            self._work_append(reversed_edges, edge.clone(reverse=True))
        remaining = projection
        z = start[2]
        forward = True
        index = 0
        while remaining > _EPSILON:
            edge = ramp_edges[index] if forward else reversed_edges[index - 1]
            length = edge.xy_length or 0.0
            if length <= _EPSILON:
                index = index + 1 if forward else index - 1
            elif length > remaining:
                first, second = edge.split(remaining)
                self._work_append(ramp, first.clone(z_start=z))
                if forward:
                    self._work_append(reset, first.clone(reverse=True))
                else:
                    self._work_append(reset, second)
                    index -= 1
                remaining = 0.0
                break
            else:
                new_z = z - length / math.tan(math.radians(angle))
                self._work_append(ramp, edge.clone(z, new_z))
                z = new_z
                remaining -= length
                index = index + 1 if forward else index - 1
            if index == 0:
                forward = True
            if index == len(ramp_edges):
                forward = False
        while index >= 1:
            self._work_append(reset, reversed_edges[index - 1])
            index -= 1
        return ramp, reset

    def _method1(
        self,
        ramp_edges: list[AnnotatedGCode],
        start: tuple[float, float, float],
        projection: float,
        angle: float,
    ) -> list[AnnotatedGCode]:
        ramp, reset = self._method1_parts(ramp_edges, start, projection, angle)
        if len(ramp) + len(reset) > MAX_RAMP_ENTRY_OUTPUT_COMMANDS:
            raise ValueError(
                f"Ramp Entry output exceeds its {MAX_RAMP_ENTRY_OUTPUT_COMMANDS} command limit"
            )
        return [*ramp, *reset]

    def _method2(
        self,
        ramp_edges: list[AnnotatedGCode],
        start: tuple[float, float, float],
        projection: float,
        angle: float,
    ) -> list[AnnotatedGCode]:
        raised: list[AnnotatedGCode] = []
        for edge in ramp_edges:
            self._work_append(raised, edge.clone(start[2], start[2]))
        result = self._method1(
            raised,
            ramp_edges[0].start_point,
            projection,
            -angle,
        )
        output: list[AnnotatedGCode] = []
        for edge in reversed(result):
            self._work_append(output, edge.clone(reverse=True))
        return output

    def _method3(
        self,
        ramp_edges: list[AnnotatedGCode],
        start: tuple[float, float, float],
        projection: float,
        angle: float,
    ) -> list[AnnotatedGCode]:
        z_half = (start[2] + ramp_edges[0].start_point[2]) / 2.0
        level: list[AnnotatedGCode] = []
        for edge in ramp_edges:
            self._work_append(level, edge.clone(z_half, z_half))
        ramp, _reset = self._method1_parts(level, start, projection, angle)
        ramp_back: list[AnnotatedGCode] = []
        for edge in reversed(ramp):
            self._work_append(
                ramp_back,
                edge.clone(
                    2.0 * z_half - edge.start_point[2],
                    2.0 * z_half - edge.end_point[2],
                    reverse=True,
                ),
            )
        if len(ramp) + len(ramp_back) > MAX_RAMP_ENTRY_OUTPUT_COMMANDS:
            raise ValueError(
                f"Ramp Entry output exceeds its {MAX_RAMP_ENTRY_OUTPUT_COMMANDS} command limit"
            )
        return [*ramp, *ramp_back]

    def _generate_linear(self, edges: list[AnnotatedGCode]) -> list[AnnotatedGCode]:
        output: list[AnnotatedGCode] = []
        method = self.definition.method
        angle = self.definition.angle_from_vertical_degrees
        for edge_index, original in enumerate(edges):
            if not self._is_plunge(original):
                self._output_append(output, original)
                continue
            no_ramp, edge = self._process_start_depth(original)
            if no_ramp is not None:
                self._output_append(output, no_ramp)
            if edge is None:
                self.unchanged_plunges += 1
                continue
            projection = abs(edge.start_point[2] - edge.end_point[2]) * math.tan(
                math.radians(angle)
            )
            if method == "RampMethod3":
                projection /= 2.0
            if projection <= _EPSILON:
                self._output_append(output, edge)
                self.unchanged_plunges += 1
                continue
            covered = False
            covered_length = 0.0
            ramp_edges: list[AnnotatedGCode] = []
            index = edge_index + 1
            while index < len(edges):
                self.scan_units += 1
                if self.scan_units > MAX_RAMP_ENTRY_SCAN_UNITS:
                    raise ValueError(
                        f"Ramp Entry search exceeds its {MAX_RAMP_ENTRY_SCAN_UNITS} unit limit"
                    )
                candidate = edges[index]
                if (
                    abs(candidate.start_point[2] - candidate.end_point[2]) > 1.0e-6
                    or not (candidate.is_line or candidate.is_arc)
                ):
                    break
                ramp_edges.append(candidate)
                covered_length += candidate.xy_length or 0.0
                if covered_length > projection:
                    covered = True
                    break
                index += 1
            if not ramp_edges or covered_length <= _EPSILON:
                self._output_append(output, edge)
                self.unchanged_plunges += 1
                continue
            if method == "RampMethod1":
                generated = self._method1(ramp_edges, edge.start_point, projection, angle)
            elif method == "RampMethod2":
                generated = self._method2(ramp_edges, edge.start_point, projection, angle)
            elif covered:
                generated = self._method3(ramp_edges, edge.start_point, projection, angle)
            else:
                generated = self._method1(
                    ramp_edges,
                    edge.start_point,
                    projection * 2.0,
                    angle,
                )
            self._output_extend(output, generated)
            self.ramped_plunges += 1
        return output

    def _helix_edges(
        self,
        ramp_edges: list[AnnotatedGCode],
        start_z: float,
    ) -> list[AnnotatedGCode]:
        loop_length = sum(edge.xy_length or 0.0 for edge in ramp_edges)
        if loop_length <= _EPSILON:
            raise ValueError("Ramp Entry helix loop has zero XY length")
        target_z = ramp_edges[-1].end_point[2]
        height = abs(start_z - target_z)
        projection = height * math.tan(
            math.radians(self.definition.angle_from_vertical_degrees)
        )
        loops = max(1, math.ceil(round(projection / loop_length, 6)))
        output_count = loops * len(ramp_edges)
        if output_count > MAX_RAMP_ENTRY_OUTPUT_COMMANDS:
            raise ValueError(
                f"Ramp Entry helix would exceed its {MAX_RAMP_ENTRY_OUTPUT_COMMANDS} command limit"
            )
        total_length = loop_length * loops
        ramp_angle = math.atan(total_length / height)
        current_z = start_z
        output: list[AnnotatedGCode] = []
        for index in range(output_count):
            edge = ramp_edges[index % len(ramp_edges)]
            delta_z = (edge.xy_length or 0.0) / math.tan(ramp_angle)
            new_z = current_z - delta_z if index + 1 < output_count else target_z
            self._work_append(output, edge.clone(current_z, new_z))
            current_z = new_z
        return output

    def _generate_helix(self, edges: list[AnnotatedGCode]) -> list[AnnotatedGCode]:
        output: list[AnnotatedGCode] = []
        move_depths = [
            edge.end_point[2]
            for edge in edges
            if edge.command.Name in Path.Geom.CmdMoveAll
        ]
        minimum_z = min(move_depths, default=0.0)
        index = 0
        while index < len(edges):
            original = edges[index]
            if not self._is_plunge(original):
                self._output_append(output, original)
                index += 1
                continue
            no_ramp, edge = self._process_start_depth(original)
            if no_ramp is not None:
                self._output_append(output, no_ramp)
            if edge is None:
                self.unchanged_plunges += 1
                index += 1
                continue
            loop_found = False
            ramp_edges: list[AnnotatedGCode] = []
            next_index = index + 1
            while next_index < len(edges):
                self.scan_units += 1
                if self.scan_units > MAX_RAMP_ENTRY_SCAN_UNITS:
                    raise ValueError(
                        f"Ramp Entry search exceeds its {MAX_RAMP_ENTRY_SCAN_UNITS} unit limit"
                    )
                candidate = edges[next_index]
                if abs(candidate.start_point[2] - candidate.end_point[2]) > 1.0e-6:
                    break
                if not (candidate.is_line or candidate.is_arc):
                    break
                ramp_edges.append(candidate)
                if Path.Geom.pointsCoincide(edge.end_point, candidate.end_point):
                    loop_found = True
                    next_index += 1
                    break
                next_index += 1
            if not loop_found:
                self._output_append(output, edge)
                self.unchanged_plunges += 1
                index += 1
                continue
            self._output_extend(
                output,
                self._helix_edges(ramp_edges, edge.start_point[2]),
            )
            self.ramped_plunges += 1
            if not math.isclose(edge.end_point[2], minimum_z, abs_tol=_EPSILON):
                index = next_index
            else:
                index += 1
        return output

    def _commands(self, edges: list[AnnotatedGCode]) -> tuple[Any, ...]:
        output: list[Any] = []
        last_x = last_y = last_z = 0.0
        rates = self.feed_rates
        for edge in edges:
            parameters = dict(edge.command.Parameters)
            x = _finite(parameters.get("X", last_x), "Ramp Entry output X")
            y = _finite(parameters.get("Y", last_y), "Ramp Entry output Y")
            z = _finite(parameters.get("Z", last_z), "Ramp Entry output Z")
            comparison_z = round(z, 8) if z else z
            if edge.command.Name in Path.Geom.CmdMoveMill:
                if not math.isclose(last_z, comparison_z, abs_tol=_EPSILON):
                    parameters["F"] = (
                        rates.vertical_feed
                        if math.isclose(x, last_x, abs_tol=_EPSILON)
                        and math.isclose(y, last_y, abs_tol=_EPSILON)
                        else rates.ramp_feed
                    )
                else:
                    parameters["F"] = rates.horizontal_feed
            elif edge.command.Name in ("G0", "G00"):
                parameters["F"] = (
                    rates.vertical_rapid
                    if not math.isclose(last_z, comparison_z, abs_tol=_EPSILON)
                    else rates.horizontal_rapid
                )
            last_x, last_y, last_z = x, y, comparison_z
            output.append(Path.Command(edge.command.Name, parameters))
        return tuple(output)

    def generate(self, base: Any):
        if base is None or not base.isDerivedFrom("Path::Feature"):
            raise ValueError("Ramp Entry base must be one Path feature")
        placed = PathUtils.getPathWithPlacement(base)
        source = tuple(getattr(placed, "Commands", ()) or ())
        if not source:
            raise ValueError("Ramp Entry base path is empty")
        if len(source) > MAX_RAMP_ENTRY_INPUT_COMMANDS:
            raise ValueError(
                f"Ramp Entry base has {len(source)} commands; its interactive limit is "
                f"{MAX_RAMP_ENTRY_INPUT_COMMANDS}"
            )
        edges = self._annotate(source)
        output_edges = (
            self._generate_helix(edges)
            if self.definition.method == "Helix"
            else self._generate_linear(edges)
        )
        self.input_edges = edges
        self.output_edges = output_edges
        commands = self._commands(output_edges)
        if len(commands) > MAX_RAMP_ENTRY_OUTPUT_COMMANDS:
            raise ValueError("Ramp Entry output exceeds its command limit")
        path = _path_with_job_center(base, commands)
        ramp_motion_count = sum(
            1
            for edge in output_edges
            if edge.command.Name in Path.Geom.CmdMoveMill
            and abs(edge.end_point[2] - edge.start_point[2]) > _EPSILON
            and (edge.xy_length or 0.0) > _EPSILON
        )
        return path, {
            "method": self.definition.method,
            "angle_from_vertical_degrees": self.definition.angle_from_vertical_degrees,
            "start_depth_mm": self.definition.start_depth_mm,
            "source_command_count": len(source),
            "normalized_command_count": len(edges),
            "output_command_count": len(commands),
            "ramped_plunge_count": self.ramped_plunges,
            "unchanged_plunge_count": self.unchanged_plunges,
            "ramp_motion_count": ramp_motion_count,
            "start_depth_split_count": self.start_depth_splits,
            "duplicate_command_count_removed": self.duplicates_removed,
            "combined_plunge_count": self.plunges_combined,
            "scan_units": self.scan_units,
        }


def generatePathWithMetadata(
    base: Any,
    definition: RampDefinition,
    feed_rates: RampFeedRates,
):
    """Generate one detached Ramp Entry path with bounded diagnostic metadata."""

    return RampGenerator(definition, feed_rates).generate(base)


class ObjectDressup:
    """Parametric Ramp Entry proxy backed by the task-free generator."""

    def __init__(self, obj):
        self.obj = obj
        obj.addProperty(
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base toolpath to modify"),
        )
        obj.addProperty(
            "App::PropertyAngle",
            "Angle",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Angle of ramp"),
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "Method",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Ramping Method"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "UseStartDepth",
            "StartDepth",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Ignore plunge motion above DressupStartDepth",
            ),
        )
        obj.addProperty(
            "App::PropertyDistance",
            "DressupStartDepth",
            "StartDepth",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Absolute Z depth below which ramp entry is generated",
            ),
        )
        for name, values in self.propertyEnumerations():
            setattr(obj, name, values)
        obj.Proxy = self
        self.setEditorProperties(obj)
        # These attributes remain available to existing human-side macros and
        # direct callers while generation itself is owned by RampGenerator.
        self.wire = None
        self.angle = None
        self.rapids = None
        self.method = None
        self.edges = []
        self.outedges = []
        self.ignoreAboveEnabled = False
        self.ignoreAbove = 0.0

    @classmethod
    def propertyEnumerations(cls, dataType="data"):
        translated = FreeCAD.Qt.translate
        values = {
            "Method": [
                (translated("CAM_DressupRampEntry", "RampMethod1"), "RampMethod1"),
                (translated("CAM_DressupRampEntry", "RampMethod2"), "RampMethod2"),
                (translated("CAM_DressupRampEntry", "RampMethod3"), "RampMethod3"),
                (translated("CAM_DressupRampEntry", "Helix"), "Helix"),
            ]
        }
        if dataType == "raw":
            return values
        index = 0 if dataType == "translated" else 1
        return [(name, [item[index] for item in items]) for name, items in values.items()]

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, obj, prop):
        if prop == "UseStartDepth":
            self.setEditorProperties(obj)
        if prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    @staticmethod
    def setEditorProperties(obj):
        if hasattr(obj, "UseStartDepth"):
            obj.setEditorMode("DressupStartDepth", 0 if obj.UseStartDepth else 2)

    def onDocumentRestored(self, obj):
        self.setEditorProperties(obj)
        if hasattr(obj, "RampFeedRate"):
            obj.Proxy.RampFeedRate = obj.RampFeedRate
            obj.removeProperty("RampFeedRate")
        if hasattr(obj, "CustomFeedRate"):
            temporary = obj.CustomFeedRate.Value
            for prop, expression in obj.ExpressionEngine:
                if prop == "CustomFeedRate":
                    temporary = expression
            obj.Proxy.CustomFeedRate = temporary
            obj.removeProperty("CustomFeedRate")

    @staticmethod
    def setup(obj):
        obj.Angle = 60.0
        obj.Method = 2
        base = PathDressup.baseOp(obj.Base)
        start_depth = getattr(base, "StartDepth", None)
        if start_depth is not None:
            obj.DressupStartDepth = start_depth

    def execute(self, obj):
        if not PathUtil.activeForOp(obj):
            obj.Path = _path_with_job_center(obj)
            return
        base = getattr(obj, "Base", None)
        if (
            base is None
            or not base.isDerivedFrom("Path::Feature")
            or not tuple(getattr(getattr(base, "Path", None), "Commands", ()) or ())
        ):
            obj.Path = _path_with_job_center(obj)
            Path.Log.warning("Ramp Entry requires one nonempty Path base")
            return
        # Preserve the historical output for invalid authored angles without
        # silently rewriting the user's property during recompute.
        angle = min(
            MAX_RAMP_ANGLE_DEGREES,
            max(MIN_RAMP_ANGLE_DEGREES, float(obj.Angle.Value)),
        )
        self.angle = angle
        self.method = str(obj.Method)
        self.ignoreAboveEnabled = bool(obj.UseStartDepth)
        self.ignoreAbove = (
            obj.DressupStartDepth if self.ignoreAboveEnabled else 0.0
        )
        try:
            generator = RampGenerator(
                RampDefinition(
                    method=self.method,
                    angle_from_vertical_degrees=angle,
                    start_depth_mm=(
                        float(obj.DressupStartDepth.Value)
                        if self.ignoreAboveEnabled
                        else None
                    ),
                ),
                feed_rates_from_base(base),
            )
            path, metadata = generator.generate(base)
        except (ArithmeticError, AttributeError, TypeError, ValueError) as exc:
            obj.Path = _path_with_job_center(obj)
            Path.Log.warning(f"Ramp Entry could not generate its path: {exc}")
            return
        self.edges = generator.input_edges
        self.outedges = generator.output_edges
        self.lastGenerationStats = metadata
        obj.Path = path

    def _compatibility_generator(self, obj=None) -> RampGenerator:
        """Build the bounded engine for preserved human-side helper methods."""

        owner = obj if obj is not None else self.obj
        base = getattr(owner, "Base", None)
        rates = (
            feed_rates_from_base(base)
            if base is not None
            else RampFeedRates(0.0, 0.0, 0.0, 0.0, 0.0)
        )
        angle = _finite(
            self.angle if self.angle is not None else owner.Angle,
            "Ramp Entry angle",
        )
        method = str(self.method or owner.Method)
        start_depth = (
            _finite(self.ignoreAbove, "Ramp Entry start depth")
            if bool(self.ignoreAboveEnabled)
            else None
        )
        return RampGenerator(RampDefinition(method, angle, start_depth), rates)

    def generateRamps(self):
        generator = self._compatibility_generator()
        result = generator._generate_linear(list(self.edges))
        self.outedges = result
        return result

    def generateHelix(self):
        generator = self._compatibility_generator()
        result = generator._generate_helix(list(self.edges))
        self.outedges = result
        return result

    def processIgnoreAbove(self, edge):
        return self._compatibility_generator()._process_start_depth(edge)

    def createHelix(self, rampedges, startZ):
        return self._compatibility_generator()._helix_edges(
            list(rampedges),
            float(startZ),
        )

    @staticmethod
    def findMinZ(edges):
        depths = [
            edge.end_point[2]
            for edge in edges
            if edge.command.Name in Path.Geom.CmdMoveAll
        ]
        return min(depths, default=99_999_999_999.0)

    def createRampMethod1(self, rampedges, p0, projectionlen, rampangle):
        return self._compatibility_generator()._method1(
            list(rampedges),
            tuple(p0),
            float(projectionlen),
            float(rampangle),
        )

    def _createRampMethod1(self, rampedges, p0, projectionlen, rampangle):
        return self._compatibility_generator()._method1_parts(
            list(rampedges),
            tuple(p0),
            float(projectionlen),
            float(rampangle),
        )

    def createRampMethod2(self, rampedges, p0, projectionlen, rampangle):
        return self._compatibility_generator()._method2(
            list(rampedges),
            tuple(p0),
            float(projectionlen),
            float(rampangle),
        )

    def createRampMethod3(self, rampedges, p0, projectionlen, rampangle):
        return self._compatibility_generator()._method3(
            list(rampedges),
            tuple(p0),
            float(projectionlen),
            float(rampangle),
        )

    def createCommands(self, obj, edges):
        generator = self._compatibility_generator(obj)
        return _path_with_job_center(obj, generator._commands(list(edges)))
