# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing Leader Lines."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingLeader import (
    mutate_drawing_leader,
    prepare_drawing_leader,
    verify_drawing_leader,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "create": frozenset(
        {
            "page",
            "owner",
            "points_on_page_mm",
            "label",
            "symbols",
            "behavior",
            "line",
        }
    ),
}


class NativeDrawingLeaderRuntime:
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
        normalized = dict(arguments)
        for optional in ("symbols", "behavior", "line"):
            normalized.setdefault(optional, None)
        operation, values = strict_variant_arguments(normalized, _FIELDS)
        context = self._context
        context.guard()
        prepared = prepare_drawing_leader(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Drawing Leader Line",
            mutate=partial(mutate_drawing_leader, prepared=prepared),
            verify=verify_drawing_leader,
        )
