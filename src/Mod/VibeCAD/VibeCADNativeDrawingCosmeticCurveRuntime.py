# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing cosmetic-curve creation."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingCosmeticCurve import (
    mutate_drawing_cosmetic_curve,
    prepare_drawing_cosmetic_curve,
    verify_drawing_cosmetic_curve,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_COMMON = frozenset({"page", "view"})
_FIELDS = {
    "create_one_point_circle": _COMMON | frozenset({"center_vertex", "radius_mm"}),
    "create_two_point_circle": _COMMON | frozenset({"center_vertex", "radius_vertex"}),
    "create_three_point_circle": _COMMON
    | frozenset(
        {
            "first_perimeter_vertex",
            "second_perimeter_vertex",
            "third_perimeter_vertex",
        }
    ),
    "create_center_start_end_arc": _COMMON
    | frozenset({"center_vertex", "start_vertex", "end_vertex"}),
}
_TRANSACTIONS = {
    "create_one_point_circle": "Create Native Drawing One-Point Circle",
    "create_two_point_circle": "Create Native Drawing Two-Point Circle",
    "create_three_point_circle": "Create Native Drawing Three-Point Circle",
    "create_center_start_end_arc": "Create Native Drawing Cosmetic Arc",
}


class NativeDrawingCosmeticCurveRuntime:
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
        prepared = prepare_drawing_cosmetic_curve(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTIONS[operation],
            mutate=partial(mutate_drawing_cosmetic_curve, prepared=prepared),
            verify=verify_drawing_cosmetic_curve,
        )
