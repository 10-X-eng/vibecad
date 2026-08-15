# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of one-step B-spline degree reduction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineDegreeDecreaseState import (
    SketchBSplineDegreeDecreasePlan,
    SketchBSplineDegreeDecreaseSnapshot,
    capture_bspline_degree_decrease_snapshot,
    parse_bspline_degree_decrease_diagnostic,
    require_bspline_degree_decrease_snapshot_unchanged,
    require_pure_bspline_degree_decrease_diagnostic,
    verify_bspline_degree_decrease_state,
)
from VibeCADNativeSketchBSplineDegreeDecreaseTarget import (
    LABEL,
    SketchBSplineDegreeDecreaseSpec,
    prepare_sketch_bspline_degree_decrease,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeTargets import object_identity


OPERATION = "decrease_bspline_degree"


@dataclass(frozen=True, slots=True)
class PreparedSketchBSplineDegreeDecrease:
    snapshot: SketchBSplineDegreeDecreaseSnapshot
    plan: SketchBSplineDegreeDecreasePlan


@dataclass(frozen=True, slots=True)
class AppliedSketchBSplineDegreeDecrease:
    prepared: PreparedSketchBSplineDegreeDecrease
    receipt: Any


def _diagnose(snapshot: SketchBSplineDegreeDecreaseSnapshot) -> Any:
    method = getattr(
        snapshot.transform.target.sketch,
        "diagnoseDecreaseBSplineDegree",
        None,
    )
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    try:
        return method(snapshot.transform.spec.geometry_index)
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} target.") from exc


def prepare_bspline_degree_decrease(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBSplineDegreeDecreaseSpec:
    return prepare_sketch_bspline_degree_decrease(document_uid, value)


def preflight_bspline_degree_decrease(
    context: NativeRuntimeContext,
    spec: SketchBSplineDegreeDecreaseSpec,
) -> PreparedSketchBSplineDegreeDecrease:
    snapshot = capture_bspline_degree_decrease_snapshot(context, spec)
    plan = parse_bspline_degree_decrease_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_bspline_degree_decrease_diagnostic(snapshot)
    return PreparedSketchBSplineDegreeDecrease(snapshot, plan)


def create_bspline_degree_decrease(
    document: Any,
    prepared: PreparedSketchBSplineDegreeDecrease,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchBSplineDegreeDecrease):
        raise TypeError("prepared must be exact Decrease B-Spline Degree state")
    snapshot = prepared.snapshot
    sketch = require_bspline_degree_decrease_snapshot_unchanged(document, snapshot)
    current_plan = parse_bspline_degree_decrease_diagnostic(
        _diagnose(snapshot), snapshot
    )
    require_pure_bspline_degree_decrease_diagnostic(snapshot)
    if current_plan != prepared.plan:
        raise NativeSketchError(f"The exact {LABEL} result changed after preflight.")
    method = getattr(sketch, "decreaseBSplineDegreeExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    try:
        receipt = method(snapshot.transform.spec.geometry_index)
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchBSplineDegreeDecrease(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_bspline_degree_decrease(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchBSplineDegreeDecrease):
        raise TypeError("draft must contain applied Decrease B-Spline Degree state")
    snapshot = applied.prepared.snapshot
    plan = applied.prepared.plan
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_bspline_degree_decrease_state(
        document,
        snapshot,
        plan,
        applied.receipt,
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "geometry_index": snapshot.transform.spec.geometry_index,
            "old_degree": plan.old_degree,
            "new_degree": plan.new_degree,
            "measured_deviation_mm": plan.maximum_deviation_mm,
            "retained_internal_geometry_count": (plan.retained_internal_geometry_count),
            "deleted_geometry_count": len(deleted_geometry),
            "created_geometry_count": len(created_geometry),
            "deleted_constraint_count": len(deleted_constraints),
            "created_constraint_count": len(created_constraints),
        },
    )
