# SPDX-License-Identifier: LGPL-2.1-or-later

"""Main-thread runtime for exact Native Drawing active-view capture."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingActiveView import (
    capture_active_view_image,
    create_active_view,
    prepare_active_view_create,
    verify_active_view_create,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


class NativeDrawingActiveViewRuntime:
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
        _operation, values = strict_variant_arguments(
            arguments,
            {
                "create_active_view": frozenset(
                    {
                        "label",
                        "page",
                        "viewport",
                        "position",
                        "scale",
                        "crop",
                        "background",
                    }
                )
            },
        )
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("Active-view creation requires one exact Native call ticket")
        context = self._context
        context.guard()
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        dispatcher = context.document_thread_dispatch
        if dispatcher is None:
            raise NativeDrawingError(
                "Main-thread Drawing capture is unavailable in this session.",
                error_code="NATIVE_DRAWING_ACTIVE_VIEW_THREAD_UNAVAILABLE",
            )

        def execute_on_document_thread() -> dict[str, Any]:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            prepared = prepare_active_view_create(context.document, values=values)
            captured = capture_active_view_image(
                context.document,
                prepared=prepared,
            )
            try:
                return run_immediate_mutation(
                    context,
                    ticket=ticket,
                    transaction_name="Create Native Drawing Active View",
                    mutate=partial(create_active_view, captured=captured),
                    verify=verify_active_view_create,
                )
            finally:
                captured.cleanup()

        return dispatcher(execute_on_document_thread)
