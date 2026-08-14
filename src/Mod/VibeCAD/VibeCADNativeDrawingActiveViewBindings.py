# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Native Drawing active-view capture."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingActiveViewRuntime import NativeDrawingActiveViewRuntime
from VibeCADNativeDrawingActiveViewSchema import (
    DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingActiveViewRuntime):
        raise TypeError("A Drawing active-view call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing active-view call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_active_view_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_active_view_runtime_bindings(
    runtime: NativeDrawingActiveViewRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingActiveViewRuntime):
        raise TypeError("runtime must be a NativeDrawingActiveViewRuntime")
    return {DRAWING_ACTIVE_VIEW_CAPABILITY_NAME: runtime}
