# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic exact Tangent forms for the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import (
    ExactConstraintExpectation,
    add_exact_constraints,
    diagnose_exact_constraint_replacement,
    diagnose_exact_constraints,
    sketch_solver_issues,
    verify_exact_constraint_appends,
    verify_exact_constraint_replacement,
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
from VibeCADNativeSketchTangentMeasure import (
    TangentMeasurement,
    measure_sketch_tangent,
    tangent_contact_satisfied,
)
from VibeCADNativeSketchTangentTarget import (
    LABEL,
    ResolvedSketchTangent,
    SketchTangentSpec,
    TangentConstraintPlan,
    make_tangent_constraints,
    prepare_sketch_tangent_target,
    resolve_sketch_tangent,
    tangent_via_point_is_on_curves,
)
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


_ORIENTATION_VALUES = (-math.pi / 2.0, math.pi / 2.0)


@dataclass(frozen=True, slots=True)
class PreparedSketchTangent:
    target: PreparedSketchConstraintTarget
    spec: SketchTangentSpec
    resolved: ResolvedSketchTangent
    measurement_before: TangentMeasurement
    solver_issues: tuple[tuple[int, ...], ...]


def prepare_sketch_tangent(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchTangentSpec:
    return prepare_sketch_tangent_target(document_uid, value)


def preflight_sketch_tangent(
    context: NativeRuntimeContext,
    spec: SketchTangentSpec,
) -> PreparedSketchTangent:
    if not isinstance(spec, SketchTangentSpec):
        raise TypeError("spec must be a SketchTangentSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = resolve_sketch_tangent(sketch, spec)
    measurement = measure_sketch_tangent(sketch, resolved)
    solver_issues = sketch_solver_issues(sketch, LABEL)
    constraints_to_apply = make_tangent_constraints(resolved)
    if resolved.replacement_index is None:
        diagnose_exact_constraints(
            sketch,
            constraints_to_apply,
            expected_index=spec.target.target.expected_constraint_count,
            label=LABEL,
        )
    else:
        if len(constraints_to_apply) != 1:
            raise NativeSketchError(
                f"{LABEL} replacement must produce exactly one constraint."
            )
        diagnose_exact_constraint_replacement(
            sketch,
            constraints_to_apply[0],
            replaced_constraint_index=resolved.replacement_index,
            expected_index=spec.target.target.expected_constraint_count - 1,
            label=LABEL,
        )
    geometry, constraints, external = current_sketch_constraint_records(
        sketch, spec.target
    )
    if (
        geometry != target.geometry_records
        or constraints != target.constraint_records
        or external != target.external_geometry_records
        or sketch_solver_issues(sketch, LABEL) != solver_issues
    ):
        raise NativeSketchError(f"{LABEL} feasibility check changed the active Sketch.")
    return PreparedSketchTangent(
        target,
        spec,
        resolved,
        measurement,
        solver_issues,
    )


def _delete_exact_constraint(sketch: Any, index: int, expected_count: int) -> None:
    delete = getattr(sketch, "delConstraint", None)
    if not callable(delete):
        raise NativeSketchError(f"{LABEL} exact constraint deletion is unavailable.")
    try:
        delete(index, True)
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact Tangent replacement deletion."
        ) from exc
    try:
        observed = int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraint count is unavailable.") from exc
    if observed != expected_count - 1:
        raise NativeSketchError(
            f"{LABEL} replacement did not delete exactly one constraint."
        )


def create_sketch_tangent(
    document: Any,
    prepared: PreparedSketchTangent,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchTangent):
        raise TypeError("prepared must be a PreparedSketchTangent")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Tangent preflight",
    )
    expected_count = prepared.spec.target.target.expected_constraint_count
    replacement_index = prepared.resolved.replacement_index
    if replacement_index is not None:
        _delete_exact_constraint(sketch, replacement_index, expected_count)
        expected_index = expected_count - 1
    else:
        expected_index = expected_count
    indices = add_exact_constraints(
        sketch,
        make_tangent_constraints(prepared.resolved),
        expected_index=expected_index,
        label="Tangent constraint set",
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


def _expectation(plan: TangentConstraintPlan) -> ExactConstraintExpectation:
    return ExactConstraintExpectation(
        "PointOnObject" if plan.support else "Tangent",
        _references(plan.references),
        True,
        None,
        1.0e-9,
        _ORIENTATION_VALUES if plan.orientation_value else (),
    )


def _replacement_summary(resolved: ResolvedSketchTangent) -> dict[str, Any] | None:
    if resolved.replaced_constraint is None or resolved.replacement_index is None:
        return None
    record = resolved.replaced_constraint
    return {
        "index": resolved.replacement_index,
        "type": record.get("type"),
        "references": list(record.get("references", [])),
    }


def verify_sketch_tangent(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchTangent):
        raise TypeError("draft must contain a PreparedSketchTangent")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    raw_indices = draft.value.get("constraint_indices")
    if not isinstance(raw_indices, tuple):
        raise TypeError("draft must contain exact Tangent constraint indices")
    indices = tuple(int(value) for value in raw_indices)
    expectations = tuple(
        _expectation(plan) for plan in prepared.resolved.plans
    )
    replacement_index = prepared.resolved.replacement_index
    if replacement_index is None:
        constraints = verify_exact_constraint_appends(
            sketch,
            prepared.target,
            constraint_indices=indices,
            solver_issues=prepared.solver_issues,
            expectations=expectations,
            label=LABEL,
        )
    else:
        if len(indices) != 1 or len(expectations) != 1:
            raise NativeSketchError(
                f"{LABEL} replacement did not produce one exact constraint."
            )
        constraints = (
            verify_exact_constraint_replacement(
                sketch,
                prepared.target,
                replaced_constraint_index=replacement_index,
                replacement_constraint_index=indices[0],
                solver_issues=prepared.solver_issues,
                expectation=expectations[0],
                label=LABEL,
            ),
        )
    measurement_after = measure_sketch_tangent(sketch, prepared.resolved)
    if (
        measurement_after.name != prepared.measurement_before.name
        or not measurement_after.satisfied()
        or not tangent_contact_satisfied(sketch, prepared.resolved)
        or not tangent_via_point_is_on_curves(sketch, prepared.resolved)
    ):
        raise NativeSketchError(
            f"{LABEL} solver result does not satisfy the exact constraint."
        )
    support_count = len(prepared.resolved.plans) - 1
    result = {
        "operation": "constrain_tangent",
        "form": prepared.resolved.target_form,
        "constraint": constraints[-1],
        "support_constraints": list(constraints[:support_count]),
        "measured_before": prepared.measurement_before.record(),
        "measured_after": measurement_after.record(),
    }
    replacement = _replacement_summary(prepared.resolved)
    if replacement is not None:
        result["replaced_constraint"] = replacement
    return sketch_geometry_result(sketch, result)
