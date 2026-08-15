# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic human-parity Trim for one exact curve in the open Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeSketchTrimDiagnostic import (
    SketchTrimPlan,
    parse_sketch_trim_diagnostic,
)
from VibeCADNativeSketchTrimState import (
    SketchTrimSnapshot,
    capture_trim_snapshot,
    require_pure_trim_diagnostic,
    require_unchanged_trim,
    verify_trim_state,
)
from VibeCADNativeSketchTrimTarget import (
    LABEL,
    SketchTrimSpec,
    prepare_sketch_trim_target,
)
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchTrim:
    state: SketchTrimSnapshot
    plan: SketchTrimPlan


def prepare_sketch_trim(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchTrimSpec:
    return prepare_sketch_trim_target(document_uid, value)


def _diagnose(sketch: Any, spec: SketchTrimSpec) -> Any:
    diagnose = getattr(sketch, "diagnoseTrim", None)
    if not callable(diagnose):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    try:
        import FreeCAD as App

        return diagnose(
            spec.selection.geometry_index,
            App.Vector(*spec.selection.reference_point_mm, 0.0),
        )
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} target.") from exc


def preflight_sketch_trim(
    context: NativeRuntimeContext,
    spec: SketchTrimSpec,
) -> PreparedSketchTrim:
    if not isinstance(spec, SketchTrimSpec):
        raise TypeError("spec must be a SketchTrimSpec")
    state = capture_trim_snapshot(context, spec)
    plan = parse_sketch_trim_diagnostic(
        _diagnose(state.target.sketch, spec),
        spec,
        state.geometry_records,
        state.constraint_records,
    )
    require_pure_trim_diagnostic(state.target.sketch, state)
    return PreparedSketchTrim(state, plan)


def create_sketch_trim(
    document: Any,
    prepared: PreparedSketchTrim,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchTrim):
        raise TypeError("prepared must be a PreparedSketchTrim")
    state = prepared.state
    sketch = require_unchanged_trim(document, state)
    try:
        import FreeCAD as App

        receipt = sketch.trim(
            state.spec.selection.geometry_index,
            App.Vector(*state.spec.selection.reference_point_mm, 0.0),
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


def verify_sketch_trim(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    receipt = draft.value.get("receipt") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchTrim):
        raise TypeError("draft must contain exact prepared Trim state")
    state = prepared.state
    plan = prepared.plan
    sketch = require_prepared_active_sketch(document, state.target)
    actual_geometry, _actual_constraints = verify_trim_state(
        sketch,
        state,
        plan,
        receipt,
    )
    replacements = [
        json.loads(actual_geometry[index])
        for index in plan.identity.geometry.created_indices
    ]
    return sketch_geometry_result(
        sketch,
        {
            "operation": "trim",
            "input_geometry_index": plan.input_geometry_index,
            "reference_point_mm": {
                "x": plan.reference_point_mm[0],
                "y": plan.reference_point_mm[1],
            },
            "outcome": plan.outcome,
            "deleted_geometry_indices": list(plan.identity.geometry.deleted_indices),
            "replacement_geometry_indices": list(
                plan.identity.geometry.created_indices
            ),
            "replacement_geometry": replacements,
        },
    )
