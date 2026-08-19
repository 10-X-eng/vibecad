# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict parsing and exact-state proof for detached Sketch Fillet diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchFilletTarget import LABEL, SketchFilletSpec
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
        "form",
        "input_geometry_indices",
        "fillet_geometry_index",
        "corner_geometry_index",
        "radius_mm",
        "trimmed",
        "construction",
        "geometry_count",
        "constraint_count",
        "geometry",
        "geometry_metadata",
        "constraints",
    }
)
_GEOMETRY_METADATA = frozenset(
    {
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "internal_type",
        "layer_id",
        "tag",
    }
)


@dataclass(frozen=True, slots=True)
class SketchFilletPlan:
    form: str
    input_geometry_indices: tuple[int, int]
    fillet_geometry_index: int
    corner_geometry_index: int | None
    radius_mm: float
    trimmed: bool
    construction: bool
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    degrees_of_freedom: int


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return value


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 1_000_002:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field} values.")
    return tuple(value)


def _issues(result: Mapping[str, Any]) -> tuple[tuple[int, ...], ...]:
    groups = []
    for field in _ISSUE_FIELDS:
        values = _sequence(result[field], field)
        if any(type(value) is not int or value < 0 for value in values) or len(
            set(values)
        ) != len(values):
            raise NativeSketchError(
                f"{LABEL} feasibility returned invalid {field} values."
            )
        groups.append(tuple(values))
    return tuple(groups)


def _metadata(record: str) -> str:
    value = json.loads(record)
    return json.dumps(
        {key: value[key] for key in _GEOMETRY_METADATA if key in value},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_geometry_plan(
    geometry_records: tuple[str, ...],
    before_records: tuple[str, ...],
    *,
    input_indices: tuple[int, int],
    fillet_index: int,
    corner_index: int | None,
    radius_mm: float,
    construction: bool,
) -> None:
    for index, before in enumerate(before_records):
        after = geometry_records[index]
        if index not in input_indices and after != before:
            raise NativeSketchError(
                f"{LABEL} feasibility changed unrelated geometry {index}."
            )
        if index in input_indices and _metadata(after) != _metadata(before):
            raise NativeSketchError(
                f"{LABEL} feasibility changed target identity or metadata at {index}."
            )

    fillet = json.loads(geometry_records[fillet_index])
    if (
        fillet.get("kind") != "circular_arc"
        or bool(fillet.get("construction")) is not construction
        or not math.isclose(
            float(fillet.get("radius_mm", math.nan)),
            radius_mm,
            rel_tol=1.0e-10,
            abs_tol=1.0e-10,
        )
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned the wrong fillet arc.")
    if corner_index is not None:
        corner = json.loads(geometry_records[corner_index])
        if corner.get("kind") != "point" or not bool(corner.get("construction")):
            raise NativeSketchError(
                f"{LABEL} feasibility returned an invalid preserved corner."
            )

    existing_tags = {
        str(json.loads(record).get("tag", "")) for record in before_records
    }
    new_tags = [
        str(json.loads(record).get("tag", ""))
        for record in geometry_records[len(before_records) :]
    ]
    if any(not tag or tag in existing_tags for tag in new_tags) or len(
        set(new_tags)
    ) != len(new_tags):
        raise NativeSketchError(f"{LABEL} feasibility returned invalid new identities.")


def parse_sketch_fillet_diagnostic(
    result: Any,
    spec: SketchFilletSpec,
    before_geometry_records: tuple[str, ...],
) -> SketchFilletPlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    accepted = result["accepted"]
    if type(accepted) is not bool:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid acceptance.")
    degrees = _integer(result["degrees_of_freedom"], "degrees of freedom")
    status = _integer(result["solver_status"], "solver status")
    issues = _issues(result)
    if not accepted:
        raise NativeSketchError(
            f"{LABEL} would introduce a solver issue; nothing changed."
        )
    if degrees < 0 or status != 0 or any(issues):
        raise NativeSketchError(
            f"{LABEL} feasibility returned inconsistent acceptance."
        )

    form = result["form"]
    if form != spec.form:
        raise NativeSketchError(f"{LABEL} feasibility analyzed the wrong target form.")
    inputs = _sequence(result["input_geometry_indices"], "input geometry")
    if len(inputs) != 2 or any(type(value) is not int or value < 0 for value in inputs):
        raise NativeSketchError(f"{LABEL} feasibility returned invalid input geometry.")
    input_indices = (inputs[0], inputs[1])
    if len(set(input_indices)) != 2:
        raise NativeSketchError(
            f"{LABEL} feasibility did not resolve two distinct curves."
        )

    before_count = len(before_geometry_records)
    if any(index >= before_count for index in input_indices):
        raise NativeSketchError(
            f"{LABEL} feasibility resolved geometry outside the preflight Sketch."
        )
    fillet_index = _integer(result["fillet_geometry_index"], "fillet index")
    geometry_count = _integer(result["geometry_count"], "geometry count")
    constraint_count = _integer(result["constraint_count"], "constraint count")
    if fillet_index != before_count or geometry_count not in {
        before_count + 1,
        before_count + 2,
    }:
        raise NativeSketchError(f"{LABEL} feasibility returned unexpected topology.")
    raw_corner = result["corner_geometry_index"]
    if raw_corner is None:
        corner_index = None
        if geometry_count != before_count + 1:
            raise NativeSketchError(
                f"{LABEL} feasibility omitted the preserved corner."
            )
    else:
        corner_index = _integer(raw_corner, "corner index")
        if corner_index != before_count + 1 or geometry_count != before_count + 2:
            raise NativeSketchError(
                f"{LABEL} feasibility returned the wrong corner index."
            )
        if not spec.preserve_corner:
            raise NativeSketchError(
                f"{LABEL} preserved a corner that was not requested."
            )

    radius = result["radius_mm"]
    if isinstance(radius, bool) or not isinstance(radius, (int, float)):
        raise NativeSketchError(f"{LABEL} feasibility returned an invalid radius.")
    radius_mm = float(radius)
    if not math.isfinite(radius_mm) or radius_mm <= 0.0:
        raise NativeSketchError(f"{LABEL} feasibility returned a non-positive radius.")
    if not math.isclose(
        radius_mm,
        spec.requested_size_mm,
        rel_tol=1.0e-10,
        abs_tol=1.0e-10,
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned a different radius.")
    trimmed = result["trimmed"]
    construction = result["construction"]
    if type(trimmed) is not bool or type(construction) is not bool:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid final states.")
    if not trimmed and corner_index is not None:
        raise NativeSketchError(f"{LABEL} cannot preserve a corner without trimming.")

    geometry = _sequence(result["geometry"], "geometry")
    metadata = _sequence(result["geometry_metadata"], "geometry metadata")
    constraints = _sequence(result["constraints"], "constraints")
    if (
        len(geometry) != geometry_count
        or len(metadata) != geometry_count
        or constraint_count < 0
        or len(constraints) != constraint_count
    ):
        raise NativeSketchError(
            f"{LABEL} feasibility returned inconsistent state counts."
        )
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
            f"{LABEL} feasibility returned unserializable state."
        ) from exc
    _validate_geometry_plan(
        geometry_records,
        before_geometry_records,
        input_indices=input_indices,
        fillet_index=fillet_index,
        corner_index=corner_index,
        radius_mm=radius_mm,
        construction=construction,
    )
    return SketchFilletPlan(
        form,
        input_indices,
        fillet_index,
        corner_index,
        radius_mm,
        trimmed,
        construction,
        geometry_records,
        constraint_records,
        degrees,
    )
