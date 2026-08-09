# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact reflection constraints for the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
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
    current_sketch_constraint_records,
    preflight_sketch_constraint_target,
    require_unchanged_sketch_constraint_target,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchSymmetricMeasure import (
    SymmetricMeasurement,
    measure_sketch_symmetric,
)
from VibeCADNativeSketchSymmetricTarget import (
    LABEL,
    ResolvedSketchSymmetric,
    SketchSymmetricSpec,
    make_symmetric_constraint,
    prepare_sketch_symmetric_target,
    resolve_sketch_symmetric,
)
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchSymmetric:
    target: PreparedSketchConstraintTarget
    spec: SketchSymmetricSpec
    resolved: ResolvedSketchSymmetric
    measurement_before: SymmetricMeasurement
    solver_issues: tuple[tuple[int, ...], ...]


def prepare_sketch_symmetric(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchSymmetricSpec:
    return prepare_sketch_symmetric_target(document_uid, value)


def preflight_sketch_symmetric(
    context: NativeRuntimeContext,
    spec: SketchSymmetricSpec,
) -> PreparedSketchSymmetric:
    if not isinstance(spec, SketchSymmetricSpec):
        raise TypeError("spec must be a SketchSymmetricSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = resolve_sketch_symmetric(sketch, spec)
    measurement = measure_sketch_symmetric(sketch, resolved)
    solver_issues = sketch_solver_issues(sketch, LABEL)
    diagnose_exact_constraint(
        sketch,
        make_symmetric_constraint(resolved),
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
    return PreparedSketchSymmetric(
        target,
        spec,
        resolved,
        measurement,
        solver_issues,
    )


def create_sketch_symmetric(
    document: Any,
    prepared: PreparedSketchSymmetric,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchSymmetric):
        raise TypeError("prepared must be a PreparedSketchSymmetric")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Symmetric preflight",
    )
    constraint_index = add_exact_constraint(
        sketch,
        make_symmetric_constraint(prepared.resolved),
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Symmetric",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "constraint_index": constraint_index,
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _expectation(
    resolved: ResolvedSketchSymmetric,
) -> ExactConstraintExpectation:
    references = []
    for slot, element in enumerate(resolved.references, start=1):
        reference: dict[str, Any] = {
            "slot": slot,
            "geometry_index": element.geometry_index,
        }
        if slot < 3 or resolved.reference_kind == "point":
            reference["position"] = element.position_code
        references.append(reference)
    return ExactConstraintExpectation(
        "Symmetric",
        tuple(references),
        True,
        None,
        0.0,
    )


def verify_sketch_symmetric(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchSymmetric):
        raise TypeError("draft must contain a PreparedSketchSymmetric")
    raw_index = draft.value.get("constraint_index")
    if type(raw_index) is not int:
        raise TypeError("draft must contain one exact Symmetric constraint index")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraints = verify_exact_constraint_appends(
        sketch,
        prepared.target,
        constraint_indices=(raw_index,),
        solver_issues=prepared.solver_issues,
        expectations=(_expectation(prepared.resolved),),
        label=LABEL,
    )
    measurement_after = measure_sketch_symmetric(sketch, prepared.resolved)
    if (
        measurement_after.reference_kind != prepared.measurement_before.reference_kind
        or not measurement_after.satisfied()
    ):
        raise NativeSketchError(
            f"{LABEL} solver result does not satisfy the exact reflection."
        )
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_symmetric",
            "form": prepared.resolved.target_form,
            "constraint": constraints[0],
            "measured_before": prepared.measurement_before.record(),
            "measured_after": measurement_after.record(),
        },
    )
