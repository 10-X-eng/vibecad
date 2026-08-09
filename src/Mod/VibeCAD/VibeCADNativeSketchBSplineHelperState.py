# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared identity, helper, and record checks for exact B-spline mutations."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError


LABEL = "B-spline mutation"


METADATA_FIELDS = frozenset(
    {
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "internal_type",
        "layer_id",
    }
)


@dataclass(frozen=True, slots=True)
class HelperAlignment:
    geometry_index: int
    geometry_tag: str
    internal_type: str
    alignment_index: int
    constraint_index: int
    constraint_tag: str


def decode_record(encoded: str, state: str) -> dict[str, Any]:
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise NativeSketchError(f"{LABEL} found invalid {state} state.") from exc
    if not isinstance(value, dict):
        raise NativeSketchError(f"{LABEL} found invalid {state} state.")
    return value


def indexed_records(records: tuple[str, ...], state: str) -> dict[int, dict[str, Any]]:
    result = {}
    for encoded in records:
        record = decode_record(encoded, state)
        index = record.get("index", record.get("geometry_index"))
        if type(index) is not int or index in result:
            raise NativeSketchError(f"{LABEL} found invalid {state} indices.")
        result[index] = record
    return result


def references(record: Mapping[str, Any]) -> dict[int, tuple[int, int]]:
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


def alignment_values(
    constraints: tuple[Any, ...],
    geometry: Mapping[int, Mapping[str, Any]],
    geometry_tags: tuple[str, ...],
    constraint_tags: tuple[str, ...],
    root: int,
) -> tuple[HelperAlignment, ...]:
    result = []
    used_helpers = set()
    used_roles = set()
    for index, constraint in enumerate(constraints):
        if str(getattr(constraint, "Type", "")) != "InternalAlignment":
            continue
        try:
            owner = int(constraint.Second)
        except (AttributeError, TypeError, ValueError, OverflowError):
            raise NativeSketchError(f"{LABEL} found malformed helper alignment.")
        if owner != root:
            continue
        try:
            helper = int(constraint.First)
            alignment_index = int(constraint.InternalAlignmentIndex)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise NativeSketchError(
                f"{LABEL} found malformed helper alignment."
            ) from exc
        helper_record = geometry.get(helper, {})
        internal_type = helper_record.get("internal_type")
        role = (internal_type, alignment_index)
        if (
            helper <= root
            or helper >= len(geometry_tags)
            or index >= len(constraint_tags)
            or helper in used_helpers
            or role in used_roles
            or internal_type not in {"BSplineControlPoint", "BSplineKnotPoint"}
            or alignment_index < 0
            or helper_record.get("construction") is not True
            or helper_record.get("kind") not in {"circle", "point"}
        ):
            raise NativeSketchError(f"{LABEL} found malformed helper alignment.")
        used_helpers.add(helper)
        used_roles.add(role)
        result.append(
            HelperAlignment(
                helper,
                geometry_tags[helper],
                internal_type,
                alignment_index,
                index,
                constraint_tags[index],
            )
        )
    return tuple(sorted(result, key=lambda item: item.geometry_index))


def stable_alignment_values(
    helpers: tuple[HelperAlignment, ...],
    created_geometry: set[int],
    created_constraints: set[int],
) -> tuple[HelperAlignment, ...]:
    """Remove only commit-time UUIDs from newly created helper identities."""

    return tuple(
        HelperAlignment(
            item.geometry_index,
            "" if item.geometry_index in created_geometry else item.geometry_tag,
            item.internal_type,
            item.alignment_index,
            item.constraint_index,
            "" if item.constraint_index in created_constraints else item.constraint_tag,
        )
        for item in helpers
    )


def require_safe_existing_helpers(
    state: Any, helpers: tuple[HelperAlignment, ...]
) -> None:
    helper_indices = {item.geometry_index for item in helpers}
    alignment_constraints = {item.constraint_index for item in helpers}
    helper_constraints = set()
    for index, encoded in enumerate(state.constraint_records):
        record = decode_record(encoded, "constraint")
        referenced = {geometry for geometry, _position in references(record).values()}
        if not referenced & helper_indices:
            continue
        helper_constraints.add(index)
        if index in alignment_constraints:
            continue
        if (
            record.get("type") not in {"Weight", "Equal"}
            or not referenced
            or not referenced <= helper_indices
            or record.get("name")
        ):
            raise NativeSketchError(
                f"{LABEL} will not discard custom constraints on spline helpers."
            )
    if any(
        item.constraint_index in helper_constraints for item in state.expression_records
    ):
        raise NativeSketchError(
            f"{LABEL} will not discard expressions on spline helpers."
        )


