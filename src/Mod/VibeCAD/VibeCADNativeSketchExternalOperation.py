# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact transaction pipeline for Sketch external-geometry actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExternalDiagnostic import (
    SketchExternalPlan,
    parse_external_diagnostic,
)
from VibeCADNativeSketchExternalState import (
    SketchExternalSnapshot,
    capture_external_snapshot,
    require_external_snapshot_unchanged,
    require_pure_external_diagnostic,
    verify_external_state,
)
from VibeCADNativeSketchExternalTarget import SketchExternalSpec
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeTargets import object_identity, object_reference


@dataclass(frozen=True, slots=True)
class PreparedSketchExternalOperation:
    snapshot: SketchExternalSnapshot
    plan: SketchExternalPlan
    label: str
    operation: str
    intersection: bool


def _diagnose(
    sketch: Any,
    source: Any,
    spec: SketchExternalSpec,
    *,
    label: str,
    intersection: bool,
) -> Any:
    diagnose = getattr(sketch, "diagnoseExternal", None)
    if not callable(diagnose):
        raise NativeSketchError(f"{label} feasibility is unavailable.")
    try:
        return diagnose(
            source.Name,
            spec.subelement,
            spec.defining,
            intersection,
        )
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {label} source.") from exc


def _plan(
    snapshot: SketchExternalSnapshot,
    *,
    label: str,
    intersection: bool,
) -> SketchExternalPlan:
    return parse_external_diagnostic(
        _diagnose(
            snapshot.target.sketch,
            snapshot.source,
            snapshot.spec,
            label=label,
            intersection=intersection,
        ),
        snapshot,
        label=label,
        intersection=intersection,
    )


def preflight_sketch_external_operation(
    context: NativeRuntimeContext,
    spec: SketchExternalSpec,
    *,
    label: str,
    operation: str,
    intersection: bool,
) -> PreparedSketchExternalOperation:
    snapshot = capture_external_snapshot(context, spec, label=label)
    plan = _plan(snapshot, label=label, intersection=intersection)
    require_pure_external_diagnostic(snapshot, label=label)
    return PreparedSketchExternalOperation(
        snapshot,
        plan,
        label,
        operation,
        intersection,
    )


def create_sketch_external_operation(
    document: Any,
    prepared: PreparedSketchExternalOperation,
    *,
    operation: str,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchExternalOperation):
        raise TypeError("prepared must be exact Sketch external-geometry state")
    if prepared.operation != operation:
        raise TypeError("prepared state belongs to a different Sketch operation")
    snapshot = prepared.snapshot
    sketch, source = require_external_snapshot_unchanged(
        document,
        snapshot,
        label=prepared.label,
    )
    current_plan = _plan(
        snapshot,
        label=prepared.label,
        intersection=prepared.intersection,
    )
    require_pure_external_diagnostic(snapshot, label=prepared.label)
    if current_plan != prepared.plan:
        raise NativeSketchError(
            f"The exact {prepared.label} result changed after preflight."
        )
    try:
        sketch.addExternal(
            source.Name,
            snapshot.spec.subelement,
            snapshot.spec.defining,
            prepared.intersection,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {prepared.label} operation."
        ) from exc
    return NativeMutationDraft(
        value=prepared,
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_external_operation(
    document: Any,
    draft: NativeMutationDraft,
    *,
    operation: str,
) -> dict[str, Any]:
    prepared = draft.value
    if not isinstance(prepared, PreparedSketchExternalOperation):
        raise TypeError("draft must contain exact Sketch external-geometry state")
    if prepared.operation != operation:
        raise TypeError("draft belongs to a different Sketch operation")
    snapshot = prepared.snapshot
    plan = prepared.plan
    sketch, affected_indices = verify_external_state(
        document,
        snapshot,
        plan,
        label=prepared.label,
    )
    source = object_reference(snapshot.source)
    if snapshot.spec.subelement:
        source["subelement"] = snapshot.spec.subelement
    indices = list(affected_indices[:32])
    payload: dict[str, Any] = {
        "operation": prepared.operation,
        "source": source,
        "role": "defining" if plan.defining else "reference",
        "outcome": plan.outcome,
        "reference_index": plan.reference_index,
        "reference_kind": plan.final_kind,
        "affected_geometry_count": len(affected_indices),
        "affected_geometry_indices": indices,
        "external_reference_count": len(plan.reference_records),
        "external_geometry_count": plan.external_geometry_count,
    }
    if len(affected_indices) > len(indices):
        payload["affected_geometry_indices_truncated"] = True
    return sketch_geometry_result(sketch, payload)
