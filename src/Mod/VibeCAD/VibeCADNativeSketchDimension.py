# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic contextual Dimension inference for the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import (
    add_exact_constraint,
    diagnose_exact_constraint,
    make_dimensional_constraint,
    sketch_solver_issues,
    verify_exact_constraint_append,
)
from VibeCADNativeSketchConstraintTargets import (
    PreparedSketchConstraintTarget,
    SketchConstraintElement,
    SketchConstraintTargetSpec,
    current_sketch_constraint_records,
    preflight_sketch_constraint_target,
    prepare_sketch_constraint_target,
    require_unchanged_sketch_constraint_target,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
        "expected_inference",
        "dimension",
        "driving",
    }
)
_DIMENSION_FIELDS = frozenset({"value", "unit"})
_INFERENCES = frozenset({"distance_x", "distance_y", "distance", "angle"})
_DISTANCE_INFERENCES = frozenset({"distance_x", "distance_y", "distance"})
_POSITION_NAMES = {0: "whole", 1: "start", 2: "end", 3: "center"}
_LINE_TYPES = frozenset({"Part::GeomLineSegment"})
_CIRCLE_TYPES = frozenset({"Part::GeomCircle", "Part::GeomArcOfCircle"})
_LINEAR_TOLERANCE = 1.0e-7
_ANGULAR_TOLERANCE = 1.0e-10
_MAX_DIMENSION = 1_000_000.0
_LABEL = "Sketch Dimension"


@dataclass(frozen=True, slots=True)
class SketchDimensionValue:
    value: float
    unit: str


@dataclass(frozen=True, slots=True)
class SketchDimensionSpec:
    target: SketchConstraintTargetSpec
    expected_inference: str
    dimension: SketchDimensionValue
    driving: bool


@dataclass(frozen=True, slots=True)
class InferredSketchDimension:
    inference: str
    constraint_type: str
    constructor_form: str
    references: tuple[SketchConstraintElement, ...]
    measured_value: float


@dataclass(frozen=True, slots=True)
class PreparedSketchDimension:
    target: PreparedSketchConstraintTarget
    spec: SketchDimensionSpec
    inferred: InferredSketchDimension
    solver_issues: tuple[tuple[int, ...], ...]


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeSketchError(f"Sketch Dimension {label} must be a number.")
    result = float(value)
    if not math.isfinite(result) or not _LINEAR_TOLERANCE <= result <= _MAX_DIMENSION:
        raise NativeSketchError(
            f"Sketch Dimension {label} must be from {_LINEAR_TOLERANCE} to "
            f"{_MAX_DIMENSION}."
        )
    return result


def _dimension(value: Any, expected_inference: str) -> SketchDimensionValue:
    if not isinstance(value, Mapping) or set(value) != _DIMENSION_FIELDS:
        raise NativeSketchError("Sketch Dimension dimension has incorrect fields.")
    unit = value["unit"]
    expected_unit = "deg" if expected_inference == "angle" else "mm"
    if unit != expected_unit:
        raise NativeSketchError(
            f"Sketch Dimension {expected_inference} requires unit {expected_unit}."
        )
    result = _number(value["value"], "value")
    if expected_inference == "angle" and result >= 180.0:
        raise NativeSketchError(
            "Sketch Dimension angle must be greater than zero and less than 180 degrees."
        )
    return SketchDimensionValue(result, unit)


def prepare_sketch_dimension(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchDimensionSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError("A Sketch Dimension definition has incorrect fields.")
    expected_inference = value["expected_inference"]
    if not isinstance(expected_inference, str) or expected_inference not in _INFERENCES:
        raise NativeSketchError(
            "Sketch Dimension expected_inference must be distance_x, distance_y, "
            "distance, or angle."
        )
    driving = value["driving"]
    if type(driving) is not bool:
        raise NativeSketchError("Sketch Dimension driving must be a boolean.")
    return SketchDimensionSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value[
                "expected_external_geometry_count"
            ],
            selection=value["selection"],
        ),
        expected_inference,
        _dimension(value["dimension"], expected_inference),
        driving,
    )