def verify_helper_reconciliation(
    *,
    label: str,
    root: int,
    before: Mapping[int, Mapping[str, Any]],
    after: Mapping[int, Mapping[str, Any]],
    old_helpers: tuple[HelperAlignment, ...],
    plan: Any,
    before_constraint_records: tuple[str, ...],
    result: Mapping[str, Any],
    control_positions: tuple[tuple[float, float, float], ...],
    knot_positions: tuple[tuple[float, float, float], ...],
    retained_count: int,
    deleted_count: int,
    exposed_count: int,
    maximum_created_constraints: int,
) -> tuple[HelperAlignment, ...]:
    """Prove that a B-spline mutation changed only generated helper state."""
    geometry_map = dict(plan.identity.geometry.old_to_new)
    deleted_geometry = set(plan.identity.geometry.deleted_indices)
    created_geometry = set(plan.identity.geometry.created_indices)
    created_constraints = set(plan.identity.constraints.created_indices)
    old_helper_indices = {item.geometry_index for item in old_helpers}
    if (
        geometry_map.get(root) != root
        or deleted_geometry - old_helper_indices
        or set(range(len(before))) - deleted_geometry != set(geometry_map)
        or retained_count != len(old_helper_indices - deleted_geometry)
        or deleted_count != len(deleted_geometry)
        or exposed_count != len(created_geometry)
    ):
        raise NativeSketchError(
            f"{label} changed the wrong durable geometry identities."
        )
    for old_index, new_index in geometry_map.items():
        if old_index == root:
            continue
        if old_index in old_helper_indices:
            if geometry_metadata(before[old_index]) != geometry_metadata(
                after[new_index]
            ):
                raise NativeSketchError(f"{label} changed retained helper metadata.")
        elif record_without_index(before[old_index]) != record_without_index(
            after[new_index]
        ):
            raise NativeSketchError(f"{label} changed unrelated geometry.")
    for index in created_geometry:
        record = after[index]
        if (
            record.get("kind") not in {"circle", "point"}
            or record.get("construction") is not True
            or record.get("internal_type")
            not in {"BSplineControlPoint", "BSplineKnotPoint"}
        ):
            raise NativeSketchError(f"{label} created invalid spline helpers.")

    geometry_tags = tuple(result["geometry_tags"])
    constraint_tags = tuple(result["constraint_tags"])
    constraints = tuple(result["constraints"])
    helpers = alignment_values(
        constraints,
        after,
        geometry_tags,
        constraint_tags,
        root,
    )
    final_helper_indices = {
        geometry_map[index] for index in old_helper_indices - deleted_geometry
    } | created_geometry
    if {item.geometry_index for item in helpers} != final_helper_indices:
        raise NativeSketchError(f"{label} exposed the wrong spline helper set.")
    control_indices = set()
    knot_indices = set()
    for helper in helpers:
        record = after[helper.geometry_index]
        positions = (
            control_positions
            if helper.internal_type == "BSplineControlPoint"
            else knot_positions
        )
        if helper.alignment_index >= len(positions):
            raise NativeSketchError(f"{label} returned out-of-range helper alignment.")
        if (
            math.dist(helper_position(record), positions[helper.alignment_index])
            > 1.0e-8
        ):
            raise NativeSketchError(f"{label} helpers do not represent the B-spline.")
        if helper.internal_type == "BSplineControlPoint":
            control_indices.add(helper.alignment_index)
        else:
            knot_indices.add(helper.alignment_index)
    if control_indices != set(range(len(control_positions))) or knot_indices != set(
        range(len(knot_positions))
    ):
        raise NativeSketchError(f"{label} did not align every spline helper.")

    constraint_map = dict(plan.identity.constraints.old_to_new)
    deleted_constraints = set(plan.identity.constraints.deleted_indices)
    if len(created_constraints) > maximum_created_constraints:
        raise NativeSketchError(f"{label} created too many constraints.")
    old_constraint_records = {
        index: decode_record(encoded, "constraint")
        for index, encoded in enumerate(before_constraint_records)
    }
    old_helper_constraints = {
        index
        for index, record in old_constraint_records.items()
        if {geometry for geometry, _position in references(record).values()}
        & deleted_geometry
    }
    if deleted_constraints - old_helper_constraints:
        raise NativeSketchError(f"{label} deleted unrelated constraints.")
    for old_index, new_index in constraint_map.items():
        expected = remap_constraint(
            old_constraint_records[old_index], new_index, geometry_map
        )
        actual = decode_record(plan.constraint_records[new_index], "constraint")
        if expected != actual:
            raise NativeSketchError(f"{label} changed a surviving constraint.")
    for index in created_constraints:
        record = decode_record(plan.constraint_records[index], "constraint")
        referenced_geometry = {
            geometry for geometry, _position in references(record).values()
        }
        if record.get("type") == "InternalAlignment":
            if not referenced_geometry <= final_helper_indices | {root}:
                raise NativeSketchError(f"{label} created wrong internal alignment.")
        elif (
            record.get("type") not in {"Weight", "Equal"}
            or not referenced_geometry <= final_helper_indices
        ):
            raise NativeSketchError(f"{label} created unrelated constraints.")
    return stable_alignment_values(helpers, created_geometry, created_constraints)


def geometry_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in METADATA_FIELDS if key in record}


def record_without_index(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "index"}


def remap_constraint(
    record: Mapping[str, Any],
    new_index: int,
    geometry_map: Mapping[int, int],
) -> dict[str, Any]:
    result = json.loads(json.dumps(record))
    result["index"] = new_index
    for field in ("references", "elements"):
        for reference in result.get(field, []):
            geometry = reference.get("geometry_index")
            if type(geometry) is int and geometry >= 0:
                mapped = geometry_map.get(geometry)
                if mapped is None:
                    raise NativeSketchError(
                        f"{LABEL} retained a constraint on deleted geometry."
                    )
                reference["geometry_index"] = mapped
    return result


def helper_position(record: Mapping[str, Any]) -> tuple[float, float, float]:
    field = "center_mm" if record.get("kind") == "circle" else "position_mm"
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
