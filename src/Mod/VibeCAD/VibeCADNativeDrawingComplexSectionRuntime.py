# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for responsive exact Drawing complex sections."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeDrawingComplexSection import (
    capture_complex_section_view_commit_state,
    create_complex_section_view,
    prepare_complex_section_view_create,
    validate_prepared_complex_section_view,
    verify_complex_section_view_create,
)
from VibeCADNativeDrawingComplexSectionInput import (
    create_complex_section_workspace,
    materialize_complex_section_snapshot,
)
from VibeCADNativeDrawingComplexSectionWorker import (
    complex_section_snapshot,
    execute_complex_section_projection,
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


class NativeDrawingComplexSectionRuntime:
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
                "create_complex_section_view": frozenset(
                    {
                        "label",
                        "symbol",
                        "page",
                        "base_view",
                        "profile",
                        "view_direction_on_base",
                        "projection_strategy",
                        "scale",
                    }
                ),
            },
        )
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError(
                "Drawing complex-section creation requires one exact Native call ticket"
            )
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeDrawingError(
                "Responsive Drawing complex-section computation is unavailable in this session.",
                error_code=(
                    "NATIVE_DRAWING_COMPLEX_SECTION_BACKGROUND_UNAVAILABLE"
                ),
            )
        prepared = prepare_complex_section_view_create(
            context.document,
            values=values,
        )
        workspace = create_complex_section_workspace()

        def prepare(cancelled: Any, progress: Any) -> Any:
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(5, "Freezing exact Drawing document")
            frozen = dispatcher(
                lambda: materialize_complex_section_snapshot(
                    context.document,
                    prepared,
                    workspace,
                )
            )
            if cancelled():
                raise NativeBackgroundCancelled()
            return execute_complex_section_projection(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_prepared_complex_section_view(context.document, prepared)

        def commit(worker_result: Any) -> Mapping[str, Any]:
            stage = "validating exact inputs"
            try:
                commit_prepared = capture_complex_section_view_commit_state(
                    context.document,
                    prepared,
                )
                stage = "importing authenticated geometry"
                projection = projection_snapshot(worker_result.projection)
                section = complex_section_snapshot(worker_result.section)
                stage = "publishing the atomic document operation"
                result = run_immediate_mutation(
                    context,
                    ticket=ticket,
                    transaction_name="Create Native Drawing Complex Section",
                    mutate=partial(
                        create_complex_section_view,
                        prepared=commit_prepared,
                        projection_snapshot=projection,
                        section_snapshot=section,
                        effective_scale=worker_result.effective_scale,
                    ),
                    verify=verify_complex_section_view_create,
                )
                stage = "painting the completed complex section"
                view = context.document.getObject(result["view"]["object_name"])
                repaint = getattr(view, "requestPrecomputedSectionPaint", None)
                if not callable(repaint):
                    raise NativeDrawingError(
                        "The installed TechDraw runtime cannot paint the committed complex section.",
                        error_code=(
                            "NATIVE_DRAWING_COMPLEX_SECTION_RUNTIME_UNAVAILABLE"
                        ),
                    )
                repaint()
                return result
            except NativeDrawingError:
                raise
            except Exception as exc:
                if callable(getattr(exc, "failure", None)):
                    raise
                raise NativeDrawingError(
                    f"Complex-section commit failed while {stage}: {str(exc)[:180]}",
                    error_code="NATIVE_DRAWING_COMPLEX_SECTION_COMMIT_FAILED",
                ) from exc

        try:
            background = manager.submit(
                document_uid=context.document_uid,
                capability_name=(
                    "drawing.complex_section.create_complex_section_view"
                ),
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Adopting exact Drawing complex-section geometry",
                cleanup=lambda _worker_result: workspace.cleanup(),
            )
        except NativeBackgroundError as exc:
            workspace.cleanup()
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_COMPLEX_SECTION_QUEUE_FAILED",
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
