# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict proof of the detached human Sketch Join Curves result."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeSketchCurvePointDiagnostic import (
    aligned_internal_geometry,
    record_without_index,
)
from VibeCADNativeSketchDiagnosticState import (
    ISSUE_FIELDS,
    bounded_count,
    diagnostic_sketch_records,
    diagnostic_solver_degrees,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchJoinState import SketchJoinSnapshot
from VibeCADNativeSketchJoinTarget import LABEL
from VibeCADNativeSketchMutationState import (
    SketchMutationIdentityPlan,
    collection_identity_plan,
    collection_index_map,
    expected_expression_records,
    geometry_records_without_tags,
)
from VibeCADNativeSketchTransformState import SketchTransformPlan


MAX_GENERATED_GEOMETRY = 4_097
FIELDS = frozenset(
    {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        *ISSUE_FIELDS,
        "first_geometry_index",
        "first_endpoint",
        "second_geometry_index",
        "second_endpoint",
        "continuity",
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
class SketchJoinPlan:
    transform: SketchTransformPlan
    first_geometry_index: int
    first_endpoint: int
    second_geometry_index: int
    second_endpoint: int
    continuity: int
    joined_geometry_index: int
    helper_count: int


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return value


def _point(record: Mapping[str, Any], field: str) -> tuple[float, float, float]:
    value = record.get(field)
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        )
    ):
        raise NativeSketchError(f"{LABEL} found an unavailable curve endpoint.")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise NativeSketchError(f"{LABEL} found an invalid curve endpoint.")
    return result


def _same_point(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-8)
        for left, right in zip(first, second, strict=True)
    )


def _constraint_references(record: Mapping[str, Any]) -> set[int]:
    values = record.get("references", [])
    if not isinstance(values, list):
        raise NativeSketchError(f"{LABEL} found malformed constraint references.")
    result = set()
    for value in values:
        if not isinstance(value, Mapping):
            raise NativeSketchError(f"{LABEL} found malformed constraint references.")
        geometry = value.get("geometry_index")
        if type(geometry) is not int:
            raise NativeSketchError(f"{LABEL} found malformed constraint references.")
        result.add(geometry)
    return result


def _expected_continuity(snapshot: SketchJoinSnapshot) -> int:
    spec = snapshot.spec
    expected = {
        (spec.first.geometry_index, spec.first.endpoint_code),
        (spec.second.geometry_index, spec.second.endpoint_code),
    }
    for encoded in snapshot.state.constraint_records:
        record = json.loads(encoded)
        if record.get("type") != "Tangent":
            continue
        references = record.get("references", [])
        actual = {
            (item.get("geometry_index"), item.get("position", 0))
            for item in references
            if isinstance(item, Mapping)
        }
        if actual == expected:
            return 1
    return 0


def _identity(
    result: Mapping[str, Any],
    snapshot: SketchJoinSnapshot,
    geometry_records: tuple[str, ...],
    constraint_records: tuple[str, ...],
) -> tuple[
    SketchMutationIdentityPlan,
    dict[int, int],
    dict[int, str],
    dict[int, str],
    dict[int, int],
    dict[int, str],
]:
    state = snapshot.state
    geometry_map, deleted_geometry, created_geometry = collection_index_map(
        result["mutation_receipt"],
        "geometry",
        len(state.geometry_records),
        len(geometry_records),
        label=LABEL,
    )
    constraint_map, deleted_constraints, created_constraints = collection_index_map(
        result["mutation_receipt"],
        "constraints",
        len(state.constraint_records),
        len(constraint_records),
        label=LABEL,
    )
    for old, new in geometry_map.items():
        if (
            str(json.loads(geometry_records[new]).get("tag", ""))
            != state.geometry_tags[old]
        ):
            raise NativeSketchError(f"{LABEL} feasibility replaced retained geometry.")
    for old, tag in deleted_geometry.items():
        if tag != state.geometry_tags[old]:
            raise NativeSketchError(f"{LABEL} feasibility deleted wrong geometry.")
    for new, tag in created_geometry.items():
        if str(json.loads(geometry_records[new]).get("tag", "")) != tag:
            raise NativeSketchError(f"{LABEL} feasibility created wrong geometry.")
    for old, tag in deleted_constraints.items():
        if tag != state.constraint_tags[old]:
            raise NativeSketchError(f"{LABEL} feasibility deleted wrong constraints.")
    return (
        SketchMutationIdentityPlan(
            collection_identity_plan(geometry_map, deleted_geometry, created_geometry),
            collection_identity_plan(
                constraint_map, deleted_constraints, created_constraints
            ),
        ),
        geometry_map,
        deleted_geometry,
        created_geometry,
        constraint_map,
        deleted_constraints,
    )


