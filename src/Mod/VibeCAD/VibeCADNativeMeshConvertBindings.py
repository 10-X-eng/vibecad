# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for exact retained Mesh conversions."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshConvertRuntime import NativeMeshConvertRuntime
from VibeCADNativeMeshConvertSchema import MESH_CONVERT_CAPABILITY_NAMES


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshConvertRuntime):
        raise TypeError("A Mesh conversion call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Mesh conversion call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_mesh_convert_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in MESH_CONVERT_CAPABILITY_NAMES:
        registry.register_implementation(NativeCapabilityImplementation(name, _execute))


def mesh_convert_runtime_bindings(runtime: NativeMeshConvertRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshConvertRuntime):
        raise TypeError("runtime must be a NativeMeshConvertRuntime")
    return {name: runtime for name in MESH_CONVERT_CAPABILITY_NAMES}
