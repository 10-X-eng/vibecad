# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state and semantic checks for Geometry-to-B-Spline conversion."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import (
    ISSUE_FIELDS,
    require_healthy_external_records,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchMutationState import grouped_geometry_members
from VibeCADNativeSketchNURBSConversionTarget import (
    LABEL,
    SketchNURBSConversionSpec,
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
        "converted_geometry_indices",
        "exposed_internal_geometry_count",
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)


@dataclass(frozen=True, slots=True)
class SketchNURBSConversionPlan:
    transform: SketchTransformPlan
    converted_geometry_indices: tuple[int, ...]
    exposed_internal_geometry_count: int
    internal_conversion_count: int
    external_copy_count: int


def _record(encoded: str, state: str) -> dict[str, Any]:
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise NativeSketchError(f"{LABEL} found invalid {state} state.")
    return value


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


def _indexed_records(records: tuple[str, ...], state: str) -> dict[int, dict[str, Any]]:
    result = {}
    for encoded in records:
        record = _record(encoded, state)
        index = record.get("index", record.get("geometry_index"))
        if type(index) is not int or index in result:
            raise NativeSketchError(f"{LABEL} found invalid {state} indices.")
        result[index] = record
    return result


def _validate_targets(
    sketch: Any,
    spec: SketchNURBSConversionSpec,
    snapshot: SketchTransformSnapshot,
) -> None:
    internal = _indexed_records(snapshot.state.geometry_records, "geometry")
    external = _indexed_records(
        snapshot.state.external_geometry_records, "external geometry"
    )
    grouped = grouped_geometry_members(sketch, label=LABEL)
    known_created = len(spec.geometry_indices)
    for index in spec.geometry_indices:
        if index >= 0:
            record = internal.get(index)
            if record is None:
                raise NativeSketchError(f"{LABEL} internal geometry index is stale.")
            if index in grouped or record.get("internal_type"):
                raise NativeSketchError(
                    f"{LABEL} does not dismantle grouped or internal-alignment geometry."
                )
            if record.get("kind") == "b_spline":
                poles = record.get("pole_count")
                knots = record.get("knot_count")
                if type(poles) is not int or type(knots) is not int:
                    raise NativeSketchError(
                        f"{LABEL} B-spline structure is unavailable."
                    )
                known_created += poles + knots
        else:
            if index > -3:
                raise NativeSketchError(
                    f"{LABEL} cannot convert Sketch axes or the origin."
                )
            record = external.get(index)
            if record is None:
                raise NativeSketchError(f"{LABEL} external geometry index is stale.")
        if record.get("kind") in {"point", "unavailable"}:
            raise NativeSketchError(f"{LABEL} requires selected edges, not points.")
    if known_created > MAX_CREATED_GEOMETRY:
        raise NativeSketchError(f"{LABEL} would create too much spline state.")


def capture_nurbs_conversion_snapshot(
    context: NativeRuntimeContext,
    spec: SketchNURBSConversionSpec,
) -> SketchTransformSnapshot:
    if not isinstance(spec, SketchNURBSConversionSpec):
        raise TypeError("spec must be a SketchNURBSConversionSpec")
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
    snapshot = SketchTransformSnapshot(target, spec, state, LABEL)
    _validate_targets(target.sketch, spec, snapshot)
    return snapshot


