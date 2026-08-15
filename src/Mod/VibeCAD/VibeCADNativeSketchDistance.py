# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact general Distance constraints for the human-opened Sketch."""

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
        "dimension",
        "driving",
    }
)
_DIMENSION_FIELDS = frozenset({"value", "unit"})
_LINE_TYPE = "Part::GeomLineSegment"
_CIRCLE_TYPE = "Part::GeomCircle"
_ARC_TYPE = "Part::GeomArcOfCircle"
_TOLERANCE = 1.0e-7
_MAX_ABSOLUTE_DIMENSION = 1_000_000.0
_LABEL = "Sketch Distance"


@dataclass(frozen=True, slots=True)
class SketchDistanceSpec:
    target: SketchConstraintTargetSpec
    dimension_mm: float
    driving: bool


@dataclass(frozen=True, slots=True)
class ResolvedSketchDistance:
    target_form: str
    constraint_type: str
    constructor_form: str
    references: tuple[SketchConstraintElement, ...]
    measured_value: float


@dataclass(frozen=True, slots=True)
class PreparedSketchDistance:
    target: PreparedSketchConstraintTarget
    spec: SketchDistanceSpec
    resolved: ResolvedSketchDistance
    solver_issues: tuple[tuple[int, ...], ...]


def _dimension(value: Any) -> float:
    if not isinstance(value, Mapping) or set(value) != _DIMENSION_FIELDS:
        raise NativeSketchError(f"{_LABEL} dimension has incorrect fields.")
    if value["unit"] != "mm":
        raise NativeSketchError(f"{_LABEL} requires unit mm.")
    raw = value["value"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise NativeSketchError(f"{_LABEL} value must be a number.")
    result = float(raw)
    if not math.isfinite(result) or abs(result) > _MAX_ABSOLUTE_DIMENSION:
        raise NativeSketchError(
            f"{_LABEL} value must be finite and from "
            f"-{_MAX_ABSOLUTE_DIMENSION} to {_MAX_ABSOLUTE_DIMENSION} mm."
        )
    return result


def prepare_sketch_distance(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchDistanceSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {_LABEL} definition has incorrect fields.")
    driving = value["driving"]
    if type(driving) is not bool:
        raise NativeSketchError(f"{_LABEL} driving must be a boolean.")
    return SketchDistanceSpec(
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
        _dimension(value["dimension"]),
        driving,
    )


def _vector_2d(value: Any, name: str) -> tuple[float, float]:
    try:
        result = float(value.x), float(value.y)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} {name} is unavailable.") from exc
    if not all(math.isfinite(coordinate) for coordinate in result):
        raise NativeSketchError(f"{_LABEL} {name} is not finite.")
    return result


def _point(sketch: Any, element: SketchConstraintElement) -> tuple[float, float]:
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{_LABEL} point lookup is unavailable.")
    try:
        point = getter(element.geometry_index, element.position_code)
    except Exception as exc:
        raise NativeSketchError(
            f"{_LABEL} point {element.geometry_index}:{element.position} is unavailable."
        ) from exc
    return _vector_2d(point, "point")


def _geometry(sketch: Any, element: SketchConstraintElement) -> Any:
    return sketch_constraint_geometry(sketch, element.geometry_index)


def _geometry_type(sketch: Any, element: SketchConstraintElement) -> str:
    return str(getattr(_geometry(sketch, element), "TypeId", "") or "")


def _kind(sketch: Any, element: SketchConstraintElement) -> str:
    if element.position != "whole":
        return "point"
    geometry_type = _geometry_type(sketch, element)
    if geometry_type == _LINE_TYPE:
        return "line"
    if geometry_type in {_CIRCLE_TYPE, _ARC_TYPE}:
        return "circle"
    return "unsupported"


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(second[0] - first[0], second[1] - first[1])


def _line_points(
    sketch: Any,
    element: SketchConstraintElement,
) -> tuple[tuple[float, float], tuple[float, float]]:
    if element.position != "whole" or _geometry_type(sketch, element) != _LINE_TYPE:
        raise NativeSketchError(f"{_LABEL} expected one whole line.")
    if element.geometry_index == -1:
        return (0.0, 0.0), (1.0, 0.0)
    if element.geometry_index == -2:
        return (0.0, 0.0), (0.0, 1.0)
    return (
        _point(sketch, SketchConstraintElement(element.geometry_index, "start")),
        _point(sketch, SketchConstraintElement(element.geometry_index, "end")),
    )


def _line_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= _TOLERANCE:
        raise NativeSketchError(f"{_LABEL} cannot target a zero-length line.")
    return abs(
        -point[0] * dy
        + point[1] * dx
        + start[0] * end[1]
        - end[0] * start[1]
    ) / length


def _circle_data(
    sketch: Any,
    element: SketchConstraintElement,
) -> tuple[tuple[float, float], float]:
    geometry = _geometry(sketch, element)
    center = _vector_2d(
        getattr(geometry, "Center", getattr(geometry, "Location", None)),
        "circle center",
    )
    try:
        radius = float(geometry.Radius)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} circle radius is unavailable.") from exc
    if not math.isfinite(radius) or radius <= _TOLERANCE:
        raise NativeSketchError(f"{_LABEL} circle radius is invalid.")
    return center, radius


