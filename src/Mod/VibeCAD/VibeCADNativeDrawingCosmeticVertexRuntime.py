# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing cosmetic-vertex creation."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingCosmeticVertex import (
    mutate_drawing_cosmetic_vertex,
    prepare_drawing_cosmetic_vertex,
    verify_drawing_cosmetic_vertex,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "create_intersections": frozenset({"page", "view", "edges"}),
    "create_offset": frozenset({"page", "view", "source_vertex", "offset_mm"}),
    "create_point": frozenset({"page", "view", "point_in_view_mm"}),
    "create_midpoints": frozenset({"page", "view", "edges"}),
    "create_quadrants": frozenset({"page", "view", "edges"}),
}
_TRANSACTIONS = {
    "create_intersections": "Create Native Drawing Intersection Vertices",
    "create_offset": "Create Native Drawing Offset Vertex",
    "create_point": "Create Native Drawing Cosmetic Vertex",
    "create_midpoints": "Create Native Drawing Midpoint Vertices",
    "create_quadrants": "Create Native Drawing Quadrant Vertices",
}


class NativeDrawingCosmeticVertexRuntime:
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
        prepared = prepare_drawing_cosmetic_vertex(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTIONS[operation],
            mutate=partial(
                mutate_drawing_cosmetic_vertex,
                prepared=prepared,
            ),
            verify=verify_drawing_cosmetic_vertex,
        )
