# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Mesh segment operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshSegment import create_mesh_segment, verify_mesh_segment
from VibeCADNativeMeshSegments import prepare_mesh_segment
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
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        self._context.guard()
        prepared = prepare_mesh_segment(
            self._context.document,
            self._context.document_uid,
            operation,
            values,
        )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=_TRANSACTIONS[operation],
            mutate=lambda document: create_mesh_segment(document, prepared),
            verify=verify_mesh_segment,
        )
