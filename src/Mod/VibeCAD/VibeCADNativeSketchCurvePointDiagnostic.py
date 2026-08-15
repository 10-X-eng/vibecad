# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict shared parsing for detached curve-at-point Sketch diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeSketchCurvePointTarget import SketchCurvePointSpec
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchMutationState import (
    SketchMutationIdentityPlan,
    collection_identity_plan,
    collection_index_map,
)
from VibeCADNativeSketchState import (
    serialize_sketch_constraint_value,
    serialize_sketch_geometry_value,
)


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
        "input_geometry_index",
        "reference_point_mm",
        "external_geometry_count",
        "mutation_receipt",
        "geometry_count",
        "constraint_count",
        "geometry",
        "geometry_metadata",
        "constraints",
    }
)


@dataclass(frozen=True, slots=True)
class CurvePointDiagnosticState:
    input_geometry_index: int
    reference_point_mm: tuple[float, float]
    identity: SketchMutationIdentityPlan
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    degrees_of_freedom: int
    geometry_mapping: dict[int, int]
    deleted_geometry: dict[int, str]
    created_geometry: dict[int, str]
    constraint_mapping: dict[int, int]
    deleted_constraints: dict[int, str]
    created_constraints: dict[int, str]


def _integer(value: Any, field: str, *, label: str) -> int:
    if type(value) is not int:
        raise NativeSketchError(f"{label} feasibility returned invalid {field}.")
    return value


def _sequence(value: Any, field: str, *, label: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 1_000_003:
        raise NativeSketchError(f"{label} feasibility returned invalid {field} values.")
    return tuple(value)


def _issues(
    result: Mapping[str, Any],
    *,
    label: str,
) -> tuple[tuple[int, ...], ...]:
    groups = []
    for field in _ISSUE_FIELDS:
        values = _sequence(result[field], field, label=label)
        if any(type(value) is not int or value < 0 for value in values) or len(
            set(values)
        ) != len(values):
            raise NativeSketchError(
                f"{label} feasibility returned invalid {field} values."
            )
        groups.append(tuple(values))
    return tuple(groups)


def record_without_index(encoded: str) -> dict[str, Any]:
    record = json.loads(encoded)
    record.pop("index", None)
    return record


def aligned_internal_geometry(
    target: int,
    geometry_records: tuple[str, ...],
    constraint_records: tuple[str, ...],
) -> set[int]:
    result: set[int] = set()
    for encoded in constraint_records:
        constraint = json.loads(encoded)
        if constraint.get("type") != "InternalAlignment":
            continue
        references = constraint.get("references", [])
        indices = {
            int(reference["geometry_index"])
            for reference in references
            if isinstance(reference, Mapping)
            and type(reference.get("geometry_index")) is int
            and int(reference["geometry_index"]) >= 0
        }
        if target not in indices:
            continue
        for index in indices - {target}:
            if index < len(geometry_records) and "internal_type" in json.loads(
                geometry_records[index]
            ):
                result.add(index)
    return result


def parse_curve_point_diagnostic(
    result: Any,
    spec: SketchCurvePointSpec,
    before_geometry_records: tuple[str, ...],
    before_constraint_records: tuple[str, ...],
    *,
    label: str,
) -> CurvePointDiagnosticState:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{label} feasibility returned incomplete diagnostics.")
    accepted = result["accepted"]
    if type(accepted) is not bool:
        raise NativeSketchError(f"{label} feasibility returned invalid acceptance.")
    degrees = _integer(
        result["degrees_of_freedom"],
        "degrees of freedom",
        label=label,
    )
    status = _integer(result["solver_status"], "solver status", label=label)
    issues = _issues(result, label=label)
    if not accepted:
        raise NativeSketchError(
            f"{label} would introduce a solver issue; nothing changed."
        )
    if degrees < 0 or status != 0 or any(issues):
        raise NativeSketchError(
            f"{label} feasibility returned inconsistent acceptance."
        )

    target = _integer(
        result["input_geometry_index"],
        "input geometry index",
        label=label,
    )
    if target != spec.selection.geometry_index or not 0 <= target < len(
        before_geometry_records
    ):
        raise NativeSketchError(f"{label} feasibility analyzed a different curve.")
    point = _sequence(result["reference_point_mm"], "reference point", label=label)
    if len(point) != 2:
        raise NativeSketchError(
            f"{label} feasibility returned an invalid reference point."
        )
    coordinates = []
    for raw, expected in zip(point, spec.selection.reference_point_mm, strict=True):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise NativeSketchError(
                f"{label} feasibility returned an invalid reference point."
            )
        coordinate = float(raw)
        if not math.isfinite(coordinate) or coordinate != expected:
            raise NativeSketchError(
                f"{label} feasibility analyzed a different reference point."
            )
        coordinates.append(coordinate)
    external_count = _integer(
        result["external_geometry_count"],
        "external count",
        label=label,
    )
    if external_count != spec.expected_external_geometry_count:
        raise NativeSketchError(f"{label} feasibility changed external geometry.")

    geometry_count = _integer(result["geometry_count"], "geometry count", label=label)
    constraint_count = _integer(
        result["constraint_count"],
        "constraint count",
        label=label,
    )
    geometry = _sequence(result["geometry"], "geometry", label=label)
    metadata = _sequence(
        result["geometry_metadata"],
        "geometry metadata",
        label=label,
    )
    constraints = _sequence(result["constraints"], "constraints", label=label)
    if (
        geometry_count < 0
        or constraint_count < 0
        or len(geometry) != geometry_count
        or len(metadata) != geometry_count
        or len(constraints) != constraint_count
    ):
        raise NativeSketchError(f"{label} feasibility returned inconsistent counts.")
    try:
        geometry_records = canonical_sketch_records(
            serialize_sketch_geometry_value(item, index, metadata[index])
            for index, item in enumerate(geometry)
        )
        constraint_records = canonical_sketch_records(
            serialize_sketch_constraint_value(item, index)
            for index, item in enumerate(constraints)
        )
    except Exception as exc:
        raise NativeSketchError(
            f"{label} feasibility returned unserializable state."
        ) from exc

    geometry_map, deleted_geometry, created_geometry = collection_index_map(
        result["mutation_receipt"],
        "geometry",
        len(before_geometry_records),
        geometry_count,
        label=label,
    )
    constraint_map, deleted_constraints, created_constraints = collection_index_map(
        result["mutation_receipt"],
        "constraints",
        len(before_constraint_records),
        constraint_count,
        label=label,
    )
    return CurvePointDiagnosticState(
        target,
        (coordinates[0], coordinates[1]),
        SketchMutationIdentityPlan(
            collection_identity_plan(
                geometry_map,
                deleted_geometry,
                created_geometry,
            ),
            collection_identity_plan(
                constraint_map,
                deleted_constraints,
                created_constraints,
            ),
        ),
        geometry_records,
        constraint_records,
        degrees,
        geometry_map,
        deleted_geometry,
        created_geometry,
        constraint_map,
        deleted_constraints,
        created_constraints,
    )
