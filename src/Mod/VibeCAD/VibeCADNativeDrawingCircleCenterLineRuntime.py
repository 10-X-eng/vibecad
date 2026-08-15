# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing circle centerlines."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingCircleCenterLine import (
    mutate_drawing_circle_center_lines,
    prepare_drawing_circle_center_lines,
    verify_drawing_circle_center_lines,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {"create": frozenset({"page", "view", "circles"})}


class NativeDrawingCircleCenterLineRuntime:
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
        if operation != "create":
            raise ValueError("operation is not a circle centerline operation")
        context = self._context
        context.guard()
        prepared = prepare_drawing_circle_center_lines(
            context.document,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Drawing Circle Centerlines",
            mutate=partial(
                mutate_drawing_circle_center_lines,
                prepared=prepared,
            ),
            verify=verify_drawing_circle_center_lines,
        )
