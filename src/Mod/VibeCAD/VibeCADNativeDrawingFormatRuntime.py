# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing format customization."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingFormat import (
    mutate_drawing_format,
    prepare_drawing_format_change,
    verify_drawing_format,
)
from VibeCADNativeDrawingFit import (
    mutate_drawing_fit,
    prepare_drawing_fit,
    verify_drawing_fit,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "set_dimension_format": frozenset({"dimension", "format_spec"}),
    "set_balloon_text": frozenset({"balloon", "text"}),
    "apply_iso_286_fit": frozenset({"dimension", "tolerance_class"}),
}
_TRANSACTION_NAMES = {
    "set_dimension_format": "Set Native Drawing Dimension Format",
    "set_balloon_text": "Set Native Drawing Balloon Text",
    "apply_iso_286_fit": "Apply Native Drawing ISO 286 Fit",
}


class NativeDrawingFormatRuntime:
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
        if operation == "apply_iso_286_fit":
            prepared_fit = prepare_drawing_fit(context.document, values=values)
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=_TRANSACTION_NAMES[operation],
                mutate=partial(mutate_drawing_fit, prepared=prepared_fit),
                verify=verify_drawing_fit,
            )
        prepared = prepare_drawing_format_change(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTION_NAMES[operation],
            mutate=partial(mutate_drawing_format, prepared=prepared),
            verify=verify_drawing_format,
        )
