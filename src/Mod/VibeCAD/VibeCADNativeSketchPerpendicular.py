# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic exact Perpendicular forms for the human-opened Sketch."""

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
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchPerpendicularMeasure import (
    PerpendicularMeasurement,
    measure_sketch_perpendicular,
)
from VibeCADNativeSketchPerpendicularTarget import (
    LABEL,
    PerpendicularConstraintPlan,
    ResolvedSketchPerpendicular,
    SketchPerpendicularSpec,
    make_perpendicular_constraints,
    perpendicular_via_point_is_on_curves,
    prepare_sketch_perpendicular_target,
    resolve_sketch_perpendicular,
)
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


_ORIENTATION_VALUES = (math.pi / 2.0, 3.0 * math.pi / 2.0)


@dataclass(frozen=True, slots=True)
class PreparedSketchPerpendicular:
    target: PreparedSketchConstraintTarget
    spec: SketchPerpendicularSpec
    resolved: ResolvedSketchPerpendicular
    measurement_before: PerpendicularMeasurement
    solver_issues: tuple[tuple[int, ...], ...]


def prepare_sketch_perpendicular(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchPerpendicularSpec:
    return prepare_sketch_perpendicular_target(document_uid, value)


def preflight_sketch_perpendicular(
    context: NativeRuntimeContext,
    spec: SketchPerpendicularSpec,
) -> PreparedSketchPerpendicular:
    if not isinstance(spec, SketchPerpendicularSpec):
        raise TypeError("spec must be a SketchPerpendicularSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = resolve_sketch_perpendicular(sketch, spec)
    measurement = measure_sketch_perpendicular(sketch, resolved)
    solver_issues = sketch_solver_issues(sketch, LABEL)
    diagnose_exact_constraints(
        sketch,
        make_perpendicular_constraints(resolved),
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
    return PreparedSketchPerpendicular(
        target,
        spec,
        resolved,
        measurement,
        solver_issues,
    )


def create_sketch_perpendicular(
    document: Any,
    prepared: PreparedSketchPerpendicular,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchPerpendicular):
        raise TypeError("prepared must be a PreparedSketchPerpendicular")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Perpendicular preflight",
    )
    indices = add_exact_constraints(
        sketch,
        make_perpendicular_constraints(prepared.resolved),
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Perpendicular constraint set",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_indices": indices},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _references(
    references: tuple[SketchConstraintElement, ...],
) -> tuple[Mapping[str, Any], ...]:
    result = []
    for slot, element in enumerate(references, start=1):
        reference: dict[str, Any] = {
            "slot": slot,
            "geometry_index": element.geometry_index,
        }
        if element.position_code:
            reference["position"] = element.position_code
        result.append(reference)
    return tuple(result)


def _expectation(plan: PerpendicularConstraintPlan) -> ExactConstraintExpectation:
    return ExactConstraintExpectation(
        "PointOnObject" if plan.support else "Perpendicular",
        _references(plan.references),
        True,
        None,
        1.0e-9,
        _ORIENTATION_VALUES if plan.orientation_value else (),
    )


def verify_sketch_perpendicular(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchPerpendicular):
        raise TypeError("draft must contain a PreparedSketchPerpendicular")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    raw_indices = draft.value.get("constraint_indices")
    if not isinstance(raw_indices, tuple):
        raise TypeError("draft must contain exact Perpendicular constraint indices")
    constraints = verify_exact_constraint_appends(
        sketch,
        prepared.target,
        constraint_indices=tuple(int(value) for value in raw_indices),
        solver_issues=prepared.solver_issues,
        expectations=tuple(_expectation(plan) for plan in prepared.resolved.plans),
        label=LABEL,
    )
    measurement_after = measure_sketch_perpendicular(sketch, prepared.resolved)
    if (
        measurement_after.name != prepared.measurement_before.name
        or not measurement_after.satisfied()
        or not perpendicular_via_point_is_on_curves(sketch, prepared.resolved)
    ):
        raise NativeSketchError(
            f"{LABEL} solver result does not satisfy the exact constraint."
        )
    support_count = len(prepared.resolved.plans) - 1
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_perpendicular",
            "form": prepared.resolved.target_form,
            "constraint": constraints[-1],
            "support_constraints": list(constraints[:support_count]),
            "measured_before": prepared.measurement_before.record(),
            "measured_after": measurement_after.record(),
        },
    )
