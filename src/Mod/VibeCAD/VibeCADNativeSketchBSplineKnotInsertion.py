# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of exact B-spline knot insertion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineKnotInsertionState import (
    SketchBSplineKnotInsertionPlan,
    SketchBSplineKnotInsertionSnapshot,
    capture_bspline_knot_insertion_snapshot,
    parse_bspline_knot_insertion_diagnostic,
    require_bspline_knot_insertion_snapshot_unchanged,
    require_pure_bspline_knot_insertion_diagnostic,
    verify_bspline_knot_insertion_state,
)
from VibeCADNativeSketchBSplineKnotInsertionTarget import (
    LABEL,
    SketchBSplineKnotInsertionSpec,
    prepare_sketch_bspline_knot_insertion,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeTargets import object_identity


OPERATION = "insert_bspline_knot"


@dataclass(frozen=True, slots=True)
class PreparedSketchBSplineKnotInsertion:
    snapshot: SketchBSplineKnotInsertionSnapshot
    plan: SketchBSplineKnotInsertionPlan


@dataclass(frozen=True, slots=True)
class AppliedSketchBSplineKnotInsertion:
    prepared: PreparedSketchBSplineKnotInsertion
    receipt: Any


def _diagnose(snapshot: SketchBSplineKnotInsertionSnapshot) -> Any:
    method = getattr(
        snapshot.transform.target.sketch, "diagnoseInsertBSplineKnot", None
    )
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    spec = snapshot.transform.spec
    try:
        return method(spec.geometry_index, spec.parameter)
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} target.") from exc


def prepare_bspline_knot_insertion(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBSplineKnotInsertionSpec:
    return prepare_sketch_bspline_knot_insertion(document_uid, value)


def preflight_bspline_knot_insertion(
    context: NativeRuntimeContext,
    spec: SketchBSplineKnotInsertionSpec,
) -> PreparedSketchBSplineKnotInsertion:
    snapshot = capture_bspline_knot_insertion_snapshot(context, spec)
    plan = parse_bspline_knot_insertion_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_bspline_knot_insertion_diagnostic(snapshot)
    return PreparedSketchBSplineKnotInsertion(snapshot, plan)


def create_bspline_knot_insertion(
    document: Any,
    prepared: PreparedSketchBSplineKnotInsertion,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchBSplineKnotInsertion):
        raise TypeError("prepared must be exact Insert Knot state")
    snapshot = prepared.snapshot
    sketch = require_bspline_knot_insertion_snapshot_unchanged(document, snapshot)
    current_plan = parse_bspline_knot_insertion_diagnostic(
        _diagnose(snapshot), snapshot
    )
    require_pure_bspline_knot_insertion_diagnostic(snapshot)
    if current_plan != prepared.plan:
        raise NativeSketchError(f"The exact {LABEL} result changed after preflight.")
    method = getattr(sketch, "insertBSplineKnotExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    spec = snapshot.transform.spec
    try:
        receipt = method(spec.geometry_index, spec.parameter)
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchBSplineKnotInsertion(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_bspline_knot_insertion(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchBSplineKnotInsertion):
        raise TypeError("draft must contain applied Insert Knot state")
    snapshot = applied.prepared.snapshot
    plan = applied.prepared.plan
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_bspline_knot_insertion_state(document, snapshot, plan, applied.receipt)
    spec = snapshot.transform.spec
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "geometry_index": spec.geometry_index,
            "requested_parameter": spec.parameter,
            "knot_index": plan.knot_index,
            "knot_parameter": plan.knot_parameter,
            "old_multiplicity": plan.old_multiplicity,
            "new_multiplicity": plan.new_multiplicity,
            "measured_displacement_mm": plan.maximum_displacement_mm,
            "retained_internal_geometry_count": plan.retained_internal_geometry_count,
            "deleted_geometry_count": len(deleted_geometry),
            "created_geometry_count": len(created_geometry),
            "deleted_constraint_count": len(deleted_constraints),
            "created_constraint_count": len(created_constraints),
        },
    )
