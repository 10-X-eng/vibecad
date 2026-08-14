# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for responsive exact Drawing details."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeDrawingDetail import (
    capture_detail_view_commit_state,
    create_detail_view,
    prepare_detail_view_create,
    validate_prepared_detail_view,
    verify_detail_view_create,
)
from VibeCADNativeDrawingDetailInput import (
    create_detail_workspace,
    materialize_detail_snapshot,
)
from VibeCADNativeDrawingDetailWorker import (
    detail_snapshot,
    execute_detail_projection,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionWorker import projection_snapshot
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


class NativeDrawingDetailRuntime:
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
                "create_detail_view": frozenset(
                    {
                        "reference",
                        "page",
                        "base_view",
                        "anchor_on_base_mm",
                        "radius_mm",
                        "position_on_page_mm",
                        "scale",
                    }
                ),
            },
        )
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("Drawing detail creation requires one exact Native call ticket")
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeDrawingError(
                "Responsive Drawing detail computation is unavailable in this session.",
                error_code="NATIVE_DRAWING_DETAIL_BACKGROUND_UNAVAILABLE",
            )
        prepared = prepare_detail_view_create(context.document, values=values)
        workspace = create_detail_workspace()

        def prepare(cancelled: Any, progress: Any) -> Any:
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(5, "Freezing exact Drawing document")
            frozen = dispatcher(
                lambda: materialize_detail_snapshot(
                    context.document,
                    prepared,
                    workspace,
                )
            )
            if cancelled():
                raise NativeBackgroundCancelled()
            return execute_detail_projection(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_prepared_detail_view(context.document, prepared)

        def commit(worker_result: Any) -> Mapping[str, Any]:
            commit_prepared = capture_detail_view_commit_state(
                context.document,
                prepared,
            )
            result = run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native Drawing Detail",
                mutate=partial(
                    create_detail_view,
                    prepared=commit_prepared,
                    projection_snapshot=projection_snapshot(worker_result.projection),
                    detail_snapshot=detail_snapshot(worker_result.detail),
                    effective_scale=worker_result.effective_scale,
                ),
                verify=verify_detail_view_create,
            )
            detail = context.document.getObject(result["view"]["object_name"])
            repaint = getattr(detail, "requestPrecomputedDetailPaint", None)
            if not callable(repaint):
                raise NativeDrawingError(
                    "The installed TechDraw runtime cannot paint the committed detail.",
                    error_code="NATIVE_DRAWING_DETAIL_RUNTIME_UNAVAILABLE",
                )
            repaint()
            return result

        try:
            background = manager.submit(
                document_uid=context.document_uid,
                capability_name="drawing.detail_view.create_detail_view",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Adopting exact Drawing detail geometry",
                cleanup=lambda _worker_result: workspace.cleanup(),
            )
        except NativeBackgroundError as exc:
            workspace.cleanup()
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_DETAIL_QUEUE_FAILED",
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
