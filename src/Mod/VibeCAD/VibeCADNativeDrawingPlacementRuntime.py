# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for explicit Drawing item placement."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingPlacement import (
    mutate_drawing_placement,
    prepare_drawing_placement,
    verify_drawing_placement,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "place_views": frozenset({"page", "views"}),
    "place_dimension_labels": frozenset({"page", "dimensions"}),
    "place_notes": frozenset({"page", "notes"}),
}


class NativeDrawingPlacementRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
        operation: str,
    ) -> dict[str, Any]:
        selected, values = strict_variant_arguments(
            arguments,
            {operation: _FIELDS[operation]},
        )
        context = self._context
        context.guard()
        prepared = prepare_drawing_placement(
            context.document,
            operation=selected,
            values=values,
        )
        transaction = {
            "place_views": "Place Native Drawing Views",
            "place_dimension_labels": "Place Native Drawing Dimension Labels",
            "place_notes": "Place Native Drawing Notes",
        }[selected]
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=transaction,
            mutate=partial(mutate_drawing_placement, prepared=prepared),
            verify=verify_drawing_placement,
        )


__all__ = ["NativeDrawingPlacementRuntime"]
