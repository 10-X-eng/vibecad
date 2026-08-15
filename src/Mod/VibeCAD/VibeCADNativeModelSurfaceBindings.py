# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Model Surface operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelSurfaceRuntime import NativeModelSurfaceRuntime


MODEL_SURFACE_CAPABILITY_NAME = "model.surface"


def _surface(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelSurfaceRuntime):
        raise TypeError("A Model Surface call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model Surface call requires argument data.")
    return runtime.mutate_surface(arguments, ticket=getattr(call, "ticket", None))


def register_model_surface_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MODEL_SURFACE_CAPABILITY_NAME, _surface)
    )


def model_surface_runtime_bindings(
    runtime: NativeModelSurfaceRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelSurfaceRuntime):
        raise TypeError("runtime must be a NativeModelSurfaceRuntime")
    return {MODEL_SURFACE_CAPABILITY_NAME: runtime}
