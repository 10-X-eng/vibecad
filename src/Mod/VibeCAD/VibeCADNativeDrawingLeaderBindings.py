# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Drawing Leader Lines."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingLeaderRuntime import NativeDrawingLeaderRuntime
from VibeCADNativeDrawingLeaderSchema import DRAWING_ANNOTATION_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingLeaderRuntime):
        raise TypeError("A Drawing leader call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing leader call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_leader_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_ANNOTATION_CAPABILITY_NAME, _execute)
    )


def drawing_leader_runtime_bindings(
    runtime: NativeDrawingLeaderRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingLeaderRuntime):
        raise TypeError("runtime must be a NativeDrawingLeaderRuntime")
    return {DRAWING_ANNOTATION_CAPABILITY_NAME: runtime}
