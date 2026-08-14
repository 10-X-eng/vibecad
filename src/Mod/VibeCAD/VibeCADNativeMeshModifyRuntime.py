# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact retained Mesh modifications."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshGmsh import (
    commit_gmsh_remesh,
    prepare_gmsh_request,
    run_gmsh_remesh,
    verify_gmsh_remesh,
)
from VibeCADNativeMeshModify import (
    create_mesh_modification,
    prepare_mesh_modification,
    verify_mesh_modification,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "harmonize_normals": frozenset({"targets"}),
    "flip_normals": frozenset({"targets"}),
    "fill_holes": frozenset({"targets", "maximum_boundary_edges"}),
    "fill_boundary": frozenset({"target", "seed_facet_index", "refinement_level"}),
    "add_triangle": frozenset({"target", "point_indices"}),
    "remove_components": frozenset({"target", "selection"}),
    "smooth": frozenset({"targets", "settings"}),
    "gmsh_remesh": frozenset(
        {
            "target",
            "algorithm",
            "minimum_element_size_mm",
            "maximum_element_size_mm",
            "surface_angle_degrees",
            "timeout_seconds",
        }
    ),
    "decimate": frozenset({"targets", "settings"}),
    "scale": frozenset({"targets", "factor"}),
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


class NativeMeshModifyRuntime:
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
        if operation == "gmsh_remesh":
            return self._start_gmsh(values, ticket)
        prepared = prepare_mesh_modification(
            self._context.document,
            self._context.document_uid,
            operation,
            values,
        )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name={
                "harmonize_normals": "Harmonize Mesh Normals",
                "flip_normals": "Flip Mesh Normals",
                "fill_holes": "Fill Mesh Holes",
                "fill_boundary": "Fill Mesh Boundary",
                "add_triangle": "Add Mesh Triangle",
                "remove_components": "Remove Mesh Components",
                "smooth": "Smooth Mesh",
                "decimate": "Decimate Mesh",
                "scale": "Scale Mesh",
            }[operation],
            mutate=lambda document: create_mesh_modification(document, prepared),
            verify=verify_mesh_modification,
        )

    def _start_gmsh(
        self,
        values: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        manager = self._context.background_manager
        dispatcher = self._context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Gmsh remeshing is unavailable in this session.",
                error_code="NATIVE_MESH_GMSH_UNAVAILABLE",
            )
        request = prepare_gmsh_request(
            self._context.document,
            self._context.document_uid,
            values,
        )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return run_gmsh_remesh(
                request,
                cancelled=cancelled,
                progress=progress,
            )

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Gmsh Remesh",
                mutate=lambda document: commit_gmsh_remesh(document, prepared),
                verify=verify_gmsh_remesh,
            )

        try:
            snapshot = manager.submit(
                document_uid=self._context.document_uid,
                capability_name="mesh.modify.gmsh_remesh",
                prepare=prepare,
                validate_before_commit=self._context.guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_GMSH_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }
