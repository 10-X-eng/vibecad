# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of the human Sketch Symmetry command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchSymmetryState import (
    SketchSymmetryPlan,
    SketchSymmetrySnapshot,
    capture_symmetry_snapshot,
    parse_symmetry_diagnostic,
    require_pure_symmetry_diagnostic,
    require_symmetry_snapshot_unchanged,
    verify_symmetry_state,
)
from VibeCADNativeSketchSymmetryTarget import (
    LABEL,
    SketchSymmetrySpec,
    prepare_sketch_symmetry,
)
from VibeCADNativeTargets import object_identity


OPERATION = "symmetry"


@dataclass(frozen=True, slots=True)
class PreparedSketchSymmetry:
    snapshot: SketchSymmetrySnapshot
    plan: SketchSymmetryPlan


@dataclass(frozen=True, slots=True)
class AppliedSketchSymmetry:
    prepared: PreparedSketchSymmetry
    receipt: Any


def _diagnose(snapshot: SketchSymmetrySnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseSymmetry", None)
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    spec = snapshot.spec
    try:
        return method(
            list(spec.geometry_indices),
            spec.reference.geometry_index,
            spec.reference.position_code,
            spec.source_value,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} targets and options."
        ) from exc


def prepare_symmetry(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchSymmetrySpec:
    return prepare_sketch_symmetry(document_uid, value)


def preflight_symmetry(
    context: NativeRuntimeContext,
    spec: SketchSymmetrySpec,
) -> PreparedSketchSymmetry:
    snapshot = capture_symmetry_snapshot(context, spec)
    plan = parse_symmetry_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_symmetry_diagnostic(snapshot)
    return PreparedSketchSymmetry(snapshot, plan)


def create_symmetry(
    document: Any,
    prepared: PreparedSketchSymmetry,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchSymmetry):
        raise TypeError("prepared must be exact Sketch Symmetry state")
    sketch = require_symmetry_snapshot_unchanged(document, prepared.snapshot)
    current_plan = parse_symmetry_diagnostic(
        _diagnose(prepared.snapshot), prepared.snapshot
    )
    require_pure_symmetry_diagnostic(prepared.snapshot)
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
    method = getattr(sketch, "symmetryExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    spec = prepared.snapshot.spec
    try:
        receipt = method(
            list(spec.geometry_indices),
            spec.reference.geometry_index,
            spec.reference.position_code,
            spec.source_value,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchSymmetry(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_symmetry(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchSymmetry):
        raise TypeError("draft must contain applied Sketch Symmetry state")
    spec = applied.prepared.snapshot.spec
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_symmetry_state(
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
            "reference": {
                "geometry_index": spec.reference.geometry_index,
                "position": spec.reference.position,
            },
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
