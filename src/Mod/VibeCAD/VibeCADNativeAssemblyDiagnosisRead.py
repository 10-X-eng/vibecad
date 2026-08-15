# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact preflight and drift guards for Assembly diagnosis reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyDiagnosisState import (
    AssemblyDiagnosisState,
    NativeAssemblyDiagnosisError,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeAssemblyJointGraph import MAX_ASSEMBLY_JOINTS, timeline_active
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import (
    NativeObjectRef,
    read_current_selection,
    resolve_object,
)


MAX_ASSEMBLY_DIAGNOSIS_PAGE = 32
_CATEGORY_ATTRIBUTES = {
    "conflicting": "conflicting_names",
    "redundant": "redundant_names",
    "partially_redundant": "partially_redundant_names",
    "malformed": "malformed_names",
}


@dataclass(frozen=True, slots=True)
class PreparedAssemblyDiagnosisPage:
    state: AssemblyDiagnosisState
    active_before: Any
    selection_before: dict[str, Any]
    category: str
    category_names: tuple[str, ...]
    offset: int
    limit: int


def _category_names(state: AssemblyDiagnosisState, category: str) -> tuple[str, ...]:
    attribute = _CATEGORY_ATTRIBUTES.get(category)
    if attribute is None:
        raise NativeAssemblyDiagnosisError(
            "The requested Assembly solver diagnosis category is unsupported."
        )
    return tuple(getattr(state, attribute))


def _exact_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblyDiagnosisError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _exact_active_assembly(
    context: NativeRuntimeContext,
    assembly_ref: NativeObjectRef,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    context.guard()
    assembly = resolve_object(
        context.document,
        assembly_ref,
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


def preflight_assembly_diagnosis_page(
    context: NativeRuntimeContext,
    *,
    assembly_ref: NativeObjectRef,
    expected_state_sha256: str,
    expected_component_count: int,
    expected_grounded_count: int,
    expected_joint_count: int,
    category: str,
    expected_category_count: int,
    offset: int,
    limit: int,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedAssemblyDiagnosisPage:
    """Freeze one exact bounded page of the most recent solver diagnosis."""

    if not isinstance(assembly_ref, NativeObjectRef):
        raise TypeError("assembly_ref must be a NativeObjectRef")
    if category not in _CATEGORY_ATTRIBUTES:
        raise NativeAssemblyDiagnosisError(
            "The requested Assembly solver diagnosis category is unsupported."
        )
    if (
        not isinstance(expected_state_sha256, str)
        or len(expected_state_sha256) != 64
        or any(value not in "0123456789abcdef" for value in expected_state_sha256)
    ):
        raise NativeAssemblyDiagnosisError(
            "expected_state_sha256 must be one lowercase SHA-256 digest."
        )
    expected_counts = (
        _exact_count(expected_component_count, "expected_component_count", 100_000),
        _exact_count(expected_grounded_count, "expected_grounded_count", 256),
        _exact_count(expected_joint_count, "expected_joint_count", 256),
    )
    expected_category_count = _exact_count(
        expected_category_count,
        f"expected_{category}_count",
        MAX_ASSEMBLY_JOINTS,
    )
    offset = _exact_count(offset, "offset", MAX_ASSEMBLY_JOINTS - 1)
    if type(limit) is not int or not 1 <= limit <= MAX_ASSEMBLY_DIAGNOSIS_PAGE:
        raise NativeAssemblyDiagnosisError(
            "Assembly diagnosis limit must be an integer from 1 through 32."
        )

    selection_before = selection_reader(context.document)
    assembly = _exact_active_assembly(context, assembly_ref, active_reader)
    state = capture_assembly_diagnosis_state(assembly)
    names = _category_names(state, category)
    actual_counts = (
        len(state.components),
        len(state.grounded_joints),
        len(state.regular_joints),
    )
    if expected_counts != actual_counts or expected_category_count != len(names):
        raise NativeAssemblyDiagnosisError(
            "The active Assembly diagnosis counts changed; read current Assemble state and retry."
        )
    if state.state_sha256 != expected_state_sha256:
        raise NativeAssemblyDiagnosisError(
            "The active Assembly diagnosis changed; read current Assemble state and retry."
        )
    if (not names and offset != 0) or (names and offset >= len(names)):
        raise NativeAssemblyDiagnosisError(
            "Assembly diagnosis offset is outside the exact current category set."
        )
    current_assembly = _exact_active_assembly(context, assembly_ref, active_reader)
    if (
        not same_assembly(assembly, current_assembly)
        or selection_reader(context.document) != selection_before
    ):
        raise NativeAssemblyDiagnosisError(
            "The active Assembly or human selection changed during diagnosis preflight."
        )
    return PreparedAssemblyDiagnosisPage(
        state=state,
        active_before=assembly,
        selection_before=selection_before,
        category=category,
        category_names=names,
        offset=offset,
        limit=limit,
    )


def verify_assembly_diagnosis_page_unchanged(
    context: NativeRuntimeContext,
    assembly_ref: NativeObjectRef,
    prepared: PreparedAssemblyDiagnosisPage,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> None:
    """Reject any state, identity, activation, turn, or selection drift."""

    if not isinstance(prepared, PreparedAssemblyDiagnosisPage):
        raise TypeError("prepared must be a PreparedAssemblyDiagnosisPage")
    current_assembly = _exact_active_assembly(context, assembly_ref, active_reader)
    current = capture_assembly_diagnosis_state(current_assembly)
    if (
        not same_assembly(prepared.active_before, current_assembly)
        or not _same_exact_state(prepared.state, current)
        or selection_reader(context.document) != prepared.selection_before
    ):
        raise NativeAssemblyDiagnosisError(
            "The active Assembly or human selection changed during diagnosis."
        )
    context.guard()
