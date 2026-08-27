# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Mesh curvature plots."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADMeshCurvatureJob import make_request, run_mesh_curvature
from VibeCADNativeMeshCurvature import (
    create_mesh_curvature,
    prepare_mesh_curvature,
    verify_mesh_curvature,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


class NativeMeshCurvatureRuntime:
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
        operation, values = strict_variant_arguments(
            arguments,
            {"vertex_curvature": frozenset({"targets"})},
        )
        if operation != "vertex_curvature":
            raise NativeMeshError("The Mesh curvature operation is not implemented.")
        self._context.guard()
        prepared = prepare_mesh_curvature(
            self._context.document,
            self._context.document_uid,
            values,
        )
        context = self._context
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Mesh curvature is unavailable in this session.",
                error_code="NATIVE_MESH_CURVATURE_UNAVAILABLE",
            )
        request = make_request(prepared)

        def validate() -> None:
            context.guard()
            if any(
                not mesh_target_still_exact(context.document, target)
                for target in prepared.targets
            ):
                raise NativeMeshError(
                    "A source Mesh changed while curvature was running; no stale result was applied.",
                    error_code="NATIVE_MESH_STATE_STALE",
                )

        def commit(result: Any) -> Mapping[str, Any]:
            response = run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Calculate Mesh Curvature",
                mutate=lambda document: create_mesh_curvature(
                    document, result.prepared
                ),
                verify=verify_mesh_curvature,
            )
            response["background_prepared"] = True
            response["cache_hit"] = bool(result.cache_hit)
            return response

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="mesh.curvature.vertex_curvature",
                prepare=lambda cancelled, progress: run_mesh_curvature(
                    request,
                    cancelled=cancelled,
                    progress=progress,
                ),
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing verified Mesh curvature",
                cleanup=lambda _result: context.state.cancel_mutation(ticket),
                changes_document=True,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_CURVATURE_QUEUE_FAILED",
            ) from exc
        try:
            def watch_status() -> None:
                import FreeCAD as App

                if bool(getattr(App, "GuiUp", False)):
                    from VibeCADMeshCurvatureGui import watch_mesh_curvature_job

                    watch_mesh_curvature_job(manager, str(snapshot.job_id))

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
