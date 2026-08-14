# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing rich annotations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingRichAnnotation import (
    drawing_rich_annotation_defaults_state,
    mutate_drawing_rich_annotation,
    prepare_drawing_rich_annotation,
    verify_drawing_rich_annotation,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_COMMON = frozenset(
    {"page", "owner", "label", "placement_on_page_mm", "width", "frame"}
)
_FIELDS = {
    "create_plain_text": _COMMON | {"text"},
    "create_rich_text": _COMMON | {"html"},
    "read_defaults": frozenset(),
}


class NativeDrawingRichAnnotationRuntime:
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
        if operation == "read_defaults":
            return drawing_rich_annotation_defaults_state()
        prepared = prepare_drawing_rich_annotation(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Drawing Rich Annotation",
            mutate=partial(mutate_drawing_rich_annotation, prepared=prepared),
            verify=verify_drawing_rich_annotation,
        )