def _vector_2d(value: Any, label: str) -> tuple[float, float]:
    try:
        x = float(value.x)
        y = float(value.y)
    except Exception as exc:
        raise NativeSketchError(f"Sketch Dimension {label} is unavailable.") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise NativeSketchError(f"Sketch Dimension {label} is not finite.")
    return x, y


def _point(sketch: Any, element: SketchConstraintElement) -> tuple[float, float]:
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError("Sketch Dimension point lookup is unavailable.")
    try:
        value = getter(element.geometry_index, element.position_code)
    except Exception as exc:
        raise NativeSketchError(
            f"Sketch Dimension point {element.geometry_index}:{element.position} "
            "is unavailable."
        ) from exc
    return _vector_2d(value, "point")


def _geometry_type(geometry: Any) -> str:
    return str(getattr(geometry, "TypeId", "") or "")


def _whole_kind(sketch: Any, element: SketchConstraintElement) -> str:
    if element.position != "whole":
        return "point"
    geometry_type = _geometry_type(
        sketch_constraint_geometry(sketch, element.geometry_index)
    )
    if geometry_type in _LINE_TYPES:
        return "line"
    if geometry_type in _CIRCLE_TYPES:
        return "circle"
    if geometry_type == "Part::GeomPoint":
        raise NativeSketchError(
            "A standalone Sketch point must be targeted at its start position."
        )
    raise NativeSketchError(
        f"Sketch Dimension does not infer a dimension from whole {geometry_type or 'geometry'}."
    )


def _line_points(sketch: Any, element: SketchConstraintElement) -> tuple:
    if element.position != "whole":
        raise NativeSketchError("Sketch Dimension expected one whole line.")
    if element.geometry_index == -1:
        return (0.0, 0.0), (1.0, 0.0)
    if element.geometry_index == -2:
        return (0.0, 0.0), (0.0, 1.0)
    return (
        _point(sketch, SketchConstraintElement(element.geometry_index, "start")),
        _point(sketch, SketchConstraintElement(element.geometry_index, "end")),
    )


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(second[0] - first[0], second[1] - first[1])


def _axis_inference(
    first_ref: SketchConstraintElement,
    first: tuple[float, float],
    second_ref: SketchConstraintElement,
    second: tuple[float, float],
) -> InferredSketchDimension:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    if abs(dx) <= _LINEAR_TOLERANCE and abs(dy) <= _LINEAR_TOLERANCE:
        raise NativeSketchError(
            "Sketch Dimension cannot dimension coincident points or a zero-length line."
        )
    if abs(dy) <= _LINEAR_TOLERANCE:
        inference = "distance_x"
        measured = abs(dx)
        references = (first_ref, second_ref) if dx >= 0.0 else (second_ref, first_ref)
    elif abs(dx) <= _LINEAR_TOLERANCE:
        inference = "distance_y"
        measured = abs(dy)
        references = (first_ref, second_ref) if dy >= 0.0 else (second_ref, first_ref)
    else:
        raise NativeSketchError(
            "Sketch Dimension selection has horizontal, vertical, and direct distance "
            "possibilities; use an explicit Distance tool."
        )
    return InferredSketchDimension(
        inference,
        "DistanceX" if inference == "distance_x" else "DistanceY",
        "two_points",
        references,
        measured,
    )


def _line_distance(
    point: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
) -> float:
    dx = line_end[0] - line_start[0]
    dy = line_end[1] - line_start[1]
    length = math.hypot(dx, dy)
    if length <= _LINEAR_TOLERANCE:
        raise NativeSketchError("Sketch Dimension cannot target a zero-length line.")
    return abs(
        -point[0] * dy
        + point[1] * dx
        + line_start[0] * line_end[1]
        - line_end[0] * line_start[1]
    ) / length


def _circle_data(sketch: Any, element: SketchConstraintElement) -> tuple:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    center = _vector_2d(
        getattr(geometry, "Center", getattr(geometry, "Location", None)),
        "circle center",
    )
    try:
        radius = float(geometry.Radius)
    except Exception as exc:
        raise NativeSketchError("Sketch Dimension circle radius is unavailable.") from exc
    if not math.isfinite(radius) or radius <= _LINEAR_TOLERANCE:
        raise NativeSketchError("Sketch Dimension circle radius is invalid.")
    return center, radius


