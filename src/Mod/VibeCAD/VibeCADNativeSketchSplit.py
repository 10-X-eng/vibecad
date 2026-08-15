# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic human-parity Split for one exact curve in the open Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchSplitDiagnostic import (
    SketchSplitPlan,
    parse_sketch_split_diagnostic,
)
from VibeCADNativeSketchSplitState import (
    SketchSplitSnapshot,
    capture_split_snapshot,
    require_pure_split_diagnostic,
    require_unchanged_split,
    verify_split_state,
)
from VibeCADNativeSketchSplitTarget import (
    LABEL,
    SketchSplitSpec,
    prepare_sketch_split_target,
)
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchSplit:
    state: SketchSplitSnapshot
    plan: SketchSplitPlan


def prepare_sketch_split(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchSplitSpec:
    return prepare_sketch_split_target(document_uid, value)


def _diagnose(sketch: Any, spec: SketchSplitSpec) -> Any:
    diagnose = getattr(sketch, "diagnoseSplit", None)
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


def preflight_sketch_split(
    context: NativeRuntimeContext,
    spec: SketchSplitSpec,
) -> PreparedSketchSplit:
    if not isinstance(spec, SketchSplitSpec):
        raise TypeError("spec must be a SketchSplitSpec")
    state = capture_split_snapshot(context, spec)
    plan = parse_sketch_split_diagnostic(
        _diagnose(state.target.sketch, spec),
        spec,
        state.geometry_records,
        state.constraint_records,
    )
    require_pure_split_diagnostic(state.target.sketch, state)
    return PreparedSketchSplit(state, plan)


def create_sketch_split(
    document: Any,
    prepared: PreparedSketchSplit,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchSplit):
        raise TypeError("prepared must be a PreparedSketchSplit")
    state = prepared.state
    sketch = require_unchanged_split(document, state)
    try:
        import FreeCAD as App

        receipt = sketch.split(
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


def verify_sketch_split(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    receipt = draft.value.get("receipt") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchSplit):
        raise TypeError("draft must contain exact prepared Split state")
    state = prepared.state
    plan = prepared.plan
    sketch = require_prepared_active_sketch(document, state.target)
    actual_geometry, _actual_constraints = verify_split_state(
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
            "operation": "split",
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
