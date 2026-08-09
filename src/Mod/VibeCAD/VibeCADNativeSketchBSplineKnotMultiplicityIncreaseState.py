# SPDX-License-Identifier: LGPL-2.1-or-later

"""Increase-specific facade over shared B-spline knot-multiplicity state."""

from __future__ import annotations

from typing import Any

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineKnotMultiplicityIncreaseTarget import (
    LABEL,
    SketchBSplineKnotMultiplicityIncreaseSpec,
)
from VibeCADNativeSketchBSplineKnotMultiplicityProof import MAX_SHAPE_DEVIATION_MM
from VibeCADNativeSketchBSplineKnotMultiplicityState import (
    SketchBSplineKnotMultiplicityPlan,
    SketchBSplineKnotMultiplicitySnapshot,
    capture_bspline_knot_multiplicity_snapshot,
    parse_bspline_knot_multiplicity_diagnostic,
    require_bspline_knot_multiplicity_snapshot_unchanged,
    require_pure_bspline_knot_multiplicity_diagnostic,
    verify_bspline_knot_multiplicity_state,
)


SketchBSplineKnotMultiplicityIncreaseSnapshot = SketchBSplineKnotMultiplicitySnapshot
SketchBSplineKnotMultiplicityIncreasePlan = SketchBSplineKnotMultiplicityPlan


def capture_bspline_knot_multiplicity_increase_snapshot(
    context: NativeRuntimeContext,
    spec: SketchBSplineKnotMultiplicityIncreaseSpec,
) -> SketchBSplineKnotMultiplicityIncreaseSnapshot:
    if not isinstance(spec, SketchBSplineKnotMultiplicityIncreaseSpec):
        raise TypeError("spec must be a SketchBSplineKnotMultiplicityIncreaseSpec")
    return capture_bspline_knot_multiplicity_snapshot(
        context,
        spec,
        label=LABEL,
        increment=1,
        maximum_allowed_deviation_mm=MAX_SHAPE_DEVIATION_MM,
    )


def parse_bspline_knot_multiplicity_increase_diagnostic(
    result: Any,
    snapshot: SketchBSplineKnotMultiplicityIncreaseSnapshot,
) -> SketchBSplineKnotMultiplicityIncreasePlan:
    return parse_bspline_knot_multiplicity_diagnostic(result, snapshot)


def require_bspline_knot_multiplicity_increase_snapshot_unchanged(
    document: Any,
    snapshot: SketchBSplineKnotMultiplicityIncreaseSnapshot,
) -> Any:
    return require_bspline_knot_multiplicity_snapshot_unchanged(document, snapshot)


def require_pure_bspline_knot_multiplicity_increase_diagnostic(
    snapshot: SketchBSplineKnotMultiplicityIncreaseSnapshot,
) -> None:
    require_pure_bspline_knot_multiplicity_diagnostic(snapshot)


def verify_bspline_knot_multiplicity_increase_state(
    document: Any,
    snapshot: SketchBSplineKnotMultiplicityIncreaseSnapshot,
    plan: SketchBSplineKnotMultiplicityIncreasePlan,
    receipt: Any,
):
    return verify_bspline_knot_multiplicity_state(document, snapshot, plan, receipt)
