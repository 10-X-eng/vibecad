# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic general Angle constraint for an open Sketch."""

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
        "expected_form",
        "dimension",
        "driving",
    }
)
_DIMENSION_FIELDS = frozenset({"value", "unit"})
_FORMS = frozenset(
    {
        "line_orientation",
        "circular_arc_span",
        "line_line",
        "via_point",
    }
)
_LINE_TYPE = "Part::GeomLineSegment"
_ARC_TYPE = "Part::GeomArcOfCircle"
_POINT_TYPE = "Part::GeomPoint"
_LINEAR_TOLERANCE = 1.0e-7
_ANGULAR_TOLERANCE_RADIANS = 1.0e-9
_ANGULAR_TOLERANCE_DEGREES = math.degrees(_ANGULAR_TOLERANCE_RADIANS)
_LABEL = "Sketch Angle"


@dataclass(frozen=True, slots=True)
class SketchAngleSpec:
    target: SketchConstraintTargetSpec
    expected_form: str
    dimension_degrees: float
    driving: bool


@dataclass(frozen=True, slots=True)
class ResolvedSketchAngle:
    target_form: str
    constructor_type: str
    references: tuple[SketchConstraintElement, ...]
    measured_degrees: float


@dataclass(frozen=True, slots=True)
class PreparedSketchAngle:
    target: PreparedSketchConstraintTarget
    spec: SketchAngleSpec
    resolved: ResolvedSketchAngle
    solver_issues: tuple[tuple[int, ...], ...]


def _dimension(value: Any) -> float:
    if not isinstance(value, Mapping) or set(value) != _DIMENSION_FIELDS:
        raise NativeSketchError(f"{_LABEL} dimension has incorrect fields.")
    if value["unit"] != "deg":
        raise NativeSketchError(f"{_LABEL} requires unit deg.")
    raw = value["value"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise NativeSketchError(f"{_LABEL} value must be a number.")
    result = float(raw)
    if not math.isfinite(result) or not -180.0 <= result <= 360.0:
        raise NativeSketchError(
            f"{_LABEL} value must be from -180 to 360 degrees."
        )
    return result


def prepare_sketch_angle(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchAngleSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {_LABEL} definition has incorrect fields.")
    expected_form = value["expected_form"]
    if not isinstance(expected_form, str) or expected_form not in _FORMS:
        raise NativeSketchError(
            f"{_LABEL} expected_form must be line_orientation, circular_arc_span, "
            "line_line, or via_point."
        )
    selection = value["selection"]
    expected_count = 3 if expected_form == "via_point" else (
        2 if expected_form == "line_line" else 1
    )
    if not isinstance(selection, list) or len(selection) != expected_count:
        raise NativeSketchError(
            f"{_LABEL} {expected_form} requires exactly {expected_count} selected "
            "element(s)."
        )
    driving = value["driving"]
    if type(driving) is not bool:
        raise NativeSketchError(f"{_LABEL} driving must be a boolean.")
    return SketchAngleSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value[
                "expected_external_geometry_count"
            ],
            selection=selection,
        ),
        expected_form,
        _dimension(value["dimension"]),
        driving,
    )


def _vector(value: Any, label: str) -> tuple[float, float]:
    try:
        x = float(value.x)
        y = float(value.y)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} {label} is unavailable.") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise NativeSketchError(f"{_LABEL} {label} is not finite.")
    return x, y


def _point(sketch: Any, element: SketchConstraintElement) -> tuple[float, float]:
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{_LABEL} point lookup is unavailable.")
    try:
        point = getter(element.geometry_index, element.position_code)
    except Exception as exc:
        raise NativeSketchError(
            f"{_LABEL} point {element.geometry_index}:{element.position} is "
            "unavailable."
        ) from exc
    return _vector(point, "point")


def _geometry_type(sketch: Any, element: SketchConstraintElement) -> str:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    return str(getattr(geometry, "TypeId", "") or "")


