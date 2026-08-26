# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing presentation state."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingPresentation import (
    show_exact_drawing,
    set_drawing_frame_visibility,
    set_drawing_grid_visibility,
    set_drawing_hidden_edge_visibility,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext


_FIELDS = {
    "show_page": {"show": frozenset({"page"})},
    "page_frames": {"set_visibility": frozenset({"page", "visible"})},
    "page_grid": {"set_visibility": frozenset({"page", "visible"})},
    "hidden_edges": {"set_visibility": frozenset({"view", "visible"})},
}


class NativeDrawingPresentationRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        presentation_kind: str,
    ) -> dict[str, Any]:
        if presentation_kind not in _FIELDS:
            raise ValueError("presentation_kind is not supported")
        _operation, values = strict_variant_arguments(
            arguments,
            _FIELDS[presentation_kind],
        )
        if presentation_kind == "show_page":
            return show_exact_drawing(self._context, values)
        if presentation_kind == "page_frames":
            return set_drawing_frame_visibility(self._context, values)
        if presentation_kind == "page_grid":
            return set_drawing_grid_visibility(self._context, values)
        if presentation_kind == "hidden_edges":
            return set_drawing_hidden_edge_visibility(self._context, values)
        raise AssertionError("unreachable Drawing presentation kind")
