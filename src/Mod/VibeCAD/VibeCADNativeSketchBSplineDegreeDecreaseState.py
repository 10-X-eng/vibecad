# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state and semantic checks for one-step B-spline degree reduction."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineDegreeDecreaseProof import (
    ReducedCurveProof,
    maximum_sampled_deviation_mm,
    reduced_curve_proof,
)
from VibeCADNativeSketchBSplineDegreeDecreaseTarget import (
    LABEL,
    SketchBSplineDegreeDecreaseSpec,
)
from VibeCADNativeSketchBSplineHelperState import (
    HelperAlignment,
    alignment_values,
    decode_record,
    geometry_metadata,
    helper_position,
    indexed_records,
    record_without_index,
    references,
    remap_constraint,
    require_safe_existing_helpers,
    stable_alignment_values,
)
from VibeCADNativeSketchDiagnosticState import (
    ISSUE_FIELDS,
    require_healthy_external_records,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchMutationState import (
    geometry_records_without_tags,
    grouped_geometry_members,
)
from VibeCADNativeSketchTargets import preflight_active_sketch
from VibeCADNativeSketchTransformState import (
    SketchTransformPlan,
    SketchTransformSnapshot,
    frozen_transform_state,
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
        "input_geometry_index",
        "output_geometry_index",
        "old_degree",
        "new_degree",
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
class SketchBSplineDegreeDecreaseSnapshot:
    transform: SketchTransformSnapshot
    proof: ReducedCurveProof
    helpers: tuple[HelperAlignment, ...]


@dataclass(frozen=True, slots=True)
class SketchBSplineDegreeDecreasePlan:
    transform: SketchTransformPlan
    old_degree: int
    new_degree: int
    retained_internal_geometry_count: int
    deleted_internal_geometry_count: int
    exposed_internal_geometry_count: int
    maximum_deviation_mm: float
    proof: ReducedCurveProof
    helpers: tuple[HelperAlignment, ...]


def capture_bspline_degree_decrease_snapshot(
    context: NativeRuntimeContext,
    spec: SketchBSplineDegreeDecreaseSpec,
) -> SketchBSplineDegreeDecreaseSnapshot:
    if not isinstance(spec, SketchBSplineDegreeDecreaseSpec):
        raise TypeError("spec must be a SketchBSplineDegreeDecreaseSpec")
    target = preflight_active_sketch(context, spec.target)
    state = frozen_transform_state(
        target.sketch,
        spec.target.expected_geometry_count,
        spec.target.expected_constraint_count,
        label=LABEL,
    )
    if (
        len(state.external_reference_records) != spec.expected_external_reference_count
        or len(state.external_geometry_records) != spec.expected_external_geometry_count
    ):
        raise NativeSketchError(f"{LABEL} external state changed; read it and retry.")
    if any(state.solver_issues):
        raise NativeSketchError(f"{LABEL} requires a Sketch without solver issues.")
    require_healthy_external_records(state.external_geometry_records, label=LABEL)
    geometry = indexed_records(state.geometry_records, "geometry")
    record = geometry.get(spec.geometry_index)
    if (
        record is None
        or spec.geometry_index in grouped_geometry_members(target.sketch, label=LABEL)
        or record.get("kind") != "b_spline"
        or record.get("internal_type")
        or type(record.get("degree")) is not int
        or record["degree"] <= 1
    ):
        raise NativeSketchError(
            f"{LABEL} requires one ungrouped internal B-spline above degree one."
        )
    proof = reduced_curve_proof(tuple(target.sketch.Geometry)[spec.geometry_index])
    constraints = tuple(target.sketch.Constraints)
    helpers = alignment_values(
        constraints,
        geometry,
        state.geometry_tags,
        state.constraint_tags,
        spec.geometry_index,
    )
    if any(
        item.alignment_index
        >= (
            len(proof.control_positions)
            if item.internal_type == "BSplineControlPoint"
            else len(proof.knot_positions)
        )
        for item in helpers
    ):
        raise NativeSketchError(f"{LABEL} found out-of-range helper alignment.")
    require_safe_existing_helpers(state, helpers)
    transform = SketchTransformSnapshot(target, spec, state, LABEL)
    return SketchBSplineDegreeDecreaseSnapshot(transform, proof, helpers)


def _integer(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return value


def parse_bspline_degree_decrease_diagnostic(
    result: Any,
    snapshot: SketchBSplineDegreeDecreaseSnapshot,
) -> SketchBSplineDegreeDecreasePlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    transform_snapshot = snapshot.transform
    spec = transform_snapshot.spec
    root = spec.geometry_index
    if (
        result["input_geometry_index"] != root
        or result["output_geometry_index"] != root
    ):
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    old_degree = _integer(result["old_degree"], "old degree", 25)
    new_degree = _integer(result["new_degree"], "new degree", 24)
    retained_count = _integer(
        result["retained_internal_geometry_count"], "retained helper count", MAX_HELPERS
    )
    deleted_count = _integer(
        result["deleted_internal_geometry_count"], "deleted helper count", MAX_HELPERS
    )
    exposed_count = _integer(
        result["exposed_internal_geometry_count"], "exposed helper count", MAX_HELPERS
    )
    plan = parse_transform_diagnostic(result, transform_snapshot)
    if (
        plan.external_reference_records
        != transform_snapshot.state.external_reference_records
        or plan.external_geometry_records
        != transform_snapshot.state.external_geometry_records
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed external geometry.")
    before = indexed_records(
        geometry_records_without_tags(transform_snapshot.state.geometry_records),
        "geometry",
    )
    after = indexed_records(plan.geometry_records, "geometry")
    geometry_map = dict(plan.identity.geometry.old_to_new)
    deleted_geometry = set(plan.identity.geometry.deleted_indices)
    created_geometry = set(plan.identity.geometry.created_indices)
    created_constraints = set(plan.identity.constraints.created_indices)
    old_helpers = {item.geometry_index for item in snapshot.helpers}
    if (
        geometry_map.get(root) != root
        or deleted_geometry - old_helpers
        or set(range(len(before))) - deleted_geometry != set(geometry_map)
        or retained_count != len(old_helpers - deleted_geometry)
        or deleted_count != len(deleted_geometry)
        or exposed_count != len(created_geometry)
    ):
        raise NativeSketchError(
            f"{LABEL} changed the wrong durable geometry identities."
        )
    raw_after = result["geometry"]
    if not isinstance(raw_after, (list, tuple)) or len(raw_after) != len(after):
        raise NativeSketchError(
            f"{LABEL} feasibility returned invalid geometry objects."
        )
    old_record = before[root]
    new_record = after[root]
    if (
        old_degree != old_record.get("degree")
        or new_degree != old_degree - 1
        or new_record.get("degree") != new_degree
        or new_record.get("kind") != "b_spline"
        or geometry_metadata(new_record) != geometry_metadata(old_record)
        or any(
            new_record.get(field) != old_record.get(field)
            for field in ("first_parameter", "last_parameter", "periodic", "closed")
        )
    ):
        raise NativeSketchError(f"{LABEL} returned the wrong degree reduction.")
    proof = reduced_curve_proof(raw_after[root])
    deviation = round(maximum_sampled_deviation_mm(snapshot.proof, proof), 12)
    if deviation > spec.maximum_deviation_mm + 1.0e-9:
        raise NativeSketchError(
            f"{LABEL} would exceed maximum_deviation_mm ({deviation:.12g} mm)."
        )
    if len(proof.control_positions) + len(proof.knot_positions) > MAX_HELPERS:
        raise NativeSketchError(f"{LABEL} would create too much spline state.")
    for old_index, new_index in geometry_map.items():
        if old_index == root:
            continue
        if old_index in old_helpers:
            if geometry_metadata(before[old_index]) != geometry_metadata(
                after[new_index]
            ):
                raise NativeSketchError(f"{LABEL} changed retained helper metadata.")
        elif record_without_index(before[old_index]) != record_without_index(
            after[new_index]
        ):
            raise NativeSketchError(f"{LABEL} changed unrelated geometry.")
    for index in created_geometry:
        record = after[index]
        if (
            record.get("kind") not in {"circle", "point"}
            or record.get("construction") is not True
            or record.get("internal_type")
            not in {"BSplineControlPoint", "BSplineKnotPoint"}
        ):
            raise NativeSketchError(f"{LABEL} created invalid spline helpers.")
    geometry_tags = tuple(result["geometry_tags"])
    constraint_tags = tuple(result["constraint_tags"])
    constraints = tuple(result["constraints"])
    helpers = alignment_values(constraints, after, geometry_tags, constraint_tags, root)
    final_helper_indices = {
        geometry_map[index] for index in old_helpers - deleted_geometry
    } | created_geometry
    if {item.geometry_index for item in helpers} != final_helper_indices:
        raise NativeSketchError(f"{LABEL} exposed the wrong spline helper set.")
    control_indices = set()
    knot_indices = set()
    for helper in helpers:
        record = after[helper.geometry_index]
        if helper.internal_type == "BSplineControlPoint":
            if helper.alignment_index >= len(proof.control_positions):
                raise NativeSketchError(
                    f"{LABEL} returned out-of-range helper alignment."
                )
            expected = proof.control_positions[helper.alignment_index]
            control_indices.add(helper.alignment_index)
        else:
            if helper.alignment_index >= len(proof.knot_positions):
                raise NativeSketchError(
                    f"{LABEL} returned out-of-range helper alignment."
                )
            expected = proof.knot_positions[helper.alignment_index]
            knot_indices.add(helper.alignment_index)
        if math.dist(helper_position(record), expected) > 1.0e-8:
            raise NativeSketchError(
                f"{LABEL} helpers do not represent the reduced B-spline."
            )
    if control_indices != set(
        range(len(proof.control_positions))
    ) or knot_indices != set(range(len(proof.knot_positions))):
        raise NativeSketchError(f"{LABEL} did not align every reduced spline helper.")
    constraint_map = dict(plan.identity.constraints.old_to_new)
    deleted_constraints = set(plan.identity.constraints.deleted_indices)
    if len(created_constraints) > MAX_CREATED_CONSTRAINTS:
        raise NativeSketchError(f"{LABEL} created too many constraints.")
    old_constraint_records = {
        index: decode_record(encoded, "constraint")
        for index, encoded in enumerate(transform_snapshot.state.constraint_records)
    }
    old_helper_constraints = {
        index
        for index, record in old_constraint_records.items()
        if {geometry for geometry, _position in references(record).values()}
        & deleted_geometry
    }
    if deleted_constraints - old_helper_constraints:
        raise NativeSketchError(f"{LABEL} deleted unrelated constraints.")
    for old_index, new_index in constraint_map.items():
        expected = remap_constraint(
            old_constraint_records[old_index], new_index, geometry_map
        )
        actual = decode_record(plan.constraint_records[new_index], "constraint")
        if expected != actual:
            raise NativeSketchError(f"{LABEL} changed a surviving constraint.")
    for index in created_constraints:
        record = decode_record(plan.constraint_records[index], "constraint")
        referenced_geometry = {
            geometry for geometry, _position in references(record).values()
        }
        if record.get("type") == "InternalAlignment":
            if not referenced_geometry <= final_helper_indices | {root}:
                raise NativeSketchError(f"{LABEL} created wrong internal alignment.")
        elif (
            record.get("type") not in {"Weight", "Equal"}
            or not referenced_geometry <= final_helper_indices
        ):
            raise NativeSketchError(f"{LABEL} created unrelated constraints.")
    return SketchBSplineDegreeDecreasePlan(
        plan,
        old_degree,
        new_degree,
        retained_count,
        deleted_count,
        exposed_count,
        deviation,
        proof,
        stable_alignment_values(helpers, created_geometry, created_constraints),
    )


def _current_helpers(snapshot: SketchBSplineDegreeDecreaseSnapshot, sketch: Any):
    state = snapshot.transform.state
    geometry = indexed_records(state.geometry_records, "geometry")
    return alignment_values(
        tuple(sketch.Constraints),
        geometry,
        state.geometry_tags,
        state.constraint_tags,
        snapshot.transform.spec.geometry_index,
    )


def require_bspline_degree_decrease_snapshot_unchanged(
    document: Any,
    snapshot: SketchBSplineDegreeDecreaseSnapshot,
) -> Any:
    sketch = require_transform_snapshot_unchanged(document, snapshot.transform)
    if _current_helpers(snapshot, sketch) != snapshot.helpers:
        raise NativeSketchError(f"{LABEL} helper alignment changed; read it and retry.")
    return sketch


def require_pure_bspline_degree_decrease_diagnostic(
    snapshot: SketchBSplineDegreeDecreaseSnapshot,
) -> None:
    require_pure_transform_diagnostic(snapshot.transform)
    if _current_helpers(snapshot, snapshot.transform.target.sketch) != snapshot.helpers:
        raise NativeSketchError(f"{LABEL} diagnosis changed the live Sketch.")


def verify_bspline_degree_decrease_state(
    document: Any,
    snapshot: SketchBSplineDegreeDecreaseSnapshot,
    plan: SketchBSplineDegreeDecreasePlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_transform_state(document, snapshot.transform, plan.transform, receipt)
    proof = reduced_curve_proof(
        tuple(sketch.Geometry)[snapshot.transform.spec.geometry_index]
    )
    deviation = round(maximum_sampled_deviation_mm(snapshot.proof, proof), 12)
    if proof.digest != plan.proof.digest or deviation != plan.maximum_deviation_mm:
        raise NativeSketchError(f"{LABEL} final B-spline representation is wrong.")
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
        raise NativeSketchError(f"{LABEL} final helper alignment is wrong.")
    return (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    )
