# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Assembly standard-fastener operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyFastener import (
    AssemblyFastenerInsertSpec,
    NativeAssemblyFastenerError,
    insert_assembly_fastener,
    preflight_insert_assembly_fastener,
    verify_inserted_assembly_fastener,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


class NativeAssemblyFastenerRuntime:
    """Mutate only the exact human-active Assembly on the frozen turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_fastener(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "insert_standard_fastener": frozenset(
                    {
                        "assembly",
                        "label",
                        "definition",
                        "expected_state_sha256",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                    }
                ),
            },
        )
        if operation != "insert_standard_fastener":
            raise NativeAssemblyFastenerError(
                "The Assembly fastener operation is not implemented."
            )
        assembly = values["assembly"]
        if not isinstance(assembly, Mapping) or set(assembly) != {"object_name"}:
            raise NativeAssemblyFastenerError(
                "assembly must contain one exact object_name."
            )
        try:
            reference = NativeObjectRef(
                self._context.document_uid,
                str(assembly["object_name"]),
            )
        except Exception as exc:
            raise NativeAssemblyFastenerError(
                "assembly.object_name must identify one exact Assembly."
            ) from exc
        spec = AssemblyFastenerInsertSpec(
            assembly_ref=reference,
            label=values["label"],
            definition=values["definition"],
            expected_state_sha256=values["expected_state_sha256"],
            expected_component_count=values["expected_component_count"],
            expected_grounded_count=values["expected_grounded_count"],
            expected_joint_count=values["expected_joint_count"],
        )
        self._context.guard()
        prepared = preflight_insert_assembly_fastener(
            self._context.document,
            spec,
        )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Insert Native Assembly Fastener",
            mutate=partial(insert_assembly_fastener, prepared=prepared),
            verify=verify_inserted_assembly_fastener,
        )
