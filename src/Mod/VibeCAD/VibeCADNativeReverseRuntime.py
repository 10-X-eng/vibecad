# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound background runtime for Reverse Engineering capabilities."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeReversePlan import (
    prepare_reverse_plan,
    process_reverse_plan,
    reverse_plan_still_exact,
)
from VibeCADNativeReverseResults import create_reverse_results, verify_reverse_results
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_REBUILD_VARIANTS = {
    "poisson_reconstruction": frozenset(
        {
            "target",
            "result_label",
            "octree_depth",
            "solver_divide",
            "samples_per_node",
            "normal_neighbors",
        }
    ),
    "view_triangulation": frozenset({"structured_clouds"}),
}
_APPROXIMATE_VARIANTS = {
    "approx_plane": frozenset({"geometry_sources"}),
    "approx_cylinder": frozenset({"cylinder_meshes"}),
    "approx_sphere": frozenset({"sphere_meshes"}),
    "approx_polynomial": frozenset({"polynomial_meshes"}),
    "approx_surface": frozenset(
        {
            "surface_source",
            "result_label",
            "u_degree",
            "v_degree",
            "u_control_points",
            "v_control_points",
            "iterations",
            "patch_size_factor",
            "parameter_correction",
            "smoothing",
            "uv_directions",
        }
    ),
    "approx_curve": frozenset({"curve_source", "result_label", "fit"}),
}
_TRANSACTIONS = {
    "poisson_reconstruction": "Poisson Reconstruction",
    "view_triangulation": "Triangulate Structured Points",
    "approx_plane": "Fit Planes",
    "approx_cylinder": "Fit Cylinders",
    "approx_sphere": "Fit Spheres",
    "approx_polynomial": "Fit Polynomial Surfaces",
    "approx_surface": "Fit B-Spline Surface",
    "approx_curve": "Fit B-Spline Curve",
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


class NativeReverseRuntime:
    def __init__(self, context: NativeRuntimeContext, capability_name: str) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        if capability_name not in {"mesh.rebuild", "mesh.approximate"}:
            raise ValueError("capability_name must be mesh.rebuild or mesh.approximate")
        self._context = context
        self._capability_name = capability_name

    @property
    def capability_name(self) -> str:
        return self._capability_name

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        variants = (
            _REBUILD_VARIANTS
            if self._capability_name == "mesh.rebuild"
            else _APPROXIMATE_VARIANTS
        )
        operation, values = strict_variant_arguments(arguments, variants)
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        context = self._context
        context.guard()
        prepared = prepare_reverse_plan(
            context.document,
            context.document_uid,
            operation,
            values,
        )
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Reverse Engineering is unavailable in this session.",
                error_code="NATIVE_REVERSE_BACKGROUND_UNAVAILABLE",
            )

        def process(cancelled: Any, progress: Any) -> Any:
            return process_reverse_plan(prepared, cancelled=cancelled, progress=progress)

        def validate() -> None:
            context.guard()
            if not reverse_plan_still_exact(context.document, prepared):
                raise NativeMeshError(
                    "An exact source changed during detached Reverse Engineering processing.",
                    error_code="NATIVE_REVERSE_STATE_STALE",
                )

        def commit(processed: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=_TRANSACTIONS[operation],
                mutate=lambda document: create_reverse_results(document, processed),
                verify=verify_reverse_results,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"{self._capability_name}.{operation}",
                prepare=process,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc), error_code="NATIVE_REVERSE_QUEUE_FAILED"
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }
