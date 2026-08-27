# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Mesh cuts and sections."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADMeshCutJob import make_request, run_mesh_cut
from VibeCADNativeMeshCut import (
    create_mesh_cut,
    mesh_cut_still_exact,
    prepare_mesh_cut,
    verify_mesh_cut,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "poly_cut": frozenset({"target", "polygon", "result"}),
    "poly_trim": frozenset({"target", "polygon", "result"}),
    "trim_by_plane": frozenset({"target", "plane", "result"}),
    "section_by_plane": frozenset(
        {"target", "plane", "result_label", "settings"}
    ),
    "cross_sections": frozenset({"targets", "planes", "settings"}),
}


class NativeMeshCutRuntime:
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
        self._context.guard()
        prepared = prepare_mesh_cut(
            self._context.document,
            self._context.document_uid,
            operation,
            values,
        )
        context = self._context
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Mesh cuts are unavailable in this session.",
                error_code="NATIVE_MESH_CUT_UNAVAILABLE",
            )
        request = make_request(prepared)

        def validate() -> None:
            context.guard()
            if not mesh_cut_still_exact(context.document, prepared):
                raise NativeMeshError(
                    "A Mesh cut input changed while the operation was running; no stale result was applied.",
                    error_code="NATIVE_MESH_STATE_STALE",
                )

        def commit(result: Any) -> Mapping[str, Any]:
            from VibeCADNativeImmediate import run_immediate_mutation

            response = run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name={
                    "poly_cut": "Mesh Polygon Cut",
                    "poly_trim": "Mesh Polygon Trim",
                    "trim_by_plane": "Trim Mesh With Plane",
                    "section_by_plane": "Section Mesh With Plane",
                    "cross_sections": "Mesh Cross-Sections",
                }[operation],
                mutate=lambda document: create_mesh_cut(document, result.prepared),
                verify=verify_mesh_cut,
            )
            response["background_prepared"] = True
            response["cache_hit"] = bool(result.cache_hit)
            return response

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"mesh.cut.{operation}",
                prepare=lambda cancelled, progress: run_mesh_cut(
                    request, cancelled=cancelled, progress=progress
                ),
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing verified Mesh cut",
                cleanup=lambda _result: context.state.cancel_mutation(ticket),
                changes_document=True,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc), error_code="NATIVE_MESH_CUT_QUEUE_FAILED"
            ) from exc
        try:
            def watch_status() -> None:
                import FreeCAD as App

                if bool(getattr(App, "GuiUp", False)):
                    from VibeCADMeshCutGui import watch_mesh_cut_job

                    watch_mesh_cut_job(manager, str(snapshot.job_id))

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
