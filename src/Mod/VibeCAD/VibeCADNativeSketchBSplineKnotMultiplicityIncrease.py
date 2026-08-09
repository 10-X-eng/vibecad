# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of one-step B-spline knot multiplicity increase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineKnotMultiplicityIncreaseState import (
    SketchBSplineKnotMultiplicityIncreasePlan,
    SketchBSplineKnotMultiplicityIncreaseSnapshot,
    capture_bspline_knot_multiplicity_increase_snapshot,
    parse_bspline_knot_multiplicity_increase_diagnostic,
    require_bspline_knot_multiplicity_increase_snapshot_unchanged,
    require_pure_bspline_knot_multiplicity_increase_diagnostic,
    verify_bspline_knot_multiplicity_increase_state,
)
from VibeCADNativeSketchBSplineKnotMultiplicityIncreaseTarget import (
    LABEL,
    SketchBSplineKnotMultiplicityIncreaseSpec,
    prepare_sketch_bspline_knot_multiplicity_increase,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeTargets import object_identity


OPERATION = "increase_bspline_knot_multiplicity"


@dataclass(frozen=True, slots=True)
class PreparedSketchBSplineKnotMultiplicityIncrease:
    snapshot: SketchBSplineKnotMultiplicityIncreaseSnapshot
    plan: SketchBSplineKnotMultiplicityIncreasePlan


@dataclass(frozen=True, slots=True)
class AppliedSketchBSplineKnotMultiplicityIncrease:
    prepared: PreparedSketchBSplineKnotMultiplicityIncrease
    receipt: Any


def _diagnose(snapshot: SketchBSplineKnotMultiplicityIncreaseSnapshot) -> Any:
    method = getattr(
        snapshot.transform.target.sketch,
        "diagnoseIncreaseBSplineKnotMultiplicity",
        None,
    )
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    spec = snapshot.transform.spec
    try:
        return method(spec.geometry_index, spec.knot_index)
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} target.") from exc


def prepare_bspline_knot_multiplicity_increase(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBSplineKnotMultiplicityIncreaseSpec:
    return prepare_sketch_bspline_knot_multiplicity_increase(document_uid, value)


def preflight_bspline_knot_multiplicity_increase(
    context: NativeRuntimeContext,
    spec: SketchBSplineKnotMultiplicityIncreaseSpec,
) -> PreparedSketchBSplineKnotMultiplicityIncrease:
    snapshot = capture_bspline_knot_multiplicity_increase_snapshot(context, spec)
    plan = parse_bspline_knot_multiplicity_increase_diagnostic(
        _diagnose(snapshot), snapshot
    )
    require_pure_bspline_knot_multiplicity_increase_diagnostic(snapshot)
    return PreparedSketchBSplineKnotMultiplicityIncrease(snapshot, plan)


def create_bspline_knot_multiplicity_increase(
    document: Any,
    prepared: PreparedSketchBSplineKnotMultiplicityIncrease,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchBSplineKnotMultiplicityIncrease):
        raise TypeError("prepared must be exact Increase Knot Multiplicity state")
    snapshot = prepared.snapshot
    sketch = require_bspline_knot_multiplicity_increase_snapshot_unchanged(
        document, snapshot
    )
    current_plan = parse_bspline_knot_multiplicity_increase_diagnostic(
        _diagnose(snapshot), snapshot
    )
    require_pure_bspline_knot_multiplicity_increase_diagnostic(snapshot)
    if current_plan != prepared.plan:
        raise NativeSketchError(f"The exact {LABEL} result changed after preflight.")
    method = getattr(sketch, "increaseBSplineKnotMultiplicityExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    spec = snapshot.transform.spec
    try:
        receipt = method(spec.geometry_index, spec.knot_index)
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchBSplineKnotMultiplicityIncrease(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_bspline_knot_multiplicity_increase(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchBSplineKnotMultiplicityIncrease):
        raise TypeError("draft must contain applied Increase Knot Multiplicity state")
    snapshot = applied.prepared.snapshot
    plan = applied.prepared.plan
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_bspline_knot_multiplicity_increase_state(
        document,
        snapshot,
        plan,
        applied.receipt,
    )
    spec = snapshot.transform.spec
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "geometry_index": spec.geometry_index,
            "knot_index": spec.knot_index,
            "knot_parameter": plan.knot_parameter,
            "old_multiplicity": plan.old_multiplicity,
            "new_multiplicity": plan.new_multiplicity,
            "measured_deviation_mm": plan.maximum_deviation_mm,
            "retained_internal_geometry_count": plan.retained_internal_geometry_count,
            "deleted_geometry_count": len(deleted_geometry),
            "created_geometry_count": len(created_geometry),
            "deleted_constraint_count": len(deleted_constraints),
            "created_constraint_count": len(created_constraints),
        },
    )
