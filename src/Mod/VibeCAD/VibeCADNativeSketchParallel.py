# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic Parallel constraint for two exact Sketch lines."""

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
        "selection",
    }
)
_LINE_TYPE = "Part::GeomLineSegment"
_LINEAR_TOLERANCE = 1.0e-7
_ANGULAR_TOLERANCE = 1.0e-7
_LABEL = "Sketch Parallel"


@dataclass(frozen=True, slots=True)
class SketchParallelSpec:
    target: SketchConstraintTargetSpec


@dataclass(frozen=True, slots=True)
class ResolvedSketchParallel:
    references: tuple[SketchConstraintElement, SketchConstraintElement]
    angular_error_before_degrees: float


@dataclass(frozen=True, slots=True)
class PreparedSketchParallel:
    target: PreparedSketchConstraintTarget
    spec: SketchParallelSpec
    resolved: ResolvedSketchParallel
    solver_issues: tuple[tuple[int, ...], ...]


def prepare_sketch_parallel(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchParallelSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {_LABEL} definition has incorrect fields.")
    selection = value["selection"]
    if not isinstance(selection, list) or len(selection) != 2:
        raise NativeSketchError(f"{_LABEL} selection must contain exactly two lines.")
    return SketchParallelSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value[
                "expected_external_geometry_count"
            ],
            selection=selection,
        )
    )


def _line_delta(
    sketch: Any,
    element: SketchConstraintElement,
) -> tuple[float, float]:
    if element.position != "whole":
        raise NativeSketchError(f"{_LABEL} targets must be exact whole lines.")
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    if str(getattr(geometry, "TypeId", "") or "") != _LINE_TYPE:
        raise NativeSketchError(f"{_LABEL} targets must both be straight lines.")
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{_LABEL} point lookup is unavailable.")
    try:
        start = getter(element.geometry_index, 1)
        end = getter(element.geometry_index, 2)
        delta = float(end.x) - float(start.x), float(end.y) - float(start.y)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} line endpoints are unavailable.") from exc
    if not all(math.isfinite(value) for value in delta):
        raise NativeSketchError(f"{_LABEL} line endpoints are not finite.")
    if math.hypot(*delta) <= _LINEAR_TOLERANCE:
        raise NativeSketchError(f"{_LABEL} cannot use a zero-length line.")
    return delta


def _angular_error_degrees(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    denominator = math.hypot(*first) * math.hypot(*second)
    sine = abs(first[0] * second[1] - first[1] * second[0]) / denominator
    return math.degrees(math.asin(min(1.0, max(0.0, sine))))


def _refuse_existing_parallel(
    sketch: Any,
    first_index: int,
    second_index: int,
) -> None:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{_LABEL} constraints are unavailable.") from exc
    expected = {first_index, second_index}
    for constraint in constraints:
        if str(getattr(constraint, "Type", "") or "") != "Parallel":
            continue
        observed = {
            int(getattr(constraint, "First", -2000)),
            int(getattr(constraint, "Second", -2000)),
        }
        if observed == expected:
            raise NativeSketchError(
                f"{_LABEL} targets already have a Parallel constraint."
            )


def _resolve_parallel(
    sketch: Any,
    spec: SketchParallelSpec,
) -> ResolvedSketchParallel:
    first, second = spec.target.selection
    if first.geometry_index == second.geometry_index:
        raise NativeSketchError(f"{_LABEL} requires two distinct lines.")
    if first.geometry_index < 0 and second.geometry_index < 0:
        raise NativeSketchError(
            f"{_LABEL} requires at least one editable internal line."
        )
    first_delta = _line_delta(sketch, first)
    second_delta = _line_delta(sketch, second)
    _refuse_existing_parallel(sketch, first.geometry_index, second.geometry_index)
    return ResolvedSketchParallel(
        (first, second),
        _angular_error_degrees(first_delta, second_delta),
    )


def _constraint(resolved: ResolvedSketchParallel) -> Any:
    import Sketcher

    first, second = resolved.references
    try:
        return Sketcher.Constraint(
            "Parallel",
            first.geometry_index,
            second.geometry_index,
        )
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact Parallel definition."
        ) from exc


def preflight_sketch_parallel(
    context: NativeRuntimeContext,
    spec: SketchParallelSpec,
) -> PreparedSketchParallel:
    if not isinstance(spec, SketchParallelSpec):
        raise TypeError("spec must be a SketchParallelSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = _resolve_parallel(sketch, spec)
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
        raise NativeSketchError(f"{_LABEL} feasibility check changed the active Sketch.")
    return PreparedSketchParallel(target, spec, resolved, solver_issues)


def create_sketch_parallel(
    document: Any,
    prepared: PreparedSketchParallel,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchParallel):
        raise TypeError("prepared must be a PreparedSketchParallel")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Parallel preflight",
    )
    index = add_exact_constraint(
        sketch,
        _constraint(prepared.resolved),
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Parallel constraint",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _references(
    resolved: ResolvedSketchParallel,
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {"slot": slot, "geometry_index": element.geometry_index}
        for slot, element in enumerate(resolved.references, start=1)
    )


def _current_angular_error(
    sketch: Any,
    resolved: ResolvedSketchParallel,
) -> float:
    return _angular_error_degrees(
        _line_delta(sketch, resolved.references[0]),
        _line_delta(sketch, resolved.references[1]),
    )


def verify_sketch_parallel(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchParallel):
        raise TypeError("draft must contain a PreparedSketchParallel")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraint = verify_exact_constraint_appends(
        sketch,
        prepared.target,
        constraint_indices=(int(draft.value["constraint_index"]),),
        solver_issues=prepared.solver_issues,
        expectations=(
            ExactConstraintExpectation(
                "Parallel",
                _references(prepared.resolved),
                True,
                None,
                0.0,
            ),
        ),
        label=_LABEL,
    )[0]
    angular_error_after = _current_angular_error(sketch, prepared.resolved)
    if math.radians(angular_error_after) > _ANGULAR_TOLERANCE:
        raise NativeSketchError(
            f"{_LABEL} solver result does not satisfy the exact constraint."
        )
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_parallel",
            "constraint": constraint,
            "measured_before": {
                "angular_error": prepared.resolved.angular_error_before_degrees,
                "unit": "deg",
            },
            "measured_after": {
                "angular_error": angular_error_after,
                "unit": "deg",
            },
        },
    )