def _validate_joined_geometry(
    snapshot: SketchJoinSnapshot,
    geometry_records: tuple[str, ...],
    constraint_records: tuple[str, ...],
    raw_constraints: tuple[Any, ...],
    geometry_map: Mapping[int, int],
    deleted_geometry: Mapping[int, str],
    created_geometry: Mapping[int, str],
    deleted_constraints: Mapping[int, str],
) -> tuple[int, int]:
    before = snapshot.state.geometry_records
    spec = snapshot.spec
    selected = set(spec.geometry_indices)
    allowed_deleted = set(selected)
    for index in selected:
        allowed_deleted.update(
            aligned_internal_geometry(index, before, snapshot.state.constraint_records)
        )
    if not selected <= set(deleted_geometry) or set(deleted_geometry) - allowed_deleted:
        raise NativeSketchError(f"{LABEL} feasibility deleted unrelated geometry.")
    for old, new in geometry_map.items():
        if record_without_index(before[old]) != record_without_index(
            geometry_records[new]
        ):
            raise NativeSketchError(f"{LABEL} feasibility changed retained geometry.")
    for index in deleted_constraints:
        if (
            not _constraint_references(
                json.loads(snapshot.state.constraint_records[index])
            )
            & allowed_deleted
        ):
            raise NativeSketchError(
                f"{LABEL} feasibility deleted unrelated constraints."
            )

    roots = [
        index
        for index in created_geometry
        if "internal_type" not in json.loads(geometry_records[index])
    ]
    if len(roots) != 1 or len(created_geometry) > MAX_GENERATED_GEOMETRY:
        raise NativeSketchError(f"{LABEL} feasibility returned wrong joined geometry.")
    root = roots[0]
    joined = json.loads(geometry_records[root])
    first = json.loads(before[spec.first.geometry_index])
    second = json.loads(before[spec.second.geometry_index])
    expected_start = _point(
        first, "end_mm" if spec.first.endpoint == "start" else "start_mm"
    )
    expected_end = _point(
        second, "end_mm" if spec.second.endpoint == "start" else "start_mm"
    )
    if (
        joined.get("kind") != "b_spline"
        or "internal_type" in joined
        or bool(joined.get("periodic"))
        or bool(joined.get("closed"))
        or (
            _expected_continuity(snapshot) == 1
            and (type(joined.get("degree")) is not int or joined["degree"] < 2)
        )
        or bool(joined.get("construction")) is not bool(first.get("construction"))
        or not _same_point(_point(joined, "start_mm"), expected_start)
        or not _same_point(_point(joined, "end_mm"), expected_end)
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned wrong joined curve.")
    helpers = aligned_internal_geometry(root, geometry_records, constraint_records)
    if set(created_geometry) != {root, *helpers}:
        raise NativeSketchError(
            f"{LABEL} feasibility returned unrelated helper geometry."
        )
    expected_helpers = joined.get("pole_count", -1) + joined.get("knot_count", -1)
    if type(expected_helpers) is not int or len(helpers) != expected_helpers:
        raise NativeSketchError(
            f"{LABEL} feasibility returned incomplete spline helpers."
        )
    for index in helpers:
        helper = json.loads(geometry_records[index])
        if (
            helper.get("internal_type")
            not in {"BSplineControlPoint", "BSplineKnotPoint"}
            or helper.get("construction") is not True
            or helper.get("kind") not in {"point", "circle"}
        ):
            raise NativeSketchError(f"{LABEL} feasibility returned malformed helpers.")
    roles = set()
    for constraint in raw_constraints:
        if (
            str(getattr(constraint, "Type", "")) != "InternalAlignment"
            or getattr(constraint, "Second", None) != root
        ):
            continue
        helper = getattr(constraint, "First", None)
        role = getattr(constraint, "InternalAlignmentIndex", None)
        if type(helper) is not int or helper not in helpers or type(role) is not int:
            raise NativeSketchError(
                f"{LABEL} feasibility returned malformed helper alignment."
            )
        internal_type = json.loads(geometry_records[helper]).get("internal_type")
        pair = (internal_type, role)
        if role < 0 or pair in roles:
            raise NativeSketchError(
                f"{LABEL} feasibility returned duplicate helper alignment."
            )
        roles.add(pair)
    expected_roles = {
        *(("BSplineControlPoint", index) for index in range(joined["pole_count"])),
        *(("BSplineKnotPoint", index) for index in range(joined["knot_count"])),
    }
    if roles != expected_roles:
        raise NativeSketchError(
            f"{LABEL} feasibility returned incomplete helper alignment."
        )
    return root, len(helpers)


def parse_join_diagnostic(
    result: Any,
    snapshot: SketchJoinSnapshot,
) -> SketchJoinPlan:
    if not isinstance(result, Mapping) or set(result) != FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    degrees = diagnostic_solver_degrees(result, label=LABEL)
    geometry, constraints = diagnostic_sketch_records(result, label=LABEL)
    spec = snapshot.spec
    endpoints = (
        _integer(result["first_geometry_index"], "first geometry index"),
        _integer(result["first_endpoint"], "first endpoint"),
        _integer(result["second_geometry_index"], "second geometry index"),
        _integer(result["second_endpoint"], "second endpoint"),
    )
    expected = (
        spec.first.geometry_index,
        spec.first.endpoint_code,
        spec.second.geometry_index,
        spec.second.endpoint_code,
    )
    continuity = _integer(result["continuity"], "continuity")
    if endpoints != expected or continuity != _expected_continuity(snapshot):
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different target.")
    if bounded_count(
        result["external_geometry_count"], "external geometry count", label=LABEL
    ) != len(snapshot.state.external_geometry_records):
        raise NativeSketchError(f"{LABEL} feasibility changed external geometry.")
    (
        identity,
        geometry_map,
        deleted_geometry,
        created_geometry,
        constraint_map,
        deleted_constraints,
    ) = _identity(result, snapshot, geometry, constraints)
    root, helper_count = _validate_joined_geometry(
        snapshot,
        geometry,
        constraints,
        tuple(result["constraints"]),
        geometry_map,
        deleted_geometry,
        created_geometry,
        deleted_constraints,
    )
    transform = SketchTransformPlan(
        identity,
        geometry_records_without_tags(geometry),
        constraints,
        snapshot.state.external_reference_records,
        snapshot.state.external_geometry_records,
        expected_expression_records(snapshot.state.expression_records, constraint_map),
        degrees,
    )
    return SketchJoinPlan(transform, *endpoints, continuity, root, helper_count)