def _arc_length(sketch: Any, element: SketchConstraintElement) -> float:
    geometry = _geometry(sketch, element)
    _center, radius = _circle_data(sketch, element)
    try:
        span = float(geometry.LastParameter) - float(geometry.FirstParameter)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} circular-arc span is unavailable.") from exc
    if not math.isfinite(span):
        raise NativeSketchError(f"{_LABEL} circular-arc span is invalid.")
    if span <= _TOLERANCE:
        span += 2.0 * math.pi
    if not _TOLERANCE < span <= 2.0 * math.pi + _TOLERANCE:
        raise NativeSketchError(f"{_LABEL} circular-arc span is invalid.")
    return radius * span


def _resolved(
    target_form: str,
    constructor_form: str,
    references: tuple[SketchConstraintElement, ...],
    measured: float,
    *,
    constraint_type: str = "Distance",
) -> ResolvedSketchDistance:
    if not math.isfinite(measured):
        raise NativeSketchError(f"{_LABEL} measurement is not finite.")
    return ResolvedSketchDistance(
        target_form,
        constraint_type,
        constructor_form,
        references,
        measured,
    )


def _resolve_single(
    sketch: Any,
    element: SketchConstraintElement,
) -> ResolvedSketchDistance:
    if element.position != "whole":
        raise NativeSketchError(
            f"{_LABEL} requires two points, or one whole line or circular arc."
        )
    if element.geometry_index in {-1, -2}:
        raise NativeSketchError(f"{_LABEL} cannot constrain an axis length.")
    geometry_type = _geometry_type(sketch, element)
    if geometry_type == _LINE_TYPE:
        measured = _distance(*_line_points(sketch, element))
        if measured <= _TOLERANCE:
            raise NativeSketchError(f"{_LABEL} cannot target a zero-length line.")
        return _resolved("line_length", "one_curve", (element,), measured)
    if geometry_type == _ARC_TYPE:
        return _resolved(
            "circular_arc_length",
            "one_curve",
            (element,),
            _arc_length(sketch, element),
        )
    if geometry_type == _CIRCLE_TYPE:
        raise NativeSketchError(
            f"{_LABEL} needs a second curve for a whole circle; use Radius or "
            "Diameter for its size."
        )
    raise NativeSketchError(
        f"{_LABEL} does not support whole {geometry_type or 'geometry'}."
    )


def _resolve_axis_point(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
) -> ResolvedSketchDistance | None:
    axes = tuple(
        element
        for element in selection
        if element.geometry_index in {-1, -2} and element.position == "whole"
    )
    if not axes:
        return None
    if len(axes) != 1:
        raise NativeSketchError(f"{_LABEL} cannot constrain two axes.")
    axis = axes[0]
    point = selection[1] if selection[0] == axis else selection[0]
    if point.position == "whole":
        raise NativeSketchError(
            f"{_LABEL} axis selection requires one other exact point."
        )
    coordinates = _point(sketch, point)
    horizontal_axis = axis.geometry_index == -1
    measured = coordinates[1 if horizontal_axis else 0]
    return _resolved(
        "horizontal_axis_to_point" if horizontal_axis else "vertical_axis_to_point",
        "two_points",
        (SketchConstraintElement(axis.geometry_index, "start"), point),
        measured,
        constraint_type="DistanceY" if horizontal_axis else "DistanceX",
    )


