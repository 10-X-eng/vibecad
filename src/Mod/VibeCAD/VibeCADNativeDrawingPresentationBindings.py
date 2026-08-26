# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Drawing presentation state."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingPresentationRuntime import (
    NativeDrawingPresentationRuntime,
)
from VibeCADNativeDrawingPresentationSchema import (
    DRAWING_PRESENTATION_CAPABILITY_NAMES,
)


def _present(call: Any, *, presentation_kind: str) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingPresentationRuntime):
        raise TypeError("A Drawing presentation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing presentation call requires argument data.")
    return runtime.execute(arguments, presentation_kind=presentation_kind)


def _show_page(call: Any) -> Mapping[str, Any]:
    return _present(call, presentation_kind="show_page")


def _page_frames(call: Any) -> Mapping[str, Any]:
    return _present(call, presentation_kind="page_frames")


def _page_grid(call: Any) -> Mapping[str, Any]:
    return _present(call, presentation_kind="page_grid")


def _hidden_edges(call: Any) -> Mapping[str, Any]:
    return _present(call, presentation_kind="hidden_edges")


def register_drawing_presentation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name, present in zip(
        DRAWING_PRESENTATION_CAPABILITY_NAMES,
        (_show_page, _page_frames, _page_grid, _hidden_edges),
        strict=True,
    ):
        registry.register_implementation(
            NativeCapabilityImplementation(
                name,
                present,
            )
        )


def drawing_presentation_runtime_bindings(
    runtime: NativeDrawingPresentationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingPresentationRuntime):
        raise TypeError("runtime must be a NativeDrawingPresentationRuntime")
    return {name: runtime for name in DRAWING_PRESENTATION_CAPABILITY_NAMES}
