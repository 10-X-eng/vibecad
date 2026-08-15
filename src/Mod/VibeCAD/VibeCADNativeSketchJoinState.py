# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact frozen live state and postconditions for Sketch Join Curves."""

from __future__ import annotations

import json
from typing import Any

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import require_healthy_external_records
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchJoinTarget import LABEL, SketchJoinSpec
from VibeCADNativeSketchMutationState import grouped_geometry_members
from VibeCADNativeSketchTargets import preflight_active_sketch
from VibeCADNativeSketchTransformState import (
    SketchTransformPlan,
    SketchTransformSnapshot,
    frozen_transform_state,
    require_pure_transform_diagnostic,
    require_transform_snapshot_unchanged,
    verify_transform_state,
)


_HUMAN_OPEN_CURVE_KINDS = frozenset(
    {
        "line",
        "circular_arc",
        "elliptical_arc",
        "hyperbolic_arc",
        "parabolic_arc",
        "b_spline",
        "bezier",
        "hyperbola",
        "parabola",
        "curve",
    }
)
SketchJoinSnapshot = SketchTransformSnapshot


def _validate_endpoint_curve(record: dict[str, Any], *, field: str) -> None:
    if (
        record.get("kind") not in _HUMAN_OPEN_CURVE_KINDS
        or "internal_type" in record
        or bool(record.get("periodic"))
        or bool(record.get("closed"))
        or "start_mm" not in record
        or "end_mm" not in record
    ):
        raise NativeSketchError(
            f"{LABEL} {field} must identify one open, non-helper curve accepted by "
            "the human Join Curves command."
        )


def capture_join_snapshot(
    context: NativeRuntimeContext,
    spec: SketchJoinSpec,
) -> SketchJoinSnapshot:
    if not isinstance(spec, SketchJoinSpec):
        raise TypeError("spec must be a SketchJoinSpec")
    target = preflight_active_sketch(context, spec.target)
    state = frozen_transform_state(
        target.sketch,
        spec.target.expected_geometry_count,
        spec.target.expected_constraint_count,
        label=LABEL,
    )
    if (
        len(state.external_reference_records) != spec.expected_external_reference_count
        or len(state.external_geometry_records) != spec.expected_external_geometry_count
    ):
        raise NativeSketchError(f"{LABEL} external state changed; read it and retry.")
    if any(state.solver_issues):
        raise NativeSketchError(f"{LABEL} requires a Sketch without solver issues.")
    require_healthy_external_records(state.external_geometry_records, label=LABEL)
    grouped = grouped_geometry_members(target.sketch, label=LABEL)
    records = tuple(json.loads(item) for item in state.geometry_records)
    for field, endpoint in (("first", spec.first), ("second", spec.second)):
        if endpoint.geometry_index >= len(records):
            raise NativeSketchError(f"{LABEL} {field} geometry index is stale.")
        if endpoint.geometry_index in grouped:
            raise NativeSketchError(
                f"{LABEL} cannot silently dismantle grouped or Text geometry."
            )
        _validate_endpoint_curve(records[endpoint.geometry_index], field=field)
    if bool(records[spec.first.geometry_index].get("construction")) is not bool(
        records[spec.second.geometry_index].get("construction")
    ):
        raise NativeSketchError(
            f"{LABEL} cannot combine construction and non-construction curves."
        )
    return SketchTransformSnapshot(target, spec, state, LABEL)


def require_pure_join_diagnostic(snapshot: SketchJoinSnapshot) -> None:
    require_pure_transform_diagnostic(snapshot)


def require_join_snapshot_unchanged(
    document: Any,
    snapshot: SketchJoinSnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot)


def verify_join_state(
    document: Any,
    snapshot: SketchJoinSnapshot,
    plan: Any,
    receipt: Any,
):
    transform = getattr(plan, "transform", None)
    if not isinstance(transform, SketchTransformPlan):
        raise TypeError("plan must contain a SketchTransformPlan")
    return verify_transform_state(document, snapshot, transform, receipt)
