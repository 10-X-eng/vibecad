# SPDX-License-Identifier: LGPL-2.1-or-later

"""Identity, expression, and canonical-state checks for Sketch mutations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from VibeCADNativeSketchConstraintToggleState import SketchExpressionRecord
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records


_INDEX_PATH = re.compile(r"^(\.?Constraints)\[(\d+)\]$")


@dataclass(frozen=True, slots=True)
class CollectionIdentityPlan:
    old_to_new: tuple[tuple[int, int], ...]
    deleted_indices: tuple[int, ...]
    created_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SketchMutationIdentityPlan:
    geometry: CollectionIdentityPlan
    constraints: CollectionIdentityPlan


def collection_identity_plan(
    mapping: Mapping[int, int],
    deleted: Mapping[int, str],
    created: Mapping[int, str],
) -> CollectionIdentityPlan:
    return CollectionIdentityPlan(
        tuple(sorted(mapping.items())),
        tuple(sorted(deleted)),
        tuple(sorted(created)),
    )


def grouped_geometry_members(sketch: Any, *, label: str) -> set[int]:
    members: set[int] = set()
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{label} constraints are unavailable.") from exc
    for constraint in constraints:
        if str(getattr(constraint, "Type", "")) not in {"Group", "Text"}:
            continue
        elements = getattr(constraint, "Elements", None)
        if not isinstance(elements, (list, tuple)):
            raise NativeSketchError(f"{label} found malformed grouped geometry.")
        for raw in elements[1:]:
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                raise NativeSketchError(f"{label} found malformed grouped geometry.")
            geometry_index = raw[0]
            if type(geometry_index) is not int:
                raise NativeSketchError(f"{label} found malformed grouped geometry.")
            if geometry_index >= 0:
                members.add(geometry_index)
    return members


def collection_index_map(
    receipt: Any,
    collection: str,
    before_count: int,
    after_count: int,
    *,
    label: str,
) -> tuple[dict[int, int], dict[int, str], dict[int, str]]:
    if not isinstance(receipt, Mapping) or set(receipt) != {"geometry", "constraints"}:
        raise NativeSketchError(f"{label} returned an invalid mutation receipt.")
    value = receipt[collection]
    if (
        not isinstance(value, Mapping)
        or set(value) != {"identity", "old_to_new", "deleted", "created"}
        or value["identity"] != "native_tag"
    ):
        raise NativeSketchError(
            f"{label} returned invalid {collection} identity mapping."
        )
    raw = value["old_to_new"]
    if not isinstance(raw, Mapping):
        raise NativeSketchError(f"{label} returned invalid {collection} index mapping.")
    result: dict[int, int] = {}
    for key, mapped in raw.items():
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal():
            raise NativeSketchError(
                f"{label} returned an invalid old {collection} index."
            )
        old = int(key)
        if (
            key != str(old)
            or type(mapped) is not int
            or not 0 <= old < before_count
            or not 0 <= mapped < after_count
            or mapped in result.values()
        ):
            raise NativeSketchError(
                f"{label} returned an invalid {collection} mapping."
            )
        result[old] = mapped

    def indexed_tags(raw_items: Any, state: str, limit: int) -> dict[int, str]:
        if not isinstance(raw_items, list):
            raise NativeSketchError(
                f"{label} returned invalid {state} {collection} values."
            )
        items: dict[int, str] = {}
        tags: set[str] = set()
        for item in raw_items:
            if not isinstance(item, Mapping) or set(item) != {"index", "tag"}:
                raise NativeSketchError(
                    f"{label} returned invalid {state} {collection} details."
                )
            index = item["index"]
            tag = item["tag"]
            if (
                type(index) is not int
                or not 0 <= index < limit
                or index in items
                or not isinstance(tag, str)
                or not tag
                or tag in tags
            ):
                raise NativeSketchError(
                    f"{label} returned an invalid {state} {collection} identity."
                )
            items[index] = tag
            tags.add(tag)
        return items

    deleted = indexed_tags(value["deleted"], "deleted", before_count)
    created = indexed_tags(value["created"], "created", after_count)
    if set(deleted.values()) & set(created.values()):
        raise NativeSketchError(f"{label} reused a deleted {collection} identity.")
    if (
        set(result) | set(deleted) != set(range(before_count))
        or set(result) & set(deleted)
        or set(result.values()) | set(created) != set(range(after_count))
        or set(result.values()) & set(created)
    ):
        raise NativeSketchError(
            f"{label} did not account for every prior and final {collection}."
        )
    return result, deleted, created


def _expression_digest(path: str, expression: str) -> str:
    encoded = json.dumps(
        [path, expression], ensure_ascii=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def expected_expression_records(
    records: tuple[SketchExpressionRecord, ...],
    mapping: Mapping[int, int],
) -> tuple[SketchExpressionRecord, ...]:
    result = []
    for record in records:
        if record.constraint_index is None:
            result.append(record)
            continue
        mapped = mapping.get(record.constraint_index)
        if mapped is None:
            continue
        path = record.path
        match = _INDEX_PATH.fullmatch(path)
        if match:
            path = f"{match.group(1)}[{mapped}]"
        result.append(
            SketchExpressionRecord(
                path,
                record.expression,
                mapped,
                _expression_digest(path, record.expression),
            )
        )
    return tuple(sorted(result, key=lambda item: (item.path, item.digest)))


def normalized_constraint_records(records: tuple[str, ...]) -> tuple[str, ...]:
    normalized = []
    for encoded in records:
        record = json.loads(encoded)
        if not bool(record.get("driving", False)):
            record.pop("value", None)
        normalized.append(record)
    return canonical_sketch_records(normalized)


def geometry_metadata_records(records: tuple[str, ...]) -> tuple[str, ...]:
    keys = {
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "internal_type",
        "layer_id",
    }
    return canonical_sketch_records(
        {key: record[key] for key in keys if key in record}
        for encoded in records
        for record in (json.loads(encoded),)
    )


def geometry_records_without_tags(records: tuple[str, ...]) -> tuple[str, ...]:
    return canonical_sketch_records(
        {key: value for key, value in record.items() if key != "tag"}
        for encoded in records
        for record in (json.loads(encoded),)
    )
