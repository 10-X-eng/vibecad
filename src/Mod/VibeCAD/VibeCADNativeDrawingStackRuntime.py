# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Drawing view stacking."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingStack import (
    mutate_drawing_stack,
    prepare_drawing_stack,
    verify_drawing_stack,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


class NativeDrawingStackRuntime:
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
        operation, values = strict_variant_arguments(
            arguments,
            {
                "stack_top": frozenset({"page", "views"}),
                "stack_bottom": frozenset({"page", "views"}),
                "stack_up": frozenset({"page", "views"}),
                "stack_down": frozenset({"page", "views"}),
            },
        )
        context = self._context
        context.guard()
        prepared = prepare_drawing_stack(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name={
                "stack_top": "Stack Native Drawing Views Top",
                "stack_bottom": "Stack Native Drawing Views Bottom",
                "stack_up": "Stack Native Drawing Views Up",
                "stack_down": "Stack Native Drawing Views Down",
            }[operation],
            mutate=partial(mutate_drawing_stack, prepared=prepared),
            verify=verify_drawing_stack,
        )
