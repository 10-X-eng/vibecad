# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact live-state and postcondition proof for Sketch Trim."""

from __future__ import annotations

from typing import Any

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCurvePointState import (
    SketchCurvePointSnapshot,
    capture_curve_point_snapshot,
    require_pure_curve_point_diagnostic,
    require_unchanged_curve_point,
    verify_curve_point_state,
)
from VibeCADNativeSketchTrimDiagnostic import SketchTrimPlan
from VibeCADNativeSketchTrimTarget import LABEL, SketchTrimSpec


_HUMAN_TRIM_KINDS = frozenset(
    {
        "line",
        "circular_arc",
        "elliptical_arc",
        "hyperbolic_arc",
        "parabolic_arc",
        "circle",
        "ellipse",
        "b_spline",
    }
)
SketchTrimSnapshot = SketchCurvePointSnapshot


def capture_trim_snapshot(
    context: NativeRuntimeContext,
    spec: SketchTrimSpec,
) -> SketchCurvePointSnapshot:
    return capture_curve_point_snapshot(
        context,
        spec,
        label=LABEL,
        human_curve_kinds=_HUMAN_TRIM_KINDS,
    )


def require_pure_trim_diagnostic(
    sketch: Any,
    snapshot: SketchTrimSnapshot,
) -> None:
    require_pure_curve_point_diagnostic(sketch, snapshot, label=LABEL)


def require_unchanged_trim(document: Any, snapshot: SketchTrimSnapshot) -> Any:
    return require_unchanged_curve_point(document, snapshot, label=LABEL)


def verify_trim_state(
    sketch: Any,
    snapshot: SketchTrimSnapshot,
    plan: SketchTrimPlan,
    receipt: Any,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(plan, SketchTrimPlan):
        raise TypeError("plan must be a SketchTrimPlan")
    return verify_curve_point_state(
        sketch,
        snapshot,
        plan,
        receipt,
        label=LABEL,
    )
