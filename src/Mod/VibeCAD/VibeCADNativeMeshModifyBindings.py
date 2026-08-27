# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for exact retained Mesh modifications."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshModifyRuntime import NativeMeshModifyRuntime
from VibeCADNativeMeshModifySchema import MESH_MODIFY_CAPABILITY_NAMES


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshModifyRuntime):
        raise TypeError("A Mesh modification call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Mesh modification call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_mesh_modify_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in MESH_MODIFY_CAPABILITY_NAMES:
        registry.register_implementation(
            NativeCapabilityImplementation(name, _execute)
        )


def mesh_modify_runtime_bindings(runtime: NativeMeshModifyRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshModifyRuntime):
        raise TypeError("runtime must be a NativeMeshModifyRuntime")
    return {name: runtime for name in MESH_MODIFY_CAPABILITY_NAMES}
