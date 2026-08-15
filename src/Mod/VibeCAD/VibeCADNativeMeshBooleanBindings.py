# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for retained Mesh booleans."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshBooleanRuntime import NativeMeshBooleanRuntime
from VibeCADNativeMeshBooleanSchema import MESH_BOOLEAN_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshBooleanRuntime):
        raise TypeError("A Mesh boolean call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Mesh boolean call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_mesh_boolean_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MESH_BOOLEAN_CAPABILITY_NAME, _execute)
    )


def mesh_boolean_runtime_bindings(runtime: NativeMeshBooleanRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshBooleanRuntime):
        raise TypeError("runtime must be a NativeMeshBooleanRuntime")
    return {MESH_BOOLEAN_CAPABILITY_NAME: runtime}
