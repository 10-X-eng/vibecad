# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of the human Sketch Offset command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchOffsetState import (
    SketchOffsetPlan,
    SketchOffsetSnapshot,
    capture_offset_snapshot,
    parse_offset_diagnostic,
    require_offset_snapshot_unchanged,
    require_pure_offset_diagnostic,
    verify_offset_state,
)
from VibeCADNativeSketchOffsetTarget import (
    LABEL,
    SketchOffsetSpec,
    prepare_sketch_offset,
)
from VibeCADNativeTargets import object_identity


OPERATION = "offset"


@dataclass(frozen=True, slots=True)
class PreparedSketchOffset:
    snapshot: SketchOffsetSnapshot
    plan: SketchOffsetPlan


@dataclass(frozen=True, slots=True)
class AppliedSketchOffset:
    prepared: PreparedSketchOffset
    receipt: Any


def _diagnose(snapshot: SketchOffsetSnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseOffset", None)
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    spec = snapshot.spec
    try:
        return method(
            list(spec.geometry_indices),
            spec.offset_length_mm,
            spec.join_value,
            spec.source_value,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} target and offset options."
        ) from exc


def prepare_offset(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchOffsetSpec:
    return prepare_sketch_offset(document_uid, value)


def preflight_offset(
    context: NativeRuntimeContext,
    spec: SketchOffsetSpec,
) -> PreparedSketchOffset:
    snapshot = capture_offset_snapshot(context, spec)
    plan = parse_offset_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_offset_diagnostic(snapshot)
    return PreparedSketchOffset(snapshot, plan)


def create_offset(
    document: Any,
    prepared: PreparedSketchOffset,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchOffset):
        raise TypeError("prepared must be exact Sketch Offset state")
    sketch = require_offset_snapshot_unchanged(document, prepared.snapshot)
    current_plan = parse_offset_diagnostic(
        _diagnose(prepared.snapshot), prepared.snapshot
    )
    require_pure_offset_diagnostic(prepared.snapshot)
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
    method = getattr(sketch, "offsetExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    spec = prepared.snapshot.spec
    try:
        receipt = method(
            list(spec.geometry_indices),
            spec.offset_length_mm,
            spec.join_value,
            spec.source_value,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchOffset(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_offset(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchOffset):
        raise TypeError("draft must contain applied Sketch Offset state")
    spec = applied.prepared.snapshot.spec
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_offset_state(
        document,
        applied.prepared.snapshot,
        applied.prepared.plan,
        applied.receipt,
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "input_geometry_count": len(spec.geometry_indices),
            "offset_distance": {"value": spec.offset_length_mm, "unit": "mm"},
            "join_type": spec.join_type,
            "source_mode": spec.source_mode,
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
