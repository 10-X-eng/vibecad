# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic human-parity Fillet for the exact human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCurveEditState import (
    SketchCurveEditSnapshot,
    capture_curve_edit_snapshot,
    require_pure_curve_edit_diagnostic,
    require_unchanged_curve_edit,
    verify_curve_edit_state,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchFilletDiagnostic import (
    SketchFilletPlan,
    parse_sketch_fillet_diagnostic,
)
from VibeCADNativeSketchFilletTarget import (
    LABEL,
    SketchFilletCorner,
    SketchFilletSpec,
    prepare_sketch_fillet_target,
)
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchFillet:
    state: SketchCurveEditSnapshot
    plan: SketchFilletPlan


def prepare_sketch_fillet(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchFilletSpec:
    return prepare_sketch_fillet_target(document_uid, value)


def _diagnose(sketch: Any, spec: SketchFilletSpec) -> Any:
    diagnose = getattr(sketch, "diagnoseFillet", None)
    if not callable(diagnose):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    try:
        if isinstance(spec.selection, SketchFilletCorner):
            return diagnose(
                spec.selection.geometry_index,
                spec.selection.position_code,
                spec.preserve_corner,
            )
        import FreeCAD as App

        first, second = spec.selection.curves
        return diagnose(
            first.geometry_index,
            second.geometry_index,
            App.Vector(*first.reference_point_mm, 0.0),
            App.Vector(*second.reference_point_mm, 0.0),
            spec.preserve_corner,
        )
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} target.") from exc


def preflight_sketch_fillet(
    context: NativeRuntimeContext,
    spec: SketchFilletSpec,
) -> PreparedSketchFillet:
    if not isinstance(spec, SketchFilletSpec):
        raise TypeError("spec must be a SketchFilletSpec")
    state = capture_curve_edit_snapshot(context, spec, label=LABEL)
    plan = parse_sketch_fillet_diagnostic(
        _diagnose(state.target.sketch, spec),
        spec,
        state.geometry_records,
    )
    if isinstance(spec.selection, SketchFilletCorner):
        if spec.selection.geometry_index not in plan.input_geometry_indices:
            raise NativeSketchError(f"{LABEL} feasibility resolved a different corner.")
    elif plan.input_geometry_indices != state.requested_geometry_indices:
        raise NativeSketchError(f"{LABEL} feasibility resolved different curves.")
    require_pure_curve_edit_diagnostic(state.target.sketch, state, label=LABEL)
    return PreparedSketchFillet(state, plan)


def create_sketch_fillet(
    document: Any,
    prepared: PreparedSketchFillet,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchFillet):
        raise TypeError("prepared must be a PreparedSketchFillet")
    state = prepared.state
    sketch = require_unchanged_curve_edit(document, state, label=LABEL)
    selection = state.spec.selection
    plan = prepared.plan
    try:
        if isinstance(selection, SketchFilletCorner):
            receipt = sketch.fillet(
                selection.geometry_index,
                selection.position_code,
                plan.radius_mm,
                True,
                state.spec.preserve_corner,
                False,
            )
        else:
            import FreeCAD as App

            first, second = selection.curves
            receipt = sketch.fillet(
                first.geometry_index,
                second.geometry_index,
                App.Vector(*first.reference_point_mm, 0.0),
                App.Vector(*second.reference_point_mm, 0.0),
                plan.radius_mm,
                True,
                state.spec.preserve_corner,
                False,
            )
        if plan.construction:
            sketch.toggleConstruction(plan.fillet_geometry_index)
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "receipt": receipt},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_fillet(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    receipt = draft.value.get("receipt") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchFillet):
        raise TypeError("draft must contain exact prepared Fillet state")
    state = prepared.state
    plan = prepared.plan
    sketch = require_prepared_active_sketch(document, state.target)
    actual_geometry, _actual_constraints = verify_curve_edit_state(
        sketch,
        state,
        planned_geometry_records=plan.geometry_records,
        planned_constraint_records=plan.constraint_records,
        receipt=receipt,
        label=LABEL,
    )

    fillet = json.loads(actual_geometry[plan.fillet_geometry_index])
    if (
        fillet.get("kind") != "circular_arc"
        or bool(fillet.get("construction")) is not plan.construction
    ):
        raise NativeSketchError(f"{LABEL} did not create the exact fillet arc.")
    result: dict[str, Any] = {
        "operation": "create_fillet",
        "form": plan.form,
        "input_geometry_indices": list(plan.input_geometry_indices),
        "fillet": fillet,
        "radius_mm": fillet.get("radius_mm"),
        "trimmed": plan.trimmed,
        "preserve_corner": state.spec.preserve_corner,
        "construction": plan.construction,
    }
    if plan.corner_geometry_index is not None:
        result["preserved_corner"] = json.loads(
            actual_geometry[plan.corner_geometry_index]
        )
    return sketch_geometry_result(sketch, result)
