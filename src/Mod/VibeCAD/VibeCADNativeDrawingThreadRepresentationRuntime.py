# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing thread representations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingThreadRepresentation import (
    mutate_drawing_thread_representation,
    prepare_drawing_thread_representation,
    verify_drawing_thread_representation,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "create_hole_side": frozenset({"page", "view", "boundary_edges"}),
    "create_hole_bottom": frozenset({"page", "view", "circles"}),
    "create_bolt_side": frozenset({"page", "view", "boundary_edges"}),
    "create_bolt_bottom": frozenset({"page", "view", "circles"}),
}
_TRANSACTIONS = {
    "create_hole_side": "Create Native Drawing Hole Side Thread",
    "create_hole_bottom": "Create Native Drawing Hole Bottom Thread",
    "create_bolt_side": "Create Native Drawing Bolt Side Thread",
    "create_bolt_bottom": "Create Native Drawing Bolt Bottom Thread",
}


class NativeDrawingThreadRepresentationRuntime:
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
        prepared = prepare_drawing_thread_representation(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTIONS[operation],
            mutate=partial(
                mutate_drawing_thread_representation,
                prepared=prepared,
            ),
            verify=verify_drawing_thread_representation,
        )
