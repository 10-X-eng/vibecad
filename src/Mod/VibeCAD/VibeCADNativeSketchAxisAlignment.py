# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of the human Remove Axes Alignment command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchAxisAlignmentState import (
    SketchAxisAlignmentPlan,
    SketchAxisAlignmentSnapshot,
    capture_axis_alignment_snapshot,
    parse_axis_alignment_diagnostic,
    require_axis_alignment_snapshot_unchanged,
    require_pure_axis_alignment_diagnostic,
    verify_axis_alignment_state,
)
from VibeCADNativeSketchAxisAlignmentTarget import (
    LABEL,
    SketchAxisAlignmentSpec,
    prepare_sketch_axis_alignment,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeTargets import object_identity


OPERATION = "remove_axis_alignment"


@dataclass(frozen=True, slots=True)
class PreparedSketchAxisAlignment:
    snapshot: SketchAxisAlignmentSnapshot
    plan: SketchAxisAlignmentPlan


@dataclass(frozen=True, slots=True)
class AppliedSketchAxisAlignment:
    prepared: PreparedSketchAxisAlignment
    receipt: Any


def _diagnose(snapshot: SketchAxisAlignmentSnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseRemoveAxesAlignment", None)
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    try:
        return method(list(snapshot.spec.geometry_indices))
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} targets."
        ) from exc


def prepare_axis_alignment(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchAxisAlignmentSpec:
    return prepare_sketch_axis_alignment(document_uid, value)


def preflight_axis_alignment(
    context: NativeRuntimeContext,
    spec: SketchAxisAlignmentSpec,
) -> PreparedSketchAxisAlignment:
    snapshot = capture_axis_alignment_snapshot(context, spec)
    plan = parse_axis_alignment_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_axis_alignment_diagnostic(snapshot)
    return PreparedSketchAxisAlignment(snapshot, plan)


def create_axis_alignment(
    document: Any,
    prepared: PreparedSketchAxisAlignment,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchAxisAlignment):
        raise TypeError("prepared must be exact Remove Axes Alignment state")
    sketch = require_axis_alignment_snapshot_unchanged(document, prepared.snapshot)
    current_plan = parse_axis_alignment_diagnostic(
        _diagnose(prepared.snapshot), prepared.snapshot
    )
    require_pure_axis_alignment_diagnostic(prepared.snapshot)
    if current_plan != prepared.plan:
        raise NativeSketchError(f"The exact {LABEL} result changed after preflight.")
    method = getattr(sketch, "removeAxesAlignmentExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    try:
        receipt = method(list(prepared.snapshot.spec.geometry_indices))
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchAxisAlignment(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_axis_alignment(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchAxisAlignment):
        raise TypeError("draft must contain applied Remove Axes Alignment state")
    snapshot = applied.prepared.snapshot
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_axis_alignment_state(
        document,
        snapshot,
        applied.prepared.plan,
        applied.receipt,
    )
    if created_geometry or deleted_geometry:
        raise NativeSketchError(f"{LABEL} unexpectedly changed geometry identity.")
    counts = {
        field: applied.prepared.plan.count(field)
        for field in (
            "removed_horizontal_constraints",
            "removed_vertical_constraints",
            "created_parallel_constraints",
            "removed_axis_symmetry_constraints",
            "removed_point_on_axis_constraints",
            "converted_distance_constraints",
        )
    }
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "input_geometry_count": len(snapshot.spec.geometry_indices),
            **counts,
            "created_constraint_count": len(created_constraints),
            "removed_constraint_count": len(deleted_constraints),
        },
    )
