# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for durable FEM result visualizations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeVisualizationCreate import (
    create_visualization,
    prepare_visualization,
    verify_visualization,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "create_table": frozenset({"analysis", "source", "label", "data"}),
    "create_histogram": frozenset({"analysis", "source", "label", "data", "view"}),
    "create_line_plot": frozenset({"analysis", "source", "label", "data", "view"}),
}
_KINDS = {
    "create_table": "table",
    "create_histogram": "histogram",
    "create_line_plot": "line_plot",
}
_TRANSACTION_NAMES = {
    "create_table": "Create FEM Result Table",
    "create_histogram": "Create FEM Result Histogram",
    "create_line_plot": "Create FEM Result Line Plot",
}


class NativeAnalyzeVisualizationRuntime:
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
        prepared = prepare_visualization(
            context.document,
            context.document_uid,
            kind=_KINDS[operation],
            **values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTION_NAMES[operation],
            mutate=lambda document: create_visualization(document, prepared),
            verify=verify_visualization,
        )
