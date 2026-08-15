# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for human-authorized Native Mesh export."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshExportRuntime import NativeMeshExportRuntime
from VibeCADNativeMeshExportSchema import MESH_EXPORT_CAPABILITY_NAME


def _export(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshExportRuntime):
        raise TypeError("A Mesh export call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Mesh export call requires argument data.")
    return runtime.export(arguments, getattr(call, "ticket", None))


def register_mesh_export_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MESH_EXPORT_CAPABILITY_NAME, _export)
    )


def mesh_export_runtime_bindings(
    runtime: NativeMeshExportRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshExportRuntime):
        raise TypeError("runtime must be a NativeMeshExportRuntime")
    return {MESH_EXPORT_CAPABILITY_NAME: runtime}
