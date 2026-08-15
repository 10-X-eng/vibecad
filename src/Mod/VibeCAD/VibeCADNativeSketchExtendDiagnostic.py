# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict parsing of the detached human Sketch Extend result."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeSketchCurvePointDiagnostic import (
    CurvePointDiagnosticState,
    parse_curve_point_diagnostic,
    record_without_index,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchMutationState import SketchMutationIdentityPlan
from VibeCADNativeSketchExtendTarget import LABEL, SketchExtendSpec


_DYNAMIC_GEOMETRY_FIELDS = frozenset(
    {"index", "start_mm", "end_mm", "first_parameter", "last_parameter"}
)


@dataclass(frozen=True, slots=True)
class SketchExtendPlan:
    input_geometry_index: int
    target_point_mm: tuple[float, float]
    endpoint: str
    extension_increment: float
    outcome: str
    new_endpoint_mm: tuple[float, float]
    changed_geometry_indices: tuple[int, ...]
    identity: SketchMutationIdentityPlan
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    degrees_of_freedom: int


def _point(record: Mapping[str, Any], field: str) -> tuple[float, float, float]:
    raw = record.get(field)
    if (
        not isinstance(raw, list)
        or len(raw) != 3
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in raw
        )
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    result = tuple(float(value) for value in raw)
    if not all(math.isfinite(value) for value in result):
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return result


def _same_point(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> bool:
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-8)
        for left, right in zip(first, second, strict=True)
    )


def _static_geometry(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in _DYNAMIC_GEOMETRY_FIELDS
    }


def _constraint_component(
    target: int,
    count: int,
    constraint_records: tuple[str, ...],
) -> set[int]:
    adjacency = {index: set() for index in range(count)}
    for encoded in constraint_records:
        record = json.loads(encoded)
        references = {
            int(reference["geometry_index"])
            for reference in record.get("references", [])
            if isinstance(reference, Mapping)
            and type(reference.get("geometry_index")) is int
            and 0 <= int(reference["geometry_index"]) < count
        }
        for index in references:
            adjacency[index].update(references - {index})
    visited = {target}
    pending = [target]
    while pending:
        current = pending.pop()
        new = adjacency[current] - visited
        visited.update(new)
        pending.extend(new)
    return visited


