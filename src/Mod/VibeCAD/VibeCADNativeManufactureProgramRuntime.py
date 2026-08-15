# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact CAM program-control operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufactureProgram import (
    CommentCreateSpec,
    create_comment,
    preflight_comment_create,
    verify_created_comment,
)
from VibeCADNativeManufactureProgramStop import (
    StopCreateSpec,
    create_stop,
    preflight_stop_create,
    verify_created_stop,
)
from VibeCADNativeManufactureProgramCustom import (
    CustomCreateSpec,
    create_custom,
    preflight_custom_create,
    verify_created_custom,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_VARIANTS = {
    "comment": frozenset({"label", "job", "comment"}),
    "stop": frozenset({"label", "job", "stop_mode"}),
    "custom": frozenset(
        {"label", "job", "tool_controller", "coolant", "blocks"}
    ),
}


class NativeManufactureProgramRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def program(
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
        if operation == "comment":
            prepared = preflight_comment_create(
                context.document,
                CommentCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    comment=values["comment"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Comment",
                mutate=partial(create_comment, prepared=prepared),
                verify=verify_created_comment,
            )
        if operation == "stop":
            prepared = preflight_stop_create(
                context.document,
                StopCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    stop_mode=values["stop_mode"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Stop",
                mutate=partial(create_stop, prepared=prepared),
                verify=verify_created_stop,
            )
        if operation == "custom":
            prepared = preflight_custom_create(
                context.document,
                CustomCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    coolant=values["coolant"],
                    blocks=values["blocks"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Custom Operation",
                mutate=partial(create_custom, prepared=prepared),
                verify=verify_created_custom,
            )
        raise AssertionError(f"Unhandled CAM program operation: {operation}")
