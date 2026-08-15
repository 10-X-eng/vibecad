# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact rounded-end Arc Slot creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCircularArc import (
    circle_point,
    verify_circular_arc_record,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MAX_SKETCH_COORDINATE_MM,
    sketch_bounded_parameter,
    sketch_point_2d,
    sketch_positive_length,
    sketch_start_angle_degrees,
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


# DrawSketchHandlerArcSlot uses Open CASCADE Precision::Confusion() for these
# topology boundaries. Keep the Native refusal boundary identical and explicit.
_OCC_CONFUSION = 1.0e-7
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "center_mm",
        "centerline_radius_mm",
        "start_angle_degrees",
        "sweep_angle_degrees",
        "slot_radius_mm",
    }
)


@dataclass(frozen=True, slots=True)
class ArcDefinition:
    role: str
    center_mm: tuple[float, float]
    radius_mm: float
    first_parameter: float
    last_parameter: float

    @property
    def start_mm(self) -> tuple[float, float]:
        return circle_point(self.center_mm, self.radius_mm, self.first_parameter)

    @property
    def end_mm(self) -> tuple[float, float]:
        return circle_point(self.center_mm, self.radius_mm, self.last_parameter)


@dataclass(frozen=True, slots=True)
class SketchArcSlotSpec:
    target: ActiveSketchTargetSpec
    center_mm: tuple[float, float]
    centerline_radius_mm: float
    start_angle_degrees: float
    sweep_angle_degrees: float
    slot_radius_mm: float

    @property
    def angle_reversed(self) -> bool:
        return self.sweep_angle_degrees < 0.0

    @property
    def start_parameter(self) -> float:
        return math.radians(self.start_angle_degrees)

    @property
    def signed_sweep(self) -> float:
        return math.radians(self.sweep_angle_degrees)

    @property
    def path_parameters(self) -> tuple[float, float]:
        lower = (
            self.start_parameter + self.signed_sweep
            if self.angle_reversed
            else self.start_parameter
        )
        first = lower % math.tau
        return first, first + abs(self.signed_sweep)

    @property
    def initial_point_mm(self) -> tuple[float, float]:
        return circle_point(
            self.center_mm,
            self.centerline_radius_mm,
            self.start_parameter,
        )

    @property
    def terminal_point_mm(self) -> tuple[float, float]:
        return circle_point(
            self.center_mm,
            self.centerline_radius_mm,
            self.start_parameter + self.signed_sweep,
        )

    @property
    def has_inner_boundary(self) -> bool:
        return self.slot_radius_mm < self.centerline_radius_mm

    @property
    def arc_definitions(self) -> tuple[ArcDefinition, ...]:
        path_first, path_last = self.path_parameters
        initial_cap_first = (
            self.start_parameter
            if self.angle_reversed
            else self.start_parameter + math.pi
        ) % math.tau
        terminal_parameter = self.start_parameter + self.signed_sweep
        terminal_cap_first = (
            terminal_parameter + math.pi
            if self.angle_reversed
            else terminal_parameter
        ) % math.tau
        result = [
            ArcDefinition(
                "outer_boundary",
                self.center_mm,
                self.centerline_radius_mm + self.slot_radius_mm,
                path_first,
                path_last,
            ),
            ArcDefinition(
                "initial_end",
                self.initial_point_mm,
                self.slot_radius_mm,
                initial_cap_first,
                initial_cap_first + math.pi,
            ),
            ArcDefinition(
                "terminal_end",
                self.terminal_point_mm,
                self.slot_radius_mm,
                terminal_cap_first,
                terminal_cap_first + math.pi,
            ),
        ]
        if self.has_inner_boundary:
            result.append(
                ArcDefinition(
                    "inner_boundary",
                    self.center_mm,
                    self.centerline_radius_mm - self.slot_radius_mm,
                    path_first,
                    path_last,
                )
            )
        return tuple(result)


@dataclass(frozen=True, slots=True)
class PreparedSketchArcSlot:
    insertion: PreparedSketchInsertion
    spec: SketchArcSlotSpec


