# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact frozen-state proof for one B-spline knot insertion."""

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
from VibeCADNativeSketchBSplineKnotInsertionTarget import (
    LABEL,
    SketchBSplineKnotInsertionSpec,
)
from VibeCADNativeSketchBSplineKnotMultiplicityProof import (
    MAX_SHAPE_DEVIATION_MM,
    KnotMultiplicityCurveProof,
    knot_multiplicity_curve_proof,
    maximum_sampled_displacement_mm,
)
from VibeCADNativeSketchBSplineKnotState import capture_bspline_knot_snapshot
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
PARAMETER_TOLERANCE = 1.0e-7
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
        "requested_parameter",
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
class SketchBSplineKnotInsertionSnapshot:
    transform: SketchTransformSnapshot
    proof: KnotMultiplicityCurveProof
    helpers: tuple[HelperAlignment, ...]


@dataclass(frozen=True, slots=True)
class SketchBSplineKnotInsertionPlan:
    transform: SketchTransformPlan
    requested_parameter: float
    knot_index: int
    knot_parameter: float
    degree: int
    old_multiplicity: int
    new_multiplicity: int
    retained_internal_geometry_count: int
    deleted_internal_geometry_count: int
    exposed_internal_geometry_count: int
    maximum_displacement_mm: float
    proof: KnotMultiplicityCurveProof
    helpers: tuple[HelperAlignment, ...]


def capture_bspline_knot_insertion_snapshot(
    context: NativeRuntimeContext,
    spec: SketchBSplineKnotInsertionSpec,
) -> SketchBSplineKnotInsertionSnapshot:
    if not isinstance(spec, SketchBSplineKnotInsertionSpec):
        raise TypeError("spec must be a SketchBSplineKnotInsertionSpec")
    base = capture_bspline_knot_snapshot(context, spec, label=LABEL)
    if not base.proof.first_parameter <= spec.parameter <= base.proof.last_parameter:
        raise NativeSketchError(f"{LABEL} parameter is outside the B-spline domain.")
    return SketchBSplineKnotInsertionSnapshot(base.transform, base.proof, base.helpers)


def _integer(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return value


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeSketchError(
            f"{LABEL} feasibility returned invalid {field}."
        ) from exc
    if not math.isfinite(result):
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return round(result, 12)


def _expected_representation(
    snapshot: SketchBSplineKnotInsertionSnapshot,
    knot_index: int,
    knot_parameter: float,
) -> tuple[tuple[float, ...], tuple[int, ...], int]:
    matching = tuple(
        index
        for index, value in enumerate(snapshot.proof.knots)
        if abs(value - knot_parameter) <= PARAMETER_TOLERANCE
    )
    if len(matching) > 1:
        raise NativeSketchError(f"{LABEL} found an ambiguous existing knot.")
    knots = list(snapshot.proof.knots)
    multiplicities = list(snapshot.proof.multiplicities)
    if matching:
        old_index = matching[0]
        if knot_index != old_index:
            raise NativeSketchError(
                f"{LABEL} feasibility returned the wrong knot index."
            )
        old_multiplicity = multiplicities[old_index]
        multiplicities[old_index] += 1
    else:
        if knot_index > len(knots):
            raise NativeSketchError(
                f"{LABEL} feasibility returned the wrong knot index."
            )
        knots.insert(knot_index, knot_parameter)
        multiplicities.insert(knot_index, 1)
        if knots != sorted(knots):
            raise NativeSketchError(
                f"{LABEL} feasibility returned the wrong knot order."
            )
        old_multiplicity = 0
    return tuple(knots), tuple(multiplicities), old_multiplicity


def parse_bspline_knot_insertion_diagnostic(
    result: Any,
    snapshot: SketchBSplineKnotInsertionSnapshot,
) -> SketchBSplineKnotInsertionPlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    spec = snapshot.transform.spec
    if result["geometry_index"] != spec.geometry_index:
        raise NativeSketchError(f"{LABEL} feasibility analyzed different geometry.")
    requested_parameter = _number(result["requested_parameter"], "requested parameter")
    knot_index = _integer(result["knot_index"], "knot index", MAX_HELPERS)
    knot_parameter = _number(result["knot_parameter"], "knot parameter")
    degree = _integer(result["degree"], "degree", 25)
    old_multiplicity = _integer(result["old_multiplicity"], "old multiplicity", 25)
    new_multiplicity = _integer(result["new_multiplicity"], "new multiplicity", 25)
    retained_count = _integer(
        result["retained_internal_geometry_count"], "retained helper count", MAX_HELPERS
    )
    deleted_count = _integer(
        result["deleted_internal_geometry_count"], "deleted helper count", MAX_HELPERS
    )
    exposed_count = _integer(
        result["exposed_internal_geometry_count"], "exposed helper count", MAX_HELPERS
    )
    if (
        abs(requested_parameter - spec.parameter) > PARAMETER_TOLERANCE
        or abs(knot_parameter - spec.parameter) > PARAMETER_TOLERANCE
    ):
        raise NativeSketchError(f"{LABEL} feasibility inserted a different parameter.")
    expected_knots, expected_multiplicities, expected_old = _expected_representation(
        snapshot, knot_index, knot_parameter
    )
    plan = parse_transform_diagnostic(result, snapshot.transform)
    if (
        plan.external_reference_records
        != snapshot.transform.state.external_reference_records
        or plan.external_geometry_records
        != snapshot.transform.state.external_geometry_records
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed external geometry.")
    before = indexed_records(
        geometry_records_without_tags(snapshot.transform.state.geometry_records),
        "geometry",
    )
    after = indexed_records(plan.geometry_records, "geometry")
    raw_after = result["geometry"]
    root = spec.geometry_index
    if (
        not isinstance(raw_after, (list, tuple))
        or len(raw_after) != len(after)
        or root not in before
        or root not in after
    ):
        raise NativeSketchError(f"{LABEL} feasibility replaced the root geometry.")
    proof = knot_multiplicity_curve_proof(raw_after[root], label=LABEL)
    if (
        degree != snapshot.proof.degree
        or old_multiplicity != expected_old
        or new_multiplicity != expected_old + 1
        or proof.degree != snapshot.proof.degree
        or proof.knots != expected_knots
        or proof.multiplicities != expected_multiplicities
        or proof.first_parameter != snapshot.proof.first_parameter
        or proof.last_parameter != snapshot.proof.last_parameter
        or proof.rational != snapshot.proof.rational
        or proof.periodic != snapshot.proof.periodic
        or proof.closed != snapshot.proof.closed
        or len(proof.control_positions) != len(snapshot.proof.control_positions) + 1
        or after[root].get("kind") != "b_spline"
        or geometry_metadata(after[root]) != geometry_metadata(before[root])
    ):
        raise NativeSketchError(f"{LABEL} returned the wrong spline representation.")
    displacement = round(
        maximum_sampled_displacement_mm(snapshot.proof, proof, label=LABEL), 12
    )
    if displacement > MAX_SHAPE_DEVIATION_MM:
        raise NativeSketchError(
            f"{LABEL} would move the curve by {displacement:.12g} mm."
        )
    if len(proof.control_positions) + len(proof.knot_positions) > MAX_HELPERS:
        raise NativeSketchError(f"{LABEL} would create too much spline state.")
    helpers = verify_helper_reconciliation(
        label=LABEL,
        root=root,
        before=before,
        after=after,
        old_helpers=snapshot.helpers,
        plan=plan,
        before_constraint_records=snapshot.transform.state.constraint_records,
        result=result,
        control_positions=proof.control_positions,
        knot_positions=proof.knot_positions,
        retained_count=retained_count,
        deleted_count=deleted_count,
        exposed_count=exposed_count,
        maximum_created_constraints=MAX_CREATED_CONSTRAINTS,
    )
    return SketchBSplineKnotInsertionPlan(
        plan,
        requested_parameter,
        knot_index,
        knot_parameter,
        degree,
        old_multiplicity,
        new_multiplicity,
        retained_count,
        deleted_count,
        exposed_count,
        displacement,
        proof,
        helpers,
    )


def _current_helpers(snapshot: SketchBSplineKnotInsertionSnapshot, sketch: Any):
    state = snapshot.transform.state
    return alignment_values(
        tuple(sketch.Constraints),
        indexed_records(state.geometry_records, "geometry"),
        state.geometry_tags,
        state.constraint_tags,
        snapshot.transform.spec.geometry_index,
    )


def require_bspline_knot_insertion_snapshot_unchanged(
    document: Any,
    snapshot: SketchBSplineKnotInsertionSnapshot,
) -> Any:
    sketch = require_transform_snapshot_unchanged(document, snapshot.transform)
    if _current_helpers(snapshot, sketch) != snapshot.helpers:
        raise NativeSketchError(f"{LABEL} helper alignment changed; read it and retry.")
    return sketch


def require_pure_bspline_knot_insertion_diagnostic(
    snapshot: SketchBSplineKnotInsertionSnapshot,
) -> None:
    require_pure_transform_diagnostic(snapshot.transform)
    if _current_helpers(snapshot, snapshot.transform.target.sketch) != snapshot.helpers:
        raise NativeSketchError(f"{LABEL} diagnosis changed the live Sketch.")


def verify_bspline_knot_insertion_state(
    document: Any,
    snapshot: SketchBSplineKnotInsertionSnapshot,
    plan: SketchBSplineKnotInsertionPlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    verified = verify_transform_state(
        document, snapshot.transform, plan.transform, receipt
    )
    sketch = verified[0]
    proof = knot_multiplicity_curve_proof(
        tuple(sketch.Geometry)[snapshot.transform.spec.geometry_index], label=LABEL
    )
    displacement = round(
        maximum_sampled_displacement_mm(snapshot.proof, proof, label=LABEL), 12
    )
    if (
        proof.digest != plan.proof.digest
        or displacement != plan.maximum_displacement_mm
    ):
        raise NativeSketchError(f"{LABEL} final B-spline representation is wrong.")
    geometry = indexed_records(plan.transform.geometry_records, "geometry")
    helpers = alignment_values(
        tuple(sketch.Constraints),
        geometry,
        tuple(str(item.Tag) for item in sketch.GeometryFacadeList),
        tuple(str(item.Tag) for item in sketch.Constraints),
        snapshot.transform.spec.geometry_index,
    )
    helpers = stable_alignment_values(
        helpers,
        set(plan.transform.identity.geometry.created_indices),
        set(plan.transform.identity.constraints.created_indices),
    )
    if helpers != plan.helpers:
        raise NativeSketchError(f"{LABEL} final helper alignment is wrong.")
    return verified
