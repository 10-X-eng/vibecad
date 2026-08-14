# SPDX-License-Identifier: LGPL-2.1-or-later

"""Task-free holding-tag evaluation shared by CAM and Native assistance.

The interactive editor owns preferences and provisional UI state.  This module
owns only deterministic geometry and path generation so callers can prepare a
complete result without opening a task dialog or mutating a document.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import SimpleNamespace
from typing import Any, Iterable, Sequence

import Path
import Path.Dressup.Utils as PathDressup
import PathScripts.PathUtils as PathUtils


@dataclass(frozen=True, slots=True)
class HoldingTagShape:
    width_mm: float
    height_mm: float
    angle_degrees: float
    fillet_radius_mm: float


@dataclass(frozen=True, slots=True)
class HoldingTagLocation:
    x_mm: float
    y_mm: float
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PreparedHoldingTagPath:
    path: Any
    path_data: Any
    tags: tuple[Any, ...]
    positions: tuple[Any, ...]
    disabled: tuple[int, ...]
    disabled_reasons: tuple[tuple[int, str], ...]
    mappers: tuple[Any, ...]
    edge_count: int
    scan_units: int


@dataclass(frozen=True, slots=True)
class EvaluatedHoldingTags:
    tags: tuple[Any, ...]
    positions: tuple[Any, ...]
    disabled: tuple[int, ...]
    disabled_reasons: tuple[tuple[int, str], ...]


def path_data_for_base(base: Any):
    """Build the shipped PathData analysis for one operation, without a feature."""

    from Path.Dressup.Tags import PathData

    return PathData(SimpleNamespace(Base=base))


def automatic_locations(
    path_data: Any,
    shape: HoldingTagShape,
    *,
    minimum_per_wire: int,
    maximum_for_longest_wire: int,
) -> tuple[HoldingTagLocation, ...]:
    """Return deterministic shipped automatic placements for explicit settings."""

    tags = path_data.generateTags(
        None,
        minimum_per_wire,
        maximum_for_longest_wire,
        shape.width_mm,
        shape.height_mm,
        shape.angle_degrees,
        shape.fillet_radius_mm,
    )
    return tuple(HoldingTagLocation(tag.x, tag.y, True) for tag in tags)


def copied_locations(
    path_data: Any,
    source: Any,
    shape: HoldingTagShape,
) -> tuple[HoldingTagLocation, ...]:
    """Map each enabled source tag onto the closest target bottom wire."""

    tags = path_data.copyTags(
        None,
        source,
        shape.width_mm,
        shape.height_mm,
        shape.angle_degrees,
        shape.fillet_radius_mm,
    )
    return tuple(HoldingTagLocation(tag.x, tag.y, True) for tag in tags)


def _requested_locations(
    values: Iterable[HoldingTagLocation | Sequence[Any]],
) -> tuple[HoldingTagLocation, ...]:
    result = []
    for value in values:
        if isinstance(value, HoldingTagLocation):
            location = value
        else:
            location = HoldingTagLocation(*value)
        result.append(
            HoldingTagLocation(
                float(location.x_mm),
                float(location.y_mm),
                bool(location.enabled),
            )
        )
    return tuple(result)


def _evaluate_locations(
    path_data: Any,
    tool_radius: float,
    shape: HoldingTagShape,
    locations: tuple[HoldingTagLocation, ...],
):
    from Path.Dressup.Tags import Tag

    raw_tags = []
    reasons: dict[int, str] = {}
    for index, location in enumerate(locations):
        tag = Tag(
            index,
            location.x_mm,
            location.y_mm,
            shape.width_mm,
            shape.height_mm,
            shape.angle_degrees,
            shape.fillet_radius_mm,
            location.enabled,
        )
        on_path = path_data.checkTag(tag)
        if not location.enabled:
            tag.enabled = False
            reasons[index] = "requested_disabled"
        elif not on_path:
            tag.enabled = False
            reasons[index] = "off_bottom_path"
        tag.createSolidsAt(path_data.minZ, tool_radius)
        raw_tags.append(tag)

    previous = None
    for index, tag in enumerate(raw_tags):
        if not tag.enabled:
            continue
        if previous is not None:
            if (
                previous.solid.BoundBox.intersect(tag.solid.BoundBox)
                and previous.solid.common(tag.solid).Faces
            ):
                tag.enabled = False
                reasons[index] = "intersects_previous_enabled_tag"
        elif path_data.edges:
            edge = path_data.edges[0]
            first = edge.valueAt(edge.FirstParameter)
            last = edge.valueAt(edge.LastParameter)
            if tag.solid.isInside(first, Path.Geom.Tolerance, True) or tag.solid.isInside(
                last,
                Path.Geom.Tolerance,
                True,
            ):
                tag.enabled = False
                reasons[index] = "intersects_path_start"
        if tag.enabled:
            previous = tag

    positions = tuple(tag.originAt(path_data.minZ) for tag in raw_tags)
    disabled = tuple(index for index, tag in enumerate(raw_tags) if not tag.enabled)
    return raw_tags, positions, disabled, reasons


def evaluate_holding_tag_locations(
    path_data: Any,
    tool_radius: float,
    shape: HoldingTagShape,
    locations: Iterable[HoldingTagLocation | Sequence[Any]],
) -> EvaluatedHoldingTags:
    """Resolve manual enablement and all shipped geometric safety disables."""

    requested = _requested_locations(locations)
    tags, positions, disabled, reasons = _evaluate_locations(
        path_data,
        float(tool_radius),
        shape,
        requested,
    )
    return EvaluatedHoldingTags(
        tags=tuple(tags),
        positions=positions,
        disabled=disabled,
        disabled_reasons=tuple(sorted(reasons.items())),
    )


def _valid_tag_start_intersection(edge: Any, intersection: Any) -> bool:
    if Path.Geom.pointsCoincide(intersection, edge.valueAt(edge.LastParameter)):
        return False
    first = edge.valueAt(edge.FirstParameter)
    last = edge.valueAt(edge.LastParameter)
    if Path.Geom.pointsCoincide(Path.Geom.xy(first), Path.Geom.xy(last)) and first.z < last.z:
        return False
    return True


def _path_with_center(commands: list[Any], job: Any):
    result = Path.Path(commands)
    result.Center = job.Path.Center
    return result


def _build_path(
    base: Any,
    path_data: Any,
    tags: list[Any],
    *,
    approximation: bool,
    max_scan_units: int | None,
    max_output_commands: int | None,
):
    from Path.Dressup.Tags import MapWireToTag

    job = PathUtils.findParentJob(base)
    if job is None:
        raise ValueError("Holding tags require a base operation in one CAM Job")
    controller = PathDressup.toolController(base)
    tolerance = job.GeometryTolerance.Value
    horizontal_feed = controller.HorizFeed.Value
    vertical_feed = controller.VertFeed.Value
    horizontal_rapid = controller.HorizRapid.Value
    vertical_rapid = controller.VertRapid.Value

    edge_count = len(path_data.edges)
    scan_units = edge_count * max(1, len(tags))
    if max_scan_units is not None and scan_units > max_scan_units:
        raise ValueError(
            f"Holding-tag path scanning requires {scan_units} edge/tag checks; "
            f"the safety limit is {max_scan_units}"
        )

    commands: list[Any] = []

    def extend(values: Iterable[Any]) -> None:
        materialized = list(values)
        if (
            max_output_commands is not None
            and len(commands) + len(materialized) > max_output_commands
        ):
            raise ValueError(
                "Holding-tag generation exceeds the output command safety limit "
                f"of {max_output_commands}"
            )
        commands.extend(materialized)

    last_edge = 0
    tag_cursor = 0
    edge = None
    mapper = None
    mappers = []
    while edge is not None or last_edge < edge_count:
        if edge is None:
            edge = path_data.edges[last_edge]
            tags_sorted = sorted(
                tags,
                key=lambda tag: (
                    tag.originAt(tag.z) - edge.valueAt(edge.FirstParameter)
                ).Length,
            )
            last_edge += 1

        if mapper is not None:
            mapper.add(edge)
            if mapper.mappingComplete():
                extend(mapper.commands)
                edge = mapper.tail
                mapper = None
            else:
                edge = None

        if edge is not None:
            tag_index = tag_cursor % len(tags)
            tag_cursor += 1
            intersection = tags_sorted[tag_index].intersects(
                edge,
                edge.FirstParameter,
            )
            if intersection and _valid_tag_start_intersection(edge, intersection):
                mapper = MapWireToTag(
                    edge,
                    tags_sorted[tag_index],
                    intersection,
                    path_data.maxZ,
                    hSpeed=horizontal_feed,
                    vSpeed=vertical_feed,
                    tolerance=tolerance,
                )
                mappers.append(mapper)
                edge = mapper.tail

        if mapper is None and tag_cursor >= len(tags):
            if edge is not None:
                if path_data.rapid.isRapid(edge):
                    vertex = edge.Vertexes[1]
                    if (
                        not commands
                        and Path.Geom.isRoughly(0, vertex.X)
                        and Path.Geom.isRoughly(0, vertex.Y)
                        and not Path.Geom.isRoughly(0, vertex.Z)
                    ):
                        extend((Path.Command("G0", {"Z": vertex.Z, "F": horizontal_rapid}),))
                    else:
                        extend(
                            (
                                Path.Command(
                                    "G0",
                                    {
                                        "X": vertex.X,
                                        "Y": vertex.Y,
                                        "Z": vertex.Z,
                                        "F": vertical_rapid,
                                    },
                                ),
                            )
                        )
                else:
                    extend(
                        Path.Geom.cmdsForEdge(
                            edge,
                            approximation=approximation,
                            hSpeed=horizontal_feed,
                            vSpeed=vertical_feed,
                            tol=tolerance,
                        )
                    )
            edge = None
            tag_cursor = 0

    return _path_with_center(commands, job), tuple(mappers), scan_units


def build_holding_tag_path(
    base: Any,
    path_data: Any,
    tags: Iterable[Any],
    *,
    approximation: bool = False,
    max_scan_units: int | None = None,
    max_output_commands: int | None = None,
):
    """Build a path from already evaluated Tag instances."""

    materialized = list(tags)
    if not materialized:
        return PathUtils.getPathWithPlacement(base), (), len(path_data.edges)
    return _build_path(
        base,
        path_data,
        materialized,
        approximation=bool(approximation),
        max_scan_units=max_scan_units,
        max_output_commands=max_output_commands,
    )


def prepare_holding_tag_path(
    base: Any,
    shape: HoldingTagShape,
    locations: Iterable[HoldingTagLocation | Sequence[Any]],
    *,
    approximation: bool = False,
    path_data: Any | None = None,
    tool_radius: float | None = None,
    max_tags: int | None = None,
    max_scan_units: int | None = None,
    max_output_commands: int | None = None,
) -> PreparedHoldingTagPath:
    """Prepare a complete holding-tag path without changing the document."""

    if not isinstance(shape, HoldingTagShape):
        raise TypeError("shape must be a HoldingTagShape")
    requested = _requested_locations(locations)
    if max_tags is not None and len(requested) > max_tags:
        raise ValueError(
            f"Holding tags requested {len(requested)} locations; the safety limit is {max_tags}"
        )
    if any(
        not math.isfinite(value)
        for location in requested
        for value in (location.x_mm, location.y_mm)
    ):
        raise ValueError("Holding-tag coordinates must be finite")
    path_data = path_data or path_data_for_base(base)
    if not path_data.edges or not path_data.baseWires:
        raise ValueError("Holding tags require a profile path with bottom cutting wires")
    if tool_radius is None:
        tool_radius = float(PathDressup.toolController(base).Tool.Diameter) / 2.0
    evaluated = evaluate_holding_tag_locations(
        path_data,
        float(tool_radius),
        shape,
        requested,
    )
    tags = list(evaluated.tags)
    positions = evaluated.positions
    disabled = evaluated.disabled
    reasons = dict(evaluated.disabled_reasons)
    if tags:
        path, mappers, scan_units = build_holding_tag_path(
            base,
            path_data,
            tags,
            approximation=bool(approximation),
            max_scan_units=max_scan_units,
            max_output_commands=max_output_commands,
        )
    else:
        path = PathUtils.getPathWithPlacement(base)
        mappers = ()
        scan_units = len(path_data.edges)

    for index, tag in enumerate(tags):
        if not tag.enabled and index not in reasons:
            reasons[index] = "tag_path_mapping_failed"
    disabled = tuple(index for index, tag in enumerate(tags) if not tag.enabled)
    return PreparedHoldingTagPath(
        path=path,
        path_data=path_data,
        tags=tuple(tags),
        positions=positions,
        disabled=disabled,
        disabled_reasons=tuple(sorted(reasons.items())),
        mappers=mappers,
        edge_count=len(path_data.edges),
        scan_units=scan_units,
    )
