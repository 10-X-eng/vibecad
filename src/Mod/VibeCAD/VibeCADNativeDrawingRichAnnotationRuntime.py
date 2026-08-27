# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing rich annotations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingRichAnnotation import (
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
    "plain_text": {"create": _COMMON | {"text"}},
    "safe_html": {"create": _COMMON | {"html"}},
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
        content_kind: str,
    ) -> dict[str, Any]:
        if content_kind not in _FIELDS:
            raise ValueError("content_kind is not supported")
        normalized = dict(arguments)
        normalized.setdefault("owner", "page")
        normalized.setdefault("width", "automatic")
        normalized.setdefault("frame", None)
        _operation, values = strict_variant_arguments(
            normalized,
            _FIELDS[content_kind],
        )
        context = self._context
        context.guard()
        prepared = prepare_drawing_rich_annotation(
            context.document,
            operation=content_kind,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Drawing Rich Annotation",
            mutate=partial(mutate_drawing_rich_annotation, prepared=prepared),
            verify=verify_drawing_rich_annotation,
        )
