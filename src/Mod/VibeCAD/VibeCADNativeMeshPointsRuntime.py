# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound background runtime for the Mesh-ribbon Points group."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativePointIO import (
    commit_point_import,
    point_input_request,
    prepare_point_import,
    verify_point_import,
)
from VibeCADNativePointPlan import (
    point_plan_still_exact,
    prepare_point_plan,
    process_point_plan,
)
from VibeCADNativePointResults import create_point_results, verify_point_results
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "import_point_cloud": frozenset(),
    "convert_to_points": frozenset({"geometry_sources", "maximum_distance_mm"}),
    "structure": frozenset(
        {"target", "result_label", "coordinate_tolerance_mm"}
    ),
    "merge": frozenset({"point_clouds", "result_label"}),
    "polygon_cut": frozenset({"target", "polygon", "result"}),
}


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


class NativeMeshPointsRuntime:
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
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        if operation == "import_point_cloud":
            return self._start_import(ticket)
        context = self._context
        context.guard()
        prepared = prepare_point_plan(
            context.document,
            context.document_uid,
            operation,
            values,
        )
        return self._start_operation(prepared, ticket)

    def _manager(self) -> tuple[Any, Any]:
        manager = self._context.background_manager
        dispatcher = self._context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background point-cloud processing is unavailable in this session.",
                error_code="NATIVE_POINT_CLOUD_BACKGROUND_UNAVAILABLE",
            )
        return manager, dispatcher

    def _response(self, snapshot: Any) -> dict[str, Any]:
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }

    def _start_import(self, ticket: NativeCallTicket) -> dict[str, Any]:
        context = self._context
        context.guard()
        manager, dispatcher = self._manager()
        authorizer = context.authorize_input
        if authorizer is None:
            raise NativeMeshError(
                "Human point-cloud input authorization is unavailable in this session.",
                error_code="NATIVE_POINT_CLOUD_IMPORT_UNAVAILABLE",
            )
        request = point_input_request()
        try:
            authorization = authorizer(request)
        except NativeInputError as exc:
            raise NativeMeshError(str(exc), error_code=exc.code) from exc
        if authorization is None:
            raise NativeMeshError(
                "The human cancelled point-cloud input authorization.",
                error_code="NATIVE_POINT_CLOUD_IMPORT_CANCELLED",
            )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return prepare_point_import(
                authorization,
                request,
                cancelled=cancelled,
                progress=progress,
            )

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Import Point Cloud",
                mutate=lambda document: commit_point_import(document, prepared),
                verify=verify_point_import,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="mesh.points.import_point_cloud",
                prepare=prepare,
                validate_before_commit=context.guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc), error_code="NATIVE_POINT_CLOUD_QUEUE_FAILED"
            ) from exc
        return self._response(snapshot)

    def _start_operation(
        self,
        prepared: Any,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        manager, dispatcher = self._manager()

        def process(cancelled: Any, progress: Any) -> Any:
            return process_point_plan(
                prepared,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            if not point_plan_still_exact(context.document, prepared):
                raise NativeMeshError(
                    "An exact source changed during detached point-cloud processing.",
                    error_code="NATIVE_POINT_CLOUD_STATE_STALE",
                )

        def commit(processed: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name={
                    "convert_to_points": "Convert Geometry to Points",
                    "structure": "Structure Point Cloud",
                    "merge": "Merge Point Clouds",
                    "polygon_cut": "Cut Point Cloud",
                }[prepared.operation],
                mutate=lambda document: create_point_results(document, processed),
                verify=verify_point_results,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"mesh.points.{prepared.operation}",
                prepare=process,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc), error_code="NATIVE_POINT_CLOUD_QUEUE_FAILED"
            ) from exc
        return self._response(snapshot)
