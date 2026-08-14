# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Mesh cuts and sections."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshCut import create_mesh_cut, prepare_mesh_cut, verify_mesh_cut
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
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name={
                "poly_cut": "Mesh Polygon Cut",
                "poly_trim": "Mesh Polygon Trim",
                "trim_by_plane": "Trim Mesh With Plane",
                "section_by_plane": "Section Mesh With Plane",
                "cross_sections": "Mesh Cross-Sections",
            }[operation],
            mutate=lambda document: create_mesh_cut(document, prepared),
            verify=verify_mesh_cut,
        )