def _signed_sweep(value: Any) -> float:
    result = sketch_bounded_parameter(
        value,
        "Arc Slot sweep_angle_degrees",
        maximum_absolute=360.0,
    )
    if (
        abs(result) >= 360.0
        or abs(math.radians(result)) <= _OCC_CONFUSION
    ):
        raise NativeSketchError(
            "Sketch Arc Slot sweep_angle_degrees must be nonzero and within "
            "-360 to 360 degrees."
        )
    return result


def prepare_sketch_arc_slot(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchArcSlotSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError("A Sketch Arc Slot definition has incorrect fields.")
    center = sketch_point_2d(value["center_mm"], "Arc Slot center_mm")
    centerline_radius = sketch_positive_length(
        value["centerline_radius_mm"],
        "Arc Slot centerline_radius_mm",
    )
    slot_radius = sketch_positive_length(
        value["slot_radius_mm"],
        "Arc Slot slot_radius_mm",
    )
    if slot_radius > centerline_radius:
        raise NativeSketchError(
            "Sketch Arc Slot slot_radius_mm must not exceed its centerline radius."
        )
    gap = centerline_radius - slot_radius
    if 0.0 < gap <= _OCC_CONFUSION:
        raise NativeSketchError(
            "Sketch Arc Slot radii are too close to the inner-boundary topology limit."
        )
    outer_radius = centerline_radius + slot_radius
    if outer_radius > MAX_SKETCH_COORDINATE_MM or any(
        abs(coordinate) + outer_radius > MAX_SKETCH_COORDINATE_MM
        for coordinate in center
    ):
        raise NativeSketchError(
            "Sketch Arc Slot boundary must remain within +/-1000000 mm."
        )
    return SketchArcSlotSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        center,
        centerline_radius,
        sketch_start_angle_degrees(
            value["start_angle_degrees"],
            "Arc Slot start_angle_degrees",
        ),
        _signed_sweep(value["sweep_angle_degrees"]),
        slot_radius,
    )


def preflight_sketch_arc_slot(
    context: NativeRuntimeContext,
    spec: SketchArcSlotSpec,
) -> PreparedSketchArcSlot:
    if not isinstance(spec, SketchArcSlotSpec):
        raise TypeError("spec must be a SketchArcSlotSpec")
    return PreparedSketchArcSlot(preflight_sketch_insertion(context, spec.target), spec)


def _exact_indices(raw: Any, expected: tuple[int, ...], label: str) -> tuple[int, ...]:
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    result = tuple(int(value) for value in values)
    if result != expected:
        raise NativeSketchError(
            f"Sketcher returned unexpected Arc Slot {label} indices."
        )
    return result


def _constraint_definitions(
    arcs: tuple[int, ...],
    *,
    reversed_angle: bool,
    has_inner_boundary: bool,
) -> tuple[tuple[str, tuple[tuple[int, int | None], ...]], ...]:
    outer, initial_end, terminal_end = arcs[:3]
    pos1, pos2 = ((1, 2) if reversed_angle else (2, 1))
    if has_inner_boundary:
        inner = arcs[3]
        leading = (
            ("Coincident", ((outer, 3), (inner, 3))),
            ("Tangent", ((inner, pos1), (terminal_end, pos1))),
            ("Tangent", ((inner, pos2), (initial_end, pos2))),
        )
    else:
        leading = (
            ("Coincident", ((outer, 3), (initial_end, pos2))),
            ("Coincident", ((outer, 3), (terminal_end, pos1))),
        )
    return (
        *leading,
        ("Tangent", ((outer, pos1), (terminal_end, pos2))),
        ("Tangent", ((outer, pos2), (initial_end, pos1))),
    )


