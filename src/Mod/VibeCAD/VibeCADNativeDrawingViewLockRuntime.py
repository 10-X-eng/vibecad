# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing view position locks."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingViewLock import (
    mutate_drawing_view_locks,
    prepare_drawing_view_lock_change,
    read_drawing_view_locks,
    verify_drawing_view_locks,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_COMMON_FIELDS = frozenset({"page", "expected_inventory_state_sha256"})
_FIELDS = {
    "set": _COMMON_FIELDS | frozenset({"views"}),
    "read_page": _COMMON_FIELDS | frozenset({"offset", "page_size"}),
}


class NativeDrawingViewLockRuntime:
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
        if operation == "read_page":
            return read_drawing_view_locks(context.document, values=values)
        prepared = prepare_drawing_view_lock_change(
            context.document,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Set Native Drawing View Locks",
            mutate=partial(mutate_drawing_view_locks, prepared=prepared),
            verify=verify_drawing_view_locks,
        )