def _line_endpoints(
    sketch: Any,
    element: SketchConstraintElement,
) -> tuple[tuple[float, float], tuple[float, float]]:
    if element.geometry_index == -1:
        return (0.0, 0.0), (1.0, 0.0)
    if element.geometry_index == -2:
        return (0.0, 0.0), (0.0, 1.0)
    if _geometry_type(sketch, element) != _LINE_TYPE:
        raise NativeSketchError(f"{_LABEL} expected a straight line or Sketch axis.")
    return (
        _point(sketch, SketchConstraintElement(element.geometry_index, "start")),
        _point(sketch, SketchConstraintElement(element.geometry_index, "end")),
    )


def _line_vector(
    sketch: Any,
    element: SketchConstraintElement,
    *,
    allow_axis: bool,
) -> tuple[float, float]:
    if element.geometry_index in {-1, -2}:
        if not allow_axis or element.position != "whole":
            raise NativeSketchError(
                f"{_LABEL} axes are supported only as whole line-line rays."
            )
    elif element.position not in {"start", "end", "whole"}:
        raise NativeSketchError(f"{_LABEL} line target has an invalid position.")
    start, end = _line_endpoints(sketch, element)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if element.position == "end":
        dx = -dx
        dy = -dy
    length = math.hypot(dx, dy)
    if length <= _LINEAR_TOLERANCE:
        raise NativeSketchError(f"{_LABEL} cannot target a zero-length line.")
    return dx / length, dy / length


def _line_orientation(
    sketch: Any,
    element: SketchConstraintElement,
) -> ResolvedSketchAngle:
    if element.position != "whole" or element.geometry_index in {-1, -2}:
        raise NativeSketchError(
            f"{_LABEL} line_orientation requires one whole non-axis line."
        )
    direction = _line_vector(sketch, element, allow_axis=False)
    return ResolvedSketchAngle(
        "line_orientation",
        "Angle",
        (SketchConstraintElement(element.geometry_index, "whole"),),
        math.degrees(math.atan2(direction[1], direction[0])),
    )


def _arc_span(
    sketch: Any,
    element: SketchConstraintElement,
) -> ResolvedSketchAngle:
    if element.position != "whole" or element.geometry_index in {-1, -2}:
        raise NativeSketchError(
            f"{_LABEL} circular_arc_span requires one whole circular arc."
        )
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    if str(getattr(geometry, "TypeId", "") or "") != _ARC_TYPE:
        raise NativeSketchError(
            f"{_LABEL} circular_arc_span requires one whole circular arc."
        )
    try:
        span = float(geometry.LastParameter) - float(geometry.FirstParameter)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} circular-arc span is unavailable.") from exc
    if not math.isfinite(span):
        raise NativeSketchError(f"{_LABEL} circular-arc span is not finite.")
    span = math.fmod(span, math.tau)
    if span <= 0.0:
        span += math.tau
    if span <= _ANGULAR_TOLERANCE_RADIANS or span >= math.tau - _ANGULAR_TOLERANCE_RADIANS:
        raise NativeSketchError(f"{_LABEL} circular-arc span is degenerate.")
    return ResolvedSketchAngle(
        "circular_arc_span",
        "Angle",
        (element,),
        math.degrees(span),
    )


def _positive_internal_angle(
    first: tuple[float, float],
    second: tuple[float, float],
) -> tuple[float, bool]:
    angle = math.atan2(
        first[0] * second[1] - first[1] * second[0],
        first[0] * second[0] + first[1] * second[1],
    )
    swapped = angle < 0.0
    if swapped:
        angle = -angle
    if (
        angle <= _ANGULAR_TOLERANCE_RADIANS
        or angle >= math.pi - _ANGULAR_TOLERANCE_RADIANS
    ):
        raise NativeSketchError(
            f"{_LABEL} parallel or collinear rays do not define a stable angle."
        )
    return angle, swapped


