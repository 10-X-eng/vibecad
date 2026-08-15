# SPDX-License-Identifier: LGPL-2.1-or-later

"""Runtime bindings for focused destructive Sketch editing tools."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchCleanupSchema import SKETCH_CLEANUP_CAPABILITY_NAMES
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime


def _cleanup(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSketchGeometryRuntime):
        raise TypeError("A Sketch cleanup call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Sketch cleanup call requires argument data.")
    return runtime.mutate_geometry(arguments, ticket=getattr(call, "ticket", None))


def register_sketch_cleanup_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in SKETCH_CLEANUP_CAPABILITY_NAMES:
        registry.register_implementation(NativeCapabilityImplementation(name, _cleanup))


def sketch_cleanup_runtime_bindings(
    runtime: NativeSketchGeometryRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeSketchGeometryRuntime):
        raise TypeError("runtime must be a NativeSketchGeometryRuntime")
    return {name: runtime for name in SKETCH_CLEANUP_CAPABILITY_NAMES}
