# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact bounded CAM probing grids."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufactureProbe import (
    ProbeCreateSpec,
    create_probe,
    preflight_probe_create,
    verify_created_probe,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_VARIANTS = {
    "create_grid": frozenset(
        {"label", "job", "tool_controller", "grid", "motion"}
    ),
}


class NativeManufactureProbeRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def probe(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        if operation != "create_grid":
            raise AssertionError(f"Unhandled CAM Probe operation: {operation}")
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        current = context.state.current_revision(context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)
        prepared = preflight_probe_create(
            context.document,
            ProbeCreateSpec(
                label=values["label"],
                job=values["job"],
                tool_controller=values["tool_controller"],
                grid=values["grid"],
                motion=values["motion"],
            ),
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native CAM Probe Grid",
            mutate=partial(create_probe, prepared=prepared),
            verify=verify_created_probe,
        )
