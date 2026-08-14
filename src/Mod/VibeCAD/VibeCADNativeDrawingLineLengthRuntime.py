# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for symmetric Drawing line resizing."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingLineLength import (
    mutate_drawing_line_length,
    prepare_drawing_line_length_change,
    read_drawing_line_lengths,
    verify_drawing_line_length,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_COMMON_FIELDS = frozenset(
    {"page", "view", "expected_inventory_state_sha256"}
)
_MUTATION_FIELDS = _COMMON_FIELDS | frozenset(
    {"target", "delta_distance_mm"}
)
_FIELDS = {
    "extend": _MUTATION_FIELDS,
    "shorten": _MUTATION_FIELDS,
    "read_view": _COMMON_FIELDS | frozenset({"offset", "page_size"}),
}


class NativeDrawingLineLengthRuntime:
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
        if operation == "read_view":
            return read_drawing_line_lengths(context.document, values=values)
        prepared = prepare_drawing_line_length_change(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=(
                "Extend Native Drawing Line"
                if operation == "extend"
                else "Shorten Native Drawing Line"
            ),
            mutate=partial(mutate_drawing_line_length, prepared=prepared),
            verify=verify_drawing_line_length,
        )
