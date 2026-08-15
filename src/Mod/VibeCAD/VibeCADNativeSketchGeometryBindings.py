# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for contextual Sketch geometry."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime


SKETCH_GEOMETRY_CAPABILITY_NAME = "sketch.geometry"


def _geometry(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSketchGeometryRuntime):
        raise TypeError("A Sketch geometry call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Sketch geometry call requires argument data.")
    return runtime.mutate_geometry(arguments, ticket=getattr(call, "ticket", None))


def register_sketch_geometry_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(SKETCH_GEOMETRY_CAPABILITY_NAME, _geometry)
    )


def sketch_geometry_runtime_bindings(
    runtime: NativeSketchGeometryRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeSketchGeometryRuntime):
        raise TypeError("runtime must be a NativeSketchGeometryRuntime")
    return {SKETCH_GEOMETRY_CAPABILITY_NAME: runtime}
