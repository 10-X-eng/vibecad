# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact CAM Area operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufactureArea import (
    AreaCreateSpec,
    AreaViewCreateSpec,
    AreaWorkplaneSpec,
    create_area,
    create_area_view,
    preflight_area_create,
    preflight_area_view_create,
    preflight_area_workplane,
    set_area_workplane,
    verify_area_workplane,
    verify_created_area,
    verify_created_area_view,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_VARIANTS = {
    "create": frozenset({"label", "sources"}),
    "create_view": frozenset({"label", "area"}),
    "set_workplane": frozenset({"area", "workplane"}),
}


class NativeManufactureAreaRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def area(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        current = context.state.current_revision(context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)

        if operation == "create":
            prepared = preflight_area_create(
                context.document,
                AreaCreateSpec(values["label"], values["sources"]),
            )
            transaction = "Create Native CAM Area"
            mutate = partial(create_area, prepared=prepared)
            verify = verify_created_area
        elif operation == "create_view":
            prepared = preflight_area_view_create(
                context.document,
                AreaViewCreateSpec(values["label"], values["area"]),
            )
            transaction = "Create Native CAM Area View"
            mutate = partial(create_area_view, prepared=prepared)
            verify = verify_created_area_view
        elif operation == "set_workplane":
            prepared = preflight_area_workplane(
                context.document,
                AreaWorkplaneSpec(values["area"], values["workplane"]),
            )
            transaction = "Set Native CAM Area Workplane"
            mutate = partial(set_area_workplane, prepared=prepared)
            verify = verify_area_workplane
        else:
            raise AssertionError(f"Unhandled CAM Area operation: {operation}")

        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=transaction,
            mutate=mutate,
            verify=verify,
        )
