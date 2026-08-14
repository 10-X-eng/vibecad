# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Mesh Analyze reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshInspectRuntime import NativeMeshInspectRuntime
from VibeCADNativeMeshInspectSchema import MESH_INSPECT_CAPABILITY_NAME


def _inspect(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshInspectRuntime):
        raise TypeError("A Mesh inspection call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Mesh inspection call requires argument data.")
    return runtime.inspect(arguments)


def register_mesh_inspect_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MESH_INSPECT_CAPABILITY_NAME, _inspect)
    )


def mesh_inspect_runtime_bindings(runtime: NativeMeshInspectRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshInspectRuntime):
        raise TypeError("runtime must be a NativeMeshInspectRuntime")
    return {MESH_INSPECT_CAPABILITY_NAME: runtime}
