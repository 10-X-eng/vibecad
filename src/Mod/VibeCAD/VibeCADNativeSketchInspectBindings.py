# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for read-only Sketch relationship queries."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchInspectRuntime import NativeSketchInspectRuntime


SKETCH_INSPECT_CAPABILITY_NAME = "sketch.inspect"


def _inspect(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSketchInspectRuntime):
        raise TypeError("A Sketch inspect call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Sketch inspect call requires argument data.")
    return runtime.inspect(arguments)


def register_sketch_inspect_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(SKETCH_INSPECT_CAPABILITY_NAME, _inspect)
    )


def sketch_inspect_runtime_bindings(
    runtime: NativeSketchInspectRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeSketchInspectRuntime):
        raise TypeError("runtime must be a NativeSketchInspectRuntime")
    return {SKETCH_INSPECT_CAPABILITY_NAME: runtime}