def _resolve_two(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
) -> ResolvedSketchDistance:
    axis_point = _resolve_axis_point(sketch, selection)
    if axis_point is not None:
        return axis_point
    first, second = selection
    first_kind, second_kind = (_kind(sketch, element) for element in selection)
    if "unsupported" in {first_kind, second_kind}:
        unsupported = first if first_kind == "unsupported" else second
        raise NativeSketchError(
            f"{_LABEL} does not support whole "
            f"{_geometry_type(sketch, unsupported) or 'geometry'}."
        )
    if first_kind == second_kind == "point":
        measured = _distance(_point(sketch, first), _point(sketch, second))
        if measured <= _TOLERANCE:
            raise NativeSketchError(
                f"{_LABEL} points are coincident; use Coincident instead."
            )
        return _resolved(
            "point_to_point",
            "two_points",
            (first, second),
            measured,
        )
    if {first_kind, second_kind} == {"point", "line"}:
        point, line = (first, second) if first_kind == "point" else (second, first)
        measured = _line_distance(_point(sketch, point), *_line_points(sketch, line))
        if measured <= _TOLERANCE:
            raise NativeSketchError(
                f"{_LABEL} point lies on the line; use Point-on-object instead."
            )
        return _resolved("point_to_line", "point_curve", (point, line), measured)
    if {first_kind, second_kind} == {"point", "circle"}:
        point, circle = (
            (first, second) if first_kind == "point" else (second, first)
        )
        center, radius = _circle_data(sketch, circle)
        measured = abs(_distance(_point(sketch, point), center) - radius)
        if measured <= _TOLERANCE:
            raise NativeSketchError(
                f"{_LABEL} point lies on the circle; use Point-on-object instead."
            )
        return _resolved(
            "point_to_circle",
            "point_curve",
            (point, circle),
            measured,
        )
    if first_kind == second_kind == "line":
        raise NativeSketchError(f"{_LABEL} does not constrain two whole lines.")
    if {first_kind, second_kind} == {"line", "circle"}:
        circle, line = (
            (first, second) if first_kind == "circle" else (second, first)
        )
        center, radius = _circle_data(sketch, circle)
        signed_gap = _line_distance(center, *_line_points(sketch, line)) - radius
        if signed_gap < -_TOLERANCE:
            raise NativeSketchError(
                f"{_LABEL} circle and line intersect; Sketcher does not support "
                "a stable driving secant distance."
            )
        if signed_gap <= _TOLERANCE:
            raise NativeSketchError(
                f"{_LABEL} circle and line are tangent; use Tangent instead."
            )
        return _resolved(
            "circle_to_line",
            "two_curves",
            (circle, line),
            signed_gap,
        )
    if first_kind == second_kind == "circle":
        first_center, first_radius = _circle_data(sketch, first)
        second_center, second_radius = _circle_data(sketch, second)
        center_distance = _distance(first_center, second_center)
        if center_distance >= first_radius and center_distance >= second_radius:
            signed = center_distance - first_radius - second_radius
        else:
            signed = (
                max(first_radius, second_radius)
                - min(first_radius, second_radius)
                - center_distance
            )
        if signed < -_TOLERANCE:
            raise NativeSketchError(
                f"{_LABEL} circles intersect; Sketcher does not support a stable "
                "driving intersection distance."
            )
        if signed <= _TOLERANCE:
            raise NativeSketchError(
                f"{_LABEL} circles are tangent; use Tangent instead."
            )
        return _resolved(
            "circle_to_circle",
            "two_curves",
            (first, second),
            signed,
        )
    raise NativeSketchError(f"{_LABEL} selection is unsupported.")


def _resolve_distance(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
) -> ResolvedSketchDistance:
    if len(selection) == 1:
        return _resolve_single(sketch, selection[0])
    return _resolve_two(sketch, selection)


