# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Sketch edit control."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchControlRuntime import NativeSketchControlRuntime


SKETCH_CONTROL_CAPABILITY_NAME = "sketch.control"


def _control(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSketchControlRuntime):
        raise TypeError("A Sketch control call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Sketch control call requires argument data.")
    return runtime.control(arguments, ticket=getattr(call, "ticket", None))


def register_sketch_control_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(SKETCH_CONTROL_CAPABILITY_NAME, _control)
    )


def sketch_control_runtime_bindings(
    runtime: NativeSketchControlRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeSketchControlRuntime):
        raise TypeError("runtime must be a NativeSketchControlRuntime")
    return {SKETCH_CONTROL_CAPABILITY_NAME: runtime}
