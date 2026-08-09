# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact straight Slot creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCircularArc import verify_circular_arc_record
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MAX_SKETCH_COORDINATE_MM,
    MIN_SKETCH_GEOMETRY_LENGTH_MM,
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
        "start_center_mm",
        "end_center_mm",
        "radius_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchSlotSpec:
    target: ActiveSketchTargetSpec
    start_center_mm: tuple[float, float]
    end_center_mm: tuple[float, float]
    radius_mm: float
    centerline_length_mm: float
    direction: tuple[float, float]
    normal: tuple[float, float]

    @property
    def arc_parameters(self) -> tuple[tuple[float, float], ...]:
        angle = math.atan2(self.direction[1], self.direction[0])
        first_start = (angle + 0.5 * math.pi) % math.tau
        second_start = (angle + 1.5 * math.pi) % math.tau
        return (
            (first_start, first_start + math.pi),
            (second_start, second_start + math.pi),
        )

    @property
    def arc_endpoints(
        self,
    ) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
        offset = (
            self.normal[0] * self.radius_mm,
            self.normal[1] * self.radius_mm,
        )
        return (
            (
                _offset(self.start_center_mm, offset, 1.0),
                _offset(self.start_center_mm, offset, -1.0),
            ),
            (
                _offset(self.end_center_mm, offset, -1.0),
                _offset(self.end_center_mm, offset, 1.0),
            ),
        )

    @property
    def line_segments(
        self,
    ) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
        first_arc, second_arc = self.arc_endpoints
        return (
            (first_arc[0], second_arc[1]),
            (first_arc[1], second_arc[0]),
        )


@dataclass(frozen=True, slots=True)
class PreparedSketchSlot:
    insertion: PreparedSketchInsertion
    spec: SketchSlotSpec


def _offset(
    point: tuple[float, float],
    vector: tuple[float, float],
    scale: float,
) -> tuple[float, float]:
    return (
        point[0] + scale * vector[0],
        point[1] + scale * vector[1],
    )


def prepare_sketch_slot(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchSlotSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError("A Sketch Slot definition has incorrect fields.")
    start = sketch_point_2d(value["start_center_mm"], "Slot start_center_mm")
    end = sketch_point_2d(value["end_center_mm"], "Slot end_center_mm")
    radius = sketch_positive_length(value["radius_mm"], "Slot radius_mm")
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length = math.hypot(delta_x, delta_y)
    if length <= MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError("Sketch Slot centers must be distinct.")
    direction = (delta_x / length, delta_y / length)
    normal = (-direction[1], direction[0])
    spec = SketchSlotSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        start,
        end,
        radius,
        length,
        direction,
        normal,
    )
    if any(
        abs(coordinate) > MAX_SKETCH_COORDINATE_MM
        for segment in spec.line_segments
        for point in segment
        for coordinate in point
    ):
        raise NativeSketchError(
            "Sketch Slot boundary must remain within +/-1000000 mm."
        )
    return spec


def preflight_sketch_slot(
    context: NativeRuntimeContext,
    spec: SketchSlotSpec,
) -> PreparedSketchSlot:
    if not isinstance(spec, SketchSlotSpec):
        raise TypeError("spec must be a SketchSlotSpec")
    return PreparedSketchSlot(preflight_sketch_insertion(context, spec.target), spec)


def _exact_indices(raw: Any, expected: tuple[int, ...], label: str) -> tuple[int, ...]:
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    result = tuple(int(value) for value in values)
    if result != expected:
        raise NativeSketchError(f"Sketcher returned unexpected Slot {label} indices.")
    return result


def create_sketch_slot(
    document: Any,
    prepared: PreparedSketchSlot,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchSlot):
        raise TypeError("prepared must be a PreparedSketchSlot")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Slot preflight",
    )
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part
    import Sketcher

    arcs = []
    for center, parameters in zip(
        (spec.start_center_mm, spec.end_center_mm),
        spec.arc_parameters,
        strict=True,
    ):
        circle = Part.Circle(
            App.Vector(*center, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            spec.radius_mm,
        )
        arcs.append(Part.ArcOfCircle(circle, *parameters))
    arc_indices = _exact_indices(
        sketch.addGeometry(arcs, False),
        (base_geometry, base_geometry + 1),
        "arc",
    )
    lines = [
        Part.LineSegment(App.Vector(*start, 0.0), App.Vector(*end, 0.0))
        for start, end in spec.line_segments
    ]
    line_indices = _exact_indices(
        sketch.addGeometry(lines, False),
        (base_geometry + 2, base_geometry + 3),
        "line",
    )
    constraints = (
        Sketcher.Constraint("Tangent", arc_indices[0], 1, line_indices[0], 1),
        Sketcher.Constraint("Tangent", arc_indices[0], 2, line_indices[1], 1),
        Sketcher.Constraint("Tangent", arc_indices[1], 2, line_indices[0], 2),
        Sketcher.Constraint("Tangent", arc_indices[1], 1, line_indices[1], 2),
        Sketcher.Constraint("Equal", arc_indices[0], arc_indices[1]),
    )
    constraint_indices = _exact_indices(
        sketch.addConstraint(list(constraints)),
        tuple(range(base_constraint, base_constraint + 5)),
        "constraint",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "arc_indices": arc_indices,
            "line_indices": line_indices,
            "constraint_indices": constraint_indices,
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _verify_constraint(
    record: Mapping[str, Any],
    constraint_type: str,
    references: list[dict[str, int]],
    label: str,
) -> None:
    if (
        record.get("type") != constraint_type
        or record.get("driving") is not True
        or record.get("active") is not True
        or record.get("virtual") is not False
        or record.get("references") != references
    ):
        raise NativeSketchError(f"Sketch Slot {label} constraint changed.")


def verify_sketch_slot(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchSlot = draft.value["prepared"]
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    arcs = tuple(draft.value["arc_indices"])
    lines = tuple(draft.value["line_indices"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    if arcs != (base_geometry, base_geometry + 1):
        raise NativeSketchError("Sketch Slot arc indices changed.")
    if lines != (base_geometry + 2, base_geometry + 3):
        raise NativeSketchError("Sketch Slot line indices changed.")
    if constraint_indices != tuple(range(base_constraint, base_constraint + 5)):
        raise NativeSketchError("Sketch Slot constraint indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=4,
        constraints_added=5,
    )

    arc_records = [serialize_sketch_geometry(sketch, index) for index in arcs]
    for record, center, parameters, endpoints in zip(
        arc_records,
        (spec.start_center_mm, spec.end_center_mm),
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
            label="Slot end Arc",
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
            raise NativeSketchError("Sketch Slot side differs from its definition.")

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in constraint_indices
    ]
    tangent_refs = (
        (arcs[0], 1, lines[0], 1),
        (arcs[0], 2, lines[1], 1),
        (arcs[1], 2, lines[0], 2),
        (arcs[1], 1, lines[1], 2),
    )
    for record, (first, first_pos, second, second_pos) in zip(
        constraints[:4],
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
    _verify_constraint(
        constraints[4],
        "Equal",
        [
            {"slot": 1, "geometry_index": arcs[0]},
            {"slot": 2, "geometry_index": arcs[1]},
        ],
        "Arc equality",
    )
    return sketch_geometry_result(
        sketch,
        {
            "arcs": arc_records,
            "lines": line_records,
            "constraints": constraints,
            "start_center_mm": [*spec.start_center_mm, 0.0],
            "end_center_mm": [*spec.end_center_mm, 0.0],
            "centerline_length_mm": spec.centerline_length_mm,
            "radius_mm": spec.radius_mm,
            "closed": True,
        },
    )
