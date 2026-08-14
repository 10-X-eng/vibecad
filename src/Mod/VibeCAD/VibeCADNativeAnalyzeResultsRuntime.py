# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM result graph operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeResults import (
    create_result_purge,
    prepare_result_purge,
    verify_result_purge,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "purge": frozenset(
        {
            "analysis",
            "expected_result_graph_sha256",
            "expected_result_object_count",
        }
    ),
}


class NativeAnalyzeResultsRuntime:
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
        _operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        prepared = prepare_result_purge(
            context.document,
            context.document_uid,
            **values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Purge FEM Results",
            mutate=lambda document: create_result_purge(document, prepared),
            verify=verify_result_purge,
        )
