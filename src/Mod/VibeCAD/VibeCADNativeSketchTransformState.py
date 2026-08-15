# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact state and postconditions for Native Sketch transformations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import sketch_solver_issues
from VibeCADNativeSketchConstraintToggleState import (
    SketchExpressionRecord,
    sketch_expression_records,
)
from VibeCADNativeSketchDiagnosticState import (
    diagnostic_external_geometry_records,
    diagnostic_external_reference_records,
    diagnostic_sketch_records,
    diagnostic_solver_degrees,
    require_healthy_external_records,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchExternalState import iter_external_reference_records
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


_VECTOR_FIELDS = frozenset({"x", "y"})
_EXPRESSION_FIELDS = frozenset({"constraint_index", "path", "expression"})


@dataclass(frozen=True, slots=True)
class FrozenSketchTransformState:
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_reference_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    geometry_tags: tuple[str, ...]
    constraint_tags: tuple[str, ...]
    solver_issues: tuple[tuple[int, ...], ...]
    configuration_token: str


@dataclass(frozen=True, slots=True)
class SketchTransformSnapshot:
    target: PreparedActiveSketchTarget
    spec: Any
    state: FrozenSketchTransformState
    label: str


@dataclass(frozen=True, slots=True)
class SketchTransformPlan:
    identity: SketchMutationIdentityPlan
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_reference_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    degrees_of_freedom: int


def _configuration_token(sketch: Any) -> str:
    placement = getattr(sketch, "Placement", None)
    rotation = getattr(placement, "Rotation", None)
    parent_method = getattr(sketch, "getParentGeoFeatureGroup", None)
    try:
        parent = parent_method() if callable(parent_method) else None
    except Exception:
        parent = None
    values = (
        tuple(
            float(getattr(getattr(placement, "Base", None), axis, 0.0))
            for axis in ("x", "y", "z")
        ),
        tuple(float(value) for value in (getattr(rotation, "Q", ()) or ())),
        str(getattr(sketch, "MapMode", "") or ""),
        str(getattr(parent, "Name", "") or ""),
        str(getattr(parent, "TypeId", "") or ""),
        float(getattr(getattr(sketch, "ArcFitTolerance", 0.0), "Value", 0.0)),
    )
    return hashlib.sha256(repr(values).encode()).hexdigest()


def _tag(value: Any, *, label: str, field: str) -> str:
    tag = str(getattr(value, "Tag", "") or "")
    if not tag or len(tag) > 128:
        raise NativeSketchError(f"{label} {field} identity is unavailable.")
    return tag


def frozen_transform_state(
    sketch: Any,
    geometry_count: int,
    constraint_count: int,
    *,
    label: str,
) -> FrozenSketchTransformState:
    try:
        actual = (int(sketch.GeometryCount), int(sketch.ConstraintCount))
        constraint_values = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{label} Sketch state is unavailable.") from exc
    if actual != (geometry_count, constraint_count):
        raise NativeSketchError(f"{label} Sketch counts changed; read them and retry.")
    geometry = canonical_sketch_records(
        iter_sketch_geometry_records(sketch, geometry_count)
    )
    constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch, constraint_count)
    )
    geometry_tags = tuple(
        str(json.loads(record).get("tag", "") or "") for record in geometry
    )
    if any(not value for value in geometry_tags):
        raise NativeSketchError(f"{label} geometry identity is unavailable.")
    return FrozenSketchTransformState(
        geometry,
        constraints,
        canonical_sketch_records(iter_external_reference_records(sketch)),
        canonical_sketch_records(iter_sketch_external_geometry_records(sketch)),
        sketch_expression_records(sketch, constraints, label=label),
        geometry_tags,
        tuple(
            _tag(value, label=label, field="constraint") for value in constraint_values
        ),
        sketch_solver_issues(sketch, label),
        _configuration_token(sketch),
    )


