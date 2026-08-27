# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact retained Mesh modifications."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import NativeArgumentError, strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADMeshModificationJob import make_request, run_mesh_modification
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshGmsh import (
    commit_gmsh_remesh,
    prepare_gmsh_request,
    run_gmsh_remesh,
    verify_gmsh_remesh,
)
from VibeCADNativeMeshModify import (
    PreparedMeshModification,
    accept_mesh_modification_results,
    create_mesh_modification,
    prepare_mesh_modification,
    verify_mesh_modification,
    verify_mesh_modification_noop,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "harmonize_normals": frozenset({"targets"}),
    "repair": frozenset({"targets", "settings"}),
    "flip_normals": frozenset({"targets"}),
    "fill_holes": frozenset({"targets", "maximum_boundary_edges"}),
    "fill_boundary": frozenset({"targets", "seed_facet_index", "refinement_level"}),
    "add_triangle": frozenset({"targets", "point_indices"}),
    "remove_components": frozenset({"targets", "selection"}),
    "smooth": frozenset({"targets", "settings"}),
    "gmsh_remesh": frozenset(
        {
            "targets",
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


def _focused_modify_arguments(
    capability_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Lower focused provider fields to the shared retained operation contract."""

    values = dict(arguments)
    if capability_name == "mesh.smooth":
        method = str(values.get("method") or "")
        method_fields = {
            "taubin": frozenset({"lambda", "mu"}),
            "laplace": frozenset({"lambda"}),
            "median": frozenset(),
        }
        if method not in method_fields:
            raise NativeArgumentError("method must be taubin, laplace, or median.")
        expected = frozenset(
            {"operation", "targets", "method", "iterations"}
        ) | method_fields[method]
        if set(values) != expected:
            names = ", ".join(sorted(expected - {"operation"}))
            raise NativeArgumentError(
                f"{method} smoothing requires exactly: {names}."
            )
        settings = {
            name: values[name]
            for name in ("method", "iterations", "lambda", "mu")
            if name in values
        }
        return {
            "operation": "smooth",
            "targets": values["targets"],
            "settings": settings,
        }
    if capability_name == "mesh.decimate":
        mode = str(values.get("mode") or "")
        mode_fields = {
            "target_facets": frozenset({"target_facet_count"}),
            "percentage": frozenset({"reduction_percent", "tolerance_mm"}),
        }
        if mode not in mode_fields:
            raise NativeArgumentError("mode must be target_facets or percentage.")
        expected = frozenset({"operation", "targets", "mode"}) | mode_fields[mode]
        if set(values) != expected:
            names = ", ".join(sorted(expected - {"operation"}))
            raise NativeArgumentError(
                f"{mode} decimation requires exactly: {names}."
            )
        settings = {
            name: values[name]
            for name in (
                "mode",
                "target_facet_count",
                "reduction_percent",
                "tolerance_mm",
            )
            if name in values
        }
        return {
            "operation": "decimate",
            "targets": values["targets"],
            "settings": settings,
        }
    if capability_name == "mesh.repair":
        defects = values.get("defects")
        if defects is not None:
            if not isinstance(defects, list) or not defects:
                raise NativeArgumentError("defects must contain inspected defect names.")
            defect_passes = {
                "non_uniform_orientation": "orientation",
                "duplicated_facets": "duplicates",
                "duplicated_points": "duplicates",
                "non_manifold_edges": "non_manifold_topology",
                "non_manifold_points": "non_manifold_topology",
                "facet_indices_out_of_range": "indices",
                "point_indices_out_of_range": "indices",
                "corrupted_facets": "indices",
                "invalid_neighbourhood": "indices",
                "degenerated_facets": "degeneracies",
                "self_intersections": "self_intersections",
                "surface_folds": "surface_folds",
                "boundary_folds": "surface_folds",
            }
            unknown = [name for name in defects if name not in defect_passes]
            if unknown:
                raise NativeArgumentError(
                    "defects contains names not returned as repairable Mesh issues."
                )
            selected = {defect_passes[name] for name in defects}
            repairs = [
                name
                for name in (
                    "orientation",
                    "duplicates",
                    "non_manifold_topology",
                    "indices",
                    "degeneracies",
                    "self_intersections",
                    "surface_folds",
                )
                if name in selected
            ]
        else:
            repairs = values.get("repairs")
        if not isinstance(repairs, list) or not repairs:
            raise NativeArgumentError("repairs must contain at least one repair pass.")
        settings = {
            "repairs": list(repairs),
            "maximum_boundary_edges": 0,
            "max_iterations": int(values.get("max_iterations", 1)),
        }
        return {
            "operation": "repair",
            "targets": values["targets"],
            "settings": settings,
        }
    return values


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
        normalized_arguments = _focused_modify_arguments(
            str(getattr(ticket, "capability_name", "") or ""),
            arguments,
        )
        operation, values = strict_variant_arguments(
            normalized_arguments,
            _VARIANTS,
            defaults={
                "fill_holes": {"maximum_boundary_edges": 3},
                "gmsh_remesh": {"timeout_seconds": 300},
            },
        )
        self._context.guard()
        if operation == "gmsh_remesh":
            return self._start_gmsh(values, ticket)
        prepared = prepare_mesh_modification(
            self._context.document,
            self._context.document_uid,
            operation,
            values,
        )
        return self._start_modification(prepared, ticket)

    def _start_modification(
        self,
        prepared: PreparedMeshModification,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        manager = self._context.background_manager
        dispatcher = self._context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Mesh modification is unavailable in this session.",
                error_code="NATIVE_MESH_MODIFICATION_UNAVAILABLE",
            )
        request = make_request(prepared)

        def prepare(cancelled: Any, progress: Any) -> Any:
            return run_mesh_modification(
                request,
                cancelled=cancelled,
                progress=progress,
            )

        def commit(result: Any) -> Mapping[str, Any]:
            accepted = accept_mesh_modification_results(
                result.request.prepared,
                result,
            )
            if not accepted.targets:
                response = verify_mesh_modification_noop(
                    self._context.document,
                    accepted,
                )
            else:
                response = run_immediate_mutation(
                    self._context,
                    ticket=ticket,
                    transaction_name={
                        "harmonize_normals": "Harmonize Mesh Normals",
                        "repair": "Repair Mesh",
                        "flip_normals": "Flip Mesh Normals",
                        "fill_holes": "Fill Mesh Holes",
                        "fill_boundary": "Fill Mesh Boundary",
                        "add_triangle": "Add Mesh Triangle",
                        "remove_components": "Remove Mesh Components",
                        "smooth": "Smooth Mesh",
                        "decimate": "Decimate Mesh",
                        "scale": "Scale Mesh",
                    }[prepared.operation],
                    mutate=lambda document: create_mesh_modification(document, accepted),
                    verify=verify_mesh_modification,
                )
            response["background_prepared"] = True
            response["cache_hit"] = bool(result.cache_hit)
            return response

        try:
            snapshot = manager.submit(
                document_uid=self._context.document_uid,
                capability_name=f"mesh.modify.{prepared.operation}",
                prepare=prepare,
                validate_before_commit=self._context.guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing verified Mesh modification",
                changes_document=True,
                document_change_resolver=lambda result: bool(result["changed"]),
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_MODIFICATION_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }

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
                changes_document=True,
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
