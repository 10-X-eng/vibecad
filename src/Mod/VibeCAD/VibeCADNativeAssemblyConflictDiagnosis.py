# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact non-mutating read of conflicting joints from the native solver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyDiagnosisState import (
    AssemblyDiagnosisState,
    NativeAssemblyDiagnosisError,
    SolverJointDiagnosis,
)
from VibeCADNativeAssemblyDiagnosisRead import (
    MAX_ASSEMBLY_DIAGNOSIS_PAGE,
    PreparedAssemblyDiagnosisPage,
    preflight_assembly_diagnosis_page,
    verify_assembly_diagnosis_page_unchanged,
)
from VibeCADNativeAssemblyJointConnectors import (
    NativeAssemblyJointConnectorError,
    connector_summary,
)
from VibeCADNativeAssemblyState import read_active_assembly
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_reference,
    read_current_selection,
)


MAX_CONFLICT_DIAGNOSIS_PAGE = MAX_ASSEMBLY_DIAGNOSIS_PAGE


@dataclass(frozen=True, slots=True)
class ConflictingConstraintsSpec:
    assembly_ref: NativeObjectRef
    expected_diagnosis_state_sha256: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_conflicting_count: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class PreparedConflictingConstraints:
    spec: ConflictingConstraintsSpec
    state: AssemblyDiagnosisState
    active_before: Any
    selection_before: dict[str, Any]


def preflight_conflicting_constraints(
    context: NativeRuntimeContext,
    spec: ConflictingConstraintsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedConflictingConstraints:
    """Freeze the exact last-solve conflict state without changing selection."""

    if not isinstance(spec, ConflictingConstraintsSpec):
        raise TypeError("spec must be a ConflictingConstraintsSpec")
    prepared = preflight_assembly_diagnosis_page(
        context,
        assembly_ref=spec.assembly_ref,
        expected_state_sha256=spec.expected_diagnosis_state_sha256,
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        category="conflicting",
        expected_category_count=spec.expected_conflicting_count,
        offset=spec.offset,
        limit=spec.limit,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    return PreparedConflictingConstraints(
        spec=spec,
        state=prepared.state,
        active_before=prepared.active_before,
        selection_before=prepared.selection_before,
    )


def _conflict_result(
    state: AssemblyDiagnosisState,
    diagnosis: SolverJointDiagnosis,
) -> dict[str, Any]:
    joint = diagnosis.joint
    try:
        first = connector_summary(joint.Reference1, joint.Offset1)
        second = connector_summary(joint.Reference2, joint.Offset2)
    except (AttributeError, NativeAssemblyJointConnectorError) as exc:
        raise NativeAssemblyDiagnosisError(
            "A conflicting joint no longer has two exact native connectors."
        ) from exc
    violating = sum(
        abs(constraint.residual) > state.residual_tolerance
        for constraint in diagnosis.constraints
    )
    if violating < 1:
        raise NativeAssemblyDiagnosisError(
            "A conflicting joint no longer has a residual above solver tolerance."
        )
    return {
        "joint": object_reference(joint),
        "label": str(getattr(joint, "Label", "") or "")[:256],
        "joint_type": str(getattr(joint, "JointType", "") or "")[:64],
        "first": first,
        "second": second,
        "constraint_count": diagnosis.constraint_count,
        "violating_constraint_count": violating,
        "maximum_absolute_residual": diagnosis.maximum_absolute_residual,
    }


def read_conflicting_constraints(
    context: NativeRuntimeContext,
    spec: ConflictingConstraintsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Return one bounded exact page matching the human conflict-selection set."""

    prepared = preflight_conflicting_constraints(
        context,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    state = prepared.state
    by_name = {str(item.joint.Name): item for item in state.joint_diagnostics}
    end = min(len(state.conflicting_names), spec.offset + spec.limit)
    page_names = state.conflicting_names[spec.offset : end]
    conflicts = [_conflict_result(state, by_name[name]) for name in page_names]
    verify_assembly_diagnosis_page_unchanged(
        context,
        spec.assembly_ref,
        PreparedAssemblyDiagnosisPage(
            state=state,
            active_before=prepared.active_before,
            selection_before=prepared.selection_before,
            category="conflicting",
            category_names=state.conflicting_names,
            offset=spec.offset,
            limit=spec.limit,
        ),
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    result = {
        "operation": "select_conflicting_constraints",
        "assembly": object_reference(state.assembly),
        "diagnosis_state_sha256": state.state_sha256,
        "solver_status": state.solver_status,
        "remaining_degrees_of_freedom": state.remaining_degrees_of_freedom,
        "residual_tolerance": state.residual_tolerance,
        "conflicting_joint_count": len(state.conflicting_names),
        "offset": spec.offset,
        "returned_count": len(conflicts),
        "conflicting_joints": conflicts,
    }
    if state.solver_message:
        result["solver_message"] = state.solver_message
    if end < len(state.conflicting_names):
        result["next_offset"] = end
    return result
