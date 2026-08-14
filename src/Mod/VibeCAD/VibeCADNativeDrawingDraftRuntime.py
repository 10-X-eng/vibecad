# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for responsive exact Draft-source views."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeDrawingDraft import (
    capture_draft_view_commit_state,
    create_draft_view,
    prepare_draft_view_create,
    validate_prepared_draft_view,
    verify_draft_view_create,
)
from VibeCADNativeDrawingDraftInput import (
    create_draft_workspace,
    materialize_draft_snapshot,
)
from VibeCADNativeDrawingDraftWorker import draft_symbol, execute_draft_render
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeImmediate import run_immediate_mutation
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


class NativeDrawingDraftRuntime:
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
                "create_draft_source_view": frozenset(
                    {
                        "page",
                        "source",
                        "orientation",
                        "position_on_page_mm",
                        "scale",
                        "style",
                    }
                ),
            },
        )
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("Draft view creation requires one exact Native call ticket")
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeDrawingError(
                "Responsive Draft rendering is unavailable in this session.",
                error_code="NATIVE_DRAWING_DRAFT_BACKGROUND_UNAVAILABLE",
            )
        prepared = prepare_draft_view_create(context.document, values=values)
        workspace = create_draft_workspace()

        def prepare(cancelled: Any, progress: Any) -> Any:
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(5, "Freezing exact Drawing document and Draft presentation")
            frozen = dispatcher(
                lambda: materialize_draft_snapshot(
                    context.document,
                    prepared,
                    workspace,
                )
            )
            if cancelled():
                raise NativeBackgroundCancelled()
            return execute_draft_render(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_prepared_draft_view(context.document, prepared)

        def commit(worker_result: Any) -> Mapping[str, Any]:
            commit_prepared = capture_draft_view_commit_state(
                context.document,
                prepared,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native Draft Drawing View",
                mutate=partial(
                    create_draft_view,
                    prepared=commit_prepared,
                    symbol=draft_symbol(worker_result),
                ),
                verify=verify_draft_view_create,
            )

        try:
            background = manager.submit(
                document_uid=context.document_uid,
                capability_name=(
                    "drawing.draft_source_view.create_draft_source_view"
                ),
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Adopting exact Draft drawing view",
                cleanup=lambda _worker_result: workspace.cleanup(),
            )
        except NativeBackgroundError as exc:
            workspace.cleanup()
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_DRAFT_QUEUE_FAILED",
            ) from exc
        except Exception:
            workspace.cleanup()
            raise
        return {
            "job": _job_summary(background),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(background.job_id),
            },
        }
