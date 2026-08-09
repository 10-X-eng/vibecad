# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly structure operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyStructure import (
    AssemblyCreateSpec,
    NativeAssemblyStructureError,
    create_assembly,
    preflight_create_assembly,
    verify_created_assembly,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeAssemblyStructureError(
            "An Assembly label must contain 1 to 160 characters."
        )
    return result


def _expected_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000:
        raise NativeAssemblyStructureError(
            "expected_assembly_count must be an integer from 0 through 10000."
        )
    return value


class NativeAssemblyStructureRuntime:
    """Execute only structure operations from one frozen Assemble turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _parent_ref(self, value: Any) -> NativeObjectRef | None:
        if value is None:
            return None
        if not isinstance(value, Mapping) or set(value) != {"object_name"}:
            raise NativeAssemblyStructureError(
                "parent_assembly must be null or one exact Assembly reference."
            )
        return NativeObjectRef(
            self._context.document_uid,
            str(value.get("object_name") or ""),
        )

    def mutate_structure(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "create_assembly": frozenset(
                    {
                        "label",
                        "parent_assembly",
                        "expected_assembly_count",
                    }
                ),
            },
        )
        if operation != "create_assembly":
            raise NativeAssemblyStructureError(
                "The Assembly structure operation is not implemented."
            )
        spec = AssemblyCreateSpec(
            label=_label(values["label"]),
            parent_ref=self._parent_ref(values["parent_assembly"]),
            expected_assembly_count=_expected_count(
                values["expected_assembly_count"]
            ),
        )
        self._context.guard()
        preflight_create_assembly(self._context.document, spec)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Create Native Assembly",
            mutate=lambda document: create_assembly(document, spec),
            verify=verify_created_assembly,
        )
