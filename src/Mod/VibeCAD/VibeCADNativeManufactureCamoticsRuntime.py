# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for optional background CAMotics presentation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeManufactureCamoticsInput import (
    preflight_camotics,
    validate_camotics,
)
from VibeCADNativeManufactureCamoticsWorker import (
    PreparedCamoticsLaunch,
    PreparedCamoticsResult,
    camotics_result_summary,
    cleanup_camotics,
    prepare_camotics,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
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


class NativeManufactureCamoticsRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {"camotics": frozenset({"job", "operations", "request"})},
        )
        if operation != "camotics" or not isinstance(ticket, NativeCallTicket):
            raise TypeError("CAMotics requires one exact Native call ticket")
        context = self._context
        context.guard()
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeManufactureError(
                "Background CAMotics work is unavailable in this session.",
                error_code="NATIVE_MANUFACTURE_CAMOTICS_UNAVAILABLE",
            )
        frozen = preflight_camotics(context.document, **values)

        def prepare(cancelled: Any, progress: Any) -> Any:
            return prepare_camotics(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_camotics(context.document, frozen)

        def present(prepared: Any) -> Mapping[str, Any]:
            revision_before = context.state.current_revision(context.document_uid)
            if frozen.request_kind == "read_result":
                if not isinstance(prepared, PreparedCamoticsResult):
                    raise TypeError("CAMotics returned the wrong prepared result type")
                result = camotics_result_summary(prepared)
            elif frozen.request_kind == "launch":
                if not isinstance(prepared, PreparedCamoticsLaunch):
                    raise TypeError("CAMotics returned the wrong launch bundle type")
                result = prepared.launch()
            else:
                raise TypeError("Unsupported frozen CAMotics request")
            revision_after = context.state.current_revision(context.document_uid)
            if revision_after != revision_before:
                raise NativeRevisionConflict(revision_before, revision_after)
            return result

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"manufacture.camotics.{frozen.request_kind}",
                prepare=prepare,
                validate_before_commit=validate,
                commit=present,
                dispatch_to_document_thread=dispatcher,
                finalize_message=(
                    "Reading exact CAMotics result"
                    if frozen.request_kind == "read_result"
                    else "Launching exact CAMotics project"
                ),
                cleanup=cleanup_camotics,
                resource_scope=f"manufacture:{frozen.job.Name}",
            )
        except NativeBackgroundError as exc:
            raise NativeManufactureError(
                str(exc),
                error_code="NATIVE_MANUFACTURE_CAMOTICS_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(snapshot.job_id),
            },
        }
