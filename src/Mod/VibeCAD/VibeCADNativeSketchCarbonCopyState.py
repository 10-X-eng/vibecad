# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state and postconditions for Native Sketch Carbon Copy."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCarbonCopyTarget import SketchCarbonCopySpec
from VibeCADNativeSketchConstraintAppend import sketch_solver_issues
from VibeCADNativeSketchConstraintToggleState import (
    SketchExpressionRecord,
    sketch_expression_records,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchExternalState import iter_external_reference_records
from VibeCADNativeSketchMutationState import (
    SketchMutationIdentityPlan,
    collection_identity_plan,
    collection_index_map,
    geometry_records_without_tags,
    normalized_constraint_records,
)
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
    serialize_sketch_constraint_value,
    serialize_sketch_external_geometry_value,
    serialize_sketch_geometry_value,
)
from VibeCADNativeSketchTargets import (
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import NativeObjectRef, object_reference, resolve_object


LABEL = "Sketch Carbon Copy"
_ISSUE_FIELDS = (
    "conflicting_constraint_indices",
    "redundant_constraint_indices",
    "partially_redundant_constraint_indices",
    "malformed_constraint_indices",
)
_FIELDS = frozenset(
    {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        *_ISSUE_FIELDS,
        "source_object_name",
        "requested_construction",
        "requested_allow_other_body",
        "requested_allow_unaligned",
        "x_inverted",
        "y_inverted",
        "copied_geometry_count",
        "copied_constraint_count",
        "copied_external_reference_count",
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
        "expressions",
        "mutation_receipt",
    }
)
_EXTERNAL_METADATA_FIELDS = frozenset(
    {
        "reference",
        "defining",
        "frozen",
        "detached",
        "missing",
        "synchronized",
    }
)
_EXPRESSION_FIELDS = frozenset({"constraint_index", "path", "expression"})
_REFERENCE_FIELDS = frozenset({"object_name", "subelement", "type"})
_EXTERNAL_KINDS = {
    0: "projection",
    1: "intersection",
    2: "projection_and_intersection",
}


@dataclass(frozen=True, slots=True)
class FrozenSketchState:
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_reference_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    solver_issues: tuple[tuple[int, ...], ...]
    configuration_token: str


@dataclass(frozen=True, slots=True)
class SketchCarbonCopySnapshot:
    target: PreparedActiveSketchTarget
    spec: SketchCarbonCopySpec
    source: Any
    target_state: FrozenSketchState
    source_state: FrozenSketchState


@dataclass(frozen=True, slots=True)
class SketchCarbonCopyPlan:
    identity: SketchMutationIdentityPlan
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_reference_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    degrees_of_freedom: int
    x_inverted: bool
    y_inverted: bool


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return value


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 1_000_000:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return tuple(value)


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


def _state(
    sketch: Any, geometry_count: int, constraint_count: int
) -> FrozenSketchState:
    try:
        actual_counts = (int(sketch.GeometryCount), int(sketch.ConstraintCount))
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} Sketch counts are unavailable.") from exc
    if actual_counts != (geometry_count, constraint_count):
        raise NativeSketchError(f"{LABEL} Sketch counts changed; read them and retry.")
    geometry = canonical_sketch_records(
        iter_sketch_geometry_records(sketch, geometry_count)
    )
    constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch, constraint_count)
    )
    return FrozenSketchState(
        geometry,
        constraints,
        canonical_sketch_records(iter_external_reference_records(sketch)),
        canonical_sketch_records(iter_sketch_external_geometry_records(sketch)),
        sketch_expression_records(sketch, constraints, label=LABEL),
        sketch_solver_issues(sketch, LABEL),
        _configuration_token(sketch),
    )


def _validate_external_health(state: FrozenSketchState, owner: str) -> None:
    for encoded in state.external_geometry_records:
        record = json.loads(encoded)
        if any(
            bool(record.get(field, False))
            for field in ("detached", "missing", "synchronized")
        ):
            raise NativeSketchError(
                f"{LABEL} cannot copy {owner} with detached, missing, or synchronizing external geometry."
            )


