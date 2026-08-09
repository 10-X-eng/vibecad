# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic human-parity Join Curves for two exact endpoints in the open Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchJoinDiagnostic import SketchJoinPlan, parse_join_diagnostic
from VibeCADNativeSketchJoinState import (
    SketchJoinSnapshot,
    capture_join_snapshot,
    require_join_snapshot_unchanged,
    require_pure_join_diagnostic,
    verify_join_state,
)
from VibeCADNativeSketchJoinTarget import (
    LABEL,
    SketchJoinSpec,
    prepare_sketch_join_target,
)
from VibeCADNativeTargets import object_identity


OPERATION = "join_curves"


@dataclass(frozen=True, slots=True)
class PreparedSketchJoin:
    snapshot: SketchJoinSnapshot
    plan: SketchJoinPlan


@dataclass(frozen=True, slots=True)
class AppliedSketchJoin:
    prepared: PreparedSketchJoin
    receipt: Any


def _diagnose(snapshot: SketchJoinSnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseJoinCurves", None)
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    spec = snapshot.spec
    try:
        return method(
            spec.first.geometry_index,
            spec.first.endpoint_code,
            spec.second.geometry_index,
            spec.second.endpoint_code,
        )
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} target.") from exc


def prepare_sketch_join(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchJoinSpec:
    return prepare_sketch_join_target(document_uid, value)


def preflight_sketch_join(
    context: NativeRuntimeContext,
    spec: SketchJoinSpec,
) -> PreparedSketchJoin:
    snapshot = capture_join_snapshot(context, spec)
    plan = parse_join_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_join_diagnostic(snapshot)
    return PreparedSketchJoin(snapshot, plan)


def create_sketch_join(
    document: Any,
    prepared: PreparedSketchJoin,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchJoin):
        raise TypeError("prepared must be exact Join Curves state")
    snapshot = prepared.snapshot
    sketch = require_join_snapshot_unchanged(document, snapshot)
    current_plan = parse_join_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_join_diagnostic(snapshot)
    if current_plan != prepared.plan:
        raise NativeSketchError(f"The exact {LABEL} result changed after preflight.")
    method = getattr(sketch, "joinCurvesExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    spec = snapshot.spec
    try:
        receipt = method(
            spec.first.geometry_index,
            spec.first.endpoint_code,
            spec.second.geometry_index,
            spec.second.endpoint_code,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchJoin(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_join(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchJoin):
        raise TypeError("draft must contain applied Join Curves state")
    snapshot = applied.prepared.snapshot
    plan = applied.prepared.plan
    sketch, created, deleted, _created_constraints, _deleted_constraints = (
        verify_join_state(document, snapshot, plan, applied.receipt)
    )
    joined = json.loads(plan.transform.geometry_records[plan.joined_geometry_index])
    spec = snapshot.spec
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "first": {
                "geometry_index": spec.first.geometry_index,
                "endpoint": spec.first.endpoint,
            },
            "second": {
                "geometry_index": spec.second.geometry_index,
                "endpoint": spec.second.endpoint,
            },
            "continuity": f"C{plan.continuity}",
            "joined_geometry_index": plan.joined_geometry_index,
            "joined_geometry": joined,
            "deleted_geometry_indices": list(deleted),
            "created_geometry_indices": list(created),
            "created_helper_count": plan.helper_count,
        },
    )
