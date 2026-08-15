# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preflight and postcondition state shared by Sketch curve edits."""

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
from VibeCADNativeSketchCurveEditTarget import (
    SketchCurveEditCorner,
    SketchCurveEditSpec,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchMutationState import (
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


_BOUNDED_CURVE_KINDS = frozenset(
    {
        "line",
        "circular_arc",
        "elliptical_arc",
        "hyperbolic_arc",
        "parabolic_arc",
        "b_spline",
        "bezier",
    }
)


@dataclass(frozen=True, slots=True)
class SketchCurveEditSnapshot:
    target: PreparedActiveSketchTarget
    spec: SketchCurveEditSpec
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    solver_issues: tuple[tuple[int, ...], ...]
    requested_geometry_indices: tuple[int, ...]


def _records(
    sketch: Any,
    spec: SketchCurveEditSpec,
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


def _validate_targets(
    sketch: Any,
    spec: SketchCurveEditSpec,
    geometry_records: tuple[str, ...],
    *,
    label: str,
) -> tuple[int, ...]:
    by_index = {
        int(record["index"]): record
        for value in geometry_records
        for record in (json.loads(value),)
    }
    if isinstance(spec.selection, SketchCurveEditCorner):
        indices = (spec.selection.geometry_index,)
        record = by_index.get(indices[0])
        if record is None or record.get("kind") != "line":
            raise NativeSketchError(f"A corner {label} target must be a line endpoint.")
        try:
            sketch.getPoint(indices[0], spec.selection.position_code)
        except Exception as exc:
            raise NativeSketchError(
                f"The exact corner {label} endpoint is unavailable."
            ) from exc
    else:
        indices = tuple(curve.geometry_index for curve in spec.selection.curves)
        if any(
            (record := by_index.get(index)) is None
            or record.get("kind") not in _BOUNDED_CURVE_KINDS
            for index in indices
        ):
            raise NativeSketchError(
                f"A curve-pair {label} target requires two internal bounded curves."
            )
    grouped = grouped_geometry_members(sketch, label=label)
    if any(index in grouped for index in indices):
        raise NativeSketchError(
            f"{label} cannot infer a grouped member; target its group handle explicitly."
        )
    return indices


def capture_curve_edit_snapshot(
    context: NativeRuntimeContext,
    spec: SketchCurveEditSpec,
    *,
    label: str,
) -> SketchCurveEditSnapshot:
    if not isinstance(spec, SketchCurveEditSpec):
        raise TypeError("spec must be a SketchCurveEditSpec")
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
    requested = _validate_targets(sketch, spec, geometry, label=label)
    solver_before = sketch_solver_issues(sketch, label)
    if any(solver_before):
        raise NativeSketchError(f"{label} requires a Sketch without solver issues.")
    return SketchCurveEditSnapshot(
        target,
        spec,
        geometry,
        constraints,
        external,
        expressions,
        solver_before,
        requested,
    )


def require_pure_curve_edit_diagnostic(
    sketch: Any,
    snapshot: SketchCurveEditSnapshot,
    *,
    label: str,
) -> None:
    current = _records(sketch, snapshot.spec, label=label)
    if (
        current
        != (
            snapshot.geometry_records,
            snapshot.constraint_records,
            snapshot.external_geometry_records,
            snapshot.expression_records,
        )
        or sketch_solver_issues(sketch, label) != snapshot.solver_issues
    ):
        raise NativeSketchError(f"{label} feasibility changed the active Sketch.")


def require_unchanged_curve_edit(
    document: Any,
    snapshot: SketchCurveEditSnapshot,
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


def verify_curve_edit_state(
    sketch: Any,
    snapshot: SketchCurveEditSnapshot,
    *,
    planned_geometry_records: tuple[str, ...],
    planned_constraint_records: tuple[str, ...],
    receipt: Any,
    label: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        geometry_count = int(sketch.GeometryCount)
        constraint_count = int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError(f"{label} final counts are unavailable.") from exc
    if geometry_count != len(planned_geometry_records) or constraint_count != len(
        planned_constraint_records
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
        planned_geometry_records
    ):
        raise NativeSketchError(
            f"{label} final geometry topology or metadata is wrong."
        )
    for index, before in enumerate(snapshot.geometry_records):
        before_tag = str(json.loads(before).get("tag", ""))
        after_tag = str(json.loads(actual_geometry[index]).get("tag", ""))
        if not before_tag or after_tag != before_tag:
            raise NativeSketchError(
                f"{label} changed durable geometry identity at index {index}."
            )
    if normalized_constraint_records(
        actual_constraints
    ) != normalized_constraint_records(planned_constraint_records):
        raise NativeSketchError(f"{label} final constraint topology is wrong.")
    if actual_external != snapshot.external_geometry_records:
        raise NativeSketchError(f"{label} changed external geometry.")
    if any(sketch_solver_issues(sketch, label)):
        raise NativeSketchError(f"{label} final Sketch has solver issues.")

    geometry_mapping, deleted_geometry, created_geometry = collection_index_map(
        receipt,
        "geometry",
        len(snapshot.geometry_records),
        len(actual_geometry),
        label=label,
    )
    expected_mapping = {index: index for index in range(len(snapshot.geometry_records))}
    expected_created = set(range(len(snapshot.geometry_records), len(actual_geometry)))
    if (
        geometry_mapping != expected_mapping
        or deleted_geometry
        or set(created_geometry) != expected_created
    ):
        raise NativeSketchError(
            f"{label} returned an unexpected geometry identity map."
        )
    for index, tag in created_geometry.items():
        if str(json.loads(actual_geometry[index]).get("tag", "")) != tag:
            raise NativeSketchError(
                f"{label} returned the wrong created geometry identity."
            )

    constraint_mapping, _deleted_constraints, _created_constraints = (
        collection_index_map(
            receipt,
            "constraints",
            len(snapshot.constraint_records),
            len(actual_constraints),
            label=label,
        )
    )
    expressions = sketch_expression_records(sketch, actual_constraints, label=label)
    if expressions != expected_expression_records(
        snapshot.expression_records, constraint_mapping
    ):
        raise NativeSketchError(
            f"{label} changed expressions beyond exact constraint mapping."
        )

    new_tags = [
        str(json.loads(record).get("tag", ""))
        for record in actual_geometry[len(snapshot.geometry_records) :]
    ]
    prior_tags = {
        str(json.loads(record).get("tag", "")) for record in snapshot.geometry_records
    }
    if any(not tag or tag in prior_tags for tag in new_tags) or len(
        set(new_tags)
    ) != len(new_tags):
        raise NativeSketchError(
            f"{label} produced invalid durable geometry identities."
        )
    return actual_geometry, actual_constraints