def _line_line(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
) -> ResolvedSketchAngle:
    first, second = selection
    for element in selection:
        if element.geometry_index in {-1, -2}:
            if element.position != "whole":
                raise NativeSketchError(
                    f"{_LABEL} line_line axes must use whole position."
                )
        elif element.position not in {"start", "end"}:
            raise NativeSketchError(
                f"{_LABEL} line_line requires start or end for each directed line ray."
            )
        _line_endpoints(sketch, element)
    angle, swapped = _positive_internal_angle(
        _line_vector(sketch, first, allow_axis=True),
        _line_vector(sketch, second, allow_axis=True),
    )
    references = (second, first) if swapped else (first, second)
    references = tuple(
        SketchConstraintElement(
            element.geometry_index,
            "start" if element.geometry_index in {-1, -2} else element.position,
        )
        for element in references
    )
    return ResolvedSketchAngle(
        "line_line",
        "Angle",
        references,
        math.degrees(angle),
    )


def _curve_is_supported(sketch: Any, element: SketchConstraintElement) -> None:
    if element.position != "whole":
        raise NativeSketchError(f"{_LABEL} via_point curves must use whole position.")
    geometry_type = _geometry_type(sketch, element)
    if geometry_type == _POINT_TYPE or not geometry_type.startswith("Part::Geom"):
        raise NativeSketchError(
            f"{_LABEL} via_point does not support {geometry_type or 'geometry'}."
        )


def _point_on_curve(
    sketch: Any,
    curve: SketchConstraintElement,
    point: tuple[float, float],
) -> bool:
    method = getattr(sketch, "isPointOnCurve", None)
    if not callable(method):
        raise NativeSketchError(f"{_LABEL} point-on-curve query is unavailable.")
    try:
        return bool(method(curve.geometry_index, point[0], point[1]))
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} point-on-curve query failed.") from exc


def _via_point(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
) -> ResolvedSketchAngle:
    first, second, point_ref = selection
    _curve_is_supported(sketch, first)
    _curve_is_supported(sketch, second)
    if point_ref.position == "whole":
        raise NativeSketchError(
            f"{_LABEL} via_point requires one exact curve point as its third element."
        )
    point = _point(sketch, point_ref)
    if not _point_on_curve(sketch, first, point) or not _point_on_curve(
        sketch,
        second,
        point,
    ):
        raise NativeSketchError(
            f"{_LABEL} via_point requires the point to lie on both curves; constrain "
            "the point onto each curve first."
        )
    method = getattr(sketch, "calculateAngleViaPoint", None)
    if not callable(method):
        raise NativeSketchError(f"{_LABEL} via-point measurement is unavailable.")
    try:
        angle = float(
            method(
                first.geometry_index,
                second.geometry_index,
                point[0],
                point[1],
            )
        )
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} via-point measurement failed.") from exc
    if not math.isfinite(angle):
        raise NativeSketchError(f"{_LABEL} via-point measurement is not finite.")
    swapped = angle < 0.0
    if swapped:
        angle = -angle
    if (
        angle <= _ANGULAR_TOLERANCE_RADIANS
        or angle >= math.pi - _ANGULAR_TOLERANCE_RADIANS
    ):
        raise NativeSketchError(
            f"{_LABEL} via-point curves do not define a stable non-collinear angle."
        )
    references = (second, first, point_ref) if swapped else selection
    return ResolvedSketchAngle(
        "via_point",
        "AngleViaPoint",
        references,
        math.degrees(angle),
    )


def _validate_requested_value(spec: SketchAngleSpec) -> None:
    value = spec.dimension_degrees
    if spec.expected_form == "line_orientation":
        if not -180.0 <= value <= 180.0:
            raise NativeSketchError(
                f"{_LABEL} line_orientation must be from -180 to 180 degrees."
            )
        return
    maximum = 360.0 if spec.expected_form == "circular_arc_span" else 180.0
    if not _ANGULAR_TOLERANCE_DEGREES < value < maximum - _ANGULAR_TOLERANCE_DEGREES:
        raise NativeSketchError(
            f"{_LABEL} {spec.expected_form} must be greater than zero and less "
            f"than {maximum:g} degrees."
        )


