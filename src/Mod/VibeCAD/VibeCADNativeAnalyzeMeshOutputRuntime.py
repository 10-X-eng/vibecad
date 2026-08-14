# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM mesh output operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeMeshOutputCreate import (
    create_erased_elements,
    create_fem_surface_conversion,
    prepare_erase_elements,
    prepare_erase_element_ranges,
    prepare_fem_surface_conversion,
    verify_erased_elements,
    verify_fem_surface_conversion,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "erase_elements": frozenset({"target", "label", "element_ids"}),
    "convert_surface": frozenset({"target", "label"}),
    "convert_deformed_surface": frozenset({"target", "result", "label"}),
    "erase_element_ranges": frozenset({"target", "label", "element_id_ranges"}),
}


class NativeAnalyzeMeshOutputRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        if operation in {"erase_elements", "erase_element_ranges"}:
            prepare = (
                prepare_erase_elements
                if operation == "erase_elements"
                else prepare_erase_element_ranges
            )
            prepared = prepare(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Erase FEM Mesh Elements",
                mutate=lambda document: create_erased_elements(document, prepared),
                verify=verify_erased_elements,
            )
        prepared = prepare_fem_surface_conversion(
            context.document,
            context.document_uid,
            **values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=(
                "Convert Deformed FEM Surface to Mesh"
                if operation == "convert_deformed_surface"
                else "Convert FEM Surface to Mesh"
            ),
            mutate=lambda document: create_fem_surface_conversion(document, prepared),
            verify=verify_fem_surface_conversion,
        )
