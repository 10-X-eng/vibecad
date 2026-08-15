# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared human-parity boundary for Native Sketch rectangle commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MIN_SKETCH_GEOMETRY_LENGTH_MM,
    same_sketch_point,
)
from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)


@dataclass(frozen=True, slots=True)
class RectangleBoundary:
    corners_mm: tuple[tuple[float, float], ...]
    alignment_types: tuple[str, ...]

    @property
    def segments(self) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
        return tuple(
            (self.corners_mm[index], self.corners_mm[(index + 1) % 4])
            for index in range(4)
        )


def rectangle_boundary(
    first: tuple[float, float],
    opposite: tuple[float, float],
) -> RectangleBoundary:
    delta_x = opposite[0] - first[0]
    delta_y = opposite[1] - first[1]
    if (
        abs(delta_x) <= MIN_SKETCH_GEOMETRY_LENGTH_MM
        or abs(delta_y) <= MIN_SKETCH_GEOMETRY_LENGTH_MM
    ):
        raise NativeSketchError(
            "Sketch Rectangle corners must define non-zero width and height."
        )
    if delta_x * delta_y > 0.0:
        second = (opposite[0], first[1])
        fourth = (first[0], opposite[1])
        alignments = ("Horizontal", "Vertical", "Horizontal", "Vertical")
    else:
        second = (first[0], opposite[1])
        fourth = (opposite[0], first[1])
        alignments = ("Vertical", "Horizontal", "Vertical", "Horizontal")
    return RectangleBoundary((first, second, opposite, fourth), alignments)


def exact_rectangle_indices(
    raw: Any,
    expected: tuple[int, ...],
    label: str,
) -> tuple[int, ...]:
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    result = tuple(int(value) for value in values)
    if result != expected:
        raise NativeSketchError(f"Sketcher returned unexpected Rectangle {label} indices.")
    return result


def create_rectangle_boundary(
    sketch: Any,
    boundary: RectangleBoundary,
    *,
    base_geometry: int,
    base_constraint: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    import FreeCAD as App
    import Part
    import Sketcher

    lines = [
        Part.LineSegment(App.Vector(*start, 0.0), App.Vector(*end, 0.0))
        for start, end in boundary.segments
    ]
    expected_geometry = tuple(range(base_geometry, base_geometry + 4))
    geometry_indices = exact_rectangle_indices(
        sketch.addGeometry(lines, False),
        expected_geometry,
        "geometry",
    )
    joint_pairs = tuple(
        (geometry_indices[index], geometry_indices[(index + 1) % 4])
        for index in range(4)
    )
    constraints = [
        Sketcher.Constraint("Coincident", first, 2, second, 1)
        for first, second in joint_pairs
    ]
    constraints.extend(
        Sketcher.Constraint(constraint_type, geometry_index)
        for constraint_type, geometry_index in zip(
            boundary.alignment_types,
            geometry_indices,
            strict=True,
        )
    )
    expected_constraints = tuple(range(base_constraint, base_constraint + 8))
    constraint_indices = exact_rectangle_indices(
        sketch.addConstraint(constraints),
        expected_constraints,
        "constraint",
    )
    return geometry_indices, constraint_indices


def _active_constraint(record: Mapping[str, Any], constraint_type: str) -> bool:
    return (
        record.get("type") == constraint_type
        and record.get("driving") is True
        and record.get("active") is True
        and record.get("virtual") is False
    )


def active_rectangle_constraint(
    record: Mapping[str, Any],
    constraint_type: str,
) -> bool:
    return _active_constraint(record, constraint_type)


def verify_rectangle_boundary(
    sketch: Any,
    boundary: RectangleBoundary,
    geometry_indices: tuple[int, ...],
    constraint_indices: tuple[int, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    geometries = [serialize_sketch_geometry(sketch, index) for index in geometry_indices]
    for record, segment in zip(geometries, boundary.segments, strict=True):
        if (
            record.get("type_id") != "Part::GeomLineSegment"
            or record.get("kind") != "line"
            or bool(record.get("construction"))
            or bool(record.get("blocked"))
            or not same_sketch_point(record.get("start_mm"), segment[0])
            or not same_sketch_point(record.get("end_mm"), segment[1])
        ):
            raise NativeSketchError("Sketch Rectangle side differs from its definition.")

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in constraint_indices
    ]
    joint_pairs = tuple(
        (geometry_indices[index], geometry_indices[(index + 1) % 4])
        for index in range(4)
    )
    for record, (first, second) in zip(constraints[:4], joint_pairs, strict=True):
        if not _active_constraint(record, "Coincident") or record.get("references") != [
            {"slot": 1, "geometry_index": first, "position": 2},
            {"slot": 2, "geometry_index": second, "position": 1},
        ]:
            raise NativeSketchError("Sketch Rectangle corner constraint changed.")
    for record, constraint_type, geometry_index in zip(
        constraints[4:],
        boundary.alignment_types,
        geometry_indices,
        strict=True,
    ):
        if not _active_constraint(record, constraint_type) or record.get("references") != [
            {"slot": 1, "geometry_index": geometry_index}
        ]:
            raise NativeSketchError("Sketch Rectangle alignment constraint changed.")
    return geometries, constraints