def _internal_alignment_pairs(
    state: FrozenSketchTransformState,
    *,
    label: str,
) -> tuple[tuple[int, int], ...]:
    pairs = []
    for encoded in state.constraint_records:
        record = json.loads(encoded)
        if record.get("type") != "InternalAlignment":
            continue
        references = record.get("references", [])
        if not isinstance(references, list) or len(references) < 2:
            raise NativeSketchError(f"{label} found malformed internal geometry.")
        by_slot = {
            item.get("slot"): item.get("geometry_index")
            for item in references
            if isinstance(item, Mapping)
        }
        first = by_slot.get(1)
        second = by_slot.get(2)
        if type(first) is not int or type(second) is not int:
            raise NativeSketchError(f"{label} found malformed internal geometry.")
        pairs.append((first, second))
    return tuple(pairs)


def _validate_targets(
    sketch: Any,
    spec: Any,
    state: FrozenSketchTransformState,
    *,
    label: str,
) -> None:
    selected = set(spec.geometry_indices)
    grouped = grouped_geometry_members(sketch, label=label)
    if any(index >= 0 and index in grouped for index in selected):
        raise NativeSketchError(
            f"{label} does not silently dismantle grouped or Text geometry."
        )
    for index in selected:
        if index >= 0:
            if index >= spec.target.expected_geometry_count:
                raise NativeSketchError(f"{label} internal geometry index is stale.")
            continue
        if index > -3:
            raise NativeSketchError(
                f"{label} cannot transform Sketch axes or the origin."
            )
        external_index = -index - 3
        if external_index >= len(state.external_geometry_records):
            raise NativeSketchError(f"{label} external geometry index is stale.")
    for internal, owner in _internal_alignment_pairs(state, label=label):
        if (internal in selected) != (owner in selected):
            raise NativeSketchError(
                f"{label} requires internal-alignment geometry and its owner together."
            )


def capture_transform_snapshot(
    context: NativeRuntimeContext,
    spec: Any,
    *,
    label: str,
) -> SketchTransformSnapshot:
    target = preflight_active_sketch(context, spec.target)
    state = frozen_transform_state(
        target.sketch,
        spec.target.expected_geometry_count,
        spec.target.expected_constraint_count,
        label=label,
    )
    if (
        len(state.external_reference_records) != spec.expected_external_reference_count
        or len(state.external_geometry_records) != spec.expected_external_geometry_count
    ):
        raise NativeSketchError(f"{label} external state changed; read it and retry.")
    if any(state.solver_issues):
        raise NativeSketchError(f"{label} requires a Sketch without solver issues.")
    require_healthy_external_records(state.external_geometry_records, label=label)
    _validate_targets(target.sketch, spec, state, label=label)
    return SketchTransformSnapshot(target, spec, state, label)


def _current_state(
    document: Any,
    snapshot: SketchTransformSnapshot,
) -> tuple[Any, FrozenSketchTransformState]:
    sketch = require_prepared_active_sketch(document, snapshot.target)
    state = frozen_transform_state(
        sketch,
        snapshot.spec.target.expected_geometry_count,
        snapshot.spec.target.expected_constraint_count,
        label=snapshot.label,
    )
    return sketch, state


def require_transform_snapshot_unchanged(
    document: Any,
    snapshot: SketchTransformSnapshot,
) -> Any:
    sketch, state = _current_state(document, snapshot)
    if state != snapshot.state:
        raise NativeSketchError(f"{snapshot.label} target changed after preflight.")
    return sketch


def require_pure_transform_diagnostic(snapshot: SketchTransformSnapshot) -> None:
    _sketch, state = _current_state(snapshot.target.context.document, snapshot)
    if state != snapshot.state:
        raise NativeSketchError(
            f"{snapshot.label} feasibility changed the live Sketch."
        )


def vector_matches(value: Any, expected: tuple[float, float]) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == _VECTOR_FIELDS
        and type(value["x"]) is float
        and type(value["y"]) is float
        and (value["x"], value["y"]) == expected
    )


