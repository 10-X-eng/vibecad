# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact CAM Property Bag creation."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufacturePropertyBag import (
    PropertyBagCreateSpec,
    create_property_bag,
    preflight_property_bag_create,
    verify_created_property_bag,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_VARIANTS = {
    "create": frozenset({"label", "destination_body", "properties"}),
}


class NativeManufacturePropertyBagRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def property_bag(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        if operation != "create":
            raise AssertionError(f"Unhandled Property Bag operation: {operation}")
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        current = context.state.current_revision(context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)
        prepared = preflight_property_bag_create(
            context.document,
            PropertyBagCreateSpec(
                label=values["label"],
                destination_body=values["destination_body"],
                properties=values["properties"],
            ),
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native CAM Property Bag",
            mutate=partial(create_property_bag, prepared=prepared),
            verify=verify_created_property_bag,
        )
