# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic unified Coincident forms for an open Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import (
    ExactConstraintExpectation,
    add_exact_constraint,
    diagnose_exact_constraint,
    sketch_solver_issues,
    verify_exact_constraint_appends,
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
        "target",
    }
)
_POINT_POINT_FIELDS = frozenset({"form", "first_point", "second_point"})
_POINT_CURVE_FIELDS = frozenset({"form", "point", "curve"})
_CONCENTRIC_FIELDS = frozenset({"form", "first_curve", "second_curve"})
_FORMS = frozenset({"point_point", "point_on_object", "concentric"})
_CONCENTRIC_TYPES = frozenset(
    {
        "Part::GeomCircle",
        "Part::GeomArcOfCircle",
        "Part::GeomEllipse",
        "Part::GeomArcOfEllipse",
    }
)
_POINT_TYPE = "Part::GeomPoint"
_BSPLINE_TYPE = "Part::GeomBSplineCurve"
_LINEAR_TOLERANCE = 1.0e-7
_LABEL = "Sketch Coincident"


@dataclass(frozen=True, slots=True)
class SketchCoincidentSpec:
    target: SketchConstraintTargetSpec
    target_form: str


@dataclass(frozen=True, slots=True)
class ResolvedSketchCoincident:
    target_form: str
    references: tuple[SketchConstraintElement, ...]
    separation_before_mm: float | None


@dataclass(frozen=True, slots=True)
class PreparedSketchCoincident:
    target: PreparedSketchConstraintTarget
    spec: SketchCoincidentSpec
    resolved: ResolvedSketchCoincident
    solver_issues: tuple[tuple[int, ...], ...]


def prepare_sketch_coincident(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchCoincidentSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {_LABEL} definition has incorrect fields.")
    raw_target = value["target"]
    if not isinstance(raw_target, Mapping):
        raise NativeSketchError(f"{_LABEL} target must be an object.")
    form = raw_target.get("form")
    if form == "point_point" and set(raw_target) == _POINT_POINT_FIELDS:
        selection = [raw_target["first_point"], raw_target["second_point"]]
    elif form == "point_on_object" and set(raw_target) == _POINT_CURVE_FIELDS:
        selection = [raw_target["point"], raw_target["curve"]]
    elif form == "concentric" and set(raw_target) == _CONCENTRIC_FIELDS:
        selection = [raw_target["first_curve"], raw_target["second_curve"]]
    else:
        raise NativeSketchError(
            f"{_LABEL} target must be one exact point_point, point_on_object, "
            "or concentric form."
        )
    return SketchCoincidentSpec(
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
        str(form),
    )


def _geometry_type(sketch: Any, element: SketchConstraintElement) -> str:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    return str(getattr(geometry, "TypeId", "") or "")


def _point(
    sketch: Any,
    element: SketchConstraintElement,
    *,
    role: str,
) -> tuple[float, float]:
    if element.position == "whole":
        raise NativeSketchError(f"{_LABEL} {role} must be one exact point.")
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{_LABEL} point lookup is unavailable.")
    try:
        value = getter(element.geometry_index, element.position_code)
        x = float(value.x)
        y = float(value.y)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} {role} is unavailable.") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise NativeSketchError(f"{_LABEL} {role} is not finite.")
    return x, y


def _separation(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    return math.hypot(second[0] - first[0], second[1] - first[1])


def _point_on_curve(
    sketch: Any,
    point: tuple[float, float],
    curve: SketchConstraintElement,
) -> bool:
    method = getattr(sketch, "isPointOnCurve", None)
    if not callable(method):
        raise NativeSketchError(f"{_LABEL} point-on-curve query is unavailable.")
    try:
        return bool(method(curve.geometry_index, point[0], point[1]))
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} point-on-curve query failed.") from exc


def _refuse_hidden_tangent_substitution(
    sketch: Any,
    references: tuple[SketchConstraintElement, ...],
) -> None:
    selected = {element.geometry_index for element in references}
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} constraints are unavailable.") from exc
    for constraint in constraints:
        if str(getattr(constraint, "Type", "") or "") != "Tangent":
            continue
        connected = {
            int(getattr(constraint, "First", -2000)),
            int(getattr(constraint, "Second", -2000)),
        }
        if selected == connected:
            raise NativeSketchError(
                f"{_LABEL} would replace an existing Tangent constraint; use the "
                "explicit Tangent operation instead."
            )


def _resolve_point_point(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
) -> ResolvedSketchCoincident:
    first, second = selection
    first_value = _point(sketch, first, role="first point")
    second_value = _point(sketch, second, role="second point")
    if (
        first.geometry_index == second.geometry_index
        and _geometry_type(sketch, first) != _BSPLINE_TYPE
    ):
        raise NativeSketchError(
            f"{_LABEL} cannot make two points of the same non-B-spline geometry "
            "coincident."
        )
    separation = _separation(first_value, second_value)
    if separation <= _LINEAR_TOLERANCE:
        raise NativeSketchError(f"{_LABEL} points are already coincident.")
    references = (first, second)
    _refuse_hidden_tangent_substitution(sketch, references)
    return ResolvedSketchCoincident("point_point", references, separation)


def _resolve_concentric(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
) -> ResolvedSketchCoincident:
    if any(element.position != "whole" for element in selection):
        raise NativeSketchError(
            f"{_LABEL} concentric requires two whole conic curves."
        )
    if any(
        _geometry_type(sketch, element) not in _CONCENTRIC_TYPES
        for element in selection
    ):
        raise NativeSketchError(
            f"{_LABEL} concentric supports only circles, ellipses, and their arcs."
        )
    references = tuple(
        SketchConstraintElement(element.geometry_index, "center")
        for element in selection
    )
    separation = _separation(
        _point(sketch, references[0], role="first center"),
        _point(sketch, references[1], role="second center"),
    )
    if separation <= _LINEAR_TOLERANCE:
        raise NativeSketchError(f"{_LABEL} conics are already concentric.")
    _refuse_hidden_tangent_substitution(sketch, references)
    return ResolvedSketchCoincident("concentric", references, separation)


