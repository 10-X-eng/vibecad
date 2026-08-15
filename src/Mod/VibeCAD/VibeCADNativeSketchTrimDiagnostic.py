# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict parsing of the detached human Sketch Trim result."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from VibeCADNativeSketchCurvePointDiagnostic import (
    CurvePointDiagnosticState,
    aligned_internal_geometry,
    parse_curve_point_diagnostic,
    record_without_index,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchMutationState import SketchMutationIdentityPlan
from VibeCADNativeSketchTrimTarget import LABEL, SketchTrimSpec


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
class SketchTrimPlan:
    input_geometry_index: int
    reference_point_mm: tuple[float, float]
    outcome: str
    identity: SketchMutationIdentityPlan
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    degrees_of_freedom: int


def _validate_geometry_identity(
    state: CurvePointDiagnosticState,
    before: tuple[str, ...],
    constraint_records: tuple[str, ...],
) -> str:
    target = state.input_geometry_index
    if target not in state.deleted_geometry:
        raise NativeSketchError(
            f"{LABEL} feasibility did not replace or delete the target curve."
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

    if len(state.created_geometry) > 2:
        raise NativeSketchError(f"{LABEL} feasibility created unexpected geometry.")
    source = json.loads(before[target])
    replacement_kinds = _REPLACEMENT_KINDS.get(str(source.get("kind")), frozenset())
    created_tags: set[str] = set()
    for index, tag in state.created_geometry.items():
        record = json.loads(state.geometry_records[index])
        if (
            str(record.get("tag", "")) != tag
            or not tag
            or tag in before_tags
            or tag in created_tags
            or record.get("kind") not in replacement_kinds
            or "internal_type" in record
            or bool(record.get("construction")) is not bool(source.get("construction"))
        ):
            raise NativeSketchError(
                f"{LABEL} feasibility returned an invalid replacement curve."
            )
        created_tags.add(tag)
    return ("deleted", "shortened", "split")[len(state.created_geometry)]


def parse_sketch_trim_diagnostic(
    result: Any,
    spec: SketchTrimSpec,
    before_geometry_records: tuple[str, ...],
    before_constraint_records: tuple[str, ...],
) -> SketchTrimPlan:
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
    return SketchTrimPlan(
        state.input_geometry_index,
        state.reference_point_mm,
        outcome,
        state.identity,
        state.geometry_records,
        state.constraint_records,
        state.degrees_of_freedom,
    )
