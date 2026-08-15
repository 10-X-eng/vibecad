# SPDX-License-Identifier: LGPL-2.1-or-later

"""Persistent-tag postconditions for Native Sketch Constraint Groups."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from VibeCADNativeSketchConstraintAppend import sketch_solver_issues
from VibeCADNativeSketchConstraintTargets import PreparedSketchConstraintTarget
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchGeometryValues import same_sketch_point
from VibeCADNativeSketchGroupTarget import LABEL, ResolvedSketchGroup
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchState import (
    MAX_CONSTRAINT_REFERENCES,
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)


def _decoded(records: Iterable[str]) -> list[dict[str, Any]]:
    return [json.loads(record) for record in records]


def _records_by_tag(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in records:
        record = dict(raw)
        tag = str(record.get("tag", "") or "")
        if not tag or tag in result:
            raise NativeSketchError(f"{LABEL} geometry tags are missing or duplicated.")
        result[tag] = record
    return result


def _tags_by_index(records: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    return {int(record["index"]): str(record["tag"]) for record in records}


def _normalized_geometry(record: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result.pop("index", None)
    return result


def _normalize_constraint(
    record: Mapping[str, Any],
    tags_by_index: Mapping[int, str],
) -> dict[str, Any]:
    result = json.loads(json.dumps(record, ensure_ascii=True))
    result.pop("index", None)
    for key in ("references", "elements"):
        for reference in result.get(key, []):
            index = int(reference["geometry_index"])
            if index >= 0:
                tag = tags_by_index.get(index)
                if tag is None:
                    raise NativeSketchError(
                        f"{LABEL} constraint references unavailable geometry {index}."
                    )
                reference["geometry_index"] = tag
    return result


def _internal_tags(constraint: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        str(reference["geometry_index"])
        for key in ("references", "elements")
        for reference in constraint.get(key, [])
        if isinstance(reference.get("geometry_index"), str)
    )


def _expected_group_constraint(
    handle_tag: str,
    resolved: ResolvedSketchGroup,
) -> dict[str, Any]:
    elements = (handle_tag, *resolved.member_tags)
    result = {
        "type": "Group",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": slot, "geometry_index": tag}
            for slot, tag in enumerate(elements[:3], start=1)
        ],
        "element_count": len(elements),
        "elements": [
            {"geometry_index": tag, "position": 0}
            for tag in elements[:MAX_CONSTRAINT_REFERENCES]
        ],
    }
    if len(elements) > MAX_CONSTRAINT_REFERENCES:
        result["elements_truncated"] = True
    return result


def _verify_raw_group_elements(
    sketch: Any,
    constraint_index: int,
    current_index_tags: Mapping[int, str],
    handle_tag: str,
    resolved: ResolvedSketchGroup,
) -> None:
    try:
        constraint = sketch.Constraints[constraint_index]
        raw_elements = constraint.Elements
        elements = tuple(
            (current_index_tags[int(item[0])], int(item[1])) for item in raw_elements
        )
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise NativeSketchError(f"{LABEL} full element list is unavailable.") from exc
    expected = tuple((tag, 0) for tag in (handle_tag, *resolved.member_tags))
    if elements != expected:
        raise NativeSketchError(f"{LABEL} full element list changed.")


def _constraint_mismatch(
    expected: list[dict[str, Any]],
    observed: list[dict[str, Any]],
) -> str:
    common = min(len(expected), len(observed))
    mismatch = next(
        (index for index in range(common) if expected[index] != observed[index]),
        common,
    )
    expected_record = expected[mismatch] if mismatch < len(expected) else None
    observed_record = observed[mismatch] if mismatch < len(observed) else None
    differing_keys = sorted(
        key
        for key in set(expected_record or {}) | set(observed_record or {})
        if (expected_record or {}).get(key) != (observed_record or {}).get(key)
    )
    expected_elements = (expected_record or {}).get("elements", [])
    observed_elements = (observed_record or {}).get("elements", [])
    element_common = min(len(expected_elements), len(observed_elements))
    element_mismatch = next(
        (
            index
            for index in range(element_common)
            if expected_elements[index] != observed_elements[index]
        ),
        element_common,
    )
    detail = json.dumps(
        {
            "expected_count": len(expected),
            "observed_count": len(observed),
            "first_mismatch_index": mismatch,
            "differing_keys": differing_keys,
            "expected_type": (expected_record or {}).get("type"),
            "observed_type": (observed_record or {}).get("type"),
            "expected_references": (expected_record or {}).get("references"),
            "observed_references": (observed_record or {}).get("references"),
            "expected_element_count": len(expected_elements),
            "observed_element_count": len(observed_elements),
            "first_element_mismatch_index": element_mismatch,
            "expected_element": (
                expected_elements[element_mismatch]
                if element_mismatch < len(expected_elements)
                else None
            ),
            "observed_element": (
                observed_elements[element_mismatch]
                if element_mismatch < len(observed_elements)
                else None
            ),
            "expected_flags": {
                key: (expected_record or {}).get(key)
                for key in ("driving", "active", "virtual")
            },
            "observed_flags": {
                key: (observed_record or {}).get(key)
                for key in ("driving", "active", "virtual")
            },
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return detail[:1_200]


def _verify_handle(
    record: Mapping[str, Any],
    resolved: ResolvedSketchGroup,
) -> None:
    if (
        record.get("type_id") != "Part::GeomLineSegment"
        or record.get("kind") != "line"
        or record.get("construction") is not True
        or bool(record.get("blocked"))
        or record.get("internal_type")
        or not same_sketch_point(record.get("start_mm"), resolved.handle_start_mm)
        or not same_sketch_point(record.get("end_mm"), resolved.handle_end_mm)
    ):
        raise NativeSketchError(f"{LABEL} construction handle changed.")


def verify_sketch_group_state(
    sketch: Any,
    target: PreparedSketchConstraintTarget,
    resolved: ResolvedSketchGroup,
    *,
    handle_tag: str,
) -> dict[str, Any]:
    before_geometry = _decoded(target.geometry_records)
    before_constraints = _decoded(target.constraint_records)
    current_geometry = list(iter_sketch_geometry_records(sketch))
    current_constraints = list(iter_sketch_constraint_records(sketch))
    current_external = canonical_sketch_records(
        iter_sketch_external_geometry_records(sketch)
    )
    if current_external != target.external_geometry_records:
        raise NativeSketchError(f"{LABEL} changed external geometry.")

    before_by_tag = _records_by_tag(before_geometry)
    current_by_tag = _records_by_tag(current_geometry)
    before_tags = frozenset(before_by_tag)
    current_tags = frozenset(current_by_tag)
    new_tags = current_tags - before_tags
    deleted_tags = before_tags - current_tags
    if new_tags != {handle_tag}:
        raise NativeSketchError(f"{LABEL} created unexpected geometry.")
    if not deleted_tags.issubset(resolved.cleanup_candidate_tags):
        raise NativeSketchError(f"{LABEL} deleted unrelated Sketch geometry.")
    for tag in before_tags & current_tags:
        if _normalized_geometry(before_by_tag[tag]) != _normalized_geometry(
            current_by_tag[tag]
        ):
            raise NativeSketchError(f"{LABEL} changed existing geometry {tag}.")
    _verify_handle(current_by_tag[handle_tag], resolved)

    before_index_tags = _tags_by_index(before_geometry)
    current_index_tags = _tags_by_index(current_geometry)
    normalized_before = [
        _normalize_constraint(record, before_index_tags)
        for record in before_constraints
    ]
    surviving_before = [
        record
        for record in normalized_before
        if not (_internal_tags(record) & deleted_tags)
    ]
    normalized_current = [
        _normalize_constraint(record, current_index_tags)
        for record in current_constraints
    ]
    expected_group = _expected_group_constraint(handle_tag, resolved)
    expected_constraints = [*surviving_before, expected_group]
    if normalized_current != expected_constraints:
        raise NativeSketchError(
            f"{LABEL} changed constraints beyond exact internal cleanup and append: "
            f"{_constraint_mismatch(expected_constraints, normalized_current)}"
        )
    group_index = len(surviving_before)
    _verify_raw_group_elements(
        sketch,
        group_index,
        current_index_tags,
        handle_tag,
        resolved,
    )
    if any(sketch_solver_issues(sketch, LABEL)):
        raise NativeSketchError(f"{LABEL} introduced a solver issue.")

    handle = current_by_tag[handle_tag]
    member_records = [current_by_tag[tag] for tag in resolved.member_tags]
    member_tags = frozenset(resolved.member_tags)
    ignored_constraints = sum(
        bool(_internal_tags(record) & member_tags) for record in surviving_before
    )
    deleted_constraint_count = len(normalized_before) - len(surviving_before)
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_group",
            "group_constraint": {
                "index": group_index,
                "type": "Group",
                "handle_index": int(handle["index"]),
                "member_indices": [int(record["index"]) for record in member_records],
                "member_count": len(member_records),
                "ignored_existing_constraint_count": ignored_constraints,
            },
            "handle": {
                key: handle[key]
                for key in (
                    "index",
                    "geometry_id",
                    "tag",
                    "type_id",
                    "kind",
                    "construction",
                    "blocked",
                    "start_mm",
                    "end_mm",
                )
                if key in handle
            },
            "members": [
                {
                    key: record[key]
                    for key in (
                        "index",
                        "geometry_id",
                        "tag",
                        "type_id",
                        "kind",
                        "construction",
                        "blocked",
                    )
                    if key in record
                }
                for record in member_records
            ],
            "internal_cleanup": {
                "deleted_geometry_count": len(deleted_tags),
                "deleted_constraint_count": deleted_constraint_count,
                "deleted_geometry_tags": sorted(deleted_tags),
            },
        },
    )