def _resolve_source(document: Any, target: Any, spec: SketchCarbonCopySpec) -> Any:
    source = resolve_object(
        document,
        spec.source,
        expected_types=("Sketcher::SketchObject",),
    )
    if source is target:
        raise NativeSketchError(
            "Carbon Copy source and target Sketch must be different."
        )
    return source


def capture_carbon_copy_snapshot(
    context: NativeRuntimeContext,
    spec: SketchCarbonCopySpec,
) -> SketchCarbonCopySnapshot:
    if not isinstance(spec, SketchCarbonCopySpec):
        raise TypeError("spec must be a SketchCarbonCopySpec")
    target = preflight_active_sketch(context, spec.target)
    source = _resolve_source(context.document, target.sketch, spec)
    target_state = _state(
        target.sketch,
        spec.target.expected_geometry_count,
        spec.target.expected_constraint_count,
    )
    source_state = _state(
        source,
        spec.expected_source_geometry_count,
        spec.expected_source_constraint_count,
    )
    expected_counts = (
        spec.expected_external_reference_count,
        spec.expected_external_geometry_count,
        spec.expected_source_external_reference_count,
        spec.expected_source_external_geometry_count,
    )
    actual_counts = (
        len(target_state.external_reference_records),
        len(target_state.external_geometry_records),
        len(source_state.external_reference_records),
        len(source_state.external_geometry_records),
    )
    if actual_counts != expected_counts:
        raise NativeSketchError(
            "Carbon Copy target or source external state changed; read it and retry."
        )
    if any(target_state.solver_issues) or any(source_state.solver_issues):
        raise NativeSketchError(
            "Carbon Copy requires target and source Sketches without solver issues."
        )
    _validate_external_health(target_state, "a target")
    _validate_external_health(source_state, "a source")
    return SketchCarbonCopySnapshot(target, spec, source, target_state, source_state)


def _current_states(
    document: Any,
    snapshot: SketchCarbonCopySnapshot,
) -> tuple[Any, Any, FrozenSketchState, FrozenSketchState]:
    target = require_prepared_active_sketch(document, snapshot.target)
    source = _resolve_source(document, target, snapshot.spec)
    target_state = _state(
        target,
        snapshot.spec.target.expected_geometry_count,
        snapshot.spec.target.expected_constraint_count,
    )
    source_state = _state(
        source,
        snapshot.spec.expected_source_geometry_count,
        snapshot.spec.expected_source_constraint_count,
    )
    return target, source, target_state, source_state


def require_carbon_copy_snapshot_unchanged(
    document: Any,
    snapshot: SketchCarbonCopySnapshot,
) -> tuple[Any, Any]:
    target, source, target_state, source_state = _current_states(document, snapshot)
    if (
        source is not snapshot.source
        or target_state != snapshot.target_state
        or source_state != snapshot.source_state
    ):
        raise NativeSketchError("Carbon Copy target or source changed after preflight.")
    return target, source


def require_pure_carbon_copy_diagnostic(snapshot: SketchCarbonCopySnapshot) -> None:
    _target, _source, target_state, source_state = _current_states(
        snapshot.target.context.document,
        snapshot,
    )
    if target_state != snapshot.target_state or source_state != snapshot.source_state:
        raise NativeSketchError("Carbon Copy feasibility changed a live Sketch.")


def _solver_state(result: Mapping[str, Any]) -> int:
    if type(result["accepted"]) is not bool or not result["accepted"]:
        raise NativeSketchError(
            "Carbon Copy would introduce a solver issue; nothing changed."
        )
    degrees = _count(result["degrees_of_freedom"], "degrees of freedom")
    if type(result["solver_status"]) is not int or result["solver_status"] != 0:
        raise NativeSketchError(
            "Carbon Copy feasibility returned an invalid solver state."
        )
    for field in _ISSUE_FIELDS:
        values = _sequence(result[field], field)
        if any(type(value) is not int or value < 0 for value in values) or values:
            raise NativeSketchError(
                "Carbon Copy feasibility returned inconsistent solver issues."
            )
    return degrees


