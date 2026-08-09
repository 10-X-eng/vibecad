# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact rounded Rectangle (Oblong) creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCircularArc import verify_circular_arc_record
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MIN_SKETCH_GEOMETRY_LENGTH_MM,
    same_sketch_number,
    same_sketch_point,
    sketch_point_2d,
    sketch_positive_length,
)
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    sketch_geometry_result,
    verify_sketch_append,
)
from VibeCADNativeSketchRectangleCommon import (
    RectangleBoundary,
    active_rectangle_constraint,
    exact_rectangle_indices,
    rectangle_boundary,
)
from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)
from VibeCADNativeTargets import object_identity


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "first_corner_mm",
        "opposite_corner_mm",
        "radius_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchOblongSpec:
    target: ActiveSketchTargetSpec
    boundary: RectangleBoundary
    radius_mm: float

    @property
    def axis_u(self) -> tuple[float, float]:
        first, second = self.boundary.corners_mm[:2]
        length = math.hypot(second[0] - first[0], second[1] - first[1])
        return ((second[0] - first[0]) / length, (second[1] - first[1]) / length)

    @property
    def axis_v(self) -> tuple[float, float]:
        first, _second, _third, fourth = self.boundary.corners_mm
        length = math.hypot(fourth[0] - first[0], fourth[1] - first[1])
        return ((fourth[0] - first[0]) / length, (fourth[1] - first[1]) / length)

    @property
    def line_segments(
        self,
    ) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
        c1, c2, c3, c4 = self.boundary.corners_mm
        u = self.axis_u
        v = self.axis_v
        r = self.radius_mm
        return (
            (_offset(c1, u, r), _offset(c2, u, -r)),
            (_offset(c2, v, r), _offset(c3, v, -r)),
            (_offset(c3, u, -r), _offset(c4, u, r)),
            (_offset(c4, v, -r), _offset(c1, v, r)),
        )

    @property
    def arc_centers(self) -> tuple[tuple[float, float], ...]:
        c1, c2, c3, c4 = self.boundary.corners_mm
        u = self.axis_u
        v = self.axis_v
        r = self.radius_mm
        return (
            _offset(_offset(c1, u, r), v, r),
            _offset(_offset(c2, u, -r), v, r),
            _offset(_offset(c3, u, -r), v, -r),
            _offset(_offset(c4, u, r), v, -r),
        )

    @property
    def arc_endpoints(
        self,
    ) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
        lines = self.line_segments
        return (
            (lines[3][1], lines[0][0]),
            (lines[0][1], lines[1][0]),
            (lines[1][1], lines[2][0]),
            (lines[2][1], lines[3][0]),
        )

    @property
    def arc_parameters(self) -> tuple[tuple[float, float], ...]:
        result = []
        for center, (start, end) in zip(
            self.arc_centers,
            self.arc_endpoints,
            strict=True,
        ):
            first = math.atan2(start[1] - center[1], start[0] - center[0])
            last = math.atan2(end[1] - center[1], end[0] - center[0])
            if first < 0.0:
                first += math.tau
            if last < 0.0:
                last += math.tau
            if last <= first:
                last += math.tau
            result.append((first, last))
        return tuple(result)


@dataclass(frozen=True, slots=True)
class PreparedSketchOblong:
    insertion: PreparedSketchInsertion
    spec: SketchOblongSpec


def _offset(
    point: tuple[float, float],
    direction: tuple[float, float],
    distance: float,
) -> tuple[float, float]:
    return (
        point[0] + direction[0] * distance,
        point[1] + direction[1] * distance,
    )


def prepare_sketch_oblong(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchOblongSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError("A Sketch Oblong definition has incorrect fields.")
    first = sketch_point_2d(value["first_corner_mm"], "Oblong first_corner_mm")
    opposite = sketch_point_2d(
        value["opposite_corner_mm"],
        "Oblong opposite_corner_mm",
    )
    boundary = rectangle_boundary(first, opposite)
    radius = sketch_positive_length(value["radius_mm"], "Oblong radius_mm")
    width = abs(opposite[0] - first[0])
    height = abs(opposite[1] - first[1])
    if radius >= 0.5 * min(width, height) - MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError(
            "Sketch Oblong radius must be smaller than half its width and height."
        )
    return SketchOblongSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        boundary,
        radius,
    )


