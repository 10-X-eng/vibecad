# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state and semantic checks for one-step B-spline degree elevation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineDegreeProof import (
    FrozenCurveProof,
    curve_proof,
    same_shape_samples,
)
from VibeCADNativeSketchBSplineDegreeTarget import (
    LABEL,
    SketchBSplineDegreeSpec,
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


MAX_CREATED_GEOMETRY = 4_096
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
        "input_geometry_indices",
        "old_degrees",
        "new_degrees",
        "exposed_internal_geometry_count",
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)
_METADATA_FIELDS = frozenset(
    {
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "internal_type",
        "layer_id",
    }
)
_INVARIANT_FIELDS = frozenset(
    {
        "first_parameter",
        "last_parameter",
        "rational",
        "periodic",
        "closed",
        "knots",
        "knot_count",
    }
)


@dataclass(frozen=True, slots=True)
class SketchBSplineDegreeSnapshot:
    transform: SketchTransformSnapshot
    proofs: tuple[FrozenCurveProof, ...]
    existing_helpers: tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class SketchBSplineDegreePlan:
    transform: SketchTransformPlan
    old_degrees: tuple[int, ...]
    new_degrees: tuple[int, ...]
    exposed_internal_geometry_count: int
    proofs: tuple[FrozenCurveProof, ...]


def _record(encoded: str, state: str) -> dict[str, Any]:
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise NativeSketchError(f"{LABEL} found invalid {state} state.")
    return value


def _indexed(records: tuple[str, ...], state: str) -> dict[int, dict[str, Any]]:
    result = {}
    for encoded in records:
        record = _record(encoded, state)
        index = record.get("index", record.get("geometry_index"))
        if type(index) is not int or index in result:
            raise NativeSketchError(f"{LABEL} found invalid {state} indices.")
        result[index] = record
    return result


def _references(record: Mapping[str, Any]) -> dict[int, tuple[int, int]]:
    result = {}
    values = record.get("references", [])
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, Mapping):
            continue
        slot = value.get("slot")
        geometry = value.get("geometry_index")
        position = value.get("position", 0)
        if type(slot) is int and type(geometry) is int and type(position) is int:
            result[slot] = geometry, position
    return result


def _helpers_by_root(
    state: Any,
    geometry: Mapping[int, Mapping[str, Any]],
    roots: tuple[int, ...],
) -> tuple[tuple[int, ...], ...]:
    selected = set(roots)
    helpers = {root: set() for root in roots}
    used = set()
    for encoded in state.constraint_records:
        record = _record(encoded, "constraint")
        if record.get("type") != "InternalAlignment":
            continue
        references = _references(record)
        helper = references.get(1, (-1, 0))[0]
        root = references.get(2, (-1, 0))[0]
        if root not in selected:
            continue
        helper_record = geometry.get(helper, {})
        if (
            helper in used
            or helper_record.get("kind") not in {"circle", "point"}
            or helper_record.get("construction") is not True
            or helper_record.get("internal_type")
            not in {"BSplineControlPoint", "BSplineKnotPoint"}
        ):
            raise NativeSketchError(f"{LABEL} found malformed existing spline helpers.")
        used.add(helper)
        helpers[root].add(helper)
    return tuple(tuple(sorted(helpers[root])) for root in roots)


def capture_bspline_degree_snapshot(
    context: NativeRuntimeContext,
    spec: SketchBSplineDegreeSpec,
) -> SketchBSplineDegreeSnapshot:
    if not isinstance(spec, SketchBSplineDegreeSpec):
        raise TypeError("spec must be a SketchBSplineDegreeSpec")
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
    geometry = _indexed(state.geometry_records, "geometry")
    grouped = grouped_geometry_members(target.sketch, label=LABEL)
    raw_geometry = tuple(target.sketch.Geometry)
    proofs = []
    desired_helpers = 0
    for index in spec.geometry_indices:
        record = geometry.get(index)
        if (
            record is None
            or index in grouped
            or record.get("kind") != "b_spline"
            or record.get("internal_type")
        ):
            raise NativeSketchError(
                f"{LABEL} requires exact ungrouped internal B-spline edges."
            )
        degree = record.get("degree")
        poles = record.get("pole_count")
        knots = record.get("knot_count")
        if (
            type(degree) is not int
            or not 1 <= degree < 25
            or type(poles) is not int
            or poles < 2
            or type(knots) is not int
            or knots < 2
        ):
            raise NativeSketchError(f"{LABEL} B-spline structure cannot be elevated.")
        desired_helpers += poles + 2 * knots
        proofs.append(curve_proof(raw_geometry[index]))
    helpers = _helpers_by_root(state, geometry, spec.geometry_indices)
    missing_helpers_upper_bound = desired_helpers - sum(map(len, helpers))
    if (
        missing_helpers_upper_bound > MAX_CREATED_GEOMETRY
        or missing_helpers_upper_bound * 2 > MAX_CREATED_CONSTRAINTS
    ):
        raise NativeSketchError(f"{LABEL} would create too much spline state.")
    transform = SketchTransformSnapshot(target, spec, state, LABEL)
    return SketchBSplineDegreeSnapshot(transform, tuple(proofs), helpers)


