# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native Mesh input and regular solids."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshIORuntime import NativeMeshIORuntime
from VibeCADNativeMeshIOSchema import MESH_IO_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshIORuntime):
        raise TypeError("A Mesh I/O call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Mesh I/O call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_mesh_io_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MESH_IO_CAPABILITY_NAME, _execute)
    )


def mesh_io_runtime_bindings(runtime: NativeMeshIORuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshIORuntime):
        raise TypeError("runtime must be a NativeMeshIORuntime")
    return {MESH_IO_CAPABILITY_NAME: runtime}
