# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact CAM ToolBit output."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureToolOutput import (
    ToolBitOutputSpec,
    export_tool_bit,
    preflight_tool_bit_output,
    require_current_output_ticket,
)
from VibeCADNativeOutput import NativeOutputError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "save": frozenset({"target", "format"}),
    "save_as": frozenset({"target", "format"}),
}


class NativeManufactureToolOutputRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def export(
        self,
        arguments: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        require_current_output_ticket(context, ticket)
        prepared = preflight_tool_bit_output(
            context,
            ToolBitOutputSpec(
                operation=operation,
                target=dict(values["target"]),
                format_name=str(values["format"]),
            ),
        )
        authorizer = context.authorize_output
        if authorizer is None:
            raise NativeManufactureError(
                "Human output authorization is unavailable in this VibeCAD session.",
                error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_UNAVAILABLE",
            )
        try:
            authorization = authorizer(prepared.output_request)
        except NativeOutputError as exc:
            raise NativeManufactureError(str(exc), error_code=exc.code) from exc
        if authorization is None:
            raise NativeManufactureError(
                "The human cancelled CAM ToolBit output authorization.",
                error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_CANCELLED",
            )
        require_current_output_ticket(context, ticket)
        return export_tool_bit(context, prepared, authorization, ticket)
