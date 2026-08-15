# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact control-point B-spline construction for Native Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MIN_SKETCH_GEOMETRY_LENGTH_MM,
    same_sketch_number,
    same_sketch_point,
    same_sketch_vector,
    sketch_point_2d,
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


MAX_NATIVE_BSPLINE_POINTS = 24
MAX_NATIVE_BSPLINE_DEGREE = 25


@dataclass(frozen=True, slots=True)
class SketchControlBSplineSpec:
    target: ActiveSketchTargetSpec
    control_points_mm: tuple[tuple[float, float], ...]
    requested_degree: int
    periodic: bool
    label: str

    @property
    def effective_degree(self) -> int:
        maximum = len(self.control_points_mm) if self.periodic else (
            len(self.control_points_mm) - 1
        )
        return min(maximum, self.requested_degree)

    @property
    def knots(self) -> tuple[float, ...]:
        count = (
            len(self.control_points_mm) + 1
            if self.periodic
            else len(self.control_points_mm) - self.effective_degree + 1
        )
        return tuple(float(index) for index in range(count))

    @property
    def multiplicities(self) -> tuple[int, ...]:
        values = [1] * len(self.knots)
        if not self.periodic:
            values[0] = self.effective_degree + 1
            values[-1] = self.effective_degree + 1
        return tuple(values)

    @property
    def weights(self) -> tuple[float, ...]:
        return (1.0,) * len(self.control_points_mm)


@dataclass(frozen=True, slots=True)
class PreparedSketchControlBSpline:
    insertion: PreparedSketchInsertion
    spec: SketchControlBSplineSpec


def prepare_control_bspline(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    fields: frozenset[str],
    periodic: bool,
    label: str,
) -> SketchControlBSplineSpec:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise NativeSketchError(f"A Sketch {label} definition has incorrect fields.")
    raw_points = value.get("control_points_mm")
    minimum = 2
    if (
        not isinstance(raw_points, list)
        or not minimum <= len(raw_points) <= MAX_NATIVE_BSPLINE_POINTS
    ):
        raise NativeSketchError(
            f"Sketch {label} control_points_mm must contain {minimum} through "
            f"{MAX_NATIVE_BSPLINE_POINTS} points."
        )
    points = tuple(
        sketch_point_2d(point, f"{label} control_points_mm[{index}]")
        for index, point in enumerate(raw_points)
    )
    for index, (first, second) in enumerate(zip(points, points[1:])):
        if math.hypot(second[0] - first[0], second[1] - first[1]) <= (
            MIN_SKETCH_GEOMETRY_LENGTH_MM
        ):
            raise NativeSketchError(
                f"Sketch {label} adjacent control points {index} and {index + 1} "
                "must be distinct."
            )
    if periodic and math.hypot(
        points[-1][0] - points[0][0],
        points[-1][1] - points[0][1],
    ) <= MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError(
            f"Sketch {label} final control point must differ from its first; "
            "periodic closure does not add a duplicate pole."
        )
    degree = value.get("degree")
    if (
        type(degree) is not int
        or degree < 1
        or degree > MAX_NATIVE_BSPLINE_DEGREE
    ):
        raise NativeSketchError(
            f"Sketch {label} degree must be an integer from 1 through "
            f"{MAX_NATIVE_BSPLINE_DEGREE}."
        )
    return SketchControlBSplineSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        points,
        degree,
        periodic,
        label,
    )


