# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native structured mesh resources."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeStructuredMeshRuntime import NativeAnalyzeStructuredMeshRuntime
from VibeCADNativeAnalyzeStructuredMeshSchema import (
    ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation, NativeCapabilityRegistry


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeStructuredMeshRuntime):
        raise TypeError("A structured mesh call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A structured mesh call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_structured_mesh_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME, _execute)
    )


def analyze_structured_mesh_runtime_bindings(
    runtime: NativeAnalyzeStructuredMeshRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeStructuredMeshRuntime):
        raise TypeError("runtime must be a NativeAnalyzeStructuredMeshRuntime")
    return {ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME: runtime}
