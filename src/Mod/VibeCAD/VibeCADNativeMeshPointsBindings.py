# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for the Mesh-ribbon Points group."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshPointsRuntime import NativeMeshPointsRuntime
from VibeCADNativeMeshPointsSchema import MESH_POINTS_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshPointsRuntime):
        raise TypeError("A point-cloud call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A point-cloud call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_mesh_points_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MESH_POINTS_CAPABILITY_NAME, _execute)
    )


def mesh_points_runtime_bindings(runtime: NativeMeshPointsRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshPointsRuntime):
        raise TypeError("runtime must be a NativeMeshPointsRuntime")
    return {MESH_POINTS_CAPABILITY_NAME: runtime}
