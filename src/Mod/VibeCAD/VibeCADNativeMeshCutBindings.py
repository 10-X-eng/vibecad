# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for retained Mesh cuts and sections."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshCutRuntime import NativeMeshCutRuntime
from VibeCADNativeMeshCutSchema import MESH_CUT_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshCutRuntime):
        raise TypeError("A Mesh cut call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Mesh cut call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_mesh_cut_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MESH_CUT_CAPABILITY_NAME, _execute)
    )


def mesh_cut_runtime_bindings(runtime: NativeMeshCutRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshCutRuntime):
        raise TypeError("runtime must be a NativeMeshCutRuntime")
    return {MESH_CUT_CAPABILITY_NAME: runtime}