def _integer_tuple(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or any(
        type(item) is not int for item in value
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return tuple(value)


def _metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in _METADATA_FIELDS if key in record}


def _position(record: Mapping[str, Any], field: str) -> tuple[float, float, float]:
    value = record.get(field)
    if not isinstance(value, list) or len(value) not in {2, 3}:
        raise NativeSketchError(f"{LABEL} found malformed spline helper geometry.")
    try:
        result = tuple(float(item) for item in (*value, 0.0)[:3])
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeSketchError(
            f"{LABEL} found malformed spline helper geometry."
        ) from exc
    if any(not math.isfinite(item) for item in result):
        raise NativeSketchError(f"{LABEL} found malformed spline helper geometry.")
    return result


def _same_position_multiset(
    actual: list[tuple[float, float, float]],
    expected: tuple[tuple[float, float, float], ...],
) -> bool:
    remaining = list(expected)
    for point in actual:
        match = next(
            (
                index
                for index, candidate in enumerate(remaining)
                if math.dist(point, candidate) <= 1.0e-8
            ),
            None,
        )
        if match is None:
            return False
        del remaining[match]
    return not remaining


def parse_bspline_degree_diagnostic(
    result: Any,
    snapshot: SketchBSplineDegreeSnapshot,
) -> SketchBSplineDegreePlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    transform_snapshot = snapshot.transform
    inputs = _integer_tuple(result["input_geometry_indices"], "input geometry")
    old_degrees = _integer_tuple(result["old_degrees"], "old degrees")
    new_degrees = _integer_tuple(result["new_degrees"], "new degrees")
    if inputs != transform_snapshot.spec.geometry_indices or not (
        len(old_degrees) == len(new_degrees) == len(inputs)
    ):
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    exposed = result["exposed_internal_geometry_count"]
    if type(exposed) is not int or not 0 <= exposed <= MAX_CREATED_GEOMETRY:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid helper growth.")

    plan = parse_transform_diagnostic(result, transform_snapshot)
    if (
        plan.external_reference_records
        != transform_snapshot.state.external_reference_records
        or plan.external_geometry_records
        != transform_snapshot.state.external_geometry_records
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed external geometry.")
    geometry_before = _indexed(
        geometry_records_without_tags(transform_snapshot.state.geometry_records),
        "geometry",
    )
    geometry_after = _indexed(plan.geometry_records, "geometry")
    old_count = transform_snapshot.spec.target.expected_geometry_count
    constraint_count = transform_snapshot.spec.target.expected_constraint_count
    geometry_identity = plan.identity.geometry
    constraint_identity = plan.identity.constraints
    if (
        dict(geometry_identity.old_to_new)
        != {index: index for index in range(old_count)}
        or geometry_identity.deleted_indices
        or geometry_identity.created_indices
        != tuple(range(old_count, len(geometry_after)))
        or dict(constraint_identity.old_to_new)
        != {index: index for index in range(constraint_count)}
        or constraint_identity.deleted_indices
        or constraint_identity.created_indices
        != tuple(range(constraint_count, len(plan.constraint_records)))
    ):
        raise NativeSketchError(f"{LABEL} changed the wrong durable identities.")

    roots = set(inputs)
    raw_after = result["geometry"]
    if not isinstance(raw_after, (list, tuple)) or len(raw_after) != len(
        geometry_after
    ):
        raise NativeSketchError(
            f"{LABEL} feasibility returned invalid geometry objects."
        )
    new_proofs = []
    expected_helpers = 0
    for position, index in enumerate(inputs):
        before = geometry_before[index]
        after = geometry_after.get(index, {})
        if (
            old_degrees[position] != before.get("degree")
            or new_degrees[position] != old_degrees[position] + 1
            or after.get("degree") != new_degrees[position]
            or after.get("kind") != "b_spline"
            or _metadata(after) != _metadata(before)
            or any(after.get(field) != before.get(field) for field in _INVARIANT_FIELDS)
        ):
            raise NativeSketchError(f"{LABEL} returned the wrong degree elevation.")
        old_mult = before.get("multiplicities")
        new_mult = after.get("multiplicities")
        if isinstance(old_mult, list) and new_mult != [value + 1 for value in old_mult]:
            raise NativeSketchError(f"{LABEL} returned wrong elevated multiplicities.")
        proof = curve_proof(raw_after[index])
        if not same_shape_samples(proof, snapshot.proofs[position]):
            raise NativeSketchError(f"{LABEL} changed the B-spline shape.")
        new_proofs.append(proof)
        poles = after.get("pole_count")
        knots = after.get("knot_count")
        old_poles = before.get("pole_count")
        old_knots = before.get("knot_count")
        if (
            type(poles) is not int
            or type(knots) is not int
            or type(old_poles) is not int
            or type(old_knots) is not int
            or not old_poles <= poles <= old_poles + old_knots
        ):
            raise NativeSketchError(f"{LABEL} returned incomplete elevated structure.")
        expected_helpers += poles + knots - len(snapshot.existing_helpers[position])
    created_helpers = set(geometry_identity.created_indices)
    existing_helpers = {
        helper for values in snapshot.existing_helpers for helper in values
    }
    if exposed != expected_helpers or len(created_helpers) != expected_helpers:
        raise NativeSketchError(f"{LABEL} exposed the wrong number of spline helpers.")
    for index, record in geometry_after.items():
        if index in roots:
            continue
        if index < old_count:
            if index in existing_helpers:
                if _metadata(record) != _metadata(geometry_before[index]):
                    raise NativeSketchError(
                        f"{LABEL} changed existing helper metadata."
                    )
            elif record != geometry_before[index]:
                raise NativeSketchError(f"{LABEL} changed unrelated geometry.")
        elif (
            index not in created_helpers
            or record.get("kind") not in {"circle", "point"}
            or record.get("construction") is not True
            or record.get("internal_type")
            not in {"BSplineControlPoint", "BSplineKnotPoint"}
        ):
            raise NativeSketchError(f"{LABEL} created invalid spline helpers.")

    if (
        plan.constraint_records[:constraint_count]
        != transform_snapshot.state.constraint_records
    ):
        raise NativeSketchError(f"{LABEL} changed existing constraints.")
    helpers_after = _helpers_by_root(plan, geometry_after, inputs)
    if {helper for values in helpers_after for helper in values} != (
        existing_helpers | created_helpers
    ):
        raise NativeSketchError(f"{LABEL} exposed the wrong spline helper set.")
    for position, helper_indices in enumerate(helpers_after):
        controls = []
        knots = []
        for helper in helper_indices:
            record = geometry_after[helper]
            if record.get("internal_type") == "BSplineControlPoint":
                controls.append(_position(record, "center_mm"))
            else:
                knots.append(_position(record, "position_mm"))
        proof = new_proofs[position]
        if not _same_position_multiset(
            controls, proof.control_positions
        ) or not _same_position_multiset(knots, proof.knot_positions):
            raise NativeSketchError(
                f"{LABEL} helpers do not represent the elevated B-spline."
            )
    allowed_helpers = created_helpers | existing_helpers
    aligned = set()
    for index in constraint_identity.created_indices:
        record = _record(plan.constraint_records[index], "constraint")
        references = _references(record)
        if record.get("type") == "InternalAlignment":
            helper = references.get(1, (-1, 0))[0]
            root = references.get(2, (-1, 0))[0]
            if helper not in created_helpers or root not in roots or helper in aligned:
                raise NativeSketchError(f"{LABEL} created wrong internal alignment.")
            aligned.add(helper)
        elif (
            record.get("type") not in {"Weight", "Equal"}
            or not references
            or any(
                geometry not in allowed_helpers
                for geometry, _position in references.values()
            )
        ):
            raise NativeSketchError(f"{LABEL} created unrelated constraints.")
    if (
        aligned != created_helpers
        or len(constraint_identity.created_indices) > MAX_CREATED_CONSTRAINTS
    ):
        raise NativeSketchError(f"{LABEL} did not align every new spline helper.")
    return SketchBSplineDegreePlan(
        plan,
        old_degrees,
        new_degrees,
        exposed,
        tuple(new_proofs),
    )


def require_bspline_degree_snapshot_unchanged(
    document: Any,
    snapshot: SketchBSplineDegreeSnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot.transform)


def require_pure_bspline_degree_diagnostic(
    snapshot: SketchBSplineDegreeSnapshot,
) -> None:
    require_pure_transform_diagnostic(snapshot.transform)


def verify_bspline_degree_state(
    document: Any,
    snapshot: SketchBSplineDegreeSnapshot,
    plan: SketchBSplineDegreePlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...]]:
    (
        sketch,
        created_geometry,
        _deleted_geometry,
        created_constraints,
        _deleted_constraints,
    ) = verify_transform_state(document, snapshot.transform, plan.transform, receipt)
    raw_geometry = tuple(sketch.Geometry)
    for position, index in enumerate(snapshot.transform.spec.geometry_indices):
        proof = curve_proof(raw_geometry[index])
        if proof.digest != plan.proofs[position].digest or not same_shape_samples(
            proof, snapshot.proofs[position]
        ):
            raise NativeSketchError(f"{LABEL} final B-spline representation is wrong.")
    return sketch, created_geometry, created_constraints
