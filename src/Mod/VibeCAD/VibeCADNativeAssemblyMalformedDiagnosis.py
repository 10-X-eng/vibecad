# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact non-mutating read of malformed native Assembly drag joints."""

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


MAX_MALFORMED_DIAGNOSIS_PAGE = MAX_ASSEMBLY_DIAGNOSIS_PAGE


@dataclass(frozen=True, slots=True)
class MalformedConstraintsSpec:
    assembly_ref: NativeObjectRef
    expected_diagnosis_state_sha256: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_malformed_count: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class PreparedMalformedConstraints:
    spec: MalformedConstraintsSpec
    state: AssemblyDiagnosisState
    active_before: Any
    selection_before: dict[str, Any]


def preflight_malformed_constraints(
    context: NativeRuntimeContext,
    spec: MalformedConstraintsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedMalformedConstraints:
    """Freeze the exact malformed set from the most recent native drag solve."""

    if not isinstance(spec, MalformedConstraintsSpec):
        raise TypeError("spec must be a MalformedConstraintsSpec")
    prepared = preflight_assembly_diagnosis_page(
        context,
        assembly_ref=spec.assembly_ref,
        expected_state_sha256=spec.expected_diagnosis_state_sha256,
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        category="malformed",
        expected_category_count=spec.expected_malformed_count,
        offset=spec.offset,
        limit=spec.limit,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    return PreparedMalformedConstraints(
        spec=spec,
        state=prepared.state,
        active_before=prepared.active_before,
        selection_before=prepared.selection_before,
    )


def _malformed_result(joint: Any, diagnosed_names: frozenset[str]) -> dict[str, Any]:
    name = str(getattr(joint, "Name", "") or "")
    if not name or name in diagnosed_names:
        raise NativeAssemblyDiagnosisError(
            "A malformed drag joint unexpectedly has native constraint diagnostics."
        )
    try:
        first = connector_summary(joint.Reference1, joint.Offset1)
        second = connector_summary(joint.Reference2, joint.Offset2)
    except (AttributeError, NativeAssemblyJointConnectorError) as exc:
        raise NativeAssemblyDiagnosisError(
            "A malformed drag joint no longer has two exact native connectors."
        ) from exc
    joint_type = str(getattr(joint, "JointType", "") or "")[:64]
    fixed_member = joint_type.casefold() == "fixed"
    return {
        "joint": object_reference(joint),
        "label": str(getattr(joint, "Label", "") or "")[:256],
        "joint_type": joint_type,
        "diagnostic_status": "malformed",
        "reason_code": "same_solver_part_in_fixed_drag_bundle",
        "bundle_role": (
            "fixed_bundle_constraint" if fixed_member else "intra_bundle_constraint"
        ),
        "first": first,
        "second": second,
        "recommended_action": (
            "Keep this Fixed joint when the rigid drag bundle is intentional; otherwise "
            "edit or remove it."
            if fixed_member
            else "Remove this extra joint or break the Fixed path between its components "
            "before dragging."
        ),
    }


def read_malformed_constraints(
    context: NativeRuntimeContext,
    spec: MalformedConstraintsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Return one exact page matching the human malformed-selection command."""

    prepared = preflight_malformed_constraints(
        context,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    state = prepared.state
    by_name = {
        str(getattr(joint, "Name", "") or ""): joint for joint in state.regular_joints
    }
    diagnosed_names = frozenset(
        str(item.joint.Name) for item in state.joint_diagnostics
    )
    end = min(len(state.malformed_names), spec.offset + spec.limit)
    page_names = state.malformed_names[spec.offset : end]
    malformed = [
        _malformed_result(by_name[name], diagnosed_names) for name in page_names
    ]
    verify_assembly_diagnosis_page_unchanged(
        context,
        spec.assembly_ref,
        PreparedAssemblyDiagnosisPage(
            state=state,
            active_before=prepared.active_before,
            selection_before=prepared.selection_before,
            category="malformed",
            category_names=state.malformed_names,
            offset=spec.offset,
            limit=spec.limit,
        ),
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    result = {
        "operation": "select_malformed_constraints",
        "assembly": object_reference(state.assembly),
        "diagnosis_state_sha256": state.state_sha256,
        "solver_scope": "most_recent_fixed_bundle_drag",
        "solver_status": state.solver_status,
        "remaining_degrees_of_freedom": state.remaining_degrees_of_freedom,
        "malformed_joint_count": len(state.malformed_names),
        "offset": spec.offset,
        "returned_count": len(malformed),
        "malformed_joints": malformed,
    }
    if state.solver_message:
        result["solver_message"] = state.solver_message
    if end < len(state.malformed_names):
        result["next_offset"] = end
    return result
