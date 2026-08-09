# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic ordered Equal chains for the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import (
    ExactConstraintExpectation,
    add_exact_constraints,
    diagnose_exact_constraints,
    sketch_solver_issues,
    verify_exact_constraint_appends,
)
from VibeCADNativeSketchConstraintTargets import (
    PreparedSketchConstraintTarget,
    SketchConstraintElement,
    current_sketch_constraint_records,
    preflight_sketch_constraint_target,
    require_unchanged_sketch_constraint_target,
)
from VibeCADNativeSketchEqualMeasure import EqualMeasurement, measure_sketch_equal
from VibeCADNativeSketchEqualTarget import (
    EqualConstraintPlan,
    LABEL,
    ResolvedSketchEqual,
    SketchEqualSpec,
    make_equal_constraints,
    prepare_sketch_equal_target,
    resolve_sketch_equal,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchEqual:
    target: PreparedSketchConstraintTarget
    spec: SketchEqualSpec
    resolved: ResolvedSketchEqual
    measurement_before: EqualMeasurement
    solver_issues: tuple[tuple[int, ...], ...]


def prepare_sketch_equal(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchEqualSpec:
    return prepare_sketch_equal_target(document_uid, value)


def preflight_sketch_equal(
    context: NativeRuntimeContext,
    spec: SketchEqualSpec,
) -> PreparedSketchEqual:
    if not isinstance(spec, SketchEqualSpec):
        raise TypeError("spec must be a SketchEqualSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = resolve_sketch_equal(sketch, spec)
    measurement = measure_sketch_equal(sketch, resolved)
    solver_issues = sketch_solver_issues(sketch, LABEL)
    diagnose_exact_constraints(
        sketch,
        make_equal_constraints(resolved),
        expected_index=spec.target.target.expected_constraint_count,
        label=LABEL,
    )
    geometry, constraints, external = current_sketch_constraint_records(
        sketch,
        spec.target,
    )
    if (
        geometry != target.geometry_records
        or constraints != target.constraint_records
        or external != target.external_geometry_records
        or sketch_solver_issues(sketch, LABEL) != solver_issues
    ):
        raise NativeSketchError(f"{LABEL} feasibility check changed the active Sketch.")
    return PreparedSketchEqual(
        target,
        spec,
        resolved,
        measurement,
        solver_issues,
    )


def create_sketch_equal(
    document: Any,
    prepared: PreparedSketchEqual,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchEqual):
        raise TypeError("prepared must be a PreparedSketchEqual")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Equal preflight",
    )
    indices = add_exact_constraints(
        sketch,
        make_equal_constraints(prepared.resolved),
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Equal constraint chain",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_indices": indices},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _references(
    references: tuple[SketchConstraintElement, SketchConstraintElement],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {"slot": slot, "geometry_index": element.geometry_index}
        for slot, element in enumerate(references, start=1)
    )


def _expectation(plan: EqualConstraintPlan) -> ExactConstraintExpectation:
    return ExactConstraintExpectation(
        "Equal",
        _references(plan.references),
        True,
        None,
        0.0,
    )


def verify_sketch_equal(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchEqual):
        raise TypeError("draft must contain a PreparedSketchEqual")
    raw_indices = draft.value.get("constraint_indices")
    if not isinstance(raw_indices, tuple):
        raise TypeError("draft must contain exact Equal constraint indices")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraints = verify_exact_constraint_appends(
        sketch,
        prepared.target,
        constraint_indices=tuple(int(value) for value in raw_indices),
        solver_issues=prepared.solver_issues,
        expectations=tuple(_expectation(plan) for plan in prepared.resolved.plans),
        label=LABEL,
    )
    measurement_after = measure_sketch_equal(sketch, prepared.resolved)
    if (
        measurement_after.family != prepared.measurement_before.family
        or measurement_after.unit != prepared.measurement_before.unit
        or not measurement_after.satisfied()
    ):
        raise NativeSketchError(
            f"{LABEL} solver result does not satisfy the exact constraint chain."
        )
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_equal",
            "family": prepared.resolved.family,
            "constraints": list(constraints),
            "measured_before": prepared.measurement_before.record(),
            "measured_after": measurement_after.record(),
        },
    )
