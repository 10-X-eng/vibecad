# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for background Visual Inspection comparison."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInspectionCompare import (
    NativeInspectionCompareError,
    capture_inspection_comparison,
    commit_inspection_comparison,
    comparison_still_exact,
    discard_prepared_comparison,
    run_inspection_comparison,
    verify_inspection_comparison,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "compare": frozenset(
        {
            "actual",
            "nominals",
            "search_radius_mm",
            "tolerance_mm",
            "require_complete",
            "result_label",
        }
    ),
}


def _job(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


class NativeInspectionCompareRuntime:
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
            _VARIANTS,
            defaults={"compare": {"require_complete": True}},
        )
        context = self._context
        context.guard()
        request = capture_inspection_comparison(
            context.document,
            context.document_uid,
            values,
        )
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeInspectionCompareError(
                "Background geometry comparison is unavailable in this session.",
                error_code="NATIVE_INSPECTION_BACKGROUND_UNAVAILABLE",
            )

        def validate() -> None:
            context.guard()
            if not comparison_still_exact(context.document, request):
                raise NativeInspectionCompareError(
                    "Comparison geometry changed while deviations were computed.",
                    error_code="NATIVE_INSPECTION_STATE_STALE",
                )

        def commit(prepared: Any) -> Mapping[str, Any]:
            try:
                return run_immediate_mutation(
                    context,
                    ticket=ticket,
                    transaction_name="Visual Inspection",
                    mutate=lambda document: commit_inspection_comparison(document, prepared),
                    verify=verify_inspection_comparison,
                )
            finally:
                discard_prepared_comparison(prepared)

        def cleanup(prepared: Any) -> None:
            discard_prepared_comparison(prepared)
            context.state.cancel_mutation(ticket)

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="inspect.compare",
                prepare=lambda cancelled, progress: run_inspection_comparison(
                    request,
                    cancelled=cancelled,
                    progress=progress,
                ),
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing verified Inspection result",
                cleanup=cleanup,
                changes_document=True,
            )
        except NativeBackgroundError as exc:
            raise NativeInspectionCompareError(
                str(exc),
                error_code="NATIVE_INSPECTION_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(snapshot.job_id),
            },
        }
