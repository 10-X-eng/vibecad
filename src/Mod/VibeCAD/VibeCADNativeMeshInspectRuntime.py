# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Mesh Analyze reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshInspect import (
    finalize_mesh_evaluation,
    inspect_mesh_bounds,
    inspect_mesh_curvature,
    inspect_mesh_facets,
    inspect_mesh_solid,
    prepare_mesh_evaluation,
    run_mesh_evaluation,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext


_VARIANTS = {
    "evaluation": frozenset({"target", "degeneration_mode"}),
    "evaluate_facet": frozenset({"target", "facet_indices"}),
    "curvature_info": frozenset({"curvature", "vertex_indices"}),
    "evaluate_solid": frozenset({"target"}),
    "bounding_box": frozenset({"target"}),
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


class NativeMeshInspectRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        self._context.guard()
        if operation == "evaluation":
            return self._start_evaluation(values)
        if operation == "evaluate_facet":
            result = inspect_mesh_facets(
                self._context.document,
                self._context.document_uid,
                values,
            )
        elif operation == "curvature_info":
            result = inspect_mesh_curvature(
                self._context.document,
                self._context.document_uid,
                values,
            )
        elif operation == "evaluate_solid":
            result = inspect_mesh_solid(
                self._context.document,
                self._context.document_uid,
                values["target"],
            )
        elif operation == "bounding_box":
            result = inspect_mesh_bounds(
                self._context.document,
                self._context.document_uid,
                values["target"],
            )
        else:
            raise NativeMeshError("The Mesh inspection operation is not implemented.")
        self._context.guard()
        return result

    def _start_evaluation(self, values: Mapping[str, Any]) -> dict[str, Any]:
        manager = self._context.background_manager
        dispatcher = self._context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Mesh quality evaluation is unavailable in this session.",
                error_code="NATIVE_MESH_EVALUATION_UNAVAILABLE",
            )
        request = prepare_mesh_evaluation(
            self._context.document,
            self._context.document_uid,
            values,
        )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return run_mesh_evaluation(
                request,
                cancelled=cancelled,
                progress=progress,
            )

        def finalize(report: Mapping[str, Any]) -> Mapping[str, Any]:
            return finalize_mesh_evaluation(
                self._context.document,
                request,
                report,
            )

        try:
            snapshot = manager.submit(
                document_uid=self._context.document_uid,
                capability_name="mesh.inspect.evaluation",
                prepare=prepare,
                validate_before_commit=self._context.guard,
                commit=finalize,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Verifying exact Mesh state",
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_EVALUATION_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }
