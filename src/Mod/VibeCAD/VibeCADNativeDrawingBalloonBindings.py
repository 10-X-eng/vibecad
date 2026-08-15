# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact projected Drawing balloons."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingBalloonRuntime import NativeDrawingBalloonRuntime
from VibeCADNativeDrawingBalloonSchema import DRAWING_BALLOON_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingBalloonRuntime):
        raise TypeError("A Drawing balloon call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing balloon call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_balloon_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_BALLOON_CAPABILITY_NAME, _execute)
    )


def drawing_balloon_runtime_bindings(
    runtime: NativeDrawingBalloonRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingBalloonRuntime):
        raise TypeError("runtime must be a NativeDrawingBalloonRuntime")
    return {DRAWING_BALLOON_CAPABILITY_NAME: runtime}
