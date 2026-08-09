# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict parsing of the detached human Sketch Split result."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

from VibeCADNativeSketchCurvePointDiagnostic import (
    CurvePointDiagnosticState,
    aligned_internal_geometry,
    parse_curve_point_diagnostic,
    record_without_index,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchMutationState import SketchMutationIdentityPlan
from VibeCADNativeSketchSplitTarget import LABEL, SketchSplitSpec


_REPLACEMENT_KINDS = {
    "line": frozenset({"line"}),
    "circular_arc": frozenset({"circular_arc"}),
    "elliptical_arc": frozenset({"elliptical_arc"}),
    "hyperbolic_arc": frozenset({"hyperbolic_arc"}),
    "parabolic_arc": frozenset({"parabolic_arc"}),
    "circle": frozenset({"circular_arc"}),
    "ellipse": frozenset({"elliptical_arc"}),
    "b_spline": frozenset({"b_spline"}),
}


@dataclass(frozen=True, slots=True)
class SketchSplitPlan:
    input_geometry_index: int
    reference_point_mm: tuple[float, float]
    outcome: str
    identity: SketchMutationIdentityPlan
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    degrees_of_freedom: int


def _point(record: dict[str, Any], field: str) -> tuple[float, float, float] | None:
    raw = record.get(field)
    if not isinstance(raw, list) or len(raw) != 3:
        return None
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw
    ):
        return None
    point = tuple(float(value) for value in raw)
    return point if all(math.isfinite(value) for value in point) else None


def _same_point(
    first: tuple[float, float, float] | None,
    second: tuple[float, float, float] | None,
) -> bool:
    return (
        first is not None
        and second is not None
        and all(
            math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-8)
            for left, right in zip(first, second, strict=True)
        )
    )


def _nondegenerate(record: dict[str, Any]) -> bool:
    first = record.get("first_parameter")
    last = record.get("last_parameter")
    if (
        not isinstance(first, bool)
        and isinstance(first, (int, float))
        and not isinstance(last, bool)
        and isinstance(last, (int, float))
    ):
        first_value = float(first)
        last_value = float(last)
        return (
            math.isfinite(first_value)
            and math.isfinite(last_value)
            and last_value - first_value > 1.0e-12
        )
    start = _point(record, "start_mm")
    end = _point(record, "end_mm")
    return (
        start is not None
        and end is not None
        and any(
            not math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-9)
            for left, right in zip(start, end, strict=True)
        )
    )


def _validate_open_piece_chain(
    source: dict[str, Any],
    pieces: list[dict[str, Any]],
) -> None:
    if len(pieces) != 2:
        return
    if not _same_point(_point(pieces[0], "end_mm"), _point(pieces[1], "start_mm")):
        raise NativeSketchError(
            f"{LABEL} feasibility returned disconnected replacement curves."
        )
    source_start = _point(source, "start_mm")
    source_end = _point(source, "end_mm")
    if source_start is not None and not _same_point(
        source_start,
        _point(pieces[0], "start_mm"),
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed the source start point.")
    if source_end is not None and not _same_point(
        source_end,
        _point(pieces[1], "end_mm"),
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed the source end point.")


def _validate_geometry_identity(
    state: CurvePointDiagnosticState,
    before: tuple[str, ...],
    constraint_records: tuple[str, ...],
) -> str:
    target = state.input_geometry_index
    if target not in state.deleted_geometry:
        raise NativeSketchError(
            f"{LABEL} feasibility did not replace the target curve."
        )
    allowed_deleted = {target} | aligned_internal_geometry(
        target,
        before,
        constraint_records,
    )
    if set(state.deleted_geometry) - allowed_deleted:
        raise NativeSketchError(f"{LABEL} feasibility deleted unrelated geometry.")

    before_tags: set[str] = set()
    for old_index, encoded in enumerate(before):
        tag = str(json.loads(encoded).get("tag", ""))
        if not tag or tag in before_tags:
            raise NativeSketchError(
                f"{LABEL} preflight geometry has invalid durable identity."
            )
        before_tags.add(tag)
        if (
            old_index in state.deleted_geometry
            and state.deleted_geometry[old_index] != tag
        ):
            raise NativeSketchError(
                f"{LABEL} feasibility reported the wrong deleted identity."
            )
    for old_index, new_index in state.geometry_mapping.items():
        if record_without_index(before[old_index]) != record_without_index(
            state.geometry_records[new_index]
        ):
            raise NativeSketchError(
                f"{LABEL} feasibility changed unrelated retained geometry."
            )

    source = json.loads(before[target])
    source_is_closed = bool(
        source.get("closed")
        or source.get("periodic")
        or source.get("kind") in {"circle", "ellipse"}
    )
    expected_count = 1 if source_is_closed else 2
    if len(state.created_geometry) != expected_count:
        raise NativeSketchError(
            f"{LABEL} feasibility returned the wrong replacement count."
        )
    replacement_kinds = _REPLACEMENT_KINDS.get(str(source.get("kind")), frozenset())
    created_tags: set[str] = set()
    pieces = []
    for index, tag in sorted(state.created_geometry.items()):
        record = json.loads(state.geometry_records[index])
        if (
            str(record.get("tag", "")) != tag
            or not tag
            or tag in before_tags
            or tag in created_tags
            or record.get("kind") not in replacement_kinds
            or "internal_type" in record
            or bool(record.get("construction")) is not bool(source.get("construction"))
            or bool(record.get("periodic"))
            or not _nondegenerate(record)
        ):
            raise NativeSketchError(
                f"{LABEL} feasibility returned an invalid replacement curve."
            )
        created_tags.add(tag)
        pieces.append(record)
    if not source_is_closed:
        _validate_open_piece_chain(source, pieces)
    return "opened" if source_is_closed else "split"


def parse_sketch_split_diagnostic(
    result: Any,
    spec: SketchSplitSpec,
    before_geometry_records: tuple[str, ...],
    before_constraint_records: tuple[str, ...],
) -> SketchSplitPlan:
    state = parse_curve_point_diagnostic(
        result,
        spec,
        before_geometry_records,
        before_constraint_records,
        label=LABEL,
    )
    outcome = _validate_geometry_identity(
        state,
        before_geometry_records,
        before_constraint_records,
    )
    return SketchSplitPlan(
        state.input_geometry_index,
        state.reference_point_mm,
        outcome,
        state.identity,
        state.geometry_records,
        state.constraint_records,
        state.degrees_of_freedom,
    )