def create_sketch_arc_slot(
    document: Any,
    prepared: PreparedSketchArcSlot,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchArcSlot):
        raise TypeError("prepared must be a PreparedSketchArcSlot")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Arc Slot preflight",
    )
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part
    import Sketcher

    geometries = []
    for definition in spec.arc_definitions:
        circle = Part.Circle(
            App.Vector(*definition.center_mm, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            definition.radius_mm,
        )
        geometries.append(
            Part.ArcOfCircle(
                circle,
                definition.first_parameter,
                definition.last_parameter,
            )
        )
    arc_indices = _exact_indices(
        sketch.addGeometry(geometries, False),
        tuple(range(base_geometry, base_geometry + len(geometries))),
        "arc",
    )
    definitions = _constraint_definitions(
        arc_indices,
        reversed_angle=spec.angle_reversed,
        has_inner_boundary=spec.has_inner_boundary,
    )
    constraints = []
    for constraint_type, references in definitions:
        first, second = references
        constraints.append(
            Sketcher.Constraint(
                constraint_type,
                first[0],
                first[1],
                second[0],
                second[1],
            )
        )
    constraint_indices = _exact_indices(
        sketch.addConstraint(constraints),
        tuple(range(base_constraint, base_constraint + len(constraints))),
        "constraint",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "arc_indices": arc_indices,
            "constraint_indices": constraint_indices,
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _verify_constraint(
    record: Mapping[str, Any],
    constraint_type: str,
    references: tuple[tuple[int, int | None], ...],
) -> None:
    expected = []
    for slot, (geometry_index, position) in enumerate(references, start=1):
        reference = {"slot": slot, "geometry_index": geometry_index}
        if position is not None:
            reference["position"] = position
        expected.append(reference)
    if (
        record.get("type") != constraint_type
        or record.get("driving") is not True
        or record.get("active") is not True
        or record.get("virtual") is not False
        or record.get("references") != expected
    ):
        raise NativeSketchError("Sketch Arc Slot inherent constraint changed.")


def verify_sketch_arc_slot(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchArcSlot = draft.value["prepared"]
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    definitions = spec.arc_definitions
    arcs = tuple(draft.value["arc_indices"])
    expected_arcs = tuple(range(base_geometry, base_geometry + len(definitions)))
    if arcs != expected_arcs:
        raise NativeSketchError("Sketch Arc Slot arc indices changed.")
    expected_constraints = _constraint_definitions(
        arcs,
        reversed_angle=spec.angle_reversed,
        has_inner_boundary=spec.has_inner_boundary,
    )
    constraint_indices = tuple(draft.value["constraint_indices"])
    if constraint_indices != tuple(
        range(base_constraint, base_constraint + len(expected_constraints))
    ):
        raise NativeSketchError("Sketch Arc Slot constraint indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=len(definitions),
        constraints_added=len(expected_constraints),
    )

    arc_records = [serialize_sketch_geometry(sketch, index) for index in arcs]
    for record, definition in zip(arc_records, definitions, strict=True):
        verify_circular_arc_record(
            record,
            center_mm=definition.center_mm,
            radius_mm=definition.radius_mm,
            first_parameter=definition.first_parameter,
            last_parameter=definition.last_parameter,
            start_mm=definition.start_mm,
            end_mm=definition.end_mm,
            label=f"Arc Slot {definition.role}",
        )
    constraint_records = [
        serialize_sketch_constraint(sketch, index) for index in constraint_indices
    ]
    for record, (constraint_type, references) in zip(
        constraint_records,
        expected_constraints,
        strict=True,
    ):
        _verify_constraint(record, constraint_type, references)
    return sketch_geometry_result(
        sketch,
        {
            "arcs": arc_records,
            "arc_roles": {
                definition.role: index
                for definition, index in zip(definitions, arcs, strict=True)
            },
            "constraints": constraint_records,
            "center_mm": [*spec.center_mm, 0.0],
            "centerline_radius_mm": spec.centerline_radius_mm,
            "start_angle_degrees": spec.start_angle_degrees,
            "sweep_angle_degrees": spec.sweep_angle_degrees,
            "slot_radius_mm": spec.slot_radius_mm,
            "clockwise": spec.angle_reversed,
            "inner_boundary_present": spec.has_inner_boundary,
            "closed": True,
        },
    )
