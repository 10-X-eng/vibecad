# SPDX-License-Identifier: LGPL-2.1-or-later

"""Decrease-specific facade over shared B-spline knot-multiplicity state."""

from __future__ import annotations

from typing import Any

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineKnotMultiplicityDecreaseTarget import (
    LABEL,
    SketchBSplineKnotMultiplicityDecreaseSpec,
)
from VibeCADNativeSketchBSplineKnotMultiplicityState import (
    SketchBSplineKnotMultiplicityPlan,
    SketchBSplineKnotMultiplicitySnapshot,
    capture_bspline_knot_multiplicity_snapshot,
    parse_bspline_knot_multiplicity_diagnostic,
    require_bspline_knot_multiplicity_snapshot_unchanged,
    require_pure_bspline_knot_multiplicity_diagnostic,
    verify_bspline_knot_multiplicity_state,
)


SketchBSplineKnotMultiplicityDecreaseSnapshot = SketchBSplineKnotMultiplicitySnapshot
SketchBSplineKnotMultiplicityDecreasePlan = SketchBSplineKnotMultiplicityPlan


def capture_bspline_knot_multiplicity_decrease_snapshot(
    context: NativeRuntimeContext,
    spec: SketchBSplineKnotMultiplicityDecreaseSpec,
) -> SketchBSplineKnotMultiplicityDecreaseSnapshot:
    if not isinstance(spec, SketchBSplineKnotMultiplicityDecreaseSpec):
        raise TypeError("spec must be a SketchBSplineKnotMultiplicityDecreaseSpec")
    return capture_bspline_knot_multiplicity_snapshot(
        context,
        spec,
        label=LABEL,
        increment=-1,
        maximum_allowed_deviation_mm=spec.maximum_deviation_mm,
    )


def parse_bspline_knot_multiplicity_decrease_diagnostic(
    result: Any,
    snapshot: SketchBSplineKnotMultiplicityDecreaseSnapshot,
) -> SketchBSplineKnotMultiplicityDecreasePlan:
    return parse_bspline_knot_multiplicity_diagnostic(result, snapshot)


def require_bspline_knot_multiplicity_decrease_snapshot_unchanged(
    document: Any,
    snapshot: SketchBSplineKnotMultiplicityDecreaseSnapshot,
) -> Any:
    return require_bspline_knot_multiplicity_snapshot_unchanged(document, snapshot)


def require_pure_bspline_knot_multiplicity_decrease_diagnostic(
    snapshot: SketchBSplineKnotMultiplicityDecreaseSnapshot,
) -> None:
    require_pure_bspline_knot_multiplicity_diagnostic(snapshot)


def verify_bspline_knot_multiplicity_decrease_state(
    document: Any,
    snapshot: SketchBSplineKnotMultiplicityDecreaseSnapshot,
    plan: SketchBSplineKnotMultiplicityDecreasePlan,
    receipt: Any,
):
    return verify_bspline_knot_multiplicity_state(document, snapshot, plan, receipt)