def preflight_control_bspline(
    context: NativeRuntimeContext,
    spec: SketchControlBSplineSpec,
) -> PreparedSketchControlBSpline:
    if not isinstance(spec, SketchControlBSplineSpec):
        raise TypeError("spec must be a SketchControlBSplineSpec")
    return PreparedSketchControlBSpline(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def _exact_index(raw: Any, expected: int, label: str) -> int:
    result = int(raw)
    if result != expected:
        raise NativeSketchError(f"Sketcher returned an unexpected {label} index.")
    return result


def _exact_indices(raw: Any, expected: tuple[int, ...], label: str) -> tuple[int, ...]:
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    result = tuple(int(value) for value in values)
    if result != expected:
        raise NativeSketchError(f"Sketcher returned unexpected {label} indices.")
    return result


def create_control_bspline(
    document: Any,
    prepared: PreparedSketchControlBSpline,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchControlBSpline):
        raise TypeError("prepared must be a PreparedSketchControlBSpline")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage=f"after {prepared.spec.label} preflight",
    )
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part
    import Sketcher

    control_indices = []
    weight_indices = []
    first_control = base_geometry
    for offset, point in enumerate(spec.control_points_mm):
        geometry_index = _exact_index(
            sketch.addGeometry(
                Part.Circle(
                    App.Vector(*point, 0.0),
                    App.Vector(0.0, 0.0, 1.0),
                    10.0,
                ),
                True,
            ),
            base_geometry + offset,
            f"{spec.label} control-point circle",
        )
        control_indices.append(geometry_index)
        constraint = (
            Sketcher.Constraint("Weight", geometry_index, 1.0)
            if offset == 0
            else Sketcher.Constraint("Equal", first_control, geometry_index)
        )
        weight_indices.append(
            _exact_index(
                sketch.addConstraint(constraint),
                base_constraint + offset,
                f"{spec.label} control-point weight constraint",
            )
        )

    poles = [App.Vector(*point, 0.0) for point in spec.control_points_mm]
    curve = Part.BSplineCurve(
        poles,
        list(spec.multiplicities),
        list(spec.knots),
        spec.periodic,
        spec.effective_degree,
        list(spec.weights),
        False,
    )
    spline_index = _exact_index(
        sketch.addGeometry(curve, False),
        base_geometry + len(control_indices),
        spec.label,
    )
    control_constraints = [
        Sketcher.Constraint(
            "InternalAlignment:Sketcher::BSplineControlPoint",
            geometry_index,
            3,
            spline_index,
            pole_index,
        )
        for pole_index, geometry_index in enumerate(control_indices)
    ]
    control_alignment_indices = _exact_indices(
        sketch.addConstraint(control_constraints),
        tuple(
            range(
                base_constraint + len(weight_indices),
                base_constraint + len(weight_indices) + len(control_indices),
            )
        ),
        f"{spec.label} control-point alignment constraint",
    )
    exposure = sketch.exposeInternalGeometry(spline_index)
    if not isinstance(exposure, Mapping):
        raise NativeSketchError(f"Sketcher did not expose {spec.label} knot points.")
    expected_knot_indices = tuple(
        range(
            spline_index + 1,
            spline_index + 1 + len(spec.knots),
        )
    )
    created = exposure.get("created")
    observed_knot_indices = tuple(
        int(item.get("geometry_index", -1))
        for item in created
        if isinstance(item, Mapping) and item.get("role") == "BSplineKnotPoint"
    ) if isinstance(created, list) else ()
    if (
        int(exposure.get("source_geometry_index", -1)) != spline_index
        or int(exposure.get("geometry_count_before", -1)) != spline_index + 1
        or int(exposure.get("geometry_count_after", -1))
        != spline_index + 1 + len(spec.knots)
        or int(exposure.get("created_count", -1)) != len(spec.knots)
        or observed_knot_indices != expected_knot_indices
    ):
        raise NativeSketchError(
            f"Sketcher exposed unexpected {spec.label} internal geometry."
        )
    knot_constraint_indices = tuple(
        range(
            base_constraint + len(weight_indices) + len(control_alignment_indices),
            base_constraint
            + len(weight_indices)
            + len(control_alignment_indices)
            + len(spec.knots),
        )
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "control_indices": tuple(control_indices),
            "spline_index": spline_index,
            "knot_indices": expected_knot_indices,
            "weight_indices": tuple(weight_indices),
            "control_alignment_indices": control_alignment_indices,
            "knot_constraint_indices": knot_constraint_indices,
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _active_constraint(record: Mapping[str, Any], constraint_type: str) -> bool:
    return bool(
        record.get("type") == constraint_type
        and record.get("driving") is True
        and record.get("active") is True
        and record.get("virtual") is False
    )


def _references(
    first: int,
    first_position: int | None = None,
    second: int | None = None,
    second_position: int | None = None,
) -> list[dict[str, int]]:
    result = [{"slot": 1, "geometry_index": first}]
    if first_position is not None:
        result[0]["position"] = first_position
    if second is not None:
        reference = {"slot": 2, "geometry_index": second}
        if second_position is not None:
            reference["position"] = second_position
        result.append(reference)
    return result


def _verify_bspline_record(
    record: Mapping[str, Any],
    spec: SketchControlBSplineSpec,
) -> None:
    expected_poles = [(*point, 0.0) for point in spec.control_points_mm]
    poles = record.get("poles_mm")
    weights = record.get("weights")
    knots = record.get("knots")
    multiplicities = record.get("multiplicities")
    endpoint_shape_matches = (
        record.get("closed") is True
        and same_sketch_vector(record.get("start_mm"), record.get("end_mm"))
        if spec.periodic
        else same_sketch_point(record.get("start_mm"), spec.control_points_mm[0])
        and same_sketch_point(record.get("end_mm"), spec.control_points_mm[-1])
    )
    if (
        record.get("type_id") != "Part::GeomBSplineCurve"
        or record.get("kind") != "b_spline"
        or bool(record.get("construction"))
        or bool(record.get("blocked"))
        or record.get("degree") != spec.effective_degree
        or record.get("pole_count") != len(expected_poles)
        or record.get("knot_count") != len(spec.knots)
        or record.get("rational") is not False
        or record.get("periodic") is not spec.periodic
        or not isinstance(poles, list)
        or len(poles) != len(expected_poles)
        or not all(
            same_sketch_vector(actual, expected)
            for actual, expected in zip(poles, expected_poles, strict=True)
        )
        or not isinstance(weights, list)
        or len(weights) != len(spec.weights)
        or not all(
            same_sketch_number(actual, expected)
            for actual, expected in zip(weights, spec.weights, strict=True)
        )
        or not isinstance(knots, list)
        or len(knots) != len(spec.knots)
        or not all(
            same_sketch_number(actual, expected)
            for actual, expected in zip(knots, spec.knots, strict=True)
        )
        or multiplicities != list(spec.multiplicities)
        or not same_sketch_number(record.get("first_parameter"), spec.knots[0])
        or not same_sketch_number(record.get("last_parameter"), spec.knots[-1])
        or not endpoint_shape_matches
    ):
        raise NativeSketchError(
            f"Sketch {spec.label} geometry differs from its exact definition."
        )


def verify_control_bspline(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchControlBSpline = draft.value["prepared"]
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    control_indices = tuple(draft.value["control_indices"])
    spline_index = int(draft.value["spline_index"])
    knot_indices = tuple(draft.value["knot_indices"])
    weight_indices = tuple(draft.value["weight_indices"])
    control_alignment_indices = tuple(draft.value["control_alignment_indices"])
    knot_constraint_indices = tuple(draft.value["knot_constraint_indices"])
    point_count = len(spec.control_points_mm)
    knot_count = len(spec.knots)
    if (
        control_indices != tuple(range(base_geometry, base_geometry + point_count))
        or spline_index != base_geometry + point_count
        or knot_indices
        != tuple(range(spline_index + 1, spline_index + 1 + knot_count))
        or weight_indices
        != tuple(range(base_constraint, base_constraint + point_count))
        or control_alignment_indices
        != tuple(
            range(
                base_constraint + point_count,
                base_constraint + 2 * point_count,
            )
        )
        or knot_constraint_indices
        != tuple(
            range(
                base_constraint + 2 * point_count,
                base_constraint + 2 * point_count + knot_count,
            )
        )
    ):
        raise NativeSketchError(f"Sketch {spec.label} durable indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=point_count + 1 + knot_count,
        constraints_added=2 * point_count + knot_count,
    )

    controls = [serialize_sketch_geometry(sketch, index) for index in control_indices]
    for record, expected in zip(controls, spec.control_points_mm, strict=True):
        if (
            record.get("type_id") != "Part::GeomCircle"
            or record.get("kind") != "circle"
            or record.get("construction") is not True
            or bool(record.get("blocked"))
            or record.get("internal_type") != "BSplineControlPoint"
            or not same_sketch_point(record.get("center_mm"), expected)
            or not same_sketch_vector(record.get("axis"), (0.0, 0.0, 1.0))
            or record.get("closed") is not True
            or not same_sketch_number(record.get("radius_mm"), controls[0].get("radius_mm"))
            or float(record.get("radius_mm", 0.0)) <= 0.0
        ):
            raise NativeSketchError(
                f"Sketch {spec.label} control-point handle changed."
            )

    spline = serialize_sketch_geometry(sketch, spline_index)
    _verify_bspline_record(spline, spec)
    knots = [serialize_sketch_geometry(sketch, index) for index in knot_indices]
    live_curve = sketch.Geometry[spline_index]
    for record, parameter in zip(knots, spec.knots, strict=True):
        expected = live_curve.value(parameter)
        if (
            record.get("type_id") != "Part::GeomPoint"
            or record.get("kind") != "point"
            or record.get("construction") is not True
            or bool(record.get("blocked"))
            or record.get("internal_type") != "BSplineKnotPoint"
            or not same_sketch_vector(
                record.get("position_mm"),
                (float(expected.x), float(expected.y), float(expected.z)),
            )
        ):
            raise NativeSketchError(f"Sketch {spec.label} knot point changed.")

    weight_constraints = [
        serialize_sketch_constraint(sketch, index) for index in weight_indices
    ]
    if (
        not _active_constraint(weight_constraints[0], "Weight")
        or weight_constraints[0].get("references") != _references(control_indices[0])
        or not same_sketch_number(weight_constraints[0].get("value"), 1.0)
    ):
        raise NativeSketchError(f"Sketch {spec.label} first pole weight changed.")
    for offset, record in enumerate(weight_constraints[1:], start=1):
        if (
            not _active_constraint(record, "Equal")
            or record.get("references")
            != _references(control_indices[0], second=control_indices[offset])
        ):
            raise NativeSketchError(f"Sketch {spec.label} equal pole weight changed.")

    control_constraints = [
        serialize_sketch_constraint(sketch, index)
        for index in control_alignment_indices
    ]
    for geometry_index, record in zip(
        control_indices,
        control_constraints,
        strict=True,
    ):
        if (
            not _active_constraint(record, "InternalAlignment")
            or record.get("references")
            != _references(geometry_index, 3, spline_index)
        ):
            raise NativeSketchError(
                f"Sketch {spec.label} control-point alignment changed."
            )
    knot_constraints = [
        serialize_sketch_constraint(sketch, index)
        for index in knot_constraint_indices
    ]
    for geometry_index, record in zip(knot_indices, knot_constraints, strict=True):
        if (
            not _active_constraint(record, "InternalAlignment")
            or record.get("references")
            != _references(geometry_index, 1, spline_index)
        ):
            raise NativeSketchError(f"Sketch {spec.label} knot alignment changed.")

    return sketch_geometry_result(
        sketch,
        {
            "spline": spline,
            "control_point_handles": controls,
            "knot_points": knots,
            "constraints": [
                *weight_constraints,
                *control_constraints,
                *knot_constraints,
            ],
            "control_points_mm": [list(point) + [0.0] for point in spec.control_points_mm],
            "requested_degree": spec.requested_degree,
            "effective_degree": spec.effective_degree,
            "periodic": spec.periodic,
            "construction": False,
        },
    )
