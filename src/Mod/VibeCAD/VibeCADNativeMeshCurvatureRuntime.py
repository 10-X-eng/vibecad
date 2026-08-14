# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Mesh curvature plots."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshCurvature import (
    create_mesh_curvature,
    prepare_mesh_curvature,
    verify_mesh_curvature,
)
from VibeCADNativeMeshErrors import NativeMeshError
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
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Calculate Mesh Curvature",
            mutate=lambda document: create_mesh_curvature(document, prepared),
            verify=verify_mesh_curvature,
        )
