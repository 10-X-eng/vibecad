# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact non-mutating read of conflicting joints from the native solver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyDiagnosisState import (
    AssemblyDiagnosisState,
    NativeAssemblyDiagnosisError,
    SolverJointDiagnosis,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeAssemblyJointConnectors import (
    NativeAssemblyJointConnectorError,
    connector_summary,
)
from VibeCADNativeAssemblyJointGraph import timeline_active
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_reference,
    read_current_selection,
    resolve_object,
)


MAX_CONFLICT_DIAGNOSIS_PAGE = 32


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


def _exact_active_assembly(
    context: NativeRuntimeContext,
    spec: ConflictingConstraintsSpec,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    context.guard()
    assembly = resolve_object(
        context.document,
        spec.assembly_ref,
        expected_types=("Assembly::AssemblyObject",),
    )
    active = active_reader(context.document)
    if not same_assembly(assembly, active):
        raise NativeAssemblyDiagnosisError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    if not timeline_active(assembly):
        raise NativeAssemblyDiagnosisError(
            "The human-active Assembly is outside the current document history."
        )
    return assembly


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
    assembly = _exact_active_assembly(context, spec, active_reader)
    state = capture_assembly_diagnosis_state(assembly)
    expected_counts = (
        spec.expected_component_count,
        spec.expected_grounded_count,
        spec.expected_joint_count,
        spec.expected_conflicting_count,
    )
    actual_counts = (
        len(state.components),
        len(state.grounded_joints),
        len(state.regular_joints),
        len(state.conflicting_names),
    )
    if expected_counts != actual_counts:
        raise NativeAssemblyDiagnosisError(
            "The active Assembly diagnosis counts changed; read current Assemble state and retry."
        )
    if state.state_sha256 != spec.expected_diagnosis_state_sha256:
        raise NativeAssemblyDiagnosisError(
            "The active Assembly diagnosis changed; read current Assemble state and retry."
        )
    if spec.limit < 1 or spec.limit > MAX_CONFLICT_DIAGNOSIS_PAGE:
        raise NativeAssemblyDiagnosisError(
            "Conflict diagnosis limit must be an integer from 1 through 32."
        )
    count = len(state.conflicting_names)
    if (
        spec.offset < 0
        or spec.offset > 255
        or ((count == 0 and spec.offset != 0) or (count > 0 and spec.offset >= count))
    ):
        raise NativeAssemblyDiagnosisError(
            "Conflict diagnosis offset is outside the exact current conflict set."
        )
    return PreparedConflictingConstraints(
        spec=spec,
        state=state,
        active_before=assembly,
        selection_before=selection_reader(context.document),
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


def _same_exact_state(
    expected: AssemblyDiagnosisState,
    current: AssemblyDiagnosisState,
) -> bool:
    return (
        current.state_sha256 == expected.state_sha256
        and current.assembly is expected.assembly
        and current.joint_group is expected.joint_group
        and len(current.components) == len(expected.components)
        and all(
            current_obj is expected_obj
            for current_obj, expected_obj in zip(
                current.components,
                expected.components,
            )
        )
        and len(current.grounded_joints) == len(expected.grounded_joints)
        and all(
            current_obj is expected_obj
            for current_obj, expected_obj in zip(
                current.grounded_joints,
                expected.grounded_joints,
            )
        )
        and len(current.regular_joints) == len(expected.regular_joints)
        and all(
            current_obj is expected_obj
            for current_obj, expected_obj in zip(
                current.regular_joints,
                expected.regular_joints,
            )
        )
    )


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
    current_assembly = _exact_active_assembly(context, spec, active_reader)
    current = capture_assembly_diagnosis_state(current_assembly)
    if (
        not same_assembly(prepared.active_before, current_assembly)
        or not _same_exact_state(state, current)
        or selection_reader(context.document) != prepared.selection_before
    ):
        raise NativeAssemblyDiagnosisError(
            "The active Assembly or human selection changed during conflict diagnosis."
        )
    context.guard()
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
