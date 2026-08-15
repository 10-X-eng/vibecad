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
    "show": frozenset({"page"}),
    "set_frame_visibility": frozenset({"page", "visible"}),
    "set_grid_visibility": frozenset({"page", "visible"}),
    "set_hidden_edges_visible": frozenset({"view", "visible"}),
}


class NativeDrawingPresentationRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _FIELDS)
        if operation == "show":
            return show_exact_drawing(self._context, values)
        if operation == "set_frame_visibility":
            return set_drawing_frame_visibility(self._context, values)
        if operation == "set_grid_visibility":
            return set_drawing_grid_visibility(self._context, values)
        if operation == "set_hidden_edges_visible":
            return set_drawing_hidden_edge_visibility(self._context, values)
        raise RuntimeError(
            f"Drawing presentation operation is unavailable: {operation}."
        )
