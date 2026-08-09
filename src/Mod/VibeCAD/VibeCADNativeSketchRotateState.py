# SPDX-License-Identifier: LGPL-2.1-or-later

"""Rotate-specific echoes over the shared exact Sketch transform state."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import ISSUE_FIELDS
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchRotateTarget import LABEL, SketchRotateSpec
from VibeCADNativeSketchTransformState import (
    FrozenSketchTransformState,
    SketchTransformPlan,
    SketchTransformSnapshot,
    capture_transform_snapshot,
    parse_transform_diagnostic,
    require_pure_transform_diagnostic,
    require_transform_snapshot_unchanged,
    vector_matches,
    verify_transform_state,
)


_FIELDS = frozenset(
    {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        *ISSUE_FIELDS,
        "geometry_count",
        "constraint_count",
        "geometry",
        "geometry_metadata",
        "constraints",
        "external_reference_count",
        "external_references",
        "external_geometry_count",
        "external_geometry",
        "external_geometry_metadata",
        "input_geometry_indices",
        "center_mm",
        "total_angle_radians",
        "copy_count",
        "equalize_dimensional_constraints",
        "deleted_originals",
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)

FrozenRotateState = FrozenSketchTransformState
SketchRotateSnapshot = SketchTransformSnapshot
SketchRotatePlan = SketchTransformPlan


def capture_rotate_snapshot(
    context: NativeRuntimeContext,
    spec: SketchRotateSpec,
) -> SketchRotateSnapshot:
    if not isinstance(spec, SketchRotateSpec):
        raise TypeError("spec must be a SketchRotateSpec")
    return capture_transform_snapshot(context, spec, label=LABEL)


def require_rotate_snapshot_unchanged(
    document: Any,
    snapshot: SketchRotateSnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot)


def require_pure_rotate_diagnostic(snapshot: SketchRotateSnapshot) -> None:
    require_pure_transform_diagnostic(snapshot)


def parse_rotate_diagnostic(
    result: Any,
    snapshot: SketchRotateSnapshot,
) -> SketchRotatePlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    spec = snapshot.spec
    if (
        tuple(result["input_geometry_indices"])
        if isinstance(result["input_geometry_indices"], (list, tuple))
        else ()
    ) != spec.geometry_indices or (
        not vector_matches(result["center_mm"], spec.center_mm)
        or type(result["total_angle_radians"]) is not float
        or result["total_angle_radians"] != spec.total_angle_radians
        or result["copy_count"] != spec.copy_count
        or result["equalize_dimensional_constraints"]
        is not spec.equalize_dimensional_constraints
        or result["deleted_originals"] is not (spec.copy_count == 0)
    ):
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    return parse_transform_diagnostic(result, snapshot)


def verify_rotate_state(
    document: Any,
    snapshot: SketchRotateSnapshot,
    plan: SketchRotatePlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    return verify_transform_state(document, snapshot, plan, receipt)
