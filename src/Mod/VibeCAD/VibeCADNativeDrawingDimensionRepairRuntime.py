# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing dimension-reference repair."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingDimensionRepair import (
    mutate_drawing_dimension_repair,
    prepare_drawing_dimension_repair,
    verify_drawing_dimension_repair,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "repair_references": frozenset(
        {"dimension", "page", "view", "replacement"}
    ),
}


class NativeDrawingDimensionRepairRuntime:
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
        self._context.guard()
        prepared = prepare_drawing_dimension_repair(
            self._context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Repair Native Drawing Dimension References",
            mutate=partial(mutate_drawing_dimension_repair, prepared=prepared),
            verify=verify_drawing_dimension_repair,
        )
