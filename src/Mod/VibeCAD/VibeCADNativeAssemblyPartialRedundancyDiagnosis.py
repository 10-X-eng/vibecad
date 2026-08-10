# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact non-mutating read of partially redundant native Assembly joints."""

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


MAX_PARTIAL_REDUNDANCY_DIAGNOSIS_PAGE = MAX_ASSEMBLY_DIAGNOSIS_PAGE


@dataclass(frozen=True, slots=True)
class PartiallyRedundantConstraintsSpec:
    assembly_ref: NativeObjectRef
    expected_diagnosis_state_sha256: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_partially_redundant_count: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class PreparedPartiallyRedundantConstraints:
    spec: PartiallyRedundantConstraintsSpec
    state: AssemblyDiagnosisState
    active_before: Any
    selection_before: dict[str, Any]


def preflight_partially_redundant_constraints(
    context: NativeRuntimeContext,
    spec: PartiallyRedundantConstraintsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedPartiallyRedundantConstraints:
    """Freeze the exact last-solve partial-redundancy state without mutation."""

    if not isinstance(spec, PartiallyRedundantConstraintsSpec):
        raise TypeError("spec must be a PartiallyRedundantConstraintsSpec")
    prepared = preflight_assembly_diagnosis_page(
        context,
        assembly_ref=spec.assembly_ref,
        expected_state_sha256=spec.expected_diagnosis_state_sha256,
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        category="partially_redundant",
        expected_category_count=spec.expected_partially_redundant_count,
        offset=spec.offset,
        limit=spec.limit,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    return PreparedPartiallyRedundantConstraints(
        spec=spec,
        state=prepared.state,
        active_before=prepared.active_before,
        selection_before=prepared.selection_before,
    )


def _partial_result(
    diagnosis: SolverJointDiagnosis,
    *,
    also_in_redundant_set: bool,
) -> dict[str, Any]:
    joint = diagnosis.joint
    if not 0 < diagnosis.redundant_constraint_count < diagnosis.constraint_count:
        raise NativeAssemblyDiagnosisError(
            "A joint no longer has a native partially redundant solver diagnosis."
        )
    try:
        first = connector_summary(joint.Reference1, joint.Offset1)
        second = connector_summary(joint.Reference2, joint.Offset2)
    except (AttributeError, NativeAssemblyJointConnectorError) as exc:
        raise NativeAssemblyDiagnosisError(
            "A partially redundant joint no longer has two exact native connectors."
        ) from exc
    return {
        "joint": object_reference(joint),
        "label": str(getattr(joint, "Label", "") or "")[:256],
        "joint_type": str(getattr(joint, "JointType", "") or "")[:64],
        "diagnostic_status": diagnosis.status,
        "also_in_redundant_set": also_in_redundant_set,
        "first": first,
        "second": second,
        "constraint_count": diagnosis.constraint_count,
        "redundant_constraint_count": diagnosis.redundant_constraint_count,
        "removed_degrees_of_freedom": diagnosis.removed_degrees_of_freedom,
    }


def read_partially_redundant_constraints(
    context: NativeRuntimeContext,
    spec: PartiallyRedundantConstraintsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Return one exact page matching the human partial-selection command."""

    prepared = preflight_partially_redundant_constraints(
        context,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    state = prepared.state
    by_name = {str(item.joint.Name): item for item in state.joint_diagnostics}
    end = min(len(state.partially_redundant_names), spec.offset + spec.limit)
    page_names = state.partially_redundant_names[spec.offset : end]
    redundant_names = frozenset(state.redundant_names)
    partial = [
        _partial_result(
            by_name[name],
            also_in_redundant_set=name in redundant_names,
        )
        for name in page_names
    ]
    verify_assembly_diagnosis_page_unchanged(
        context,
        spec.assembly_ref,
        PreparedAssemblyDiagnosisPage(
            state=state,
            active_before=prepared.active_before,
            selection_before=prepared.selection_before,
            category="partially_redundant",
            category_names=state.partially_redundant_names,
            offset=spec.offset,
            limit=spec.limit,
        ),
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    result = {
        "operation": "select_partially_redundant_constraints",
        "assembly": object_reference(state.assembly),
        "diagnosis_state_sha256": state.state_sha256,
        "solver_status": state.solver_status,
        "remaining_degrees_of_freedom": state.remaining_degrees_of_freedom,
        "partially_redundant_joint_count": len(state.partially_redundant_names),
        "offset": spec.offset,
        "returned_count": len(partial),
        "partially_redundant_joints": partial,
    }
    if state.solver_message:
        result["solver_message"] = state.solver_message
    if end < len(state.partially_redundant_names):
        result["next_offset"] = end
    return result
