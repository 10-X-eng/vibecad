# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact general Drawing centerlines."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingGeneralCenterLine import (
    mutate_drawing_general_center_line,
    prepare_drawing_general_center_line,
    verify_drawing_general_center_line,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "create_face": frozenset({"page", "view", "faces"}),
    "create_between_edges": frozenset({"page", "view", "edges"}),
    "create_between_vertices": frozenset({"page", "view", "vertices"}),
}
_TRANSACTIONS = {
    "create_face": "Create Native Drawing Face Centerline",
    "create_between_edges": "Create Native Drawing Two-Edge Centerline",
    "create_between_vertices": "Create Native Drawing Two-Vertex Centerline",
}


class NativeDrawingGeneralCenterLineRuntime:
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
        operation, values = strict_variant_arguments(arguments, _FIELDS)
        context = self._context
        context.guard()
        prepared = prepare_drawing_general_center_line(
            context.document, operation=operation, values=values
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTIONS[operation],
            mutate=partial(
                mutate_drawing_general_center_line, prepared=prepared
            ),
            verify=verify_drawing_general_center_line,
        )
