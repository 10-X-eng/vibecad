# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact projected Drawing balloons."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingBalloon import (
    mutate_drawing_balloon,
    prepare_drawing_balloon,
    verify_drawing_balloon,
)
from VibeCADNativeDrawingBalloonEdit import (
    mutate_drawing_balloon_edit,
    prepare_drawing_balloon_edit,
    verify_drawing_balloon_edit,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "create": frozenset(
        {
            "label",
            "text",
            "page",
            "view",
            "anchor",
            "bubble_offset_in_view_mm",
        }
    ),
    "set_text": frozenset({"balloon", "text"}),
    "set_style": frozenset({"balloon", "style"}),
    "move_bubble": frozenset({"balloon", "bubble_offset_in_view_mm"}),
}
_TRANSACTION_NAMES = {
    "create": "Create Native Drawing Balloon",
    "set_text": "Set Native Drawing Balloon Text",
    "set_style": "Set Native Drawing Balloon Style",
    "move_bubble": "Move Native Drawing Balloon",
}


class NativeDrawingBalloonRuntime:
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
        if operation == "create":
            prepared = prepare_drawing_balloon(
                context.document,
                operation=operation,
                values=values,
            )
            mutate = partial(mutate_drawing_balloon, prepared=prepared)
            verify = verify_drawing_balloon
        else:
            prepared = prepare_drawing_balloon_edit(
                context.document,
                operation=operation,
                values=values,
            )
            mutate = partial(mutate_drawing_balloon_edit, prepared=prepared)
            verify = verify_drawing_balloon_edit
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTION_NAMES[operation],
            mutate=mutate,
            verify=verify,
        )
