# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic human-parity Extend for one exact curve endpoint in the open Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExtendDiagnostic import (
    SketchExtendPlan,
    parse_sketch_extend_diagnostic,
)
from VibeCADNativeSketchExtendState import (
    SketchExtendSnapshot,
    capture_extend_snapshot,
    require_pure_extend_diagnostic,
    require_unchanged_extend,
    verify_extend_state,
)
from VibeCADNativeSketchExtendTarget import (
    LABEL,
    SketchExtendSpec,
    prepare_sketch_extend_target,
)
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


_ENDPOINT_VALUES = {"start": 1, "end": 2}


@dataclass(frozen=True, slots=True)
class PreparedSketchExtend:
    state: SketchExtendSnapshot
    plan: SketchExtendPlan


def prepare_sketch_extend(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchExtendSpec:
    return prepare_sketch_extend_target(document_uid, value)


def _diagnose(sketch: Any, spec: SketchExtendSpec) -> Any:
    diagnose = getattr(sketch, "diagnoseExtend", None)
    if not callable(diagnose):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    try:
        import FreeCAD as App

        return diagnose(
            spec.selection.geometry_index,
            App.Vector(*spec.selection.reference_point_mm, 0.0),
            _ENDPOINT_VALUES[spec.endpoint],
        )
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} target.") from exc


def preflight_sketch_extend(
    context: NativeRuntimeContext,
    spec: SketchExtendSpec,
) -> PreparedSketchExtend:
    if not isinstance(spec, SketchExtendSpec):
        raise TypeError("spec must be a SketchExtendSpec")
    state = capture_extend_snapshot(context, spec)
    plan = parse_sketch_extend_diagnostic(
        _diagnose(state.target.sketch, spec),
        spec,
        state.geometry_records,
        state.constraint_records,
    )
    require_pure_extend_diagnostic(state.target.sketch, state)
    return PreparedSketchExtend(state, plan)


def create_sketch_extend(
    document: Any,
    prepared: PreparedSketchExtend,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchExtend):
        raise TypeError("prepared must be a PreparedSketchExtend")
    state = prepared.state
    plan = prepared.plan
    sketch = require_unchanged_extend(document, state)
    try:
        receipt = sketch.extend(
            plan.input_geometry_index,
            plan.extension_increment,
            _ENDPOINT_VALUES[plan.endpoint],
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "receipt": receipt},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_extend(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    receipt = draft.value.get("receipt") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchExtend):
        raise TypeError("draft must contain exact prepared Extend state")
    state = prepared.state
    plan = prepared.plan
    sketch = require_prepared_active_sketch(document, state.target)
    verify_extend_state(sketch, state, plan, receipt)
    return sketch_geometry_result(
        sketch,
        {
            "operation": "extend",
            "geometry_index": plan.input_geometry_index,
            "endpoint": plan.endpoint,
            "target_point_mm": {
                "x": plan.target_point_mm[0],
                "y": plan.target_point_mm[1],
            },
            "outcome": plan.outcome,
            "new_endpoint_mm": {
                "x": plan.new_endpoint_mm[0],
                "y": plan.new_endpoint_mm[1],
            },
            "changed_geometry_indices": list(plan.changed_geometry_indices),
        },
    )