def _diagnostic_tags(
    result: Mapping[str, Any],
    field: str,
    expected_count: int,
    *,
    label: str,
) -> tuple[str, ...]:
    values = result[field]
    if not isinstance(values, (list, tuple)) or len(values) != expected_count:
        raise NativeSketchError(f"{label} feasibility returned invalid {field}.")
    if any(
        not isinstance(value, str) or not value or len(value) > 128 for value in values
    ):
        raise NativeSketchError(f"{label} feasibility returned invalid {field}.")
    tags = tuple(values)
    if len(set(tags)) != len(tags):
        raise NativeSketchError(f"{label} feasibility returned duplicate {field}.")
    return tags


def _receipt_identity(
    receipt: Any,
    snapshot: SketchTransformSnapshot,
    geometry_count: int,
    constraint_count: int,
    geometry_tags: tuple[str, ...],
    constraint_tags: tuple[str, ...],
) -> SketchMutationIdentityPlan:
    label = snapshot.label
    geometry_map, deleted_geometry, created_geometry = collection_index_map(
        receipt,
        "geometry",
        snapshot.spec.target.expected_geometry_count,
        geometry_count,
        label=label,
    )
    constraint_map, deleted_constraints, created_constraints = collection_index_map(
        receipt,
        "constraints",
        snapshot.spec.target.expected_constraint_count,
        constraint_count,
        label=label,
    )
    for old, new in geometry_map.items():
        if geometry_tags[new] != snapshot.state.geometry_tags[old]:
            raise NativeSketchError(f"{label} feasibility replaced surviving geometry.")
    for old, tag in deleted_geometry.items():
        if tag != snapshot.state.geometry_tags[old]:
            raise NativeSketchError(f"{label} feasibility deleted the wrong geometry.")
    for new, tag in created_geometry.items():
        if geometry_tags[new] != tag:
            raise NativeSketchError(
                f"{label} feasibility returned wrong created geometry."
            )
    for old, new in constraint_map.items():
        if constraint_tags[new] != snapshot.state.constraint_tags[old]:
            raise NativeSketchError(
                f"{label} feasibility replaced a surviving constraint."
            )
    for old, tag in deleted_constraints.items():
        if tag != snapshot.state.constraint_tags[old]:
            raise NativeSketchError(
                f"{label} feasibility deleted the wrong constraint."
            )
    for new, tag in created_constraints.items():
        if constraint_tags[new] != tag:
            raise NativeSketchError(
                f"{label} feasibility returned wrong created constraint."
            )
    return SketchMutationIdentityPlan(
        collection_identity_plan(geometry_map, deleted_geometry, created_geometry),
        collection_identity_plan(
            constraint_map, deleted_constraints, created_constraints
        ),
    )


