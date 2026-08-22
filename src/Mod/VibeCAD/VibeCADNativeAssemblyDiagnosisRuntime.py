# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Assembly joint diagnosis."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyConflictDiagnosis import (
    ConflictingConstraintsSpec,
    read_conflicting_constraints,
)
from VibeCADNativeAssemblyComponentJoints import (
    ComponentJointsSpec,
    DEFAULT_COMPONENT_JOINT_PAGE,
    MAX_COMPONENT_JOINT_PAGE,
    capture_component_joint_state,
    read_component_joints,
)
from VibeCADNativeAssemblyDiagnosisState import (
    NativeAssemblyDiagnosisError,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeAssemblyDiagnosisRead import MAX_ASSEMBLY_DIAGNOSIS_PAGE
from VibeCADNativeAssemblyMalformedDiagnosis import (
    MalformedConstraintsSpec,
    read_malformed_constraints,
)
from VibeCADNativeAssemblyPartialRedundancyDiagnosis import (
    PartiallyRedundantConstraintsSpec,
    read_partially_redundant_constraints,
)
from VibeCADNativeAssemblyRedundantDiagnosis import (
    RedundantConstraintsSpec,
    read_redundant_constraints,
)
from VibeCADNativeAssemblyState import read_active_assembly
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import NativeObjectRef, NativeTargetError


_CATEGORY_OPERATIONS = frozenset(
    {
        "select_conflicting_constraints",
        "select_redundant_constraints",
        "select_partially_redundant_constraints",
        "select_malformed_constraints",
    }
)


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeArgumentError("Native capability arguments must be an object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "").strip()
    required = set()
    allowed = {"offset", "limit"}
    if operation == "select_joints_of_component":
        required.add("component")
        allowed.add("component")
    elif operation not in _CATEGORY_OPERATIONS and operation != "read":
        raise NativeArgumentError("Native capability operation is unavailable.")
    if not required <= set(values) or not set(values) <= allowed:
        raise NativeArgumentError(
            "Native capability arguments do not match the selected operation."
        )
    return operation, values


def _count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblyDiagnosisError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _positive_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise NativeAssemblyDiagnosisError(
            f"{field} must be an integer from 1 through {maximum}."
        )
    return value


def _object_ref(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyDiagnosisError(
            f"{field} must identify one object."
        )
    try:
        return NativeObjectRef(document_uid, str(value["object_name"]))
    except NativeTargetError as exc:
        raise NativeAssemblyDiagnosisError(str(exc)) from exc


class NativeAssemblyDiagnosisRuntime:
    """Read exact joint diagnosis in one frozen assembly turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def diagnose(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = _arguments(arguments)
        self._context.guard()
        assembly = read_active_assembly(self._context.document)
        if assembly is None:
            raise NativeAssemblyDiagnosisError("No Assembly is active.")
        assembly_ref = NativeObjectRef(
            self._context.document_uid,
            str(assembly.Name),
        )
        offset = _count(values.get("offset", 0), "offset", 255)

        if operation == "select_joints_of_component":
            state = capture_component_joint_state(assembly)
            spec = ComponentJointsSpec(
                assembly_ref=assembly_ref,
                component_ref=_object_ref(
                    self._context.document_uid,
                    values["component"],
                    "component",
                ),
                expected_joint_graph_state_sha256=state.state_sha256,
                expected_component_count=len(state.components),
                expected_joint_count=len(state.joints),
                offset=offset,
                limit=_positive_count(
                    values.get("limit", DEFAULT_COMPONENT_JOINT_PAGE),
                    "limit",
                    MAX_COMPONENT_JOINT_PAGE,
                ),
            )
            return read_component_joints(self._context, spec)

        state = capture_assembly_diagnosis_state(assembly)
        if operation == "read":
            self._context.guard()
            return {
                "assembly": {
                    "object_name": str(assembly.Name),
                    "label": str(getattr(assembly, "Label", "") or ""),
                },
                **state.summary(),
            }
        common = {
            "assembly_ref": assembly_ref,
            "expected_diagnosis_state_sha256": state.state_sha256,
            "expected_component_count": len(state.components),
            "expected_grounded_count": len(state.grounded_joints),
            "expected_joint_count": len(state.regular_joints),
            "offset": offset,
            "limit": _positive_count(
                values.get("limit", 32),
                "limit",
                MAX_ASSEMBLY_DIAGNOSIS_PAGE,
            ),
        }
        if operation == "select_conflicting_constraints":
            return read_conflicting_constraints(
                self._context,
                ConflictingConstraintsSpec(
                    **common,
                    expected_conflicting_count=len(state.conflicting_names),
                ),
            )
        if operation == "select_redundant_constraints":
            return read_redundant_constraints(
                self._context,
                RedundantConstraintsSpec(
                    **common,
                    expected_redundant_count=len(state.redundant_names),
                ),
            )
        if operation == "select_partially_redundant_constraints":
            return read_partially_redundant_constraints(
                self._context,
                PartiallyRedundantConstraintsSpec(
                    **common,
                    expected_partially_redundant_count=len(
                        state.partially_redundant_names
                    ),
                ),
            )
        if operation == "select_malformed_constraints":
            return read_malformed_constraints(
                self._context,
                MalformedConstraintsSpec(
                    **common,
                    expected_malformed_count=len(state.malformed_names),
                ),
            )
        raise NativeAssemblyDiagnosisError(
            "The Assembly diagnosis operation is not implemented."
        )

    def component_joints(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise NativeArgumentError("Native capability arguments must be an object.")
        values = dict(arguments)
        if values.pop("operation", None) != "read":
            raise NativeArgumentError("Native capability operation is unavailable.")
        return self.diagnose(
            {"operation": "select_joints_of_component", **values}
        )
