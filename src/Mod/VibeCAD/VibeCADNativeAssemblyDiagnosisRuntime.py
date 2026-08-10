# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly diagnosis reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyConflictDiagnosis import (
    ConflictingConstraintsSpec,
    read_conflicting_constraints,
)
from VibeCADNativeAssemblyDiagnosisState import NativeAssemblyDiagnosisError
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
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import NativeObjectRef


_CONFLICT_FIELDS = frozenset(
    {
        "assembly",
        "expected_diagnosis_state_sha256",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_conflicting_count",
        "offset",
        "limit",
    }
)
_REDUNDANT_FIELDS = frozenset(
    {
        "assembly",
        "expected_diagnosis_state_sha256",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_redundant_count",
        "offset",
        "limit",
    }
)
_PARTIALLY_REDUNDANT_FIELDS = frozenset(
    {
        "assembly",
        "expected_diagnosis_state_sha256",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_partially_redundant_count",
        "offset",
        "limit",
    }
)
_MALFORMED_FIELDS = frozenset(
    {
        "assembly",
        "expected_diagnosis_state_sha256",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_malformed_count",
        "offset",
        "limit",
    }
)


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


def _digest(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeAssemblyDiagnosisError(
            "expected_diagnosis_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _object_ref(document_uid: str, value: Any) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyDiagnosisError(
            "assembly must contain one exact object_name."
        )
    name = value.get("object_name")
    if not isinstance(name, str) or not name or len(name) > 128:
        raise NativeAssemblyDiagnosisError(
            "assembly.object_name must identify one exact document object."
        )
    return NativeObjectRef(document_uid, name)


class NativeAssemblyDiagnosisRuntime:
    """Execute read-only diagnosis in one frozen human-selected turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def diagnose(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "select_conflicting_constraints": _CONFLICT_FIELDS,
                "select_redundant_constraints": _REDUNDANT_FIELDS,
                "select_partially_redundant_constraints": (
                    _PARTIALLY_REDUNDANT_FIELDS
                ),
                "select_malformed_constraints": _MALFORMED_FIELDS,
            },
        )
        common = {
            "assembly_ref": _object_ref(
                self._context.document_uid,
                values["assembly"],
            ),
            "expected_diagnosis_state_sha256": _digest(
                values["expected_diagnosis_state_sha256"]
            ),
            "expected_component_count": _count(
                values["expected_component_count"],
                "expected_component_count",
                100_000,
            ),
            "expected_grounded_count": _count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                256,
            ),
            "expected_joint_count": _count(
                values["expected_joint_count"],
                "expected_joint_count",
                256,
            ),
            "offset": _count(values["offset"], "offset", 255),
            "limit": _positive_count(values["limit"], "limit", 32),
        }
        if operation == "select_conflicting_constraints":
            spec = ConflictingConstraintsSpec(
                **common,
                expected_conflicting_count=_count(
                    values["expected_conflicting_count"],
                    "expected_conflicting_count",
                    256,
                ),
            )
            return read_conflicting_constraints(self._context, spec)
        if operation == "select_redundant_constraints":
            spec = RedundantConstraintsSpec(
                **common,
                expected_redundant_count=_count(
                    values["expected_redundant_count"],
                    "expected_redundant_count",
                    256,
                ),
            )
            return read_redundant_constraints(self._context, spec)
        if operation == "select_partially_redundant_constraints":
            spec = PartiallyRedundantConstraintsSpec(
                **common,
                expected_partially_redundant_count=_count(
                    values["expected_partially_redundant_count"],
                    "expected_partially_redundant_count",
                    256,
                ),
            )
            return read_partially_redundant_constraints(self._context, spec)
        if operation == "select_malformed_constraints":
            spec = MalformedConstraintsSpec(
                **common,
                expected_malformed_count=_count(
                    values["expected_malformed_count"],
                    "expected_malformed_count",
                    256,
                ),
            )
            return read_malformed_constraints(self._context, spec)
        raise NativeAssemblyDiagnosisError(
            "The Assembly diagnosis operation is not implemented."
        )
