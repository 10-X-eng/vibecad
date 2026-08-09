# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Active/Inactive changes in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchActiveState import (
    FrozenSketchActiveState,
    expected_constraint_records,
    read_sketch_active_state,
    sketch_geometry_metadata,
    validate_sketch_active_targets,
)
from VibeCADNativeSketchActiveTarget import (
    LABEL,
    SketchActiveSpec,
    prepare_sketch_active_target,
)
from VibeCADNativeSketchConstraintToggleDiagnostic import (
    diagnose_constraint_state_changes,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import (
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchActive:
    target: PreparedActiveSketchTarget
    spec: SketchActiveSpec
    state: FrozenSketchActiveState
    resolved_constraints: tuple[dict[str, Any], ...]
    diagnosed_degrees_of_freedom: int


def prepare_sketch_active(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchActiveSpec:
    return prepare_sketch_active_target(document_uid, value)


def _diagnose_active_changes(sketch: Any, spec: SketchActiveSpec) -> int:
    return diagnose_constraint_state_changes(
        sketch,
        spec.targets,
        method_name="diagnoseActiveChanges",
        state_result_field="active_states",
        target_state_field="active",
        label=LABEL,
    )


def preflight_sketch_active(
    context: NativeRuntimeContext,
    spec: SketchActiveSpec,
) -> PreparedSketchActive:
    if not isinstance(spec, SketchActiveSpec):
        raise TypeError("spec must be a SketchActiveSpec")
    target = preflight_active_sketch(context, spec.target)
    sketch = target.sketch
    state = read_sketch_active_state(sketch, spec)
    if len(state.external_geometry_records) != spec.expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; read its current state "
            "and retry."
        )
    if any(state.solver_issues):
        raise NativeSketchError(
            f"{LABEL} requires a Sketch without current solver issues."
        )
    resolved = validate_sketch_active_targets(sketch, spec, state)
    degrees = _diagnose_active_changes(sketch, spec)
    if read_sketch_active_state(sketch, spec) != state:
        raise NativeSketchError(f"{LABEL} feasibility changed the active Sketch.")
    return PreparedSketchActive(target, spec, state, resolved, degrees)


def _require_unchanged(
    document: Any,
    prepared: PreparedSketchActive,
    *,
    stage: str,
) -> Any:
    sketch = require_prepared_active_sketch(document, prepared.target)
    if read_sketch_active_state(sketch, prepared.spec) != prepared.state:
        raise NativeSketchError(f"The active Sketch changed {stage}.")
    return sketch


def create_sketch_active(
    document: Any,
    prepared: PreparedSketchActive,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchActive):
        raise TypeError("prepared must be a PreparedSketchActive")
    sketch = _require_unchanged(
        document,
        prepared,
        stage="after Active/Inactive preflight",
    )
    try:
        for target in prepared.spec.targets:
            sketch.toggleActive(target.constraint_index)
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} states.") from exc
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _changed_constraints(prepared: PreparedSketchActive) -> list[dict[str, Any]]:
    return [
        {
            "constraint_index": target.constraint_index,
            "constraint_type": str(record["type"]),
            "previous_active": target.expected_active,
            "current_active": target.active,
        }
        for target, record in zip(
            prepared.spec.targets,
            prepared.resolved_constraints,
            strict=True,
        )
    ]


def verify_sketch_active(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchActive):
        raise TypeError("draft must contain exact prepared Active/Inactive state")
    sketch = require_prepared_active_sketch(document, prepared.target)
    current = read_sketch_active_state(sketch, prepared.spec)
    if sketch_geometry_metadata(current.geometry_records) != sketch_geometry_metadata(
        prepared.state.geometry_records
    ):
        raise NativeSketchError(f"{LABEL} changed Sketch geometry metadata.")
    if current.external_geometry_records != prepared.state.external_geometry_records:
        raise NativeSketchError(f"{LABEL} changed external geometry.")
    expected_constraints = expected_constraint_records(
        prepared.state,
        prepared.spec.targets,
    )
    if current.constraint_records != expected_constraints:
        raise NativeSketchError(
            f"{LABEL} changed constraints beyond the exact requested states."
        )
    if current.expression_records != prepared.state.expression_records:
        raise NativeSketchError(f"{LABEL} changed Sketch expressions.")
    if any(current.solver_issues):
        raise NativeSketchError(f"{LABEL} introduced a solver issue.")
    return sketch_geometry_result(
        sketch,
        {
            "operation": "toggle_active_inactive",
            "changed_constraints": _changed_constraints(prepared),
            "diagnosed_degrees_of_freedom": (prepared.diagnosed_degrees_of_freedom),
        },
    )
