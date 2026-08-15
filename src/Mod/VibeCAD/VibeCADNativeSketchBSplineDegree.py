# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of one-step B-spline degree elevation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineDegreeState import (
    SketchBSplineDegreePlan,
    SketchBSplineDegreeSnapshot,
    capture_bspline_degree_snapshot,
    parse_bspline_degree_diagnostic,
    require_bspline_degree_snapshot_unchanged,
    require_pure_bspline_degree_diagnostic,
    verify_bspline_degree_state,
)
from VibeCADNativeSketchBSplineDegreeTarget import (
    LABEL,
    SketchBSplineDegreeSpec,
    prepare_sketch_bspline_degree,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeTargets import object_identity


OPERATION = "increase_bspline_degree"


@dataclass(frozen=True, slots=True)
class PreparedSketchBSplineDegree:
    snapshot: SketchBSplineDegreeSnapshot
    plan: SketchBSplineDegreePlan


@dataclass(frozen=True, slots=True)
class AppliedSketchBSplineDegree:
    prepared: PreparedSketchBSplineDegree
    receipt: Any


def _diagnose(snapshot: SketchBSplineDegreeSnapshot) -> Any:
    method = getattr(
        snapshot.transform.target.sketch,
        "diagnoseIncreaseBSplineDegree",
        None,
    )
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    try:
        return method(list(snapshot.transform.spec.geometry_indices))
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} targets."
        ) from exc


def prepare_bspline_degree(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBSplineDegreeSpec:
    return prepare_sketch_bspline_degree(document_uid, value)


def preflight_bspline_degree(
    context: NativeRuntimeContext,
    spec: SketchBSplineDegreeSpec,
) -> PreparedSketchBSplineDegree:
    snapshot = capture_bspline_degree_snapshot(context, spec)
    plan = parse_bspline_degree_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_bspline_degree_diagnostic(snapshot)
    return PreparedSketchBSplineDegree(snapshot, plan)


def create_bspline_degree(
    document: Any,
    prepared: PreparedSketchBSplineDegree,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchBSplineDegree):
        raise TypeError("prepared must be exact Increase B-Spline Degree state")
    snapshot = prepared.snapshot
    sketch = require_bspline_degree_snapshot_unchanged(document, snapshot)
    current_plan = parse_bspline_degree_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_bspline_degree_diagnostic(snapshot)
    if current_plan != prepared.plan:
        raise NativeSketchError(f"The exact {LABEL} result changed after preflight.")
    method = getattr(sketch, "increaseBSplineDegreeExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    try:
        receipt = method(list(snapshot.transform.spec.geometry_indices))
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchBSplineDegree(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_bspline_degree(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchBSplineDegree):
        raise TypeError("draft must contain applied Increase B-Spline Degree state")
    plan = applied.prepared.plan
    sketch, created_geometry, created_constraints = verify_bspline_degree_state(
        document,
        applied.prepared.snapshot,
        plan,
        applied.receipt,
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "geometry_indices": list(
                applied.prepared.snapshot.transform.spec.geometry_indices
            ),
            "old_degrees": list(plan.old_degrees),
            "new_degrees": list(plan.new_degrees),
            "exposed_internal_geometry_count": (plan.exposed_internal_geometry_count),
            "created_geometry_count": len(created_geometry),
            "created_constraint_count": len(created_constraints),
        },
    )
