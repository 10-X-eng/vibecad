# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing section-view positioning."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingSectionPosition import (
    mutate_drawing_section_position,
    prepare_drawing_section_position,
    verify_drawing_section_position,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "align_axis": frozenset({"page", "section_view", "axis"}),
    "align_edge_to_vertex": frozenset(
        {"page", "section_view", "section_edge", "base_view", "base_vertex"}
    ),
}


class NativeDrawingSectionPositionRuntime:
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
        prepared = prepare_drawing_section_position(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=(
                "Align Native Drawing Section View to Base Axis"
                if operation == "align_axis"
                else "Align Native Drawing Section Edge to Base Vertex"
            ),
            mutate=partial(mutate_drawing_section_position, prepared=prepared),
            verify=verify_drawing_section_position,
        )
