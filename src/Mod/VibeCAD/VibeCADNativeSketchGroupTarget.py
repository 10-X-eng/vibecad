# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact whole-geometry targets for Native Sketch Constraint Groups."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeSketchConstraintTargets import (
    PreparedSketchConstraintTarget,
    SketchConstraintElement,
    SketchConstraintTargetSpec,
    prepare_sketch_constraint_target,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import MIN_SKETCH_GEOMETRY_LENGTH_MM


LABEL = "Sketch Constraint Group"
MAX_GROUP_MEMBERS = 16
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
    }
)
_INTERNAL_GEOMETRY_PARENTS = frozenset(
    {
        "Part::GeomEllipse",
        "Part::GeomArcOfEllipse",
        "Part::GeomArcOfHyperbola",
        "Part::GeomArcOfParabola",
        "Part::GeomBSplineCurve",
    }
)


@dataclass(frozen=True, slots=True)
class SketchGroupSpec:
    target: SketchConstraintTargetSpec


@dataclass(frozen=True, slots=True)
class ResolvedSketchGroup:
    references: tuple[SketchConstraintElement, ...]
    member_tags: tuple[str, ...]
    member_type_ids: tuple[str, ...]
    handle_start_mm: tuple[float, float]
    handle_end_mm: tuple[float, float]
    cleanup_parent_indices: tuple[int, ...]
    cleanup_candidate_tags: frozenset[str]


def prepare_sketch_group_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchGroupSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    selection = value["selection"]
    if not isinstance(selection, list) or not 2 <= len(selection) <= MAX_GROUP_MEMBERS:
        raise NativeSketchError(
            f"{LABEL} selection must contain two through {MAX_GROUP_MEMBERS} geometries."
        )
    return SketchGroupSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value["expected_external_geometry_count"],
            selection=selection,
            maximum_selection=MAX_GROUP_MEMBERS,
        )
    )


def _constraint_elements(constraint: Any) -> tuple[tuple[int, int], ...]:
    raw = getattr(constraint, "Elements", None)
    if not isinstance(raw, (list, tuple)) or not raw:
        raise NativeSketchError("Sketch Group/Text constraint elements are unavailable.")
    try:
        result = tuple((int(item[0]), int(item[1])) for item in raw)
    except (IndexError, TypeError, ValueError) as exc:
        raise NativeSketchError("Sketch Group/Text constraint elements are malformed.") from exc
    if any(index < 0 or position != 0 for index, position in result):
        raise NativeSketchError("Sketch Group/Text constraint elements are malformed.")
    return result


def _existing_group_roles(sketch: Any) -> tuple[frozenset[int], dict[int, int]]:
    handles: set[int] = set()
    members: dict[int, int] = {}
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError("Sketch Group constraints are unavailable.") from exc
    for constraint in constraints:
        constraint_type = str(getattr(constraint, "Type", "") or "")
        if constraint_type not in {"Group", "Text"}:
            continue
        elements = _constraint_elements(constraint)
        if constraint_type == "Group" and len(elements) < 3:
            raise NativeSketchError("A Sketch Group constraint is malformed.")
        handle = elements[0][0]
        if handle in handles:
            raise NativeSketchError("A Sketch geometry is reused as more than one group handle.")
        handles.add(handle)
        for member, _position in elements[1:]:
            if member in members:
                raise NativeSketchError("A Sketch geometry belongs to more than one group.")
            members[member] = handle
    return frozenset(handles), members


def _tagged_geometry_records(
    target: PreparedSketchConstraintTarget,
) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    records: dict[int, dict[str, Any]] = {}
    tags: dict[int, str] = {}
    observed_tags: set[str] = set()
    for encoded in target.geometry_records:
        record = json.loads(encoded)
        index = int(record["index"])
        tag = str(record.get("tag", "") or "")
        if not tag or tag in observed_tags:
            raise NativeSketchError(
                f"{LABEL} requires unique persistent tags for all Sketch geometry."
            )
        observed_tags.add(tag)
        records[index] = record
        tags[index] = tag
    return records, tags


