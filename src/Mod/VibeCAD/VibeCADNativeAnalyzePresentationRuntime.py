# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for legacy FEM result presentation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzePresentation import (
    present_legacy_result,
    set_post_auto_recompute,
)
from VibeCADNativeAnalyzeClipping import (
    add_face_clipping_plane,
    remove_all_view_clipping_planes,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext


_VARIANTS = {
    "show_result": frozenset(
        {"result", "field", "deformation_scale", "visible"}
    ),
    "set_post_auto_recompute": frozenset({"expected_enabled", "enabled"}),
    "add_clipping_plane": frozenset(
        {
            "face",
            "reverse",
            "expected_clipping_state_sha256",
            "expected_clipping_plane_count",
        }
    ),
    "remove_all_clipping_planes": frozenset(
        {
            "expected_clipping_state_sha256",
            "expected_clipping_plane_count",
        }
    ),
}


class NativeAnalyzePresentationRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        if operation == "show_result":
            return present_legacy_result(self._context, **values)
        if operation == "set_post_auto_recompute":
            return set_post_auto_recompute(self._context, **values)
        if operation == "add_clipping_plane":
            return add_face_clipping_plane(self._context, **values)
        if operation == "remove_all_clipping_planes":
            return remove_all_view_clipping_planes(self._context, **values)
        raise RuntimeError(f"Analyze presentation operation is unavailable: {operation}.")
