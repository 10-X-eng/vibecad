# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact state checks for one-step B-spline knot multiplicity changes."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineHelperState import (
    HelperAlignment,
    alignment_values,
    geometry_metadata,
    indexed_records,
    stable_alignment_values,
    verify_helper_reconciliation,
)
from VibeCADNativeSketchBSplineKnotState import capture_bspline_knot_snapshot
from VibeCADNativeSketchBSplineKnotMultiplicityProof import (
    KnotMultiplicityCurveProof,
    knot_multiplicity_curve_proof,
    maximum_sampled_deviation_mm,
    maximum_sampled_displacement_mm,
)
from VibeCADNativeSketchDiagnosticState import ISSUE_FIELDS
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchMutationState import geometry_records_without_tags
from VibeCADNativeSketchTransformState import (
    SketchTransformPlan,
    SketchTransformSnapshot,
    parse_transform_diagnostic,
    require_pure_transform_diagnostic,
    require_transform_snapshot_unchanged,
    verify_transform_state,
)


MAX_HELPERS = 4_096
MAX_CREATED_CONSTRAINTS = 8_192
_FIELDS = frozenset(
    {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        *ISSUE_FIELDS,
        "geometry_count",
        "constraint_count",
        "geometry",
        "geometry_metadata",
        "constraints",
        "external_reference_count",
        "external_references",
        "external_geometry_count",
        "external_geometry",
        "external_geometry_metadata",
        "geometry_index",
        "knot_index",
        "knot_parameter",
        "degree",
        "old_multiplicity",
        "new_multiplicity",
        "retained_internal_geometry_count",
        "deleted_internal_geometry_count",
        "exposed_internal_geometry_count",
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)


@dataclass(frozen=True, slots=True)
class SketchBSplineKnotMultiplicitySnapshot:
    label: str
    increment: int
    maximum_allowed_deviation_mm: float
    transform: SketchTransformSnapshot
    proof: KnotMultiplicityCurveProof
    helpers: tuple[HelperAlignment, ...]


@dataclass(frozen=True, slots=True)
class SketchBSplineKnotMultiplicityPlan:
    transform: SketchTransformPlan
    knot_parameter: float
    degree: int
    old_multiplicity: int
    new_multiplicity: int
    retained_internal_geometry_count: int
    deleted_internal_geometry_count: int
    exposed_internal_geometry_count: int
    maximum_deviation_mm: float
    proof: KnotMultiplicityCurveProof
    helpers: tuple[HelperAlignment, ...]


def capture_bspline_knot_multiplicity_snapshot(
    context: NativeRuntimeContext,
    spec: Any,
    *,
    label: str,
    increment: int,
    maximum_allowed_deviation_mm: float,
) -> SketchBSplineKnotMultiplicitySnapshot:
    if increment not in {-1, 1}:
        raise ValueError("increment must be -1 or 1")
    if (
        not math.isfinite(maximum_allowed_deviation_mm)
        or maximum_allowed_deviation_mm < 0
    ):
        raise ValueError("maximum_allowed_deviation_mm must be finite and non-negative")
    base = capture_bspline_knot_snapshot(context, spec, label=label)
    proof = base.proof
    if spec.knot_index >= len(proof.knots):
        raise NativeSketchError(f"{label} requires one current B-spline knot.")
    if increment > 0 and proof.multiplicities[spec.knot_index] >= proof.degree:
        raise NativeSketchError(
            f"{label} requires a knot whose multiplicity is below the spline degree."
        )
    return SketchBSplineKnotMultiplicitySnapshot(
        label,
        increment,
        maximum_allowed_deviation_mm,
        base.transform,
        proof,
        base.helpers,
    )


def _integer(value: Any, field: str, maximum: int, *, label: str) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeSketchError(f"{label} feasibility returned invalid {field}.")
    return value


def _number(value: Any, field: str, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeSketchError(
            f"{label} feasibility returned invalid {field}."
        ) from exc
    if not math.isfinite(result):
        raise NativeSketchError(f"{label} feasibility returned invalid {field}.")
    return round(result, 12)


def parse_bspline_knot_multiplicity_diagnostic(
    result: Any,
    snapshot: SketchBSplineKnotMultiplicitySnapshot,
) -> SketchBSplineKnotMultiplicityPlan:
    label = snapshot.label
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{label} feasibility returned incomplete diagnostics.")
    transform_snapshot = snapshot.transform
    spec = transform_snapshot.spec
    root = spec.geometry_index
    if result["geometry_index"] != root or result["knot_index"] != spec.knot_index:
        raise NativeSketchError(f"{label} feasibility analyzed a different operation.")
    knot_parameter = _number(result["knot_parameter"], "knot parameter", label=label)
    degree = _integer(result["degree"], "degree", 25, label=label)
    old_multiplicity = _integer(
        result["old_multiplicity"], "old multiplicity", 25, label=label
    )
    new_multiplicity = _integer(
        result["new_multiplicity"], "new multiplicity", 25, label=label
    )
    retained_count = _integer(
        result["retained_internal_geometry_count"],
        "retained helper count",
        MAX_HELPERS,
        label=label,
    )
    deleted_count = _integer(
        result["deleted_internal_geometry_count"],
        "deleted helper count",
        MAX_HELPERS,
        label=label,
    )
    exposed_count = _integer(
        result["exposed_internal_geometry_count"],
        "exposed helper count",
        MAX_HELPERS,
        label=label,
    )
    plan = parse_transform_diagnostic(result, transform_snapshot)
    if (
        plan.external_reference_records
        != transform_snapshot.state.external_reference_records
        or plan.external_geometry_records
        != transform_snapshot.state.external_geometry_records
    ):
        raise NativeSketchError(f"{label} feasibility changed external geometry.")
    before = indexed_records(
        geometry_records_without_tags(transform_snapshot.state.geometry_records),
        "geometry",
    )
    after = indexed_records(plan.geometry_records, "geometry")
    raw_after = result["geometry"]
    if not isinstance(raw_after, (list, tuple)) or len(raw_after) != len(after):
        raise NativeSketchError(
            f"{label} feasibility returned invalid geometry objects."
        )
    if root not in before or root not in after:
        raise NativeSketchError(f"{label} feasibility replaced the root geometry.")
    old_record = before[root]
    new_record = after[root]
    proof = knot_multiplicity_curve_proof(raw_after[root], label=label)
    expected_knots = list(snapshot.proof.knots)
    expected_multiplicities = list(snapshot.proof.multiplicities)
    expected_multiplicities[spec.knot_index] += snapshot.increment
    if expected_multiplicities[spec.knot_index] == 0:
        del expected_knots[spec.knot_index]
        del expected_multiplicities[spec.knot_index]
    endpoint_removed = (
        snapshot.increment < 0
        and new_multiplicity == 0
        and spec.knot_index in {0, len(snapshot.proof.knots) - 1}
    )
    expected_first_parameter = (
        expected_knots[0]
        if endpoint_removed and spec.knot_index == 0
        else snapshot.proof.first_parameter
    )
    expected_last_parameter = (
        expected_knots[-1]
        if endpoint_removed and spec.knot_index == len(snapshot.proof.knots) - 1
        else snapshot.proof.last_parameter
    )
    if (
        degree != snapshot.proof.degree
        or old_multiplicity != snapshot.proof.multiplicities[spec.knot_index]
        or new_multiplicity != old_multiplicity + snapshot.increment
        or knot_parameter != snapshot.proof.knots[spec.knot_index]
        or proof.degree != snapshot.proof.degree
        or proof.knots != tuple(expected_knots)
        or proof.multiplicities != tuple(expected_multiplicities)
        or proof.first_parameter != expected_first_parameter
        or proof.last_parameter != expected_last_parameter
        or proof.rational != snapshot.proof.rational
        or proof.periodic != snapshot.proof.periodic
        or proof.closed != snapshot.proof.closed
        or len(proof.control_positions)
        != len(snapshot.proof.control_positions) + snapshot.increment
        or new_record.get("kind") != "b_spline"
        or geometry_metadata(new_record) != geometry_metadata(old_record)
    ):
        raise NativeSketchError(f"{label} returned the wrong spline representation.")
    deviation_method = (
        maximum_sampled_displacement_mm
        if snapshot.increment > 0
        else maximum_sampled_deviation_mm
    )
    deviation = round(deviation_method(snapshot.proof, proof, label=label), 12)
    if deviation > snapshot.maximum_allowed_deviation_mm + 1.0e-9:
        if snapshot.increment > 0:
            raise NativeSketchError(
                f"{label} would move the curve by {deviation:.12g} mm."
            )
        raise NativeSketchError(
            f"{label} would exceed maximum_deviation_mm ({deviation:.12g} mm)."
        )
    if len(proof.control_positions) + len(proof.knot_positions) > MAX_HELPERS:
        raise NativeSketchError(f"{label} would create too much spline state.")
    helpers = verify_helper_reconciliation(
        label=label,
        root=root,
        before=before,
        after=after,
        old_helpers=snapshot.helpers,
        plan=plan,
        before_constraint_records=transform_snapshot.state.constraint_records,
        result=result,
        control_positions=proof.control_positions,
        knot_positions=proof.knot_positions,
        retained_count=retained_count,
        deleted_count=deleted_count,
        exposed_count=exposed_count,
        maximum_created_constraints=MAX_CREATED_CONSTRAINTS,
    )
    return SketchBSplineKnotMultiplicityPlan(
        plan,
        knot_parameter,
        degree,
        old_multiplicity,
        new_multiplicity,
        retained_count,
        deleted_count,
        exposed_count,
        deviation,
        proof,
        helpers,
    )


def _current_helpers(snapshot: SketchBSplineKnotMultiplicitySnapshot, sketch: Any):
    state = snapshot.transform.state
    geometry = indexed_records(state.geometry_records, "geometry")
    return alignment_values(
        tuple(sketch.Constraints),
        geometry,
        state.geometry_tags,
        state.constraint_tags,
        snapshot.transform.spec.geometry_index,
    )


def require_bspline_knot_multiplicity_snapshot_unchanged(
    document: Any,
    snapshot: SketchBSplineKnotMultiplicitySnapshot,
) -> Any:
    sketch = require_transform_snapshot_unchanged(document, snapshot.transform)
    if _current_helpers(snapshot, sketch) != snapshot.helpers:
        raise NativeSketchError(
            f"{snapshot.label} helper alignment changed; read it and retry."
        )
    return sketch


def require_pure_bspline_knot_multiplicity_diagnostic(
    snapshot: SketchBSplineKnotMultiplicitySnapshot,
) -> None:
    require_pure_transform_diagnostic(snapshot.transform)
    if _current_helpers(snapshot, snapshot.transform.target.sketch) != snapshot.helpers:
        raise NativeSketchError(f"{snapshot.label} diagnosis changed the live Sketch.")


def verify_bspline_knot_multiplicity_state(
    document: Any,
    snapshot: SketchBSplineKnotMultiplicitySnapshot,
    plan: SketchBSplineKnotMultiplicityPlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    verified = verify_transform_state(
        document, snapshot.transform, plan.transform, receipt
    )
    sketch = verified[0]
    proof = knot_multiplicity_curve_proof(
        tuple(sketch.Geometry)[snapshot.transform.spec.geometry_index],
        label=snapshot.label,
    )
    deviation_method = (
        maximum_sampled_displacement_mm
        if snapshot.increment > 0
        else maximum_sampled_deviation_mm
    )
    deviation = round(deviation_method(snapshot.proof, proof, label=snapshot.label), 12)
    if proof.digest != plan.proof.digest or deviation != plan.maximum_deviation_mm:
        raise NativeSketchError(
            f"{snapshot.label} final B-spline representation is wrong."
        )
    geometry = indexed_records(plan.transform.geometry_records, "geometry")
    current_helpers = alignment_values(
        tuple(sketch.Constraints),
        geometry,
        tuple(str(item.Tag) for item in sketch.GeometryFacadeList),
        tuple(str(item.Tag) for item in sketch.Constraints),
        snapshot.transform.spec.geometry_index,
    )
    current_helpers = stable_alignment_values(
        current_helpers,
        set(plan.transform.identity.geometry.created_indices),
        set(plan.transform.identity.constraints.created_indices),
    )
    if current_helpers != plan.helpers:
        raise NativeSketchError(f"{snapshot.label} final helper alignment is wrong.")
    return verified