def _constraint_arguments(prepared: PreparedSketchDistance) -> tuple[Any, ...]:
    resolved = prepared.resolved
    references = resolved.references
    value = prepared.spec.dimension_mm
    if resolved.constructor_form == "one_curve":
        return (resolved.constraint_type, references[0].geometry_index, value)
    if resolved.constructor_form == "two_points":
        first, second = references
        return (
            resolved.constraint_type,
            first.geometry_index,
            first.position_code,
            second.geometry_index,
            second.position_code,
            value,
        )
    if resolved.constructor_form == "point_curve":
        point, curve = references
        return (
            resolved.constraint_type,
            point.geometry_index,
            point.position_code,
            curve.geometry_index,
            value,
        )
    if resolved.constructor_form == "two_curves":
        first, second = references
        return (
            resolved.constraint_type,
            first.geometry_index,
            second.geometry_index,
            value,
        )
    raise NativeSketchError(f"{_LABEL} constructor form is unavailable.")


def _expected_references(
    resolved: ResolvedSketchDistance,
) -> list[dict[str, Any]]:
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
        for slot, element in enumerate(resolved.references, start=1)
    ]


def preflight_sketch_distance(
    context: NativeRuntimeContext,
    spec: SketchDistanceSpec,
) -> PreparedSketchDistance:
    if not isinstance(spec, SketchDistanceSpec):
        raise TypeError("spec must be a SketchDistanceSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = _resolve_distance(sketch, spec.target.selection)
    if (
        resolved.target_form
        not in {"horizontal_axis_to_point", "vertical_axis_to_point"}
        and spec.dimension_mm <= _TOLERANCE
    ):
        raise NativeSketchError(
            f"{_LABEL} value for {resolved.target_form} must be greater than zero."
        )
    if not spec.driving and not math.isclose(
        resolved.measured_value,
        spec.dimension_mm,
        rel_tol=1.0e-9,
        abs_tol=_TOLERANCE,
    ):
        raise NativeSketchError(
            f"{_LABEL} reference measurement changed; read the current Sketch and retry."
        )
    solver_issues = sketch_solver_issues(sketch, _LABEL)
    prepared = PreparedSketchDistance(target, spec, resolved, solver_issues)
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
        raise NativeSketchError(f"{_LABEL} feasibility check changed the active Sketch.")
    return prepared


def create_sketch_distance(
    document: Any,
    prepared: PreparedSketchDistance,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchDistance):
        raise TypeError("prepared must be a PreparedSketchDistance")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Distance preflight",
    )
    constraint = make_dimensional_constraint(
        _constraint_arguments(prepared),
        driving=prepared.spec.driving,
    )
    index = add_exact_constraint(
        sketch,
        constraint,
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Distance",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_distance(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchDistance):
        raise TypeError("draft must contain a PreparedSketchDistance")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraint = verify_exact_constraint_append(
        sketch,
        prepared.target,
        constraint_index=int(draft.value["constraint_index"]),
        solver_issues=prepared.solver_issues,
        constraint_type=prepared.resolved.constraint_type,
        references=_expected_references(prepared.resolved),
        driving=prepared.spec.driving,
        value=prepared.spec.dimension_mm,
        tolerance=_TOLERANCE,
        label=_LABEL,
    )
    measured_after = _resolve_distance(sketch, prepared.spec.target.selection)
    if (
        measured_after.target_form != prepared.resolved.target_form
        or measured_after.constraint_type != prepared.resolved.constraint_type
        or measured_after.references != prepared.resolved.references
        or not math.isclose(
            measured_after.measured_value,
            prepared.spec.dimension_mm,
            rel_tol=1.0e-9,
            abs_tol=_TOLERANCE,
        )
    ):
        raise NativeSketchError(f"{_LABEL} solver result does not satisfy its exact value.")
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_distance",
            "target_form": prepared.resolved.target_form,
            "constraint": constraint,
            "measured_before": {
                "value": prepared.resolved.measured_value,
                "unit": "mm",
            },
            "measured_after": {
                "value": measured_after.measured_value,
                "unit": "mm",
            },
        },
    )