def _diagnostic_sketch_records(result: Mapping[str, Any]):
    geometry_count = _count(result["geometry_count"], "geometry count")
    constraint_count = _count(result["constraint_count"], "constraint count")
    geometry = _sequence(result["geometry"], "geometry")
    metadata = _sequence(result["geometry_metadata"], "geometry metadata")
    constraints = _sequence(result["constraints"], "constraints")
    if (
        len(geometry) != geometry_count
        or len(metadata) != geometry_count
        or len(constraints) != constraint_count
    ):
        raise NativeSketchError(
            "Carbon Copy feasibility returned inconsistent Sketch counts."
        )
    try:
        geometry_records = canonical_sketch_records(
            serialize_sketch_geometry_value(value, index, metadata[index])
            for index, value in enumerate(geometry)
        )
        constraint_records = canonical_sketch_records(
            serialize_sketch_constraint_value(value, index)
            for index, value in enumerate(constraints)
        )
    except Exception as exc:
        raise NativeSketchError(
            "Carbon Copy feasibility returned unreadable Sketch state."
        ) from exc
    return geometry_records, constraint_records


def _diagnostic_references(
    result: Mapping[str, Any],
    snapshot: SketchCarbonCopySnapshot,
) -> tuple[str, ...]:
    count = _count(result["external_reference_count"], "external reference count")
    values = _sequence(result["external_references"], "external references")
    expected = (
        snapshot.target_state.external_reference_records
        + snapshot.source_state.external_reference_records
    )
    if count != len(values) or count != len(expected):
        raise NativeSketchError(
            "Carbon Copy feasibility returned the wrong external links."
        )
    records = []
    for index, (value, expected_encoded) in enumerate(
        zip(values, expected, strict=True)
    ):
        if not isinstance(value, Mapping) or set(value) != _REFERENCE_FIELDS:
            raise NativeSketchError(
                "Carbon Copy feasibility returned an invalid external link."
            )
        expected_record = json.loads(expected_encoded)
        expected_object = expected_record.get("object", {})
        kind = (
            _EXTERNAL_KINDS.get(value["type"]) if type(value["type"]) is int else None
        )
        if (
            value["object_name"] != expected_object.get("object_name")
            or value["subelement"] != expected_record.get("subelement", "")
            or kind != expected_record.get("kind")
        ):
            raise NativeSketchError(
                "Carbon Copy feasibility changed an external source link."
            )
        obj = resolve_object(
            snapshot.target.context.document,
            NativeObjectRef(snapshot.spec.source.document_uid, value["object_name"]),
        )
        records.append(
            {
                "reference_index": index,
                "object": object_reference(obj),
                "subelement": value["subelement"],
                "kind": kind,
            }
        )
    return canonical_sketch_records(records)


def _diagnostic_external_geometry(result: Mapping[str, Any]) -> tuple[str, ...]:
    count = _count(result["external_geometry_count"], "external geometry count")
    geometry = _sequence(result["external_geometry"], "external geometry")
    metadata = _sequence(
        result["external_geometry_metadata"], "external geometry metadata"
    )
    if len(geometry) != count or len(metadata) != count:
        raise NativeSketchError(
            "Carbon Copy feasibility returned inconsistent external geometry."
        )
    records = []
    for index, (value, item) in enumerate(zip(geometry, metadata, strict=True)):
        if not isinstance(item, Mapping) or set(item) != _EXTERNAL_METADATA_FIELDS:
            raise NativeSketchError(
                "Carbon Copy feasibility returned invalid external metadata."
            )
        if any(bool(item[field]) for field in ("detached", "missing", "synchronized")):
            raise NativeSketchError(
                "Carbon Copy feasibility returned unhealthy external geometry."
            )
        try:
            records.append(
                serialize_sketch_external_geometry_value(value, -3 - index, item)
            )
        except Exception as exc:
            raise NativeSketchError(
                "Carbon Copy feasibility returned unreadable external geometry."
            ) from exc
    return canonical_sketch_records(records)