def _expression_digest(path: str, expression: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [path, expression], ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _diagnostic_expressions(
    result: Mapping[str, Any],
    snapshot: SketchTransformSnapshot,
    identity: SketchMutationIdentityPlan,
    final_constraint_count: int,
) -> tuple[SketchExpressionRecord, ...]:
    label = snapshot.label
    mapping = dict(identity.constraints.old_to_new)
    records = list(
        expected_expression_records(snapshot.state.expression_records, mapping)
    )
    used_paths = {record.path for record in records}
    created = set(identity.constraints.created_indices)
    values = result["expressions"]
    if not isinstance(values, (list, tuple)) or len(values) > final_constraint_count:
        raise NativeSketchError(f"{label} feasibility returned invalid expressions.")
    for value in values:
        if not isinstance(value, Mapping) or set(value) != _EXPRESSION_FIELDS:
            raise NativeSketchError(
                f"{label} feasibility returned an invalid expression."
            )
        index = value["constraint_index"]
        path = value["path"]
        expression = value["expression"]
        if (
            type(index) is not int
            or index not in created
            or not isinstance(path, str)
            or path != f"Constraints[{index}]"
            or path in used_paths
            or not isinstance(expression, str)
            or not expression
            or len(expression) > 65_536
        ):
            raise NativeSketchError(
                f"{label} feasibility returned an invalid expression target."
            )
        used_paths.add(path)
        records.append(
            SketchExpressionRecord(
                path, expression, index, _expression_digest(path, expression)
            )
        )
    records.sort(key=lambda item: (item.path, item.digest))
    return tuple(records)


def parse_transform_diagnostic(
    result: Mapping[str, Any],
    snapshot: SketchTransformSnapshot,
) -> SketchTransformPlan:
    label = snapshot.label
    degrees = diagnostic_solver_degrees(result, label=label)
    geometry, constraints = diagnostic_sketch_records(result, label=label)
    references = diagnostic_external_reference_records(
        result,
        snapshot.target.context.document,
        snapshot.target.spec.reference.document_uid,
        label=label,
    )
    external = diagnostic_external_geometry_records(result, label=label)
    geometry_tags = _diagnostic_tags(
        result, "geometry_tags", len(geometry), label=label
    )
    constraint_tags = _diagnostic_tags(
        result, "constraint_tags", len(constraints), label=label
    )
    identity = _receipt_identity(
        result["mutation_receipt"],
        snapshot,
        len(geometry),
        len(constraints),
        geometry_tags,
        constraint_tags,
    )
    expressions = _diagnostic_expressions(result, snapshot, identity, len(constraints))
    return SketchTransformPlan(
        identity,
        geometry_records_without_tags(geometry),
        constraints,
        references,
        external,
        expressions,
        degrees,
    )


def _normalized_final_constraints(
    records: tuple[str, ...],
    created_indices: tuple[int, ...],
) -> tuple[str, ...]:
    """Ignore only live-view label placement for newly created dimensions."""

    created = set(created_indices)
    without_new_label_placement = []
    for encoded in records:
        record = json.loads(encoded)
        if record.get("index") in created:
            record.pop("label_distance", None)
            record.pop("label_position", None)
        without_new_label_placement.append(record)
    return normalized_constraint_records(
        canonical_sketch_records(without_new_label_placement)
    )


def verify_transform_state(
    document: Any,
    snapshot: SketchTransformSnapshot,
    plan: SketchTransformPlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    label = snapshot.label
    sketch = require_prepared_active_sketch(document, snapshot.target)
    actual_geometry = canonical_sketch_records(iter_sketch_geometry_records(sketch))
    actual_constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch)
    )
    actual_references = canonical_sketch_records(
        iter_external_reference_records(sketch)
    )
    actual_external = canonical_sketch_records(
        iter_sketch_external_geometry_records(sketch)
    )
    if geometry_records_without_tags(actual_geometry) != plan.geometry_records:
        raise NativeSketchError(f"{label} final geometry is wrong.")
    created_constraints = plan.identity.constraints.created_indices
    if _normalized_final_constraints(
        actual_constraints,
        created_constraints,
    ) != _normalized_final_constraints(
        plan.constraint_records,
        created_constraints,
    ):
        raise NativeSketchError(f"{label} final constraints are wrong.")
    if (
        actual_references != plan.external_reference_records
        or actual_external != plan.external_geometry_records
    ):
        raise NativeSketchError(f"{label} final external geometry is wrong.")
    if (
        sketch_expression_records(sketch, actual_constraints, label=label)
        != plan.expression_records
    ):
        raise NativeSketchError(f"{label} final expressions are wrong.")
    if (
        sketch_solver_issues(sketch, label) != ((), (), (), ())
        or int(sketch.DoF) != plan.degrees_of_freedom
        or _configuration_token(sketch) != snapshot.state.configuration_token
    ):
        raise NativeSketchError(
            f"{label} final solver or configuration state is wrong."
        )
    actual_constraint_tags = tuple(
        _tag(value, label=label, field="constraint") for value in sketch.Constraints
    )
    actual_geometry_tags = tuple(
        str(json.loads(record).get("tag", "") or "") for record in actual_geometry
    )
    actual_identity = _receipt_identity(
        receipt,
        snapshot,
        len(actual_geometry),
        len(actual_constraints),
        actual_geometry_tags,
        actual_constraint_tags,
    )
    if actual_identity != plan.identity:
        raise NativeSketchError(f"{label} returned the wrong mutation receipt.")
    return (
        sketch,
        actual_identity.geometry.created_indices,
        actual_identity.geometry.deleted_indices,
        actual_identity.constraints.created_indices,
        actual_identity.constraints.deleted_indices,
    )
