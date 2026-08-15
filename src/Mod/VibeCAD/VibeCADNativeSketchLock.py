# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic absolute or pairwise-relative Lock for an open Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import (
    ExactConstraintExpectation,
    add_exact_constraints,
    diagnose_exact_constraints,
    make_dimensional_constraint,
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
        "driving",
    }
)
_ABSOLUTE_FIELDS = frozenset({"form", "point", "expected_position_mm"})
_RELATIVE_FIELDS = frozenset(
    {"form", "point", "reference", "expected_offset_mm"}
)
_XY_FIELDS = frozenset({"x", "y"})
_LINEAR_TOLERANCE = 1.0e-7
_MAX_COORDINATE_MM = 1_000_000.0
_LABEL = "Sketch Lock"


@dataclass(frozen=True, slots=True)
class SketchLockSpec:
    target: SketchConstraintTargetSpec
    target_form: str
    expected_x_mm: float
    expected_y_mm: float
    driving: bool


@dataclass(frozen=True, slots=True)
class ResolvedSketchLock:
    target_form: str
    point: SketchConstraintElement
    reference: SketchConstraintElement | None
    measured_x_mm: float
    measured_y_mm: float


@dataclass(frozen=True, slots=True)
class PreparedSketchLock:
    target: PreparedSketchConstraintTarget
    spec: SketchLockSpec
    resolved: ResolvedSketchLock
    solver_issues: tuple[tuple[int, ...], ...]


def _xy(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != _XY_FIELDS:
        raise NativeSketchError(f"{_LABEL} {label} has incorrect fields.")
    result = []
    for axis in ("x", "y"):
        raw = value[axis]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise NativeSketchError(f"{_LABEL} {label} {axis} must be a number.")
        coordinate = float(raw)
        if not math.isfinite(coordinate) or not (
            -_MAX_COORDINATE_MM <= coordinate <= _MAX_COORDINATE_MM
        ):
            raise NativeSketchError(
                f"{_LABEL} {label} {axis} must be from "
                f"-{_MAX_COORDINATE_MM:g} to {_MAX_COORDINATE_MM:g} mm."
            )
        result.append(coordinate)
    return result[0], result[1]


def prepare_sketch_lock(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchLockSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {_LABEL} definition has incorrect fields.")
    raw_target = value["target"]
    if not isinstance(raw_target, Mapping):
        raise NativeSketchError(f"{_LABEL} target must be an object.")
    form = raw_target.get("form")
    if form == "absolute" and set(raw_target) == _ABSOLUTE_FIELDS:
        raw_selection = [raw_target["point"]]
        expected_x, expected_y = _xy(
            raw_target["expected_position_mm"],
            "expected_position_mm",
        )
    elif form == "relative" and set(raw_target) == _RELATIVE_FIELDS:
        raw_selection = [raw_target["point"], raw_target["reference"]]
        expected_x, expected_y = _xy(
            raw_target["expected_offset_mm"],
            "expected_offset_mm",
        )
    else:
        raise NativeSketchError(
            f"{_LABEL} target must be one exact absolute or relative form."
        )
    driving = value["driving"]
    if type(driving) is not bool:
        raise NativeSketchError(f"{_LABEL} driving must be a boolean.")
    return SketchLockSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value[
                "expected_external_geometry_count"
            ],
            selection=raw_selection,
        ),
        str(form),
        expected_x,
        expected_y,
        driving,
    )


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


def _resolve_lock(sketch: Any, spec: SketchLockSpec) -> ResolvedSketchLock:
    point = spec.target.selection[0]
    if point.geometry_index == -1:
        raise NativeSketchError(
            f"{_LABEL} cannot lock the origin as a target point."
        )
    point_value = _point(sketch, point, role="target point")
    reference = None
    if spec.target_form == "absolute":
        measured_x, measured_y = point_value
    else:
        reference = spec.target.selection[1]
        reference_value = _point(sketch, reference, role="reference point")
        measured_x = reference_value[0] - point_value[0]
        measured_y = reference_value[1] - point_value[1]
    if not math.isclose(
        measured_x,
        spec.expected_x_mm,
        rel_tol=1.0e-9,
        abs_tol=_LINEAR_TOLERANCE,
    ) or not math.isclose(
        measured_y,
        spec.expected_y_mm,
        rel_tol=1.0e-9,
        abs_tol=_LINEAR_TOLERANCE,
    ):
        raise NativeSketchError(
            f"{_LABEL} expected measurement changed; read the current Sketch and retry."
        )
    return ResolvedSketchLock(
        spec.target_form,
        point,
        reference,
        measured_x,
        measured_y,
    )


