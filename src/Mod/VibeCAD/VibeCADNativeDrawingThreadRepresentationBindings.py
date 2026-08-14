# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing thread representations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingThreadRepresentationRuntime import (
    NativeDrawingThreadRepresentationRuntime,
)
from VibeCADNativeDrawingThreadRepresentationSchema import (
    DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingThreadRepresentationRuntime):
        raise TypeError("A thread-representation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A thread-representation call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_thread_representation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_thread_representation_runtime_bindings(
    runtime: NativeDrawingThreadRepresentationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingThreadRepresentationRuntime):
        raise TypeError("runtime must be NativeDrawingThreadRepresentationRuntime")
    return {DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME: runtime}
