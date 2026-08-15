# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic human-parity Chamfer for the exact human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchChamferDiagnostic import (
    SketchChamferPlan,
    parse_sketch_chamfer_diagnostic,
)
from VibeCADNativeSketchChamferTarget import (
    LABEL,
    SketchChamferCorner,
    SketchChamferSpec,
    prepare_sketch_chamfer_target,
)
from VibeCADNativeSketchCurveEditState import (
    SketchCurveEditSnapshot,
    capture_curve_edit_snapshot,
    require_pure_curve_edit_diagnostic,
    require_unchanged_curve_edit,
    verify_curve_edit_state,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchChamfer:
    state: SketchCurveEditSnapshot
    plan: SketchChamferPlan


def prepare_sketch_chamfer(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchChamferSpec:
    return prepare_sketch_chamfer_target(document_uid, value)


def _diagnose(sketch: Any, spec: SketchChamferSpec) -> Any:
    diagnose = getattr(sketch, "diagnoseChamfer", None)
    if not callable(diagnose):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    try:
        if isinstance(spec.selection, SketchChamferCorner):
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


def preflight_sketch_chamfer(
    context: NativeRuntimeContext,
    spec: SketchChamferSpec,
) -> PreparedSketchChamfer:
    if not isinstance(spec, SketchChamferSpec):
        raise TypeError("spec must be a SketchChamferSpec")
    state = capture_curve_edit_snapshot(context, spec, label=LABEL)
    plan = parse_sketch_chamfer_diagnostic(
        _diagnose(state.target.sketch, spec),
        spec,
        state.geometry_records,
    )
    if isinstance(spec.selection, SketchChamferCorner):
        if spec.selection.geometry_index not in plan.input_geometry_indices:
            raise NativeSketchError(f"{LABEL} feasibility resolved a different corner.")
    elif plan.input_geometry_indices != state.requested_geometry_indices:
        raise NativeSketchError(f"{LABEL} feasibility resolved different curves.")
    require_pure_curve_edit_diagnostic(state.target.sketch, state, label=LABEL)
    return PreparedSketchChamfer(state, plan)


def _source_pair_is_construction(
    state: SketchCurveEditSnapshot,
    input_geometry_indices: tuple[int, int],
) -> bool:
    return all(
        bool(json.loads(state.geometry_records[index]).get("construction"))
        for index in input_geometry_indices
    )


def create_sketch_chamfer(
    document: Any,
    prepared: PreparedSketchChamfer,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchChamfer):
        raise TypeError("prepared must be a PreparedSketchChamfer")
    state = prepared.state
    sketch = require_unchanged_curve_edit(document, state, label=LABEL)
    selection = state.spec.selection
    plan = prepared.plan
    try:
        if isinstance(selection, SketchChamferCorner):
            receipt = sketch.fillet(
                selection.geometry_index,
                selection.position_code,
                plan.radius_mm,
                True,
                state.spec.preserve_corner,
                True,
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
                True,
            )
        if _source_pair_is_construction(state, plan.input_geometry_indices):
            # Mirror DrawSketchHandlerFillet's precomputed Chamfer construction index.
            sketch.toggleConstruction(len(state.geometry_records) + 1)
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "receipt": receipt},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_chamfer(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    receipt = draft.value.get("receipt") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchChamfer):
        raise TypeError("draft must contain exact prepared Chamfer state")
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

    support_arc = json.loads(actual_geometry[plan.support_arc_geometry_index])
    chamfer = json.loads(actual_geometry[plan.chamfer_geometry_index])
    if (
        support_arc.get("kind") != "circular_arc"
        or not bool(support_arc.get("construction"))
        or not math.isclose(
            float(support_arc.get("radius_mm", math.nan)),
            plan.radius_mm,
            rel_tol=1.0e-10,
            abs_tol=1.0e-10,
        )
    ):
        raise NativeSketchError(f"{LABEL} did not create its exact support arc.")
    if (
        chamfer.get("kind") != "line"
        or bool(chamfer.get("construction")) is not plan.construction
    ):
        raise NativeSketchError(f"{LABEL} did not create the exact chamfer line.")

    result: dict[str, Any] = {
        "operation": "create_chamfer",
        "form": plan.form,
        "input_geometry_indices": list(plan.input_geometry_indices),
        "chamfer": chamfer,
        "support_arc_geometry_index": plan.support_arc_geometry_index,
        "radius_mm": plan.radius_mm,
        "trimmed": plan.trimmed,
        "preserve_corner": state.spec.preserve_corner,
        "construction": plan.construction,
    }
    if plan.corner_geometry_index is not None:
        result["preserved_corner"] = json.loads(
            actual_geometry[plan.corner_geometry_index]
        )
    return sketch_geometry_result(sketch, result)
