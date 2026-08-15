# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact non-periodic interpolated B-spline in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchControlBSpline import MAX_NATIVE_BSPLINE_POINTS
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


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "interpolation_points_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchInterpolatedBSplineSpec:
    target: ActiveSketchTargetSpec
    interpolation_points_mm: tuple[tuple[float, float], ...]
    periodic: bool
    label: str


@dataclass(frozen=True, slots=True)
class PreparedSketchInterpolatedBSpline:
    insertion: PreparedSketchInsertion
    spec: SketchInterpolatedBSplineSpec


def prepare_interpolated_bspline(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    fields: frozenset[str],
    periodic: bool,
    label: str,
) -> SketchInterpolatedBSplineSpec:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise NativeSketchError(
            f"A Sketch {label} definition has incorrect fields."
        )
    raw_points = value.get("interpolation_points_mm")
    if (
        not isinstance(raw_points, list)
        or not 2 <= len(raw_points) <= MAX_NATIVE_BSPLINE_POINTS
    ):
        raise NativeSketchError(
            f"Sketch {label} interpolation_points_mm must contain "
            f"2 through {MAX_NATIVE_BSPLINE_POINTS} points."
        )
    points = tuple(
        sketch_point_2d(point, f"interpolation_points_mm[{index}]")
        for index, point in enumerate(raw_points)
    )
    for index, (first, second) in enumerate(zip(points, points[1:])):
        if math.hypot(second[0] - first[0], second[1] - first[1]) <= (
            MIN_SKETCH_GEOMETRY_LENGTH_MM
        ):
            raise NativeSketchError(
                f"Sketch {label} adjacent interpolation points "
                f"{index} and {index + 1} must be distinct."
            )
    if periodic and math.hypot(
        points[-1][0] - points[0][0],
        points[-1][1] - points[0][1],
    ) <= MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError(
            f"Sketch {label} final interpolation point must differ from its first; "
            "periodic closure does not add a duplicate point."
        )
    return SketchInterpolatedBSplineSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        points,
        periodic,
        label,
    )


