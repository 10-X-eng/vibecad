# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of the human Sketch Rotate / polar transform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchRotateState import (
    SketchRotatePlan,
    SketchRotateSnapshot,
    capture_rotate_snapshot,
    parse_rotate_diagnostic,
    require_pure_rotate_diagnostic,
    require_rotate_snapshot_unchanged,
    verify_rotate_state,
)
from VibeCADNativeSketchRotateTarget import (
    LABEL,
    SketchRotateSpec,
    prepare_sketch_rotate,
)
from VibeCADNativeTargets import object_identity


OPERATION = "rotate"


@dataclass(frozen=True, slots=True)
class PreparedSketchRotate:
    snapshot: SketchRotateSnapshot
    plan: SketchRotatePlan


@dataclass(frozen=True, slots=True)
class AppliedSketchRotate:
    prepared: PreparedSketchRotate
    receipt: Any


def _diagnose(snapshot: SketchRotateSnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseRotate", None)
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    spec = snapshot.spec
    try:
        import FreeCAD as App

        return method(
            list(spec.geometry_indices),
            App.Vector(*spec.center_mm, 0.0),
            spec.total_angle_radians,
            spec.copy_count,
            spec.equalize_dimensional_constraints,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} target and polar options."
        ) from exc


def prepare_rotate(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchRotateSpec:
    return prepare_sketch_rotate(document_uid, value)


def preflight_rotate(
    context: NativeRuntimeContext,
    spec: SketchRotateSpec,
) -> PreparedSketchRotate:
    snapshot = capture_rotate_snapshot(context, spec)
    plan = parse_rotate_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_rotate_diagnostic(snapshot)
    return PreparedSketchRotate(snapshot, plan)


def create_rotate(
    document: Any,
    prepared: PreparedSketchRotate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchRotate):
        raise TypeError("prepared must be exact Sketch Rotate state")
    sketch = require_rotate_snapshot_unchanged(document, prepared.snapshot)
    current_plan = parse_rotate_diagnostic(
        _diagnose(prepared.snapshot), prepared.snapshot
    )
    require_pure_rotate_diagnostic(prepared.snapshot)
    if current_plan != prepared.plan:
        changed = tuple(
            field
            for field in (
                "identity",
                "geometry_records",
                "constraint_records",
                "external_reference_records",
                "external_geometry_records",
                "expression_records",
                "degrees_of_freedom",
            )
            if getattr(current_plan, field) != getattr(prepared.plan, field)
        )
        raise NativeSketchError(
            f"The exact {LABEL} result changed after preflight: "
            + ", ".join(changed)
            + "."
        )
    method = getattr(sketch, "rotateExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    spec = prepared.snapshot.spec
    try:
        import FreeCAD as App

        receipt = method(
            list(spec.geometry_indices),
            App.Vector(*spec.center_mm, 0.0),
            spec.total_angle_radians,
            spec.copy_count,
            spec.equalize_dimensional_constraints,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchRotate(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_rotate(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchRotate):
        raise TypeError("draft must contain applied Sketch Rotate state")
    spec = applied.prepared.snapshot.spec
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_rotate_state(
        document,
        applied.prepared.snapshot,
        applied.prepared.plan,
        applied.receipt,
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "mode": "move" if spec.copy_count == 0 else "polar_array",
            "input_geometry_count": len(spec.geometry_indices),
            "center_mm": {"x": spec.center_mm[0], "y": spec.center_mm[1]},
            "total_angle": {"value": spec.total_angle_degrees, "unit": "deg"},
            "copy_count": spec.copy_count,
            "constraint_mode": spec.constraint_mode,
            "created_geometry_count": len(created_geometry),
            "deleted_geometry_count": len(deleted_geometry),
            "created_constraint_count": len(created_constraints),
            "deleted_constraint_count": len(deleted_constraints),
            "created_geometry_indices": list(created_geometry[:32]),
            "deleted_geometry_indices": list(deleted_geometry[:32]),
            "created_constraint_indices": list(created_constraints[:32]),
            "deleted_constraint_indices": list(deleted_constraints[:32]),
        },
    )
