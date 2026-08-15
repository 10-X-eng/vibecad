# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Native implementation of the human Sketch Carbon Copy command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCarbonCopyState import (
    SketchCarbonCopyPlan,
    SketchCarbonCopySnapshot,
    capture_carbon_copy_snapshot,
    parse_carbon_copy_diagnostic,
    require_carbon_copy_snapshot_unchanged,
    require_pure_carbon_copy_diagnostic,
    verify_carbon_copy_state,
)
from VibeCADNativeSketchCarbonCopyTarget import (
    SketchCarbonCopySpec,
    prepare_sketch_carbon_copy,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeTargets import object_identity, object_reference


OPERATION = "carbon_copy"


@dataclass(frozen=True, slots=True)
class PreparedSketchCarbonCopy:
    snapshot: SketchCarbonCopySnapshot
    plan: SketchCarbonCopyPlan


@dataclass(frozen=True, slots=True)
class AppliedSketchCarbonCopy:
    prepared: PreparedSketchCarbonCopy
    receipt: Any


def _diagnose(snapshot: SketchCarbonCopySnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseCarbonCopy", None)
    if not callable(method):
        raise NativeSketchError("Sketch Carbon Copy feasibility is unavailable.")
    try:
        return method(
            snapshot.source.Name,
            snapshot.spec.construction,
            snapshot.spec.allow_other_body,
            snapshot.spec.allow_unaligned,
        )
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact Carbon Copy source and permission mode."
        ) from exc


def prepare_carbon_copy(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchCarbonCopySpec:
    return prepare_sketch_carbon_copy(document_uid, value)


def preflight_carbon_copy(
    context: NativeRuntimeContext,
    spec: SketchCarbonCopySpec,
) -> PreparedSketchCarbonCopy:
    snapshot = capture_carbon_copy_snapshot(context, spec)
    plan = parse_carbon_copy_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_carbon_copy_diagnostic(snapshot)
    return PreparedSketchCarbonCopy(snapshot, plan)


def create_carbon_copy(
    document: Any,
    prepared: PreparedSketchCarbonCopy,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchCarbonCopy):
        raise TypeError("prepared must be exact Sketch Carbon Copy state")
    target, source = require_carbon_copy_snapshot_unchanged(
        document,
        prepared.snapshot,
    )
    current_plan = parse_carbon_copy_diagnostic(
        _diagnose(prepared.snapshot),
        prepared.snapshot,
    )
    require_pure_carbon_copy_diagnostic(prepared.snapshot)
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
                "x_inverted",
                "y_inverted",
            )
            if getattr(current_plan, field) != getattr(prepared.plan, field)
        )
        raise NativeSketchError(
            "The exact Carbon Copy result changed after preflight: "
            + ", ".join(changed)
            + "."
        )
    method = getattr(target, "carbonCopyExact", None)
    if not callable(method):
        raise NativeSketchError("Exact Sketch Carbon Copy execution is unavailable.")
    try:
        receipt = method(
            source.Name,
            prepared.snapshot.spec.construction,
            prepared.snapshot.spec.allow_other_body,
            prepared.snapshot.spec.allow_unaligned,
        )
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact Carbon Copy operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchCarbonCopy(prepared, receipt),
        recompute_targets=(target,),
        changed=(object_identity(target),),
    )


def verify_carbon_copy(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchCarbonCopy):
        raise TypeError("draft must contain applied Sketch Carbon Copy state")
    prepared = applied.prepared
    target, geometry_indices, constraint_indices = verify_carbon_copy_state(
        document,
        prepared.snapshot,
        prepared.plan,
        applied.receipt,
    )
    geometry = list(geometry_indices[:32])
    constraints = list(constraint_indices[:32])
    payload: dict[str, Any] = {
        "operation": OPERATION,
        "source_sketch": object_reference(prepared.snapshot.source),
        "geometry_mode": "construction"
        if prepared.snapshot.spec.construction
        else "regular",
        "reference_permission": prepared.snapshot.spec.reference_permission,
        "x_inverted": prepared.plan.x_inverted,
        "y_inverted": prepared.plan.y_inverted,
        "copied_geometry_count": len(geometry_indices),
        "copied_constraint_count": len(constraint_indices),
        "created_geometry_indices": geometry,
        "created_constraint_indices": constraints,
        "external_reference_count": len(prepared.plan.external_reference_records),
        "external_geometry_count": len(prepared.plan.external_geometry_records),
    }
    if len(geometry_indices) > len(geometry):
        payload["created_geometry_indices_truncated"] = True
    if len(constraint_indices) > len(constraints):
        payload["created_constraint_indices_truncated"] = True
    return sketch_geometry_result(target, payload)