def _validate_identity_and_scope(
    state: CurvePointDiagnosticState,
    before_geometry: tuple[str, ...],
    before_constraints: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any], tuple[int, ...]]:
    geometry_count = len(before_geometry)
    constraint_count = len(before_constraints)
    expected_geometry_map = {index: index for index in range(geometry_count)}
    expected_constraint_map = {index: index for index in range(constraint_count)}
    if (
        state.geometry_mapping != expected_geometry_map
        or state.deleted_geometry
        or state.created_geometry
        or state.constraint_mapping != expected_constraint_map
        or state.deleted_constraints
        or state.created_constraints
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed collection identity.")
    if (
        len(state.geometry_records) != geometry_count
        or len(state.constraint_records) != constraint_count
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed collection counts.")
    if any(
        record_without_index(before) != record_without_index(after)
        for before, after in zip(
            before_constraints,
            state.constraint_records,
            strict=True,
        )
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed constraints.")

    changed = tuple(
        index
        for index, (before, after) in enumerate(
            zip(before_geometry, state.geometry_records, strict=True)
        )
        if record_without_index(before) != record_without_index(after)
    )
    target = state.input_geometry_index
    allowed = _constraint_component(target, geometry_count, before_constraints)
    if target not in changed or set(changed) - allowed:
        raise NativeSketchError(
            f"{LABEL} feasibility changed geometry outside the target constraint component."
        )
    return (
        json.loads(before_geometry[target]),
        json.loads(state.geometry_records[target]),
        changed,
    )


def _project_line_target(
    before: Mapping[str, Any],
    target_point: tuple[float, float],
) -> tuple[float, float, float]:
    start = _point(before, "start_mm")
    end = _point(before, "end_mm")
    direction = tuple(right - left for left, right in zip(start, end, strict=True))
    denominator = sum(value * value for value in direction)
    if denominator <= 1.0e-20:
        raise NativeSketchError(f"{LABEL} requires a nondegenerate line segment.")
    requested = (target_point[0], target_point[1], 0.0)
    parameter = (
        sum(
            (value - origin) * axis
            for value, origin, axis in zip(requested, start, direction, strict=True)
        )
        / denominator
    )
    return tuple(
        origin + parameter * axis for origin, axis in zip(start, direction, strict=True)
    )


def _project_arc_target(
    before: Mapping[str, Any],
    target_point: tuple[float, float],
) -> tuple[float, float, float]:
    center = _point(before, "center_mm")
    radius = before.get("radius_mm")
    if (
        isinstance(radius, bool)
        or not isinstance(radius, (int, float))
        or not math.isfinite(float(radius))
        or float(radius) <= 0.0
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned an invalid arc radius.")
    offset = (target_point[0] - center[0], target_point[1] - center[1])
    distance = math.hypot(*offset)
    if distance <= 1.0e-10:
        raise NativeSketchError(f"{LABEL} target point cannot be the arc center.")
    scale = float(radius) / distance
    return center[0] + offset[0] * scale, center[1] + offset[1] * scale, center[2]


def _span(record: Mapping[str, Any], kind: str) -> float:
    if kind == "line":
        start = _point(record, "start_mm")
        end = _point(record, "end_mm")
        return math.sqrt(
            sum((right - left) ** 2 for left, right in zip(start, end, strict=True))
        )
    first = record.get("first_parameter")
    last = record.get("last_parameter")
    if (
        isinstance(first, bool)
        or not isinstance(first, (int, float))
        or isinstance(last, bool)
        or not isinstance(last, (int, float))
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned an invalid arc range.")
    return float(last) - float(first)


def _validate_target_geometry(
    before: dict[str, Any],
    after: dict[str, Any],
    spec: SketchExtendSpec,
    increment: float,
) -> tuple[str, tuple[float, float]]:
    kind = before.get("kind")
    if kind not in {"line", "circular_arc"} or after.get("kind") != kind:
        raise NativeSketchError(f"{LABEL} feasibility changed the target curve kind.")
    if _static_geometry(before) != _static_geometry(after):
        raise NativeSketchError(f"{LABEL} feasibility changed fixed curve properties.")

    selected_field = "start_mm" if spec.endpoint == "start" else "end_mm"
    other_field = "end_mm" if spec.endpoint == "start" else "start_mm"
    old_selected = _point(before, selected_field)
    new_selected = _point(after, selected_field)
    if _same_point(old_selected, new_selected):
        raise NativeSketchError(
            f"{LABEL} feasibility did not move the selected endpoint."
        )
    if not _same_point(_point(before, other_field), _point(after, other_field)):
        raise NativeSketchError(f"{LABEL} feasibility moved the opposite endpoint.")

    target_point = spec.selection.reference_point_mm
    expected = (
        _project_line_target(before, target_point)
        if kind == "line"
        else _project_arc_target(before, target_point)
    )
    if not _same_point(new_selected, expected):
        raise NativeSketchError(
            f"{LABEL} feasibility did not project the exact target point."
        )
    old_span = _span(before, str(kind))
    new_span = _span(after, str(kind))
    if (
        not math.isfinite(old_span)
        or not math.isfinite(new_span)
        or new_span <= 1.0e-10
        or not math.isclose(
            new_span - old_span,
            increment,
            rel_tol=0.0,
            abs_tol=1.0e-8,
        )
    ):
        raise NativeSketchError(f"{LABEL} feasibility returned the wrong curve extent.")
    return (
        "extended" if increment > 0.0 else "shortened",
        (new_selected[0], new_selected[1]),
    )


def parse_sketch_extend_diagnostic(
    result: Any,
    spec: SketchExtendSpec,
    before_geometry_records: tuple[str, ...],
    before_constraint_records: tuple[str, ...],
) -> SketchExtendPlan:
    if not isinstance(result, Mapping):
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    base_result = dict(result)
    try:
        endpoint = base_result.pop("input_endpoint")
        raw_increment = base_result.pop("extension_increment")
    except KeyError as exc:
        raise NativeSketchError(
            f"{LABEL} feasibility returned incomplete diagnostics."
        ) from exc
    state = parse_curve_point_diagnostic(
        base_result,
        spec,
        before_geometry_records,
        before_constraint_records,
        label=LABEL,
    )
    if endpoint != spec.endpoint:
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different endpoint.")
    if isinstance(raw_increment, bool) or not isinstance(raw_increment, (int, float)):
        raise NativeSketchError(f"{LABEL} feasibility returned an invalid increment.")
    increment = float(raw_increment)
    if not math.isfinite(increment) or abs(increment) <= 1.0e-10:
        raise NativeSketchError(f"{LABEL} feasibility returned an invalid increment.")

    before, after, changed = _validate_identity_and_scope(
        state,
        before_geometry_records,
        before_constraint_records,
    )
    outcome, new_endpoint = _validate_target_geometry(
        before,
        after,
        spec,
        increment,
    )
    return SketchExtendPlan(
        state.input_geometry_index,
        state.reference_point_mm,
        spec.endpoint,
        increment,
        outcome,
        new_endpoint,
        changed,
        state.identity,
        state.geometry_records,
        state.constraint_records,
        state.degrees_of_freedom,
    )
