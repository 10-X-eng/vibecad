# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document runtime for related CAM setup creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureFollowUp import (
    create_follow_up_setup,
    preflight_follow_up_setup,
    prepare_follow_up_stock,
    validate_follow_up_setup,
    verify_follow_up_setup,
)
from VibeCADNativeManufactureJobState import capture_job_creation_environment
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "resource_scope": str(snapshot.resource_scope),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
    }


class NativeManufactureFollowUpRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context
        self._creation_state_sha256 = capture_job_creation_environment().state_sha256

    def create(
        self,
        arguments: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {"create": frozenset({"remaining_stock", "label"})},
        )
        if operation != "create" or not isinstance(ticket, NativeCallTicket):
            raise TypeError("Follow-up CAM setup requires one exact Native call ticket")
        context = self._context
        context.guard()
        current = context.state.current_revision(context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeManufactureError(
                "Background retained-stock preparation is unavailable in this session.",
                error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_UNAVAILABLE",
            )
        frozen = preflight_follow_up_setup(
            context.document,
            context.document_uid,
            expected_creation_state_sha256=self._creation_state_sha256,
            **values,
        )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return prepare_follow_up_stock(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_follow_up_setup(context.document, frozen)

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create CAM Follow-up Setup",
                mutate=lambda document: create_follow_up_setup(
                    document,
                    frozen,
                    prepared,
                ),
                verify=verify_follow_up_setup,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="manufacture.follow_up_setup.create",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Creating follow-up CAM setup",
                resource_scope=f"manufacture:{frozen.source_job.Name}",
            )
        except NativeBackgroundError as exc:
            raise NativeManufactureError(
                str(exc),
                error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(snapshot.job_id),
            },
        }
