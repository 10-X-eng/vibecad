# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Mesh segment operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshSegment import create_mesh_segment, verify_mesh_segment
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshSegments import (
    BACKGROUND_SEGMENT_OPERATIONS,
    capture_background_mesh_segment,
    prepare_mesh_segment,
)
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "merge": frozenset({"sources", "result_label"}),
    "split_components": frozenset({"target", "result_label_prefix"}),
    "mesh_segmentation": frozenset(
        {"target", "surfaces", "smoothing_steps", "result_label_prefix"}
    ),
    "segmentation_best_fit": frozenset({"target", "surfaces", "result_label_prefix"}),
    "reverse_segmentation": frozenset(
        {
            "target",
            "minimum_facets",
            "curvature_tolerance",
            "distance_tolerance_mm",
            "smoothing_steps",
            "include_unused_facets",
            "create_boundary_faces",
            "result_label_prefix",
        }
    ),
    "segmentation_manual": frozenset({"target", "selection", "result"}),
    "segmentation_from_components": frozenset({"targets", "result_label_prefix"}),
    "mesh_boundary": frozenset({"targets", "make_faces_when_closed"}),
}
_TRANSACTIONS = {
    "merge": "Merge Meshes",
    "split_components": "Split Mesh Components",
    "mesh_segmentation": "Segment Mesh by Curvature",
    "segmentation_best_fit": "Segment Mesh by Best Fit",
    "reverse_segmentation": "Segment Mesh by Planar Surfaces",
    "segmentation_manual": "Segment Selected Mesh Facets",
    "segmentation_from_components": "Segment Mesh Components",
    "mesh_boundary": "Create Mesh Boundaries",
}


def _focused_segment_arguments(
    capability_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    values = dict(arguments)
    if capability_name == "mesh.combine":
        values.setdefault("result_label", "Combined Mesh")
    elif capability_name == "mesh.separate":
        values.setdefault("result_label_prefix", "Component")
    return values


class NativeMeshSegmentRuntime:
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
        normalized_arguments = _focused_segment_arguments(
            str(getattr(ticket, "capability_name", "") or ""),
            arguments,
        )
        operation, values = strict_variant_arguments(normalized_arguments, _VARIANTS)
        context = self._context
        context.guard()
        if operation in BACKGROUND_SEGMENT_OPERATIONS:
            captured = capture_background_mesh_segment(
                context.document,
                context.document_uid,
                operation,
                values,
            )
            manager = context.background_manager
            dispatcher = context.document_thread_dispatch
            if manager is None or dispatcher is None:
                raise NativeMeshError(
                    "Background Mesh segmentation is unavailable in this session.",
                    error_code="NATIVE_MESH_SEGMENTATION_UNAVAILABLE",
                )
            from VibeCADMeshSegmentationJob import make_request, run_mesh_segmentation

            request = make_request(captured)

            def validate() -> None:
                context.guard()
                if not all(
                    mesh_target_still_exact(context.document, target)
                    for target in captured.targets
                ):
                    raise NativeMeshError(
                        "A source Mesh changed while segmentation was running; no stale result was applied.",
                        error_code="NATIVE_MESH_STATE_STALE",
                    )

            def commit(result: Any) -> Mapping[str, Any]:
                return run_immediate_mutation(
                    context,
                    ticket=ticket,
                    transaction_name=_TRANSACTIONS[operation],
                    mutate=lambda document: create_mesh_segment(
                        document,
                        result.prepared,
                    ),
                    verify=verify_mesh_segment,
                )

            def cleanup(_result: Any) -> None:
                context.state.cancel_mutation(ticket)

            try:
                snapshot = manager.submit(
                    document_uid=context.document_uid,
                    capability_name=f"mesh.segment.{operation}",
                    prepare=lambda cancelled, progress: run_mesh_segmentation(
                        request,
                        cancelled=cancelled,
                        progress=progress,
                    ),
                    validate_before_commit=validate,
                    commit=commit,
                    dispatch_to_document_thread=dispatcher,
                    finalize_message="Publishing verified Mesh segments",
                    cleanup=cleanup,
                    changes_document=True,
                    document_change_resolver=lambda result: bool(
                        result.get("changed", True)
                    ),
                )
            except NativeBackgroundError as exc:
                raise NativeMeshError(
                    str(exc),
                    error_code="NATIVE_MESH_SEGMENTATION_QUEUE_FAILED",
                ) from exc
            try:
                def watch_status() -> None:
                    import FreeCAD as App

                    if not bool(getattr(App, "GuiUp", False)):
                        return
                    from VibeCADMeshSegmentationGui import watch_mesh_segmentation_job

                    watch_mesh_segmentation_job(manager, str(snapshot.job_id))

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

        prepared = prepare_mesh_segment(
            context.document,
            context.document_uid,
            operation,
            values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTIONS[operation],
            mutate=lambda document: create_mesh_segment(document, prepared),
            verify=verify_mesh_segment,
        )
