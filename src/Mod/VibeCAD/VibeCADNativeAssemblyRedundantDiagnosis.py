# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact non-mutating read of fully redundant native Assembly joints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyDiagnosisRead import (
    MAX_ASSEMBLY_DIAGNOSIS_PAGE,
    PreparedAssemblyDiagnosisPage,
    preflight_assembly_diagnosis_page,
    verify_assembly_diagnosis_page_unchanged,
)
from VibeCADNativeAssemblyDiagnosisState import (
    AssemblyDiagnosisState,
    NativeAssemblyDiagnosisError,
    SolverJointDiagnosis,
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


MAX_REDUNDANT_DIAGNOSIS_PAGE = MAX_ASSEMBLY_DIAGNOSIS_PAGE


@dataclass(frozen=True, slots=True)
class RedundantConstraintsSpec:
    assembly_ref: NativeObjectRef
    expected_diagnosis_state_sha256: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_redundant_count: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class PreparedRedundantConstraints:
    spec: RedundantConstraintsSpec
    state: AssemblyDiagnosisState
    active_before: Any
    selection_before: dict[str, Any]


def preflight_redundant_constraints(
    context: NativeRuntimeContext,
    spec: RedundantConstraintsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedRedundantConstraints:
    """Freeze the exact last-solve redundant-joint state without mutation."""

    if not isinstance(spec, RedundantConstraintsSpec):
        raise TypeError("spec must be a RedundantConstraintsSpec")
    prepared = preflight_assembly_diagnosis_page(
        context,
        assembly_ref=spec.assembly_ref,
        expected_state_sha256=spec.expected_diagnosis_state_sha256,
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        category="redundant",
        expected_category_count=spec.expected_redundant_count,
        offset=spec.offset,
        limit=spec.limit,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    return PreparedRedundantConstraints(
        spec=spec,
        state=prepared.state,
        active_before=prepared.active_before,
        selection_before=prepared.selection_before,
    )


def _redundant_result(diagnosis: SolverJointDiagnosis) -> dict[str, Any]:
    joint = diagnosis.joint
    if (
        diagnosis.constraint_count < 1
        or diagnosis.redundant_constraint_count < 1
        or not any(
            value.specification.startswith("Redundant")
            for value in diagnosis.constraints
        )
    ):
        raise NativeAssemblyDiagnosisError(
            "A joint no longer belongs to the native redundant solver category."
        )
    try:
        first = connector_summary(joint.Reference1, joint.Offset1)
        second = connector_summary(joint.Reference2, joint.Offset2)
    except (AttributeError, NativeAssemblyJointConnectorError) as exc:
        raise NativeAssemblyDiagnosisError(
            "A redundant joint no longer has two exact native connectors."
        ) from exc
    return {
        "joint": object_reference(joint),
        "label": str(getattr(joint, "Label", "") or "")[:256],
        "joint_type": str(getattr(joint, "JointType", "") or "")[:64],
        "diagnostic_status": diagnosis.status,
        "redundancy": (
            "complete"
            if diagnosis.redundant_constraint_count == diagnosis.constraint_count
            else "partial"
        ),
        "first": first,
        "second": second,
        "constraint_count": diagnosis.constraint_count,
        "redundant_constraint_count": diagnosis.redundant_constraint_count,
        "removed_degrees_of_freedom": diagnosis.removed_degrees_of_freedom,
    }


def read_redundant_constraints(
    context: NativeRuntimeContext,
    spec: RedundantConstraintsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Return one exact page matching the human redundant-selection command."""

    prepared = preflight_redundant_constraints(
        context,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    state = prepared.state
    by_name = {str(item.joint.Name): item for item in state.joint_diagnostics}
    end = min(len(state.redundant_names), spec.offset + spec.limit)
    page_names = state.redundant_names[spec.offset : end]
    redundant = [_redundant_result(by_name[name]) for name in page_names]
    verify_assembly_diagnosis_page_unchanged(
        context,
        spec.assembly_ref,
        PreparedAssemblyDiagnosisPage(
            state=state,
            active_before=prepared.active_before,
            selection_before=prepared.selection_before,
            category="redundant",
            category_names=state.redundant_names,
            offset=spec.offset,
            limit=spec.limit,
        ),
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    result = {
        "operation": "select_redundant_constraints",
        "assembly": object_reference(state.assembly),
        "diagnosis_state_sha256": state.state_sha256,
        "solver_status": state.solver_status,
        "remaining_degrees_of_freedom": state.remaining_degrees_of_freedom,
        "redundant_joint_count": len(state.redundant_names),
        "offset": spec.offset,
        "returned_count": len(redundant),
        "redundant_joints": redundant,
    }
    if state.solver_message:
        result["solver_message"] = state.solver_message
    if end < len(state.redundant_names):
        result["next_offset"] = end
    return result