def _resolve_point_on_object(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
) -> ResolvedSketchCoincident:
    point, curve = selection
    point_value = _point(sketch, point, role="point")
    if curve.position != "whole":
        raise NativeSketchError(f"{_LABEL} curve must use whole position.")
    if point.geometry_index == curve.geometry_index:
        raise NativeSketchError(
            f"{_LABEL} cannot constrain a geometry point onto its own curve."
        )
    curve_type = _geometry_type(sketch, curve)
    if curve_type == _POINT_TYPE or not curve_type.startswith("Part::Geom"):
        raise NativeSketchError(
            f"{_LABEL} point_on_object does not support {curve_type or 'geometry'}."
        )
    if _point_on_curve(sketch, point_value, curve):
        raise NativeSketchError(f"{_LABEL} point already lies on the curve.")
    references = (point, curve)
    _refuse_hidden_tangent_substitution(sketch, references)
    return ResolvedSketchCoincident("point_on_object", references, None)


def _resolve_coincident(
    sketch: Any,
    spec: SketchCoincidentSpec,
) -> ResolvedSketchCoincident:
    if spec.target_form == "point_point":
        return _resolve_point_point(sketch, spec.target.selection)
    if spec.target_form == "concentric":
        return _resolve_concentric(sketch, spec.target.selection)
    return _resolve_point_on_object(sketch, spec.target.selection)


def _constraint_arguments(
    resolved: ResolvedSketchCoincident,
) -> tuple[Any, ...]:
    first, second = resolved.references
    if resolved.target_form == "point_on_object":
        return (
            "PointOnObject",
            first.geometry_index,
            first.position_code,
            second.geometry_index,
        )
    return (
        "Coincident",
        first.geometry_index,
        first.position_code,
        second.geometry_index,
        second.position_code,
    )


def _constraint(resolved: ResolvedSketchCoincident) -> Any:
    import Sketcher

    try:
        return Sketcher.Constraint(*_constraint_arguments(resolved))
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact Coincident constraint definition."
        ) from exc


def preflight_sketch_coincident(
    context: NativeRuntimeContext,
    spec: SketchCoincidentSpec,
) -> PreparedSketchCoincident:
    if not isinstance(spec, SketchCoincidentSpec):
        raise TypeError("spec must be a SketchCoincidentSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = _resolve_coincident(sketch, spec)
    solver_issues = sketch_solver_issues(sketch, _LABEL)
    diagnose_exact_constraint(
        sketch,
        _constraint(resolved),
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
            f"{_LABEL} feasibility check changed the active Sketch."
        )
    return PreparedSketchCoincident(target, spec, resolved, solver_issues)


def create_sketch_coincident(
    document: Any,
    prepared: PreparedSketchCoincident,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchCoincident):
        raise TypeError("prepared must be a PreparedSketchCoincident")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Coincident preflight",
    )
    index = add_exact_constraint(
        sketch,
        _constraint(prepared.resolved),
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Coincident",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _expected_references(
    resolved: ResolvedSketchCoincident,
) -> tuple[Mapping[str, Any], ...]:
    result = []
    for slot, element in enumerate(resolved.references, start=1):
        reference: dict[str, Any] = {
            "slot": slot,
            "geometry_index": element.geometry_index,
        }
        if element.position_code:
            reference["position"] = element.position_code
        result.append(reference)
    return tuple(result)


def _measurement(
    sketch: Any,
    resolved: ResolvedSketchCoincident,
) -> dict[str, Any]:
    first, second = resolved.references
    if resolved.target_form == "point_on_object":
        return {
            "point_on_curve": _point_on_curve(
                sketch,
                _point(sketch, first, role="point"),
                second,
            )
        }
    separation = _separation(
        _point(sketch, first, role="first point"),
        _point(sketch, second, role="second point"),
    )
    return {
        "satisfied": separation <= _LINEAR_TOLERANCE,
        "separation": separation,
        "unit": "mm",
    }


def verify_sketch_coincident(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchCoincident):
        raise TypeError("draft must contain a PreparedSketchCoincident")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraint_type = (
        "PointOnObject"
        if prepared.resolved.target_form == "point_on_object"
        else "Coincident"
    )
    constraint = verify_exact_constraint_appends(
        sketch,
        prepared.target,
        constraint_indices=(int(draft.value["constraint_index"]),),
        solver_issues=prepared.solver_issues,
        expectations=(
            ExactConstraintExpectation(
                constraint_type,
                _expected_references(prepared.resolved),
                True,
                None,
                0.0,
            ),
        ),
        label=_LABEL,
    )[0]
    measured_after = _measurement(sketch, prepared.resolved)
    satisfied = (
        bool(measured_after["point_on_curve"])
        if prepared.resolved.target_form == "point_on_object"
        else bool(measured_after["satisfied"])
    )
    if not satisfied:
        raise NativeSketchError(
            f"{_LABEL} solver result does not satisfy its exact target."
        )
    measured_before = (
        {"point_on_curve": False}
        if prepared.resolved.target_form == "point_on_object"
        else {
            "satisfied": False,
            "separation": prepared.resolved.separation_before_mm,
            "unit": "mm",
        }
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_coincident",
            "target_form": prepared.resolved.target_form,
            "constraint": constraint,
            "measured_before": measured_before,
            "measured_after": measured_after,
        },
    )
