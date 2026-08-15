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
    DRAWING_PRESENTATION_CAPABILITY_NAME,
)


def _present(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingPresentationRuntime):
        raise TypeError("A Drawing presentation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing presentation call requires argument data.")
    return runtime.execute(arguments)


def register_drawing_presentation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_PRESENTATION_CAPABILITY_NAME,
            _present,
        )
    )


def drawing_presentation_runtime_bindings(
    runtime: NativeDrawingPresentationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingPresentationRuntime):
        raise TypeError("runtime must be a NativeDrawingPresentationRuntime")
    return {DRAWING_PRESENTATION_CAPABILITY_NAME: runtime}
