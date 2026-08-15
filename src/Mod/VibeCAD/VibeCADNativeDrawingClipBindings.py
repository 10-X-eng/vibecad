# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Drawing clip groups."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingClipRuntime import NativeDrawingClipRuntime
from VibeCADNativeDrawingClipSchema import DRAWING_CLIP_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingClipRuntime):
        raise TypeError("A Drawing clip-group call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing clip-group call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_clip_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_CLIP_CAPABILITY_NAME, _execute)
    )


def drawing_clip_runtime_bindings(
    runtime: NativeDrawingClipRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingClipRuntime):
        raise TypeError("runtime must be a NativeDrawingClipRuntime")
    return {DRAWING_CLIP_CAPABILITY_NAME: runtime}
