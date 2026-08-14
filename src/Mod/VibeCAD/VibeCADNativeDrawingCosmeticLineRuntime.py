# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing cosmetic-line creation."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingCosmeticLine import (
    mutate_drawing_cosmetic_line,
    prepare_drawing_cosmetic_line,
    verify_drawing_cosmetic_line,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "create_parallel": frozenset({"page", "view", "reference_edge", "through_vertex"}),
    "create_perpendicular": frozenset(
        {"page", "view", "reference_edge", "through_vertex"}
    ),
    "create_between_vertices": frozenset({"page", "view", "vertices"}),
}
_TRANSACTIONS = {
    "create_parallel": "Create Native Drawing Parallel Cosmetic Line",
    "create_perpendicular": "Create Native Drawing Perpendicular Cosmetic Line",
    "create_between_vertices": "Create Native Drawing Two-Point Cosmetic Line",
}


class NativeDrawingCosmeticLineRuntime:
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
        prepared = prepare_drawing_cosmetic_line(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTIONS[operation],
            mutate=partial(mutate_drawing_cosmetic_line, prepared=prepared),
            verify=verify_drawing_cosmetic_line,
        )
