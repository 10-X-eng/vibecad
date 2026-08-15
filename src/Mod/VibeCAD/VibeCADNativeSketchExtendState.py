# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact live-state and postcondition proof for Sketch Extend."""

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
from VibeCADNativeSketchExtendDiagnostic import SketchExtendPlan
from VibeCADNativeSketchExtendTarget import LABEL, SketchExtendSpec


_HUMAN_EXTEND_KINDS = frozenset({"line", "circular_arc"})
SketchExtendSnapshot = SketchCurvePointSnapshot


def capture_extend_snapshot(
    context: NativeRuntimeContext,
    spec: SketchExtendSpec,
) -> SketchCurvePointSnapshot:
    return capture_curve_point_snapshot(
        context,
        spec,
        label=LABEL,
        human_curve_kinds=_HUMAN_EXTEND_KINDS,
    )


def require_pure_extend_diagnostic(
    sketch: Any,
    snapshot: SketchExtendSnapshot,
) -> None:
    require_pure_curve_point_diagnostic(sketch, snapshot, label=LABEL)


def require_unchanged_extend(document: Any, snapshot: SketchExtendSnapshot) -> Any:
    return require_unchanged_curve_point(document, snapshot, label=LABEL)


def verify_extend_state(
    sketch: Any,
    snapshot: SketchExtendSnapshot,
    plan: SketchExtendPlan,
    receipt: Any,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(plan, SketchExtendPlan):
        raise TypeError("plan must be a SketchExtendPlan")
    return verify_curve_point_state(
        sketch,
        snapshot,
        plan,
        receipt,
        label=LABEL,
    )
