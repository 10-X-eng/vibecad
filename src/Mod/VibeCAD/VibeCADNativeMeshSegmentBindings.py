# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for retained Mesh segment operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshSegmentRuntime import NativeMeshSegmentRuntime
from VibeCADNativeMeshSegmentSchema import MESH_SEGMENT_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeMeshSegmentRuntime):
        raise TypeError("A Mesh segment call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Mesh segment call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_mesh_segment_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MESH_SEGMENT_CAPABILITY_NAME, _execute)
    )


def mesh_segment_runtime_bindings(runtime: NativeMeshSegmentRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeMeshSegmentRuntime):
        raise TypeError("runtime must be a NativeMeshSegmentRuntime")
    return {MESH_SEGMENT_CAPABILITY_NAME: runtime}