def prepare_sketch_interpolated_bspline(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchInterpolatedBSplineSpec:
    return prepare_interpolated_bspline(
        document_uid,
        value,
        fields=_FIELDS,
        periodic=False,
        label="interpolated B-spline",
    )


def preflight_sketch_interpolated_bspline(
    context: NativeRuntimeContext,
    spec: SketchInterpolatedBSplineSpec,
) -> PreparedSketchInterpolatedBSpline:
    if not isinstance(spec, SketchInterpolatedBSplineSpec):
        raise TypeError("spec must be a SketchInterpolatedBSplineSpec")
    return PreparedSketchInterpolatedBSpline(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def _interpolated_curve(
    points: tuple[tuple[float, float], ...],
    periodic: bool,
):
    import FreeCAD as App
    import Part

    curve = Part.BSplineCurve()
    curve.interpolate([App.Vector(*point, 0.0) for point in points], periodic)
    curve.increaseDegree(3)
    return curve


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


def create_sketch_interpolated_bspline(
    document: Any,
    prepared: PreparedSketchInterpolatedBSpline,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchInterpolatedBSpline):
        raise TypeError("prepared must be a PreparedSketchInterpolatedBSpline")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after interpolated B-spline preflight",
    )
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part
    import Sketcher

    interpolation_indices = []
    for offset, point in enumerate(spec.interpolation_points_mm):
        interpolation_indices.append(
            _exact_index(
                sketch.addGeometry(Part.Point(App.Vector(*point, 0.0)), True),
                base_geometry + offset,
                f"{spec.label} input point",
            )
        )

    curve = _interpolated_curve(spec.interpolation_points_mm, spec.periodic)
    spline_index = _exact_index(
        sketch.addGeometry(curve, False),
        base_geometry + len(interpolation_indices),
        spec.label,
    )
    input_constraints = []
    knot_number = 0
    for offset, geometry_index in enumerate(interpolation_indices):
        if not spec.periodic and len(interpolation_indices) == 3 and offset == 1:
            input_constraints.append(
                Sketcher.Constraint("PointOnObject", geometry_index, 1, spline_index)
            )
        else:
            input_constraints.append(
                Sketcher.Constraint(
                    "InternalAlignment:Sketcher::BSplineKnotPoint",
                    geometry_index,
                    1,
                    spline_index,
                    knot_number,
                )
            )
            knot_number += 1
    input_constraint_indices = _exact_indices(
        sketch.addConstraint(input_constraints),
        tuple(range(base_constraint, base_constraint + len(input_constraints))),
        f"{spec.label} input constraint",
    )

    pole_count = int(curve.NbPoles)
    missing_knot_count = int(curve.NbKnots) - knot_number
    if missing_knot_count < 0:
        raise NativeSketchError(f"Sketch {spec.label} has an invalid knot count.")
    exposure = sketch.exposeInternalGeometry(spline_index)
    if not isinstance(exposure, Mapping):
        raise NativeSketchError(f"Sketcher did not expose {spec.label} internals.")
    expected_control_indices = tuple(
        range(spline_index + 1, spline_index + 1 + pole_count)
    )
    expected_knot_indices = tuple(
        range(
            spline_index + 1 + pole_count,
            spline_index + 1 + pole_count + missing_knot_count,
        )
    )
    created = exposure.get("created")
    observed_control_indices = tuple(
        int(item.get("geometry_index", -1))
        for item in created
        if isinstance(item, Mapping) and item.get("role") == "BSplineControlPoint"
    ) if isinstance(created, list) else ()
    observed_knot_indices = tuple(
        int(item.get("geometry_index", -1))
        for item in created
        if isinstance(item, Mapping) and item.get("role") == "BSplineKnotPoint"
    ) if isinstance(created, list) else ()
    if (
        int(exposure.get("source_geometry_index", -1)) != spline_index
        or int(exposure.get("geometry_count_before", -1)) != spline_index + 1
        or int(exposure.get("geometry_count_after", -1))
        != spline_index + 1 + pole_count + missing_knot_count
        or int(exposure.get("created_count", -1))
        != pole_count + missing_knot_count
        or observed_control_indices != expected_control_indices
        or observed_knot_indices != expected_knot_indices
    ):
        raise NativeSketchError(
            "Sketcher exposed unexpected interpolated B-spline internal geometry."
        )
    control_constraint_indices = tuple(
        range(
            base_constraint + len(input_constraint_indices),
            base_constraint + len(input_constraint_indices) + 2 * pole_count,
        )
    )
    knot_constraint_indices = tuple(
        range(
            base_constraint + len(input_constraint_indices) + 2 * pole_count,
            base_constraint
            + len(input_constraint_indices)
            + 2 * pole_count
            + missing_knot_count,
        )
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "interpolation_indices": tuple(interpolation_indices),
            "spline_index": spline_index,
            "input_constraint_indices": input_constraint_indices,
            "control_indices": expected_control_indices,
            "control_constraint_indices": control_constraint_indices,
            "knot_indices": expected_knot_indices,
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
) -> list[dict[str, int]]:
    result = [{"slot": 1, "geometry_index": first}]
    if first_position is not None:
        result[0]["position"] = first_position
    if second is not None:
        result.append({"slot": 2, "geometry_index": second})
    return result


def _vectors_match(actual: Any, expected: Any) -> bool:
    return bool(
        isinstance(actual, list)
        and len(actual) == len(expected)
        and all(
            same_sketch_vector(value, (point.x, point.y, point.z))
            for value, point in zip(actual, expected, strict=True)
        )
    )


def _numbers_match(actual: Any, expected: Any) -> bool:
    return bool(
        isinstance(actual, list)
        and len(actual) == len(expected)
        and all(
            same_sketch_number(value, target)
            for value, target in zip(actual, expected, strict=True)
        )
    )


def _verify_spline_record(record: Mapping[str, Any], expected: Any) -> None:
    poles = expected.getPoles()
    weights = expected.getWeights()
    knots = expected.getKnots()
    multiplicities = expected.getMultiplicities()
    if (
        record.get("type_id") != "Part::GeomBSplineCurve"
        or record.get("kind") != "b_spline"
        or bool(record.get("construction"))
        or bool(record.get("blocked"))
        or record.get("degree") != int(expected.Degree)
        or record.get("pole_count") != int(expected.NbPoles)
        or record.get("knot_count") != int(expected.NbKnots)
        or record.get("rational") is not bool(expected.isRational())
        or record.get("periodic") is not bool(expected.isPeriodic())
        or record.get("closed") is not bool(expected.isClosed())
        or not _vectors_match(record.get("poles_mm"), poles)
        or not _numbers_match(record.get("weights"), weights)
        or not _numbers_match(record.get("knots"), knots)
        or record.get("multiplicities") != list(multiplicities)
        or not same_sketch_number(record.get("first_parameter"), expected.FirstParameter)
        or not same_sketch_number(record.get("last_parameter"), expected.LastParameter)
        or not same_sketch_vector(
            record.get("start_mm"),
            (expected.StartPoint.x, expected.StartPoint.y, expected.StartPoint.z),
        )
        or not same_sketch_vector(
            record.get("end_mm"),
            (expected.EndPoint.x, expected.EndPoint.y, expected.EndPoint.z),
        )
    ):
        raise NativeSketchError(
            "Sketch interpolated B-spline geometry differs from its exact definition."
        )


def verify_sketch_interpolated_bspline(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchInterpolatedBSpline = draft.value["prepared"]
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    interpolation_indices = tuple(draft.value["interpolation_indices"])
    spline_index = int(draft.value["spline_index"])
    input_constraint_indices = tuple(draft.value["input_constraint_indices"])
    control_indices = tuple(draft.value["control_indices"])
    control_constraint_indices = tuple(draft.value["control_constraint_indices"])
    knot_indices = tuple(draft.value["knot_indices"])
    knot_constraint_indices = tuple(draft.value["knot_constraint_indices"])
    point_count = len(spec.interpolation_points_mm)
    expected = _interpolated_curve(spec.interpolation_points_mm, spec.periodic)
    pole_count = int(expected.NbPoles)
    input_knot_count = point_count - (
        1 if not spec.periodic and point_count == 3 else 0
    )
    missing_knot_count = int(expected.NbKnots) - input_knot_count
    if (
        interpolation_indices != tuple(range(base_geometry, base_geometry + point_count))
        or spline_index != base_geometry + point_count
        or input_constraint_indices
        != tuple(range(base_constraint, base_constraint + point_count))
        or control_indices
        != tuple(range(spline_index + 1, spline_index + 1 + pole_count))
        or control_constraint_indices
        != tuple(
            range(
                base_constraint + point_count,
                base_constraint + point_count + 2 * pole_count,
            )
        )
        or knot_indices
        != tuple(
            range(
                spline_index + 1 + pole_count,
                spline_index + 1 + pole_count + missing_knot_count,
            )
        )
        or knot_constraint_indices
        != tuple(
            range(
                base_constraint + point_count + 2 * pole_count,
                base_constraint
                + point_count
                + 2 * pole_count
                + missing_knot_count,
            )
        )
    ):
        raise NativeSketchError(
            "Sketch interpolated B-spline durable indices changed."
        )
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=point_count + 1 + pole_count + missing_knot_count,
        constraints_added=point_count + 2 * pole_count + missing_knot_count,
    )

    input_points = [
        serialize_sketch_geometry(sketch, index) for index in interpolation_indices
    ]
    for offset, (record, point) in enumerate(
        zip(input_points, spec.interpolation_points_mm, strict=True)
    ):
        expected_internal = (
            None
            if not spec.periodic and point_count == 3 and offset == 1
            else "BSplineKnotPoint"
        )
        if (
            record.get("type_id") != "Part::GeomPoint"
            or record.get("kind") != "point"
            or record.get("construction") is not True
            or bool(record.get("blocked"))
            or record.get("internal_type") != expected_internal
            or not same_sketch_point(record.get("position_mm"), point)
        ):
            raise NativeSketchError(
                "Sketch interpolated B-spline input point changed."
            )

    spline = serialize_sketch_geometry(sketch, spline_index)
    _verify_spline_record(spline, expected)
    input_constraints = [
        serialize_sketch_constraint(sketch, index)
        for index in input_constraint_indices
    ]
    for offset, (geometry_index, record) in enumerate(
        zip(interpolation_indices, input_constraints, strict=True)
    ):
        constraint_type = (
            "PointOnObject"
            if not spec.periodic and point_count == 3 and offset == 1
            else "InternalAlignment"
        )
        if (
            not _active_constraint(record, constraint_type)
            or record.get("references")
            != _references(geometry_index, 1, spline_index)
        ):
            raise NativeSketchError(
                "Sketch interpolated B-spline input alignment changed."
            )

    controls = [serialize_sketch_geometry(sketch, index) for index in control_indices]
    expected_poles = expected.getPoles()
    durable_radius = controls[0].get("radius_mm")
    if type(durable_radius) not in {int, float} or float(durable_radius) <= 0.0:
        raise NativeSketchError(
            "Sketch interpolated B-spline control-point weight is invalid."
        )
    for record, pole in zip(controls, expected_poles, strict=True):
        if (
            record.get("type_id") != "Part::GeomCircle"
            or record.get("kind") != "circle"
            or record.get("construction") is not True
            or bool(record.get("blocked"))
            or record.get("internal_type") != "BSplineControlPoint"
            or not same_sketch_vector(
                record.get("center_mm"),
                (pole.x, pole.y, pole.z),
            )
            or not same_sketch_vector(record.get("axis"), (0.0, 0.0, 1.0))
            or not same_sketch_number(record.get("radius_mm"), durable_radius)
            or record.get("closed") is not True
        ):
            raise NativeSketchError(
                "Sketch interpolated B-spline control-point handle changed."
            )

    control_constraints = [
        serialize_sketch_constraint(sketch, index)
        for index in control_constraint_indices
    ]
    for offset, geometry_index in enumerate(control_indices):
        alignment = control_constraints[2 * offset]
        weight = control_constraints[2 * offset + 1]
        if (
            not _active_constraint(alignment, "InternalAlignment")
            or alignment.get("references")
            != _references(geometry_index, 3, spline_index)
        ):
            raise NativeSketchError(
                "Sketch interpolated B-spline control alignment changed."
            )
        if offset == 0:
            valid_weight = (
                _active_constraint(weight, "Weight")
                and weight.get("references") == _references(geometry_index)
                and same_sketch_number(weight.get("value"), 1.0)
            )
        else:
            valid_weight = (
                _active_constraint(weight, "Equal")
                and weight.get("references")
                == _references(geometry_index, second=control_indices[0])
            )
        if not valid_weight:
            raise NativeSketchError(
                "Sketch interpolated B-spline control weight changed."
            )

    exposed_knots = [
        serialize_sketch_geometry(sketch, index) for index in knot_indices
    ]
    expected_knots = expected.getKnots()[-missing_knot_count:] if missing_knot_count else []
    for record, parameter in zip(exposed_knots, expected_knots, strict=True):
        position = expected.value(parameter)
        if (
            record.get("type_id") != "Part::GeomPoint"
            or record.get("kind") != "point"
            or record.get("construction") is not True
            or bool(record.get("blocked"))
            or record.get("internal_type") != "BSplineKnotPoint"
            or not same_sketch_vector(
                record.get("position_mm"),
                (position.x, position.y, position.z),
            )
        ):
            raise NativeSketchError(
                "Sketch interpolated B-spline exposed knot changed."
            )
    knot_constraints = [
        serialize_sketch_constraint(sketch, index)
        for index in knot_constraint_indices
    ]
    for geometry_index, record in zip(
        knot_indices,
        knot_constraints,
        strict=True,
    ):
        if (
            not _active_constraint(record, "InternalAlignment")
            or record.get("references")
            != _references(geometry_index, 1, spline_index)
        ):
            raise NativeSketchError(
                "Sketch interpolated B-spline exposed knot alignment changed."
            )

    return sketch_geometry_result(
        sketch,
        {
            "spline": spline,
            "interpolation_point_handles": input_points,
            "control_point_handles": controls,
            "exposed_knot_points": exposed_knots,
            "constraints": [
                *input_constraints,
                *control_constraints,
                *knot_constraints,
            ],
            "interpolation_points_mm": [
                list(point) + [0.0] for point in spec.interpolation_points_mm
            ],
            "effective_degree": int(expected.Degree),
            "periodic": spec.periodic,
            "construction": False,
        },
    )