def _expression_digest(path: str, expression: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [path, expression], ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _diagnostic_expressions(
    result: Mapping[str, Any],
    snapshot: SketchCarbonCopySnapshot,
    final_constraint_count: int,
) -> tuple[SketchExpressionRecord, ...]:
    values = _sequence(result["expressions"], "expressions")
    records = list(snapshot.target_state.expression_records)
    used_indices = set()
    for value in values:
        if not isinstance(value, Mapping) or set(value) != _EXPRESSION_FIELDS:
            raise NativeSketchError(
                "Carbon Copy feasibility returned an invalid expression."
            )
        index = value["constraint_index"]
        path = value["path"]
        expression = value["expression"]
        if (
            type(index) is not int
            or not snapshot.spec.target.expected_constraint_count
            <= index
            < final_constraint_count
            or index in used_indices
            or not isinstance(path, str)
            or path != f"Constraints[{index}]"
            or len(path) > 1_024
            or not isinstance(expression, str)
            or not expression
            or len(expression) > 65_536
        ):
            raise NativeSketchError(
                "Carbon Copy feasibility returned an invalid expression target."
            )
        used_indices.add(index)
        records.append(
            SketchExpressionRecord(
                path, expression, index, _expression_digest(path, expression)
            )
        )
    records.sort(key=lambda item: (item.path, item.digest))
    return tuple(records)


def parse_carbon_copy_diagnostic(
    result: Any,
    snapshot: SketchCarbonCopySnapshot,
) -> SketchCarbonCopyPlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(
            "Carbon Copy feasibility returned incomplete diagnostics."
        )
    spec = snapshot.spec
    if (
        result["source_object_name"] != snapshot.source.Name
        or result["requested_construction"] is not spec.construction
        or result["requested_allow_other_body"] is not spec.allow_other_body
        or result["requested_allow_unaligned"] is not spec.allow_unaligned
        or type(result["x_inverted"]) is not bool
        or type(result["y_inverted"]) is not bool
        or result["copied_geometry_count"] != spec.expected_source_geometry_count
        or result["copied_constraint_count"] != spec.expected_source_constraint_count
        or result["copied_external_reference_count"]
        != spec.expected_source_external_reference_count
    ):
        raise NativeSketchError(
            "Carbon Copy feasibility analyzed a different operation."
        )
    degrees = _solver_state(result)
    geometry, constraints = _diagnostic_sketch_records(result)
    expected_geometry_count = (
        spec.target.expected_geometry_count + spec.expected_source_geometry_count
    )
    expected_constraint_count = (
        spec.target.expected_constraint_count + spec.expected_source_constraint_count
    )
    if (
        len(geometry) != expected_geometry_count
        or len(constraints) != expected_constraint_count
    ):
        raise NativeSketchError(
            "Carbon Copy feasibility returned unexpected copied counts."
        )
    if (
        geometry[: spec.target.expected_geometry_count]
        != snapshot.target_state.geometry_records
    ):
        raise NativeSketchError(
            "Carbon Copy feasibility changed existing target geometry."
        )
    if normalized_constraint_records(
        constraints[: spec.target.expected_constraint_count]
    ) != normalized_constraint_records(snapshot.target_state.constraint_records):
        raise NativeSketchError(
            "Carbon Copy feasibility changed existing target constraints."
        )
    # New geometry identities are allocated independently by each detached diagnosis and by the
    # eventual live commit. Existing target identity is guarded above and the exact created-index
    # contract is guarded by the mutation receipt, so generated tags are deliberately excluded
    # from the repeatable geometry plan.
    geometry = geometry_records_without_tags(geometry)
    references = _diagnostic_references(result, snapshot)
    external_geometry = _diagnostic_external_geometry(result)
    expressions = _diagnostic_expressions(result, snapshot, len(constraints))
    geometry_map, deleted_geometry, created_geometry = collection_index_map(
        result["mutation_receipt"],
        "geometry",
        spec.target.expected_geometry_count,
        len(geometry),
        label=LABEL,
    )
    constraint_map, deleted_constraints, created_constraints = collection_index_map(
        result["mutation_receipt"],
        "constraints",
        spec.target.expected_constraint_count,
        len(constraints),
        label=LABEL,
    )
    expected_geometry_map = {
        index: index for index in range(spec.target.expected_geometry_count)
    }
    expected_constraint_map = {
        index: index for index in range(spec.target.expected_constraint_count)
    }
    if (
        geometry_map != expected_geometry_map
        or constraint_map != expected_constraint_map
        or deleted_geometry
        or deleted_constraints
        or set(created_geometry)
        != set(range(spec.target.expected_geometry_count, len(geometry)))
        or set(created_constraints)
        != set(range(spec.target.expected_constraint_count, len(constraints)))
    ):
        raise NativeSketchError(
            "Carbon Copy feasibility returned the wrong identity mapping."
        )
    return SketchCarbonCopyPlan(
        SketchMutationIdentityPlan(
            collection_identity_plan(geometry_map, deleted_geometry, created_geometry),
            collection_identity_plan(
                constraint_map, deleted_constraints, created_constraints
            ),
        ),
        geometry,
        constraints,
        references,
        external_geometry,
        expressions,
        degrees,
        result["x_inverted"],
        result["y_inverted"],
    )


def verify_carbon_copy_state(
    document: Any,
    snapshot: SketchCarbonCopySnapshot,
    plan: SketchCarbonCopyPlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...]]:
    target = require_prepared_active_sketch(document, snapshot.target)
    source = _resolve_source(document, target, snapshot.spec)
    source_state = _state(
        source,
        snapshot.spec.expected_source_geometry_count,
        snapshot.spec.expected_source_constraint_count,
    )
    if source is not snapshot.source or source_state != snapshot.source_state:
        raise NativeSketchError("Carbon Copy changed its source Sketch.")
    actual_geometry = canonical_sketch_records(iter_sketch_geometry_records(target))
    actual_constraints = canonical_sketch_records(
        iter_sketch_constraint_records(target)
    )
    actual_references = canonical_sketch_records(
        iter_external_reference_records(target)
    )
    actual_external = canonical_sketch_records(
        iter_sketch_external_geometry_records(target)
    )
    if geometry_records_without_tags(actual_geometry) != geometry_records_without_tags(
        plan.geometry_records
    ):
        raise NativeSketchError("Carbon Copy final geometry is wrong.")
    if normalized_constraint_records(
        actual_constraints
    ) != normalized_constraint_records(plan.constraint_records):
        raise NativeSketchError("Carbon Copy final constraints are wrong.")
    if (
        actual_references != plan.external_reference_records
        or actual_external != plan.external_geometry_records
    ):
        raise NativeSketchError("Carbon Copy final external geometry is wrong.")
    actual_expressions = sketch_expression_records(
        target, actual_constraints, label=LABEL
    )
    if actual_expressions != plan.expression_records:
        raise NativeSketchError("Carbon Copy final expressions are wrong.")
    if (
        sketch_solver_issues(target, LABEL) != ((), (), (), ())
        or int(target.DoF) != plan.degrees_of_freedom
    ):
        raise NativeSketchError("Carbon Copy final solver state is wrong.")
    geometry_map, deleted_geometry, created_geometry = collection_index_map(
        receipt,
        "geometry",
        snapshot.spec.target.expected_geometry_count,
        len(actual_geometry),
        label=LABEL,
    )
    constraint_map, deleted_constraints, created_constraints = collection_index_map(
        receipt,
        "constraints",
        snapshot.spec.target.expected_constraint_count,
        len(actual_constraints),
        label=LABEL,
    )
    actual_identity = SketchMutationIdentityPlan(
        collection_identity_plan(geometry_map, deleted_geometry, created_geometry),
        collection_identity_plan(
            constraint_map, deleted_constraints, created_constraints
        ),
    )
    if actual_identity != plan.identity:
        raise NativeSketchError("Carbon Copy returned the wrong mutation receipt.")
    for index, before in enumerate(snapshot.target_state.geometry_records):
        if json.loads(actual_geometry[index]).get("tag") != json.loads(before).get(
            "tag"
        ):
            raise NativeSketchError(
                "Carbon Copy changed an existing geometry identity."
            )
    for index, tag in created_geometry.items():
        if json.loads(actual_geometry[index]).get("tag") != tag:
            raise NativeSketchError(
                "Carbon Copy returned the wrong created geometry identity."
            )
    return target, tuple(sorted(created_geometry)), tuple(sorted(created_constraints))