def _point_curve_dimension(
    point_ref: SketchConstraintElement,
    curve_ref: SketchConstraintElement,
    measured: float,
) -> InferredSketchDimension:
    if measured <= _LINEAR_TOLERANCE:
        raise NativeSketchError(
            "Sketch Dimension point-to-curve distance is zero; use the matching "
            "geometric constraint."
        )
    return InferredSketchDimension(
        "distance",
        "Distance",
        "point_curve",
        (point_ref, curve_ref),
        measured,
    )


def _curve_curve_dimension(
    first: SketchConstraintElement,
    second: SketchConstraintElement,
    measured: float,
) -> InferredSketchDimension:
    if abs(measured) <= _LINEAR_TOLERANCE:
        raise NativeSketchError(
            "Sketch Dimension curve distance is zero or tangent; use an explicit "
            "geometric constraint."
        )
    return InferredSketchDimension(
        "distance",
        "Distance",
        "two_curves",
        (first, second),
        abs(measured),
    )


def _line_intersection(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> tuple[float, float] | None:
    first_dx = first_end[0] - first_start[0]
    first_dy = first_end[1] - first_start[1]
    second_dx = second_end[0] - second_start[0]
    second_dy = second_end[1] - second_start[1]
    denominator = first_dx * second_dy - first_dy * second_dx
    scale = max(
        math.hypot(first_dx, first_dy) * math.hypot(second_dx, second_dy),
        1.0,
    )
    if abs(denominator) <= _ANGULAR_TOLERANCE * scale:
        return None
    offset_x = second_start[0] - first_start[0]
    offset_y = second_start[1] - first_start[1]
    parameter = (offset_x * second_dy - offset_y * second_dx) / denominator
    return (
        first_start[0] + parameter * first_dx,
        first_start[1] + parameter * first_dy,
    )


def _closest_endpoint(
    start: tuple[float, float],
    end: tuple[float, float],
    point: tuple[float, float],
) -> str:
    return "start" if _distance(point, start) < _distance(point, end) else "end"


def _two_line_dimension(
    sketch: Any,
    first_ref: SketchConstraintElement,
    second_ref: SketchConstraintElement,
) -> InferredSketchDimension:
    first_start, first_end = _line_points(sketch, first_ref)
    second_start, second_end = _line_points(sketch, second_ref)
    first_vector = (
        first_end[0] - first_start[0],
        first_end[1] - first_start[1],
    )
    second_vector = (
        second_end[0] - second_start[0],
        second_end[1] - second_start[1],
    )
    first_length = math.hypot(*first_vector)
    second_length = math.hypot(*second_vector)
    if first_length <= _LINEAR_TOLERANCE or second_length <= _LINEAR_TOLERANCE:
        raise NativeSketchError("Sketch Dimension cannot target a zero-length line.")
    cross = first_vector[0] * second_vector[1] - first_vector[1] * second_vector[0]
    if abs(cross) <= _ANGULAR_TOLERANCE * first_length * second_length:
        measured = _line_distance(second_start, first_start, first_end)
        return _point_curve_dimension(
            SketchConstraintElement(second_ref.geometry_index, "start"),
            first_ref,
            measured,
        )

    intersection = _line_intersection(
        first_start,
        first_end,
        second_start,
        second_end,
    )
    if intersection is None:
        raise NativeSketchError("Sketch Dimension line-angle inference failed.")
    first_position = _closest_endpoint(first_start, first_end, intersection)
    second_position = _closest_endpoint(second_start, second_end, intersection)
    first_direction = first_vector
    second_direction = second_vector
    if first_position == "end":
        first_direction = (-first_direction[0], -first_direction[1])
    if second_position == "end":
        second_direction = (-second_direction[0], -second_direction[1])
    angle = math.atan2(
        first_direction[0] * second_direction[1]
        - first_direction[1] * second_direction[0],
        first_direction[1] * second_direction[1]
        + first_direction[0] * second_direction[0],
    )
    references = (
        SketchConstraintElement(first_ref.geometry_index, first_position),
        SketchConstraintElement(second_ref.geometry_index, second_position),
    )
    if angle < 0.0:
        angle = -angle
        references = (references[1], references[0])
    if angle <= _ANGULAR_TOLERANCE or angle >= math.pi - _ANGULAR_TOLERANCE:
        raise NativeSketchError(
            "Sketch Dimension collinear lines do not have one stable inferred angle."
        )
    return InferredSketchDimension(
        "angle",
        "Angle",
        "two_points",
        references,
        math.degrees(angle),
    )


def _infer_dimension(sketch: Any, selection: tuple) -> InferredSketchDimension:
    kinds = tuple(_whole_kind(sketch, element) for element in selection)
    if len(selection) == 1:
        element = selection[0]
        if kinds[0] == "point":
            root = SketchConstraintElement(-1, "start")
            return _axis_inference(element, _point(sketch, element), root, (0.0, 0.0))
        if kinds[0] == "line":
            if element.geometry_index in {-1, -2}:
                raise NativeSketchError(
                    "Sketch Dimension does not create a length dimension on an axis."
                )
            start = SketchConstraintElement(element.geometry_index, "start")
            end = SketchConstraintElement(element.geometry_index, "end")
            return _axis_inference(start, _point(sketch, start), end, _point(sketch, end))
        raise NativeSketchError(
            "Sketch Dimension radius/diameter choice depends on an explicit tool or "
            "human preference and is not inferred."
        )

    first, second = selection
    first_kind, second_kind = kinds
    if first_kind == second_kind == "point":
        return _axis_inference(first, _point(sketch, first), second, _point(sketch, second))
    if {first_kind, second_kind} == {"point", "line"}:
        point_ref, line_ref = (
            (first, second) if first_kind == "point" else (second, first)
        )
        measured = _line_distance(
            _point(sketch, point_ref),
            *_line_points(sketch, line_ref),
        )
        return _point_curve_dimension(point_ref, line_ref, measured)
    if {first_kind, second_kind} == {"point", "circle"}:
        point_ref, circle_ref = (
            (first, second) if first_kind == "point" else (second, first)
        )
        center, radius = _circle_data(sketch, circle_ref)
        measured = abs(_distance(_point(sketch, point_ref), center) - radius)
        return _point_curve_dimension(point_ref, circle_ref, measured)
    if first_kind == second_kind == "line":
        return _two_line_dimension(sketch, first, second)
    if {first_kind, second_kind} == {"line", "circle"}:
        circle_ref, line_ref = (
            (first, second) if first_kind == "circle" else (second, first)
        )
        center, radius = _circle_data(sketch, circle_ref)
        measured = abs(_line_distance(center, *_line_points(sketch, line_ref)) - radius)
        return _curve_curve_dimension(circle_ref, line_ref, measured)
    if first_kind == second_kind == "circle":
        first_center, first_radius = _circle_data(sketch, first)
        second_center, second_radius = _circle_data(sketch, second)
        center_distance = _distance(first_center, second_center)
        if center_distance >= first_radius and center_distance >= second_radius:
            measured = center_distance - first_radius - second_radius
        else:
            measured = (
                max(first_radius, second_radius)
                - min(first_radius, second_radius)
                - center_distance
            )
        return _curve_curve_dimension(first, second, measured)
    raise NativeSketchError(
        "Sketch Dimension selection does not have one supported dimensional outcome."
    )


def preflight_sketch_dimension(
    context: NativeRuntimeContext,
    spec: SketchDimensionSpec,
) -> PreparedSketchDimension:
    if not isinstance(spec, SketchDimensionSpec):
        raise TypeError("spec must be a SketchDimensionSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    inferred = _infer_dimension(target.target.sketch, spec.target.selection)
    if inferred.inference != spec.expected_inference:
        raise NativeSketchError(
            f"Sketch Dimension inferred {inferred.inference}, not the expected "
            f"{spec.expected_inference}; read the current Sketch and retry."
        )
    if not spec.driving and not math.isclose(
        inferred.measured_value,
        spec.dimension.value,
        rel_tol=1.0e-9,
        abs_tol=_LINEAR_TOLERANCE,
    ):
        raise NativeSketchError(
            "Sketch reference Dimension measurement changed; read the current Sketch "
            "and retry."
        )
    sketch = target.target.sketch
    solver_issues = sketch_solver_issues(sketch, _LABEL)
    prepared = PreparedSketchDimension(target, spec, inferred, solver_issues)
    constraint = make_dimensional_constraint(
        _constraint_arguments(prepared),
        driving=spec.driving,
    )
    diagnose_exact_constraint(
        sketch,
        constraint,
        expected_index=spec.target.target.expected_constraint_count,
        label=_LABEL,
    )
    geometry, constraints, external = current_sketch_constraint_records(
        sketch,
        spec.target,
    )
    if (
        geometry != target.geometry_records
        or constraints != target.constraint_records
        or external != target.external_geometry_records
        or sketch_solver_issues(sketch, _LABEL) != solver_issues
    ):
        raise NativeSketchError(
            "Sketch Dimension feasibility check changed the active Sketch."
        )
    return prepared


def _constraint_arguments(prepared: PreparedSketchDimension) -> tuple[Any, ...]:
    inferred = prepared.inferred
    value = prepared.spec.dimension.value
    if inferred.inference == "angle":
        value = math.radians(value)
    references = inferred.references
    if inferred.constructor_form == "two_points":
        first, second = references
        return (
            inferred.constraint_type,
            first.geometry_index,
            first.position_code,
            second.geometry_index,
            second.position_code,
            value,
        )
    if inferred.constructor_form == "point_curve":
        point, curve = references
        return (
            inferred.constraint_type,
            point.geometry_index,
            point.position_code,
            curve.geometry_index,
            value,
        )
    if inferred.constructor_form == "two_curves":
        first, second = references
        return (
            inferred.constraint_type,
            first.geometry_index,
            second.geometry_index,
            value,
        )
    raise NativeSketchError("Sketch Dimension constructor form is unavailable.")


def create_sketch_dimension(
    document: Any,
    prepared: PreparedSketchDimension,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchDimension):
        raise TypeError("prepared must be a PreparedSketchDimension")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Dimension preflight",
    )

    expected_index = prepared.spec.target.target.expected_constraint_count
    constraint = make_dimensional_constraint(
        _constraint_arguments(prepared),
        driving=prepared.spec.driving,
    )
    index = add_exact_constraint(
        sketch,
        constraint,
        expected_index=expected_index,
        label="inferred Dimension",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _expected_references(inferred: InferredSketchDimension) -> list[dict[str, Any]]:
    return [
        {
            "slot": slot,
            "geometry_index": element.geometry_index,
            **(
                {"position": element.position_code}
                if element.position_code
                else {}
            ),
        }
        for slot, element in enumerate(inferred.references, start=1)
    ]


def verify_sketch_dimension(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchDimension = draft.value["prepared"]
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    index = int(draft.value["constraint_index"])
    expected_value = prepared.spec.dimension.value
    if prepared.inferred.inference == "angle":
        expected_value = math.radians(expected_value)
    constraint = verify_exact_constraint_append(
        sketch,
        prepared.target,
        constraint_index=index,
        solver_issues=prepared.solver_issues,
        constraint_type=prepared.inferred.constraint_type,
        references=_expected_references(prepared.inferred),
        driving=prepared.spec.driving,
        value=expected_value,
        tolerance=_LINEAR_TOLERANCE,
        label=_LABEL,
    )
    measured_after = _infer_dimension(sketch, prepared.spec.target.selection)
    if (
        measured_after.inference != prepared.inferred.inference
        or not math.isclose(
            measured_after.measured_value,
            prepared.spec.dimension.value,
            rel_tol=1.0e-9,
            abs_tol=_LINEAR_TOLERANCE,
        )
    ):
        raise NativeSketchError(
            "Sketch Dimension solver result does not satisfy its exact value."
        )
    payload = {
        "operation": "infer_dimension",
        "inference": prepared.inferred.inference,
        "constraint": constraint,
        "measured_before": {
            "value": prepared.inferred.measured_value,
            "unit": prepared.spec.dimension.unit,
        },
        "measured_after": {
            "value": measured_after.measured_value,
            "unit": prepared.spec.dimension.unit,
        },
    }
    return sketch_geometry_result(sketch, payload)
