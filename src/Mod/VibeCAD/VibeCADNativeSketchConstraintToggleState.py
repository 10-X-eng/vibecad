# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact state for constraint-state toggle operations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from VibeCADNativeSketchConstraintAppend import sketch_solver_issues
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)


MAX_EXPRESSION_COUNT = 1_000_000
MAX_EXPRESSION_PATH_LENGTH = 1_024
MAX_EXPRESSION_LENGTH = 65_536
_INDEX_PATH = re.compile(r"^Constraints\[(\d+)\]$")
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
class SketchExpressionRecord:
    path: str
    expression: str
    constraint_index: int | None
    digest: str


@dataclass(frozen=True, slots=True)
class FrozenSketchConstraintToggleState:
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    solver_issues: tuple[tuple[int, ...], ...]


def sketch_geometry_metadata(records: tuple[str, ...]) -> tuple[str, ...]:
    return canonical_sketch_records(
        {key: record[key] for key in _GEOMETRY_METADATA if key in record}
        for encoded in records
        for record in (json.loads(encoded),)
    )


def constraint_records_by_index(
    records: tuple[str, ...],
) -> dict[int, dict[str, Any]]:
    return {
        int(record["index"]): record
        for encoded in records
        for record in (json.loads(encoded),)
    }


def _constraint_name_indices(constraint_records: tuple[str, ...]) -> dict[str, int]:
    names: dict[str, int] = {}
    duplicates: set[str] = set()
    for encoded in constraint_records:
        record = json.loads(encoded)
        name = str(record.get("name", "") or "")
        if not name:
            continue
        if name in names:
            duplicates.add(name)
        else:
            names[name] = int(record["index"])
    for name in duplicates:
        names.pop(name, None)
    return names


def _constraint_expression_index(path: str, names: dict[str, int]) -> int | None:
    normalized = path.lstrip(".")
    indexed = _INDEX_PATH.fullmatch(normalized)
    if indexed:
        return int(indexed.group(1))
    if normalized.startswith("Constraints."):
        return names.get(normalized[len("Constraints.") :])
    return None


def _expression_digest(path: str, expression: str) -> str:
    encoded = json.dumps(
        [path, expression],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expression_records(
    sketch: Any,
    constraint_records: tuple[str, ...],
    label: str,
) -> tuple[SketchExpressionRecord, ...]:
    try:
        raw_records = list(getattr(sketch, "ExpressionEngine", []) or [])
    except Exception as exc:
        raise NativeSketchError(f"{label} expressions are unavailable.") from exc
    if len(raw_records) > MAX_EXPRESSION_COUNT:
        raise NativeSketchError(f"{label} has too many expressions to verify exactly.")
    names = _constraint_name_indices(constraint_records)
    records = []
    for raw in raw_records:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise NativeSketchError(f"{label} found a malformed expression record.")
        path = str(raw[0])
        expression = str(raw[1])
        if (
            len(path) > MAX_EXPRESSION_PATH_LENGTH
            or len(expression) > MAX_EXPRESSION_LENGTH
        ):
            raise NativeSketchError(f"{label} found an unbounded expression record.")
        index = _constraint_expression_index(path, names)
        if path.lstrip(".").startswith("Constraints") and index is None:
            raise NativeSketchError(
                f"{label} cannot resolve a constraint expression path exactly."
            )
        records.append(
            SketchExpressionRecord(
                path,
                expression,
                index,
                _expression_digest(path, expression),
            )
        )
    records.sort(key=lambda item: (item.path, item.digest))
    return tuple(records)


def sketch_expression_records(
    sketch: Any,
    constraint_records: tuple[str, ...],
    *,
    label: str,
) -> tuple[SketchExpressionRecord, ...]:
    """Read exact expression records for topology-changing Sketch operations."""

    return _expression_records(sketch, constraint_records, label)


def read_sketch_constraint_toggle_state(
    sketch: Any,
    spec: Any,
    *,
    label: str,
) -> FrozenSketchConstraintToggleState:
    constraints = canonical_sketch_records(
        iter_sketch_constraint_records(
            sketch,
            spec.target.expected_constraint_count,
        )
    )
    return FrozenSketchConstraintToggleState(
        canonical_sketch_records(
            iter_sketch_geometry_records(
                sketch,
                spec.target.expected_geometry_count,
            )
        ),
        constraints,
        canonical_sketch_records(iter_sketch_external_geometry_records(sketch)),
        _expression_records(sketch, constraints, label),
        sketch_solver_issues(sketch, label),
    )


def expected_constraint_state_records(
    state: FrozenSketchConstraintToggleState,
    targets: Iterable[Any],
    *,
    record_field: str,
    target_field: str,
) -> tuple[str, ...]:
    by_index = {target.constraint_index: target for target in targets}
    expected = []
    for encoded in state.constraint_records:
        record = json.loads(encoded)
        target = by_index.get(int(record["index"]))
        if target is not None:
            record[record_field] = bool(getattr(target, target_field))
        expected.append(
            json.dumps(
                record,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return tuple(expected)
