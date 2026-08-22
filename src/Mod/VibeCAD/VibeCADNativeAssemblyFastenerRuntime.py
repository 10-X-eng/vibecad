# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Assembly standard-fastener operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyFastener import (
    AssemblyFastenerEditSpec,
    AssemblyFastenerInsertSpec,
    NativeAssemblyFastenerError,
    edit_assembly_fastener,
    insert_assembly_fastener,
    preflight_edit_assembly_fastener,
    preflight_insert_assembly_fastener,
    verify_edited_assembly_fastener,
    verify_inserted_assembly_fastener,
)
from VibeCADNativeAssemblyState import read_active_assembly
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
                    {"label", "definition"}
                ),
                "edit_standard_fastener": frozenset(
                    {"occurrence", "label", "definition"}
                ),
            },
        )

        def exact_reference(field: str) -> NativeObjectRef:
            value = values[field]
            if not isinstance(value, Mapping) or set(value) != {"object_name"}:
                raise NativeAssemblyFastenerError(
                    f"{field} must contain one exact object_name."
                )
            try:
                return NativeObjectRef(
                    self._context.document_uid,
                    str(value["object_name"]),
                )
            except Exception as exc:
                raise NativeAssemblyFastenerError(
                    f"{field}.object_name must identify one exact document object."
                ) from exc

        self._context.guard()
        assembly = read_active_assembly(self._context.document)
        if assembly is None:
            raise NativeAssemblyFastenerError("No Assembly is active.")
        assembly_ref = NativeObjectRef(
            self._context.document_uid,
            str(assembly.Name),
        )
        if operation == "edit_standard_fastener":
            prepared_edit = preflight_edit_assembly_fastener(
                self._context.document,
                AssemblyFastenerEditSpec(
                    assembly_ref=assembly_ref,
                    occurrence_ref=exact_reference("occurrence"),
                    label=values["label"],
                    definition=values["definition"],
                ),
            )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Edit Native Assembly Fastener",
                mutate=partial(edit_assembly_fastener, prepared=prepared_edit),
                verify=verify_edited_assembly_fastener,
            )
        if operation != "insert_standard_fastener":
            raise NativeAssemblyFastenerError(
                "The Assembly fastener operation is not implemented."
            )
        spec = AssemblyFastenerInsertSpec(
            assembly_ref=assembly_ref,
            label=values["label"],
            definition=values["definition"],
        )
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