def _constraint_arguments(
    prepared: PreparedSketchLock,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    resolved = prepared.resolved
    point = resolved.point
    if resolved.target_form == "absolute":
        prefix = (point.geometry_index, point.position_code)
    else:
        reference = resolved.reference
        if reference is None:
            raise NativeSketchError(f"{_LABEL} relative reference is unavailable.")
        prefix = (
            point.geometry_index,
            point.position_code,
            reference.geometry_index,
            reference.position_code,
        )
    return (
        ("DistanceX", *prefix, prepared.spec.expected_x_mm),
        ("DistanceY", *prefix, prepared.spec.expected_y_mm),
    )


def _constraints(prepared: PreparedSketchLock) -> tuple[Any, ...]:
    return tuple(
        make_dimensional_constraint(arguments, driving=prepared.spec.driving)
        for arguments in _constraint_arguments(prepared)
    )


def preflight_sketch_lock(
    context: NativeRuntimeContext,
    spec: SketchLockSpec,
) -> PreparedSketchLock:
    if not isinstance(spec, SketchLockSpec):
        raise TypeError("spec must be a SketchLockSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = _resolve_lock(sketch, spec)
    solver_issues = sketch_solver_issues(sketch, _LABEL)
    prepared = PreparedSketchLock(target, spec, resolved, solver_issues)
    diagnose_exact_constraints(
        sketch,
        _constraints(prepared),
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


def create_sketch_lock(
    document: Any,
    prepared: PreparedSketchLock,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchLock):
        raise TypeError("prepared must be a PreparedSketchLock")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Lock preflight",
    )
    indices = add_exact_constraints(
        sketch,
        _constraints(prepared),
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Lock",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_indices": indices},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _references(
    resolved: ResolvedSketchLock,
) -> tuple[Mapping[str, Any], ...]:
    result = [
        {
            "slot": 1,
            "geometry_index": resolved.point.geometry_index,
            "position": resolved.point.position_code,
        }
    ]
    if resolved.reference is not None:
        result.append(
            {
                "slot": 2,
                "geometry_index": resolved.reference.geometry_index,
                "position": resolved.reference.position_code,
            }
        )
    return tuple(result)


def verify_sketch_lock(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchLock):
        raise TypeError("draft must contain a PreparedSketchLock")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    references = _references(prepared.resolved)
    expectations = tuple(
        ExactConstraintExpectation(
            constraint_type,
            references,
            prepared.spec.driving,
            value,
            _LINEAR_TOLERANCE,
        )
        for constraint_type, value in (
            ("DistanceX", prepared.spec.expected_x_mm),
            ("DistanceY", prepared.spec.expected_y_mm),
        )
    )
    constraints = verify_exact_constraint_appends(
        sketch,
        prepared.target,
        constraint_indices=tuple(draft.value["constraint_indices"]),
        solver_issues=prepared.solver_issues,
        expectations=expectations,
        label=_LABEL,
    )
    measured_after = _resolve_lock(sketch, prepared.spec)
    if (
        measured_after.target_form != prepared.resolved.target_form
        or measured_after.point != prepared.resolved.point
        or measured_after.reference != prepared.resolved.reference
    ):
        raise NativeSketchError(f"{_LABEL} solver changed the exact target form.")
    before = {
        "x": prepared.resolved.measured_x_mm,
        "y": prepared.resolved.measured_y_mm,
        "unit": "mm",
    }
    after = {
        "x": measured_after.measured_x_mm,
        "y": measured_after.measured_y_mm,
        "unit": "mm",
    }
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_lock",
            "target_form": prepared.resolved.target_form,
            "constraints": list(constraints),
            "measured_before": before,
            "measured_after": after,
        },
    )