def _geometry_bounds(geometry: Any, index: int) -> tuple[float, float, float, float]:
    to_shape = getattr(geometry, "toShape", None)
    if not callable(to_shape):
        raise NativeSketchError(f"{LABEL} geometry {index} has no finite shape bounds.")
    try:
        bounds = to_shape().BoundBox
        values = tuple(
            float(getattr(bounds, attribute))
            for attribute in ("XMin", "YMin", "XMax", "YMax")
        )
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} geometry {index} bounds are unavailable."
        ) from exc
    if not all(math.isfinite(value) for value in values):
        raise NativeSketchError(f"{LABEL} geometry {index} bounds are not finite.")
    return values


def _handle_points(
    sketch: Any,
    references: tuple[SketchConstraintElement, ...],
) -> tuple[tuple[float, float], tuple[float, float]]:
    bounds = [
        _geometry_bounds(
            sketch_constraint_geometry(sketch, element.geometry_index),
            element.geometry_index,
        )
        for element in references
    ]
    minimum_x = min(value[0] for value in bounds)
    minimum_y = min(value[1] for value in bounds)
    maximum_y = max(value[3] for value in bounds)
    if maximum_y - minimum_y <= MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError(
            f"{LABEL} members produce a zero-height construction handle."
        )
    return (minimum_x, minimum_y), (minimum_x, maximum_y)


def _cleanup_candidates(
    sketch: Any,
    selected: frozenset[int],
    tags: Mapping[int, str],
) -> frozenset[str]:
    result = set()
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError("Sketch constraints are unavailable.") from exc
    for constraint in constraints:
        if str(getattr(constraint, "Type", "") or "") != "InternalAlignment":
            continue
        try:
            child = int(constraint.First)
            parent = int(constraint.Second)
        except Exception as exc:
            raise NativeSketchError("A Sketch InternalAlignment constraint is malformed.") from exc
        if parent in selected:
            tag = tags.get(child)
            if tag is None:
                raise NativeSketchError(
                    "A selected Sketch geometry has malformed internal alignment."
                )
            result.add(tag)
    return frozenset(result)


def resolve_sketch_group(
    sketch: Any,
    target: PreparedSketchConstraintTarget,
    spec: SketchGroupSpec,
) -> ResolvedSketchGroup:
    if not isinstance(spec, SketchGroupSpec):
        raise TypeError("spec must be a SketchGroupSpec")
    if not isinstance(target, PreparedSketchConstraintTarget):
        raise TypeError("target must be a PreparedSketchConstraintTarget")
    records, tags = _tagged_geometry_records(target)
    handles, existing_members = _existing_group_roles(sketch)
    type_ids = []
    member_tags = []
    cleanup_parents = []
    for element in spec.target.selection:
        index = element.geometry_index
        if element.position != "whole" or index < 0:
            raise NativeSketchError(
                f"{LABEL} members must be exact whole internal geometries."
            )
        if index in handles:
            raise NativeSketchError(
                f"{LABEL} cannot nest existing group handle geometry {index}."
            )
        if index in existing_members:
            raise NativeSketchError(
                f"{LABEL} geometry {index} already belongs to group handle "
                f"{existing_members[index]}."
            )
        record = records[index]
        type_id = str(record.get("type_id", "") or "")
        if not type_id.startswith("Part::Geom"):
            raise NativeSketchError(f"{LABEL} geometry {index} is unsupported.")
        type_ids.append(type_id)
        member_tags.append(tags[index])
        if type_id in _INTERNAL_GEOMETRY_PARENTS:
            cleanup_parents.append(index)
    references = spec.target.selection
    selected = frozenset(element.geometry_index for element in references)
    handle_start, handle_end = _handle_points(sketch, references)
    return ResolvedSketchGroup(
        references,
        tuple(member_tags),
        tuple(type_ids),
        handle_start,
        handle_end,
        tuple(sorted(cleanup_parents, reverse=True)),
        _cleanup_candidates(sketch, selected, tags),
    )
