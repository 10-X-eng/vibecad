# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of the human Sketch Scale command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchScaleState import (
    SketchScalePlan,
    SketchScaleSnapshot,
    capture_scale_snapshot,
    parse_scale_diagnostic,
    require_pure_scale_diagnostic,
    require_scale_snapshot_unchanged,
    verify_scale_state,
)
from VibeCADNativeSketchScaleTarget import LABEL, SketchScaleSpec, prepare_sketch_scale
from VibeCADNativeTargets import object_identity


OPERATION = "scale"


@dataclass(frozen=True, slots=True)
class PreparedSketchScale:
    snapshot: SketchScaleSnapshot
    plan: SketchScalePlan


@dataclass(frozen=True, slots=True)
class AppliedSketchScale:
    prepared: PreparedSketchScale
    receipt: Any


def _diagnose(snapshot: SketchScaleSnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseScale", None)
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    spec = snapshot.spec
    try:
        import FreeCAD as App

        return method(
            list(spec.geometry_indices),
            App.Vector(*spec.center_mm, 0.0),
            spec.scale_factor,
            spec.keep_originals,
            False,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} target and scale options."
        ) from exc


def prepare_scale(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchScaleSpec:
    return prepare_sketch_scale(document_uid, value)


def preflight_scale(
    context: NativeRuntimeContext,
    spec: SketchScaleSpec,
) -> PreparedSketchScale:
    snapshot = capture_scale_snapshot(context, spec)
    plan = parse_scale_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_scale_diagnostic(snapshot)
    return PreparedSketchScale(snapshot, plan)


def create_scale(
    document: Any,
    prepared: PreparedSketchScale,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchScale):
        raise TypeError("prepared must be exact Sketch Scale state")
    sketch = require_scale_snapshot_unchanged(document, prepared.snapshot)
    current_plan = parse_scale_diagnostic(
        _diagnose(prepared.snapshot), prepared.snapshot
    )
    require_pure_scale_diagnostic(prepared.snapshot)
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
    method = getattr(sketch, "scaleExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    spec = prepared.snapshot.spec
    try:
        import FreeCAD as App

        receipt = method(
            list(spec.geometry_indices),
            App.Vector(*spec.center_mm, 0.0),
            spec.scale_factor,
            spec.keep_originals,
            False,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchScale(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_scale(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchScale):
        raise TypeError("draft must contain applied Sketch Scale state")
    spec = applied.prepared.snapshot.spec
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_scale_state(
        document,
        applied.prepared.snapshot,
        applied.prepared.plan,
        applied.receipt,
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "mode": "copy" if spec.keep_originals else "replace",
            "input_geometry_count": len(spec.geometry_indices),
            "center_mm": {"x": spec.center_mm[0], "y": spec.center_mm[1]},
            "scale_factor": spec.scale_factor,
            "keep_originals": spec.keep_originals,
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
