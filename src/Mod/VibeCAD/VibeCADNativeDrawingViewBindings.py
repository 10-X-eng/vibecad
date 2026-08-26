# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Drawing standard views."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingViewRuntime import NativeDrawingViewRuntime
from VibeCADNativeDrawingViewSchema import DRAWING_VIEW_CAPABILITY_NAMES


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingViewRuntime):
        raise TypeError("A Drawing view call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing view call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_view_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in DRAWING_VIEW_CAPABILITY_NAMES:
        registry.register_implementation(
            NativeCapabilityImplementation(name, _execute)
        )


def drawing_view_runtime_bindings(
    runtime: NativeDrawingViewRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingViewRuntime):
        raise TypeError("runtime must be a NativeDrawingViewRuntime")
    return {name: runtime for name in DRAWING_VIEW_CAPABILITY_NAMES}
