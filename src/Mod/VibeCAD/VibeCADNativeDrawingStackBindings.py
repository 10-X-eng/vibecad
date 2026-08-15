# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Drawing view stacking."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingStackRuntime import NativeDrawingStackRuntime
from VibeCADNativeDrawingStackSchema import DRAWING_STACK_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingStackRuntime):
        raise TypeError("A Drawing stack call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing stack call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_stack_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_STACK_CAPABILITY_NAME, _execute)
    )


def drawing_stack_runtime_bindings(
    runtime: NativeDrawingStackRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingStackRuntime):
        raise TypeError("runtime must be a NativeDrawingStackRuntime")
    return {DRAWING_STACK_CAPABILITY_NAME: runtime}
