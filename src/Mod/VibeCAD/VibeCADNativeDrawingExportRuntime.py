# SPDX-License-Identifier: LGPL-2.1-or-later

"""Background, human-authorized runtime for Drawing output."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingExport import (
    drawing_output_source_summary,
    drawing_print_source_summary,
    prepare_drawing_page_export,
    prepare_drawing_print_all,
    validate_drawing_output,
    verify_drawing_page_export_source,
    verify_drawing_print_all_source,
    write_drawing_page,
)
from VibeCADNativeOutput import NativeOutputError, publish_authorized_output
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


class NativeDrawingExportRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _require_ticket(self, ticket: NativeCallTicket) -> None:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("Drawing output requires one exact Native call ticket")
        current = self._context.state.current_revision(self._context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)

    @staticmethod
    def _next(snapshot: Any) -> dict[str, Any]:
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(snapshot.job_id),
            },
        }

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "svg": frozenset({"page"}),
                "dxf": frozenset({"page"}),
                "pdf": frozenset({"page"}),
                "print_all": frozenset(),
            },
        )
        self._context.guard()
        self._require_ticket(ticket)
        if operation == "print_all":
            return self._print_all(ticket)
        return self._export_page(operation, values["page"], ticket)

    def _export_page(
        self,
        operation: str,
        page_target: Any,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        authorizer = context.authorize_output
        if manager is None or dispatcher is None or authorizer is None:
            raise NativeDrawingError(
                "Background human-authorized Drawing export is unavailable in this session.",
                error_code="NATIVE_DRAWING_OUTPUT_UNAVAILABLE",
            )
        prepared = prepare_drawing_page_export(
            context,
            page_target=page_target,
            format_name=operation,
        )
        try:
            authorization = authorizer(prepared.output_request)
        except NativeOutputError as exc:
            raise NativeDrawingError(str(exc), error_code=exc.code) from exc
        if authorization is None:
            raise NativeDrawingError(
                "The human cancelled Drawing output authorization.",
                error_code="NATIVE_DRAWING_OUTPUT_CANCELLED",
            )
        self._require_ticket(ticket)

        def validate_source() -> None:
            self._require_ticket(ticket)
            verify_drawing_page_export_source(context, prepared)

        def prepare(cancelled: Any, progress: Any) -> Mapping[str, Any]:
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(10, f"Rendering Drawing page as {operation.upper()}")

            def writer(path: str) -> None:
                if cancelled():
                    raise NativeBackgroundCancelled()
                dispatcher(lambda: write_drawing_page(prepared, path))

            artifact = publish_authorized_output(
                prepared.output_request,
                authorization,
                writer=writer,
                guard=lambda: dispatcher(validate_source),
                validator=lambda path: validate_drawing_output(
                    Path(path), operation
                ),
                temporary_suffix=f".{operation}",
            )
            progress(90, "Drawing output verified and published")
            return {
                "output": artifact.summary(),
                "source": drawing_output_source_summary(prepared),
            }

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"drawing.export.{operation}",
                prepare=prepare,
                validate_before_commit=lambda: None,
                commit=lambda result: result,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_OUTPUT_QUEUE_FAILED",
            ) from exc
        return self._next(snapshot)

    def _print_all(self, ticket: NativeCallTicket) -> dict[str, Any]:
        context = self._context
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeDrawingError(
                "Responsive human-authorized Drawing printing is unavailable in this session.",
                error_code="NATIVE_DRAWING_PRINT_UNAVAILABLE",
            )
        prepared = prepare_drawing_print_all(context)

        def validate_source() -> None:
            self._require_ticket(ticket)
            verify_drawing_print_all_source(context, prepared)

        def prepare(cancelled: Any, progress: Any) -> Mapping[str, Any]:
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(10, "Waiting for human print authorization")

            def request_print() -> Mapping[str, Any]:
                validate_source()
                import TechDrawGui

                outcome = dict(
                    TechDrawGui.printAllDrawingPages(
                        context.document,
                        validate_source,
                    )
                )
                validate_source()
                return outcome

            outcome = dict(dispatcher(request_print))
            progress(
                90,
                "Print request submitted"
                if outcome.get("submitted")
                else "Print authorization cancelled",
            )
            return {
                "print": {
                    "authorized": bool(outcome.get("authorized")),
                    "submitted": bool(outcome.get("submitted")),
                    "output_mode": str(outcome.get("output_mode") or "printer"),
                    "page_count": int(outcome.get("page_count") or 0),
                },
                "source": drawing_print_source_summary(prepared),
            }

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="drawing.export.print_all",
                prepare=prepare,
                validate_before_commit=lambda: None,
                commit=lambda result: result,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_PRINT_QUEUE_FAILED",
            ) from exc
        return self._next(snapshot)
