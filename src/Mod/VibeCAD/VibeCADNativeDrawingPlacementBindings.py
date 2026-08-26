# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime bindings for explicit Drawing item placement."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingPlacementRuntime import NativeDrawingPlacementRuntime
from VibeCADNativeDrawingPlacementSchema import DRAWING_PLACEMENT_CAPABILITY_NAMES


def _execute(call: Any, *, operation: str) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingPlacementRuntime):
        raise TypeError("A Drawing placement call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing placement call requires argument data.")
    return runtime.execute(
        arguments,
        ticket=getattr(call, "ticket", None),
        operation=operation,
    )


def _place_views(call: Any) -> Mapping[str, Any]:
    return _execute(call, operation="place_views")


def _place_dimension_labels(call: Any) -> Mapping[str, Any]:
    return _execute(call, operation="place_dimension_labels")


def _place_notes(call: Any) -> Mapping[str, Any]:
    return _execute(call, operation="place_notes")


def register_drawing_placement_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name, execute in zip(
        DRAWING_PLACEMENT_CAPABILITY_NAMES,
        (_place_views, _place_dimension_labels, _place_notes),
        strict=True,
    ):
        registry.register_implementation(
            NativeCapabilityImplementation(name, execute)
        )


def drawing_placement_runtime_bindings(
    runtime: NativeDrawingPlacementRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingPlacementRuntime):
        raise TypeError("runtime must be a NativeDrawingPlacementRuntime")
    return {name: runtime for name in DRAWING_PLACEMENT_CAPABILITY_NAMES}


__all__ = [
    "drawing_placement_runtime_bindings",
    "register_drawing_placement_capability_implementations",
]