def preflight_sketch_oblong(
    context: NativeRuntimeContext,
    spec: SketchOblongSpec,
) -> PreparedSketchOblong:
    if not isinstance(spec, SketchOblongSpec):
        raise TypeError("spec must be a SketchOblongSpec")
    return PreparedSketchOblong(preflight_sketch_insertion(context, spec.target), spec)


def create_sketch_oblong(
    document: Any,
    prepared: PreparedSketchOblong,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchOblong):
        raise TypeError("prepared must be a PreparedSketchOblong")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Oblong preflight",
    )
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part
    import Sketcher

    lines = [
        Part.LineSegment(App.Vector(*start, 0.0), App.Vector(*end, 0.0))
        for start, end in spec.line_segments
    ]
    line_indices = exact_rectangle_indices(
        sketch.addGeometry(lines, False),
        tuple(range(base_geometry, base_geometry + 4)),
        "Oblong line",
    )
    arcs = []
    for center, (first, last) in zip(
        spec.arc_centers,
        spec.arc_parameters,
        strict=True,
    ):
        circle = Part.Circle(
            App.Vector(*center, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            spec.radius_mm,
        )
        arcs.append(Part.ArcOfCircle(circle, first, last))
    arc_indices = exact_rectangle_indices(
        sketch.addGeometry(arcs, False),
        tuple(range(base_geometry + 4, base_geometry + 8)),
        "Oblong arc",
    )
    corner_points = [
        Part.Point(App.Vector(*spec.boundary.corners_mm[index], 0.0))
        for index in (0, 2)
    ]
    point_indices = exact_rectangle_indices(
        sketch.addGeometry(corner_points, True),
        (base_geometry + 8, base_geometry + 9),
        "Oblong construction point",
    )

    constraints = _oblong_constraints(
        Sketcher,
        line_indices,
        arc_indices,
        point_indices,
        spec.boundary.alignment_types,
    )
    constraint_indices = exact_rectangle_indices(
        sketch.addConstraint(constraints),
        tuple(range(base_constraint, base_constraint + 19)),
        "Oblong constraint",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "line_indices": line_indices,
            "arc_indices": arc_indices,
            "point_indices": point_indices,
            "constraint_indices": constraint_indices,
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _oblong_constraints(
    Sketcher: Any,
    lines: tuple[int, ...],
    arcs: tuple[int, ...],
    points: tuple[int, ...],
    alignments: tuple[str, ...],
) -> list[Any]:
    tangent_references = (
        (lines[0], 1, arcs[0], 2),
        (lines[0], 2, arcs[1], 1),
        (lines[1], 1, arcs[1], 2),
        (lines[1], 2, arcs[2], 1),
        (lines[2], 1, arcs[2], 2),
        (lines[2], 2, arcs[3], 1),
        (lines[3], 1, arcs[3], 2),
        (lines[3], 2, arcs[0], 1),
    )
    result = [
        Sketcher.Constraint("Tangent", first, first_pos, second, second_pos)
        for first, first_pos, second, second_pos in tangent_references
    ]
    result.extend(
        Sketcher.Constraint(constraint_type, line)
        for constraint_type, line in zip(alignments, lines, strict=True)
    )
    result.extend(
        Sketcher.Constraint("Equal", first, second)
        for first, second in zip(arcs, arcs[1:])
    )
    result.extend(
        (
            Sketcher.Constraint("PointOnObject", points[0], 1, lines[0]),
            Sketcher.Constraint("PointOnObject", points[0], 1, lines[3]),
            Sketcher.Constraint("PointOnObject", points[1], 1, lines[1]),
            Sketcher.Constraint("PointOnObject", points[1], 1, lines[2]),
        )
    )
    return result


def _verify_constraint(
    record: Mapping[str, Any],
    constraint_type: str,
    references: list[dict[str, int]],
    label: str,
) -> None:
    if (
        not active_rectangle_constraint(record, constraint_type)
        or record.get("references") != references
    ):
        raise NativeSketchError(f"Sketch Oblong {label} constraint changed.")


def verify_sketch_oblong(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchOblong = draft.value["prepared"]
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    lines = tuple(draft.value["line_indices"])
    arcs = tuple(draft.value["arc_indices"])
    points = tuple(draft.value["point_indices"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    if lines != tuple(range(base_geometry, base_geometry + 4)):
        raise NativeSketchError("Sketch Oblong line indices changed.")
    if arcs != tuple(range(base_geometry + 4, base_geometry + 8)):
        raise NativeSketchError("Sketch Oblong arc indices changed.")
    if points != (base_geometry + 8, base_geometry + 9):
        raise NativeSketchError("Sketch Oblong construction point indices changed.")
    if constraint_indices != tuple(range(base_constraint, base_constraint + 19)):
        raise NativeSketchError("Sketch Oblong constraint indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=10,
        constraints_added=19,
    )
    line_records = [serialize_sketch_geometry(sketch, index) for index in lines]
    for record, (start, end) in zip(line_records, spec.line_segments, strict=True):
        if (
            record.get("type_id") != "Part::GeomLineSegment"
            or record.get("kind") != "line"
            or bool(record.get("construction"))
            or bool(record.get("blocked"))
            or not same_sketch_point(record.get("start_mm"), start)
            or not same_sketch_point(record.get("end_mm"), end)
        ):
            raise NativeSketchError("Sketch Oblong line geometry changed.")

    arc_records = [serialize_sketch_geometry(sketch, index) for index in arcs]
    for record, center, parameters, endpoints in zip(
        arc_records,
        spec.arc_centers,
        spec.arc_parameters,
        spec.arc_endpoints,
        strict=True,
    ):
        verify_circular_arc_record(
            record,
            center_mm=center,
            radius_mm=spec.radius_mm,
            first_parameter=parameters[0],
            last_parameter=parameters[1],
            start_mm=endpoints[0],
            end_mm=endpoints[1],
            label="Oblong corner Arc",
        )

    point_records = [serialize_sketch_geometry(sketch, index) for index in points]
    for record, corner in zip(
        point_records,
        (spec.boundary.corners_mm[0], spec.boundary.corners_mm[2]),
        strict=True,
    ):
        if (
            record.get("type_id") != "Part::GeomPoint"
            or record.get("kind") != "point"
            or record.get("construction") is not True
            or bool(record.get("blocked"))
            or not same_sketch_point(record.get("position_mm"), corner)
        ):
            raise NativeSketchError("Sketch Oblong construction point changed.")

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in constraint_indices
    ]
    tangent_refs = (
        (lines[0], 1, arcs[0], 2),
        (lines[0], 2, arcs[1], 1),
        (lines[1], 1, arcs[1], 2),
        (lines[1], 2, arcs[2], 1),
        (lines[2], 1, arcs[2], 2),
        (lines[2], 2, arcs[3], 1),
        (lines[3], 1, arcs[3], 2),
        (lines[3], 2, arcs[0], 1),
    )
    for record, (first, first_pos, second, second_pos) in zip(
        constraints[:8],
        tangent_refs,
        strict=True,
    ):
        _verify_constraint(
            record,
            "Tangent",
            [
                {"slot": 1, "geometry_index": first, "position": first_pos},
                {"slot": 2, "geometry_index": second, "position": second_pos},
            ],
            "tangent",
        )
    for record, constraint_type, line in zip(
        constraints[8:12],
        spec.boundary.alignment_types,
        lines,
        strict=True,
    ):
        _verify_constraint(
            record,
            constraint_type,
            [{"slot": 1, "geometry_index": line}],
            "alignment",
        )
    for record, first, second in zip(
        constraints[12:15],
        arcs[:-1],
        arcs[1:],
        strict=True,
    ):
        _verify_constraint(
            record,
            "Equal",
            [
                {"slot": 1, "geometry_index": first},
                {"slot": 2, "geometry_index": second},
            ],
            "arc equality",
        )
    point_on_object_refs = (
        (points[0], lines[0]),
        (points[0], lines[3]),
        (points[1], lines[1]),
        (points[1], lines[2]),
    )
    for record, (point, line) in zip(
        constraints[15:],
        point_on_object_refs,
        strict=True,
    ):
        _verify_constraint(
            record,
            "PointOnObject",
            [
                {"slot": 1, "geometry_index": point, "position": 1},
                {"slot": 2, "geometry_index": line},
            ],
            "corner incidence",
        )
    if not all(
        same_sketch_number(record.get("radius_mm"), spec.radius_mm)
        for record in arc_records
    ):
        raise NativeSketchError("Sketch Oblong corner Arc radius changed.")
    return sketch_geometry_result(
        sketch,
        {
            "geometries": [*line_records, *arc_records],
            "construction_points": point_records,
            "constraints": constraints,
            "corners_mm": [
                [x, y, 0.0] for x, y in spec.boundary.corners_mm
            ],
            "radius_mm": spec.radius_mm,
            "closed": True,
        },
    )
