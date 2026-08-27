# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Mesh booleans."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshBoolean import (
    capture_mesh_boolean,
    commit_prepared_mesh_boolean,
    verify_prepared_mesh_boolean,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "union": frozenset({"first", "second", "result_label"}),
    "intersection": frozenset({"first", "second", "result_label"}),
    "difference": frozenset({"first", "second", "result_label"}),
}


class NativeMeshBooleanRuntime:
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
        context = self._context
        context.guard()
        request = capture_mesh_boolean(
            context.document,
            context.document_uid,
            operation,
            values,
        )
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Mesh booleans are unavailable in this session.",
                error_code="NATIVE_MESH_BOOLEAN_UNAVAILABLE",
            )
        from VibeCADMeshBooleanJob import run_mesh_boolean

        def validate() -> None:
            context.guard()
            if not mesh_target_still_exact(
                context.document, request.first
            ) or not mesh_target_still_exact(context.document, request.second):
                raise NativeMeshError(
                    "A source Mesh changed while its boolean was running; no stale result was applied.",
                    error_code="NATIVE_MESH_STATE_STALE",
                )

        def commit(prepared: Any) -> Mapping[str, Any]:
            from VibeCADNativeImmediate import run_immediate_mutation

            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Mesh {operation.title()}",
                mutate=lambda document: commit_prepared_mesh_boolean(document, prepared),
                verify=verify_prepared_mesh_boolean,
            )

        def cleanup(_prepared: Any) -> None:
            context.state.cancel_mutation(ticket)

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"mesh.boolean.{operation}",
                prepare=lambda cancelled, progress: run_mesh_boolean(
                    request,
                    cancelled=cancelled,
                    progress=progress,
                ),
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing verified Mesh boolean",
                cleanup=cleanup,
                changes_document=True,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_BOOLEAN_QUEUE_FAILED",
            ) from exc
        try:
            def watch_status() -> None:
                import FreeCAD as App

                if not bool(getattr(App, "GuiUp", False)):
                    return
                from VibeCADMeshBooleanGui import watch_mesh_boolean_job

                watch_mesh_boolean_job(manager, str(snapshot.job_id))

            dispatcher(watch_status)
        except Exception:
            pass
        return {
            "job": {
                "job_id": str(snapshot.job_id),
                "capability": str(snapshot.capability_name),
                "phase": str(snapshot.phase),
                "progress_percent": int(snapshot.progress_percent),
                "progress_message": str(snapshot.progress_message),
                "terminal": bool(snapshot.terminal),
            },
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }
