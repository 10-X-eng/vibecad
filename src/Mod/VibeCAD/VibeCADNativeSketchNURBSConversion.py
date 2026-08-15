# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of the human Geometry-to-B-Spline command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchNURBSConversionState import (
    SketchNURBSConversionPlan,
    capture_nurbs_conversion_snapshot,
    parse_nurbs_conversion_diagnostic,
    require_nurbs_conversion_snapshot_unchanged,
    require_pure_nurbs_conversion_diagnostic,
    verify_nurbs_conversion_state,
)
from VibeCADNativeSketchNURBSConversionTarget import (
    LABEL,
    SketchNURBSConversionSpec,
    prepare_sketch_nurbs_conversion,
)
from VibeCADNativeSketchTransformState import SketchTransformSnapshot
from VibeCADNativeTargets import object_identity


OPERATION = "convert_to_nurbs"


@dataclass(frozen=True, slots=True)
class PreparedSketchNURBSConversion:
    snapshot: SketchTransformSnapshot
    plan: SketchNURBSConversionPlan


@dataclass(frozen=True, slots=True)
class AppliedSketchNURBSConversion:
    prepared: PreparedSketchNURBSConversion
    receipt: Any


def _diagnose(snapshot: SketchTransformSnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseConvertToNURBS", None)
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    try:
        return method(list(snapshot.spec.geometry_indices))
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} targets."
        ) from exc


def prepare_nurbs_conversion(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchNURBSConversionSpec:
    return prepare_sketch_nurbs_conversion(document_uid, value)


def preflight_nurbs_conversion(
    context: NativeRuntimeContext,
    spec: SketchNURBSConversionSpec,
) -> PreparedSketchNURBSConversion:
    snapshot = capture_nurbs_conversion_snapshot(context, spec)
    plan = parse_nurbs_conversion_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_nurbs_conversion_diagnostic(snapshot)
    return PreparedSketchNURBSConversion(snapshot, plan)


def create_nurbs_conversion(
    document: Any,
    prepared: PreparedSketchNURBSConversion,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchNURBSConversion):
        raise TypeError("prepared must be exact Geometry-to-B-Spline state")
    sketch = require_nurbs_conversion_snapshot_unchanged(document, prepared.snapshot)
    current_plan = parse_nurbs_conversion_diagnostic(
        _diagnose(prepared.snapshot), prepared.snapshot
    )
    require_pure_nurbs_conversion_diagnostic(prepared.snapshot)
    if current_plan != prepared.plan:
        raise NativeSketchError(f"The exact {LABEL} result changed after preflight.")
    method = getattr(sketch, "convertToNURBSExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    try:
        receipt = method(list(prepared.snapshot.spec.geometry_indices))
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchNURBSConversion(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_nurbs_conversion(
    document: Any, draft: NativeMutationDraft
) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchNURBSConversion):
        raise TypeError("draft must contain applied Geometry-to-B-Spline state")
    plan = applied.prepared.plan
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_nurbs_conversion_state(
        document,
        applied.prepared.snapshot,
        plan,
        applied.receipt,
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "input_geometry_count": len(
                applied.prepared.snapshot.spec.geometry_indices
            ),
            "converted_geometry_indices": list(plan.converted_geometry_indices),
            "internal_conversion_count": plan.internal_conversion_count,
            "external_copy_count": plan.external_copy_count,
            "exposed_internal_geometry_count": plan.exposed_internal_geometry_count,
            "created_geometry_count": len(created_geometry),
            "removed_geometry_count": len(deleted_geometry),
            "created_constraint_count": len(created_constraints),
            "removed_constraint_count": len(deleted_constraints),
        },
    )
