# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Driving/Reference changes in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintToggleDiagnostic import (
    diagnose_constraint_state_changes,
)
from VibeCADNativeSketchDrivingState import (
    FrozenSketchDrivingState,
    expected_constraint_records,
    expected_expression_records,
    read_sketch_driving_state,
    sketch_geometry_metadata,
    validate_sketch_driving_targets,
)
from VibeCADNativeSketchDrivingTarget import (
    LABEL,
    SketchDrivingSpec,
    prepare_sketch_driving_target,
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
class PreparedSketchDriving:
    target: PreparedActiveSketchTarget
    spec: SketchDrivingSpec
    state: FrozenSketchDrivingState
    resolved_constraints: tuple[dict[str, Any], ...]
    diagnosed_degrees_of_freedom: int


def prepare_sketch_driving(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchDrivingSpec:
    return prepare_sketch_driving_target(document_uid, value)


def _diagnose_driving_changes(sketch: Any, spec: SketchDrivingSpec) -> int:
    return diagnose_constraint_state_changes(
        sketch,
        spec.targets,
        method_name="diagnoseDrivingChanges",
        state_result_field="driving_states",
        target_state_field="driving",
        label=LABEL,
    )


def preflight_sketch_driving(
    context: NativeRuntimeContext,
    spec: SketchDrivingSpec,
) -> PreparedSketchDriving:
    if not isinstance(spec, SketchDrivingSpec):
        raise TypeError("spec must be a SketchDrivingSpec")
    target = preflight_active_sketch(context, spec.target)
    sketch = target.sketch
    state = read_sketch_driving_state(sketch, spec)
    if len(state.external_geometry_records) != spec.expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; read its current state "
            "and retry."
        )
    if any(state.solver_issues):
        raise NativeSketchError(
            f"{LABEL} requires a Sketch without current solver issues."
        )
    resolved = validate_sketch_driving_targets(sketch, spec, state)
    degrees = _diagnose_driving_changes(sketch, spec)
    if read_sketch_driving_state(sketch, spec) != state:
        raise NativeSketchError(f"{LABEL} feasibility changed the active Sketch.")
    return PreparedSketchDriving(target, spec, state, resolved, degrees)


def _require_unchanged(
    document: Any,
    prepared: PreparedSketchDriving,
    *,
    stage: str,
) -> Any:
    sketch = require_prepared_active_sketch(document, prepared.target)
    if read_sketch_driving_state(sketch, prepared.spec) != prepared.state:
        raise NativeSketchError(f"The active Sketch changed {stage}.")
    return sketch


def create_sketch_driving(
    document: Any,
    prepared: PreparedSketchDriving,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchDriving):
        raise TypeError("prepared must be a PreparedSketchDriving")
    sketch = _require_unchanged(
        document,
        prepared,
        stage="after Driving/Reference preflight",
    )
    try:
        for target in prepared.spec.targets:
            sketch.toggleDriving(target.constraint_index)
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} states.") from exc
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _changed_constraints(prepared: PreparedSketchDriving) -> list[dict[str, Any]]:
    before_expressions = prepared.state.expression_records
    result = []
    for target, record in zip(
        prepared.spec.targets,
        prepared.resolved_constraints,
        strict=True,
    ):
        result.append(
            {
                "constraint_index": target.constraint_index,
                "constraint_type": str(record["type"]),
                "previous_driving": target.expected_driving,
                "current_driving": target.driving,
                "expression_removed": bool(
                    not target.driving
                    and any(
                        expression.constraint_index == target.constraint_index
                        for expression in before_expressions
                    )
                ),
            }
        )
    return result


def verify_sketch_driving(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchDriving):
        raise TypeError("draft must contain exact prepared Driving/Reference state")
    sketch = require_prepared_active_sketch(document, prepared.target)
    current = read_sketch_driving_state(sketch, prepared.spec)
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
    expected_expressions = expected_expression_records(
        prepared.state,
        prepared.spec.targets,
    )
    if current.expression_records != expected_expressions:
        raise NativeSketchError(
            f"{LABEL} changed expressions beyond removing exact reference targets."
        )
    if any(current.solver_issues):
        raise NativeSketchError(f"{LABEL} introduced a solver issue.")
    return sketch_geometry_result(
        sketch,
        {
            "operation": "toggle_driving_reference",
            "changed_constraints": _changed_constraints(prepared),
            "diagnosed_degrees_of_freedom": (prepared.diagnosed_degrees_of_freedom),
        },
    )