def _resolve_angle(sketch: Any, spec: SketchAngleSpec) -> ResolvedSketchAngle:
    _validate_requested_value(spec)
    selection = spec.target.selection
    if spec.expected_form == "line_orientation":
        resolved = _line_orientation(sketch, selection[0])
    elif spec.expected_form == "circular_arc_span":
        resolved = _arc_span(sketch, selection[0])
    elif spec.expected_form == "line_line":
        resolved = _line_line(sketch, selection)
    else:
        resolved = _via_point(sketch, selection)
    if resolved.target_form != spec.expected_form:
        raise NativeSketchError(
            f"{_LABEL} resolved {resolved.target_form}, not {spec.expected_form}."
        )
    return resolved


def _constraint_arguments(prepared: PreparedSketchAngle) -> tuple[Any, ...]:
    resolved = prepared.resolved
    value = math.radians(prepared.spec.dimension_degrees)
    if resolved.target_form in {"line_orientation", "circular_arc_span"}:
        return (
            "Angle",
            resolved.references[0].geometry_index,
            value,
        )
    if resolved.target_form == "line_line":
        first, second = resolved.references
        return (
            "Angle",
            first.geometry_index,
            first.position_code,
            second.geometry_index,
            second.position_code,
            value,
        )
    first, second, point = resolved.references
    return (
        "AngleViaPoint",
        first.geometry_index,
        second.geometry_index,
        point.geometry_index,
        point.position_code,
        value,
    )


def preflight_sketch_angle(
    context: NativeRuntimeContext,
    spec: SketchAngleSpec,
) -> PreparedSketchAngle:
    if not isinstance(spec, SketchAngleSpec):
        raise TypeError("spec must be a SketchAngleSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = _resolve_angle(sketch, spec)
    if not spec.driving and not math.isclose(
        resolved.measured_degrees,
        spec.dimension_degrees,
        rel_tol=1.0e-9,
        abs_tol=_ANGULAR_TOLERANCE_DEGREES,
    ):
        raise NativeSketchError(
            f"{_LABEL} reference measurement changed; read the current Sketch and retry."
        )
    solver_issues = sketch_solver_issues(sketch, _LABEL)
    prepared = PreparedSketchAngle(target, spec, resolved, solver_issues)
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


def create_sketch_angle(
    document: Any,
    prepared: PreparedSketchAngle,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchAngle):
        raise TypeError("prepared must be a PreparedSketchAngle")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Angle preflight",
    )
    constraint = make_dimensional_constraint(
        _constraint_arguments(prepared),
        driving=prepared.spec.driving,
    )
    index = add_exact_constraint(
        sketch,
        constraint,
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Angle",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _expected_references(resolved: ResolvedSketchAngle) -> list[dict[str, Any]]:
    return [
        {
            "slot": slot,
            "geometry_index": element.geometry_index,
            **({"position": element.position_code} if element.position_code else {}),
        }
        for slot, element in enumerate(resolved.references, start=1)
    ]


def verify_sketch_angle(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchAngle):
        raise TypeError("draft must contain a PreparedSketchAngle")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraint = verify_exact_constraint_append(
        sketch,
        prepared.target,
        constraint_index=int(draft.value["constraint_index"]),
        solver_issues=prepared.solver_issues,
        constraint_type="Angle",
        references=_expected_references(prepared.resolved),
        driving=prepared.spec.driving,
        value=math.radians(prepared.spec.dimension_degrees),
        tolerance=_ANGULAR_TOLERANCE_RADIANS,
        label=_LABEL,
    )
    measured_after = _resolve_angle(sketch, prepared.spec)
    if (
        measured_after.target_form != prepared.resolved.target_form
        or measured_after.references != prepared.resolved.references
        or not math.isclose(
            measured_after.measured_degrees,
            prepared.spec.dimension_degrees,
            rel_tol=1.0e-9,
            abs_tol=_ANGULAR_TOLERANCE_DEGREES,
        )
    ):
        raise NativeSketchError(
            f"{_LABEL} solver result does not satisfy its exact value and branch."
        )
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_angle",
            "target_form": prepared.resolved.target_form,
            "constraint": constraint,
            "measured_before": {
                "value": prepared.resolved.measured_degrees,
                "unit": "deg",
            },
            "measured_after": {
                "value": measured_after.measured_degrees,
                "unit": "deg",
            },
        },
    )
