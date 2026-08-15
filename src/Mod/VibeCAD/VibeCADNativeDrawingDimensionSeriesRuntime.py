# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for grouped Drawing dimension series."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingDimensionSeries import (
    mutate_drawing_dimension_series,
    prepare_drawing_dimension_series,
    restore_drawing_dimension_series_after_abort,
    verify_drawing_dimension_series,
)
from VibeCADNativeDrawingDimensionSeriesSchema import (
    DRAWING_DIMENSION_SERIES_OPERATIONS,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    operation: frozenset({"label", "page", "view", "vertices"})
    for operation in DRAWING_DIMENSION_SERIES_OPERATIONS
}
_TRANSACTION_NAMES = {
    "create_horizontal_chain": "Create Native Drawing Horizontal Chain",
    "create_vertical_chain": "Create Native Drawing Vertical Chain",
    "create_oblique_chain": "Create Native Drawing Oblique Chain",
    "create_horizontal_coordinate": "Create Native Drawing Horizontal Coordinate Series",
    "create_vertical_coordinate": "Create Native Drawing Vertical Coordinate Series",
    "create_oblique_coordinate": "Create Native Drawing Oblique Coordinate Series",
}


class NativeDrawingDimensionSeriesRuntime:
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
        prepared = prepare_drawing_dimension_series(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTION_NAMES[operation],
            mutate=partial(mutate_drawing_dimension_series, prepared=prepared),
            verify=verify_drawing_dimension_series,
            after_abort=partial(
                restore_drawing_dimension_series_after_abort,
                prepared=prepared,
            ),
        )
