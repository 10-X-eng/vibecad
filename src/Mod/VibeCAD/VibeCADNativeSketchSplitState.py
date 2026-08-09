# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact live-state and postcondition proof for Sketch Split."""

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
from VibeCADNativeSketchSplitDiagnostic import SketchSplitPlan
from VibeCADNativeSketchSplitTarget import LABEL, SketchSplitSpec


_HUMAN_SPLIT_KINDS = frozenset(
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
SketchSplitSnapshot = SketchCurvePointSnapshot


def capture_split_snapshot(
    context: NativeRuntimeContext,
    spec: SketchSplitSpec,
) -> SketchCurvePointSnapshot:
    return capture_curve_point_snapshot(
        context,
        spec,
        label=LABEL,
        human_curve_kinds=_HUMAN_SPLIT_KINDS,
    )


def require_pure_split_diagnostic(
    sketch: Any,
    snapshot: SketchSplitSnapshot,
) -> None:
    require_pure_curve_point_diagnostic(sketch, snapshot, label=LABEL)


def require_unchanged_split(document: Any, snapshot: SketchSplitSnapshot) -> Any:
    return require_unchanged_curve_point(document, snapshot, label=LABEL)


def verify_split_state(
    sketch: Any,
    snapshot: SketchSplitSnapshot,
    plan: SketchSplitPlan,
    receipt: Any,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(plan, SketchSplitPlan):
        raise TypeError("plan must be a SketchSplitPlan")
    return verify_curve_point_state(
        sketch,
        snapshot,
        plan,
        receipt,
        label=LABEL,
    )
