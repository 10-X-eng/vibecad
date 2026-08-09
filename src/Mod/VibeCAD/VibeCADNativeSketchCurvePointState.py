# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact state proof for curve-at-point Sketch mutations."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import sketch_solver_issues
from VibeCADNativeSketchConstraintToggleState import (
    SketchExpressionRecord,
    sketch_expression_records,
)
from VibeCADNativeSketchCurvePointTarget import SketchCurvePointSpec
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchMutationState import (
    SketchMutationIdentityPlan,
    collection_identity_plan,
    collection_index_map,
    expected_expression_records,
    geometry_records_without_tags,
    grouped_geometry_members,
    normalized_constraint_records,
)
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)
from VibeCADNativeSketchTargets import (
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    require_prepared_active_sketch,
)


@dataclass(frozen=True, slots=True)
class SketchCurvePointSnapshot:
    target: PreparedActiveSketchTarget
    spec: SketchCurvePointSpec
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    solver_issues: tuple[tuple[int, ...], ...]


def _records(
    sketch: Any,
    spec: SketchCurvePointSpec,
    *,
    label: str,
) -> tuple[tuple[str, ...], ...]:
    geometry = canonical_sketch_records(
        iter_sketch_geometry_records(sketch, spec.target.expected_geometry_count)
    )
    constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch, spec.target.expected_constraint_count)
    )
    external = canonical_sketch_records(iter_sketch_external_geometry_records(sketch))
    expressions = sketch_expression_records(sketch, constraints, label=label)
    return geometry, constraints, external, expressions


def capture_curve_point_snapshot(
    context: NativeRuntimeContext,
    spec: SketchCurvePointSpec,
    *,
    label: str,
    human_curve_kinds: frozenset[str],
) -> SketchCurvePointSnapshot:
    if not isinstance(spec, SketchCurvePointSpec):
        raise TypeError("spec must be a SketchCurvePointSpec")
    target = preflight_active_sketch(context, spec.target)
    sketch = target.sketch
    geometry, constraints, external, expressions = _records(
        sketch,
        spec,
        label=label,
    )
    if len(external) != spec.expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; read it and retry."
        )
    index = spec.selection.geometry_index
    if index >= len(geometry):
        raise NativeSketchError(
            f"{label} target geometry is outside the active Sketch."
        )
    record = json.loads(geometry[index])
    if record.get("kind") not in human_curve_kinds or "internal_type" in record:
        raise NativeSketchError(
            f"{label} requires one curve accepted by the human {label.removeprefix('Sketch ')} command."
        )
    if index in grouped_geometry_members(sketch, label=label):
        raise NativeSketchError(
            f"{label} cannot infer a grouped member; target its group handle explicitly."
        )
    solver_before = sketch_solver_issues(sketch, label)
    if any(solver_before):
        raise NativeSketchError(f"{label} requires a Sketch without solver issues.")
    return SketchCurvePointSnapshot(
        target,
        spec,
        geometry,
        constraints,
        external,
        expressions,
        solver_before,
    )


def require_pure_curve_point_diagnostic(
    sketch: Any,
    snapshot: SketchCurvePointSnapshot,
    *,
    label: str,
) -> None:
    if (
        _records(sketch, snapshot.spec, label=label)
        != (
            snapshot.geometry_records,
            snapshot.constraint_records,
            snapshot.external_geometry_records,
            snapshot.expression_records,
        )
        or sketch_solver_issues(sketch, label) != snapshot.solver_issues
    ):
        raise NativeSketchError(f"{label} feasibility changed the active Sketch.")


def require_unchanged_curve_point(
    document: Any,
    snapshot: SketchCurvePointSnapshot,
    *,
    label: str,
) -> Any:
    sketch = require_prepared_active_sketch(document, snapshot.target)
    if (
        _records(sketch, snapshot.spec, label=label)
        != (
            snapshot.geometry_records,
            snapshot.constraint_records,
            snapshot.external_geometry_records,
            snapshot.expression_records,
        )
        or sketch_solver_issues(sketch, label) != snapshot.solver_issues
    ):
        raise NativeSketchError(f"The active Sketch changed after {label} preflight.")
    return sketch


