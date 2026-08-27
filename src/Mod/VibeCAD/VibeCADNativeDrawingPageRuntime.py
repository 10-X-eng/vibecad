# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Drawing page operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingPage import (
    change_keep_updated,
    create_page,
    drawing_template_input_request,
    edit_template_fields,
    prepare_authorized_page_create,
    prepare_built_in_page_create,
    prepare_default_page_create,
    prepare_template_field_edit,
    prepare_keep_updated_edit,
    verify_created_page,
    verify_template_field_edit,
    verify_keep_updated_edit,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError
from VibeCADNativeDrawingRedraw import (
    adopt_page_redraw,
    capture_page_redraw_commit_state,
    page_redraw_transaction_factory,
    prepare_page_redraw,
    validate_prepared_page_redraw,
    verify_page_redraw,
)
from VibeCADNativeDrawingRedrawInput import (
    create_redraw_workspace,
    materialize_redraw_snapshot,
)
from VibeCADNativeDrawingRedrawWorker import execute_page_redraw
from VibeCADNativeDrawingReadiness import drawing_page_readiness
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


class NativeDrawingPageRuntime:
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
        normalized = dict(arguments)
        if normalized.get("operation") == "page_default":
            normalized.setdefault("template", "")
        if normalized.get("operation") == "inspect_page_readiness":
            normalized.setdefault("offset", 0)
        operation, values = strict_variant_arguments(
            normalized,
            {
                "page_default": frozenset({"template"}),
                "page_template": frozenset(),
                "fill_template_fields": frozenset({"page", "updates"}),
                "redraw_page": frozenset({"page"}),
                "set_keep_updated": frozenset({"page", "keep_updated"}),
                "inspect_page_readiness": frozenset({"page", "offset"}),
            },
        )
        context = self._context
        context.guard()
        if operation == "inspect_page_readiness":
            return drawing_page_readiness(
                context.document,
                target=values["page"],
                offset=values["offset"],
            )
        if operation == "redraw_page":
            return self._redraw_page(values, ticket=ticket)
        if operation == "set_keep_updated":
            prepared = prepare_keep_updated_edit(
                context.document,
                target=values["page"],
                keep_updated=values["keep_updated"],
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Set Native Drawing Update Policy",
                mutate=partial(change_keep_updated, prepared=prepared),
                verify=verify_keep_updated_edit,
            )
        if operation == "fill_template_fields":
            prepared = prepare_template_field_edit(
                context.document,
                target=values["page"],
                updates=tuple(values["updates"]),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Edit Native Drawing Template Fields",
                mutate=partial(edit_template_fields, prepared=prepared),
                verify=verify_template_field_edit,
            )
        if operation == "page_default":
            template = str(values.get("template") or "")
            prepared = (
                prepare_built_in_page_create(
                    context.document,
                    template=template,
                )
                if template
                else prepare_default_page_create(context.document)
            )
        else:
            authorizer = context.authorize_input
            if authorizer is None:
                raise NativeDrawingError(
                    "Human Drawing template authorization is unavailable in this session.",
                    error_code="NATIVE_DRAWING_TEMPLATE_INPUT_UNAVAILABLE",
                )
            request = drawing_template_input_request()
            try:
                authorization = authorizer(request)
            except NativeInputError as exc:
                raise NativeDrawingError(str(exc), error_code=exc.code) from exc
            if authorization is None:
                raise NativeDrawingError(
                    "The human cancelled Drawing template selection.",
                    error_code="NATIVE_DRAWING_TEMPLATE_INPUT_CANCELLED",
                )
            prepared = prepare_authorized_page_create(
                context.document,
                authorization,
                request,
            )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Drawing Page",
            mutate=partial(create_page, prepared=prepared),
            verify=verify_created_page,
        )

    def _redraw_page(
        self,
        values: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("Drawing page redraw requires one exact Native call ticket")
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeDrawingError(
                "Responsive Drawing page redraw is unavailable in this session.",
                error_code="NATIVE_DRAWING_REDRAW_BACKGROUND_UNAVAILABLE",
            )
        prepared = prepare_page_redraw(context.document, target=values["page"])
        workspace = create_redraw_workspace()

        def prepare(cancelled: Any, progress: Any) -> Any:
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(5, "Freezing exact Drawing document")
            frozen = dispatcher(
                lambda: materialize_redraw_snapshot(
                    context.document,
                    prepared,
                    workspace,
                )
            )
            if cancelled():
                raise NativeBackgroundCancelled()
            return execute_page_redraw(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_prepared_page_redraw(context.document, prepared)

        def commit(worker_result: Any) -> Mapping[str, Any]:
            commit_prepared = capture_page_redraw_commit_state(
                context.document,
                prepared,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Redraw Native Drawing Page",
                mutate=partial(
                    adopt_page_redraw,
                    prepared=commit_prepared,
                    worker_result=worker_result,
                ),
                verify=verify_page_redraw,
                transaction_factory=page_redraw_transaction_factory(commit_prepared),
            )

        try:
            background = manager.submit(
                document_uid=context.document_uid,
                capability_name="drawing.redraw_page",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Adopting exact Drawing page caches",
                cleanup=lambda _worker_result: workspace.cleanup(),
            )
        except NativeBackgroundError as exc:
            workspace.cleanup()
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_REDRAW_QUEUE_FAILED",
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