def _integer_tuple(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or any(
        type(item) is not int for item in value
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return tuple(value)


def _expected_converted_indices(snapshot: SketchTransformSnapshot) -> tuple[int, ...]:
    next_index = snapshot.spec.target.expected_geometry_count
    result = []
    for index in snapshot.spec.geometry_indices:
        if index >= 0:
            result.append(index)
        else:
            result.append(next_index)
            next_index += 1
    return tuple(result)


def _expected_deleted_constraints(snapshot: SketchTransformSnapshot) -> set[int]:
    selected = {index for index in snapshot.spec.geometry_indices if index >= 0}
    deleted = set()
    for encoded in snapshot.state.constraint_records:
        record = _record(encoded, "constraint")
        references = _references(record)
        involved = [value for value in references.values() if value[0] in selected]
        if not involved:
            continue
        if record.get("type") != "Coincident" or any(
            position == 3 for _geometry, position in involved
        ):
            deleted.add(record["index"])
    return deleted


def _validate_created_constraints(
    plan: SketchTransformPlan,
    internal_roots: set[int],
    helpers: set[int],
) -> None:
    records = _indexed_records(plan.constraint_records, "constraint")
    aligned_helpers = []
    for index in plan.identity.constraints.created_indices:
        record = records.get(index)
        if record is None or record.get("type") not in {
            "InternalAlignment",
            "Weight",
            "Equal",
        }:
            raise NativeSketchError(f"{LABEL} created unexpected constraints.")
        references = _references(record)
        if record["type"] == "InternalAlignment":
            if (
                references.get(1, (-1, 0))[0] not in helpers
                or references.get(2, (-1, 0))[0] not in internal_roots
            ):
                raise NativeSketchError(f"{LABEL} created wrong internal alignment.")
            aligned_helpers.append(references[1][0])
        elif any(
            geometry not in helpers for geometry, _position in references.values()
        ):
            raise NativeSketchError(f"{LABEL} created unrelated spline constraints.")
    if len(aligned_helpers) != len(helpers) or set(aligned_helpers) != helpers:
        raise NativeSketchError(f"{LABEL} exposed the wrong spline internals.")


def parse_nurbs_conversion_diagnostic(
    result: Any,
    snapshot: SketchTransformSnapshot,
) -> SketchNURBSConversionPlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    inputs = _integer_tuple(result["input_geometry_indices"], "input geometry")
    converted = _integer_tuple(
        result["converted_geometry_indices"], "converted geometry"
    )
    expected_converted = _expected_converted_indices(snapshot)
    if inputs != snapshot.spec.geometry_indices or converted != expected_converted:
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    exposed = result["exposed_internal_geometry_count"]
    if type(exposed) is not int or not 0 <= exposed <= MAX_CREATED_GEOMETRY:
        raise NativeSketchError(
            f"{LABEL} feasibility returned invalid exposed geometry."
        )

    plan = parse_transform_diagnostic(result, snapshot)
    if (
        plan.external_reference_records != snapshot.state.external_reference_records
        or plan.external_geometry_records != snapshot.state.external_geometry_records
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed external geometry.")
    internal_inputs = {index for index in snapshot.spec.geometry_indices if index >= 0}
    external_count = len(snapshot.spec.geometry_indices) - len(internal_inputs)
    roots = set(converted)
    created = set(plan.identity.geometry.created_indices)
    deleted = set(plan.identity.geometry.deleted_indices)
    if deleted != internal_inputs or not roots <= created:
        raise NativeSketchError(f"{LABEL} replaced the wrong geometry.")
    helpers = created - roots
    if (
        len(created) > MAX_CREATED_GEOMETRY
        or len(plan.identity.constraints.created_indices) > MAX_CREATED_CONSTRAINTS
        or exposed != len(helpers)
    ):
        raise NativeSketchError(f"{LABEL} created an invalid amount of spline state.")

    geometry = _indexed_records(plan.geometry_records, "geometry")
    for index in roots:
        if geometry.get(index, {}).get("kind") != "b_spline":
            raise NativeSketchError(f"{LABEL} did not create B-spline geometry.")
    expected_helpers = 0
    for index in internal_inputs:
        root = geometry[index]
        poles = root.get("pole_count")
        knots = root.get("knot_count")
        if type(poles) is not int or type(knots) is not int or poles < 2 or knots < 2:
            raise NativeSketchError(f"{LABEL} returned invalid B-spline structure.")
        expected_helpers += poles + knots
    if expected_helpers != exposed or any(
        geometry.get(index, {}).get("construction") is not True
        or geometry[index].get("kind") not in {"circle", "point"}
        for index in helpers
    ):
        raise NativeSketchError(f"{LABEL} exposed the wrong spline internals.")

    if set(plan.identity.constraints.deleted_indices) != _expected_deleted_constraints(
        snapshot
    ):
        raise NativeSketchError(f"{LABEL} removed the wrong constraints.")
    _validate_created_constraints(plan, internal_inputs, helpers)
    return SketchNURBSConversionPlan(
        plan,
        converted,
        exposed,
        len(internal_inputs),
        external_count,
    )


def require_nurbs_conversion_snapshot_unchanged(
    document: Any,
    snapshot: SketchTransformSnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot)


def require_pure_nurbs_conversion_diagnostic(snapshot: SketchTransformSnapshot) -> None:
    require_pure_transform_diagnostic(snapshot)


def verify_nurbs_conversion_state(
    document: Any,
    snapshot: SketchTransformSnapshot,
    plan: SketchNURBSConversionPlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    return verify_transform_state(document, snapshot, plan.transform, receipt)