def _actual_identity_plan(
    receipt: Any,
    snapshot: SketchCurvePointSnapshot,
    geometry_count: int,
    constraint_count: int,
    *,
    label: str,
) -> tuple[
    SketchMutationIdentityPlan,
    tuple[dict[int, str], dict[int, str]],
    dict[int, int],
]:
    geometry_map, deleted_geometry, created_geometry = collection_index_map(
        receipt,
        "geometry",
        len(snapshot.geometry_records),
        geometry_count,
        label=label,
    )
    constraint_map, deleted_constraints, created_constraints = collection_index_map(
        receipt,
        "constraints",
        len(snapshot.constraint_records),
        constraint_count,
        label=label,
    )
    return (
        SketchMutationIdentityPlan(
            collection_identity_plan(geometry_map, deleted_geometry, created_geometry),
            collection_identity_plan(
                constraint_map,
                deleted_constraints,
                created_constraints,
            ),
        ),
        (deleted_geometry, created_geometry),
        constraint_map,
    )


def _geometry_mismatch_detail(
    actual: tuple[str, ...],
    planned: tuple[str, ...],
) -> str:
    if len(actual) != len(planned):
        return "record count"
    for index, (actual_encoded, planned_encoded) in enumerate(
        zip(actual, planned, strict=True)
    ):
        actual_record = json.loads(actual_encoded)
        planned_record = json.loads(planned_encoded)
        actual_record.pop("tag", None)
        planned_record.pop("tag", None)
        differing = sorted(
            key
            for key in set(actual_record) | set(planned_record)
            if actual_record.get(key) != planned_record.get(key)
        )
        if differing:
            return f"index {index} fields {', '.join(differing[:4])}"
    return "canonical ordering"


def verify_curve_point_state(
    sketch: Any,
    snapshot: SketchCurvePointSnapshot,
    plan: Any,
    receipt: Any,
    *,
    label: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        geometry_count = int(sketch.GeometryCount)
        constraint_count = int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError(f"{label} final counts are unavailable.") from exc
    if geometry_count != len(plan.geometry_records) or constraint_count != len(
        plan.constraint_records
    ):
        raise NativeSketchError(f"{label} produced unexpected final counts.")

    actual_geometry = canonical_sketch_records(iter_sketch_geometry_records(sketch))
    actual_constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch)
    )
    actual_external = canonical_sketch_records(
        iter_sketch_external_geometry_records(sketch)
    )
    if geometry_records_without_tags(actual_geometry) != geometry_records_without_tags(
        plan.geometry_records
    ):
        detail = _geometry_mismatch_detail(actual_geometry, plan.geometry_records)
        raise NativeSketchError(f"{label} final geometry state is wrong ({detail}).")
    if normalized_constraint_records(
        actual_constraints
    ) != normalized_constraint_records(plan.constraint_records):
        raise NativeSketchError(f"{label} final constraint topology is wrong.")
    if actual_external != snapshot.external_geometry_records:
        raise NativeSketchError(f"{label} changed external geometry.")
    if any(sketch_solver_issues(sketch, label)):
        raise NativeSketchError(f"{label} final Sketch has solver issues.")

    identity, geometry_receipt, constraint_map = _actual_identity_plan(
        receipt,
        snapshot,
        geometry_count,
        constraint_count,
        label=label,
    )
    if identity != plan.identity:
        raise NativeSketchError(f"{label} returned an unexpected identity map.")
    deleted_geometry, created_geometry = geometry_receipt
    for old_index, new_index in identity.geometry.old_to_new:
        before_tag = str(
            json.loads(snapshot.geometry_records[old_index]).get("tag", "")
        )
        after_tag = str(json.loads(actual_geometry[new_index]).get("tag", ""))
        if not before_tag or after_tag != before_tag:
            raise NativeSketchError(f"{label} changed retained geometry identity.")
    for old_index, tag in deleted_geometry.items():
        if tag != str(json.loads(snapshot.geometry_records[old_index]).get("tag", "")):
            raise NativeSketchError(f"{label} reported the wrong deleted geometry.")
    prior_tags = {
        str(json.loads(record).get("tag", "")) for record in snapshot.geometry_records
    }
    for new_index, tag in created_geometry.items():
        if (
            tag != str(json.loads(actual_geometry[new_index]).get("tag", ""))
            or tag in prior_tags
        ):
            raise NativeSketchError(f"{label} reported the wrong created geometry.")

    expressions = sketch_expression_records(sketch, actual_constraints, label=label)
    if expressions != expected_expression_records(
        snapshot.expression_records,
        constraint_map,
    ):
        raise NativeSketchError(
            f"{label} changed expressions beyond exact constraint mapping."
        )
    return actual_geometry, actual_constraints
