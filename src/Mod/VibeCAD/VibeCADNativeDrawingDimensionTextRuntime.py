# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing dimension-text changes."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingDimensionText import (
    mutate_drawing_dimension_text,
    prepare_drawing_dimension_text,
    verify_drawing_dimension_text,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_BASE_FIELDS = frozenset({"page", "dimensions"})
_FIELDS = {
    "insert_diameter_prefix": _BASE_FIELDS,
    "insert_square_prefix": _BASE_FIELDS,
    "insert_repetition_prefix": _BASE_FIELDS | {"repeat_count"},
    "remove_prefix": _BASE_FIELDS,
    "increase_decimals": _BASE_FIELDS,
    "decrease_decimals": _BASE_FIELDS,
}
_TRANSACTION_NAMES = {
    "insert_diameter_prefix": "Insert Native Drawing Diameter Prefix",
    "insert_square_prefix": "Insert Native Drawing Square Prefix",
    "insert_repetition_prefix": "Insert Native Drawing Repetition Prefix",
    "remove_prefix": "Remove Native Drawing Dimension Prefix",
    "increase_decimals": "Increase Native Drawing Dimension Precision",
    "decrease_decimals": "Decrease Native Drawing Dimension Precision",
}


class NativeDrawingDimensionTextRuntime:
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
        prepared = prepare_drawing_dimension_text(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTION_NAMES[operation],
            mutate=partial(
                mutate_drawing_dimension_text,
                prepared=prepared,
            ),
            verify=verify_drawing_dimension_text,
        )
