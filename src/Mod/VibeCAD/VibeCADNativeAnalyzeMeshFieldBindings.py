# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native Gmsh refinement fields."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeMeshFieldRuntime import NativeAnalyzeMeshFieldRuntime
from VibeCADNativeAnalyzeMeshFieldSchema import ANALYZE_MESH_FIELD_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation, NativeCapabilityRegistry


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeMeshFieldRuntime):
        raise TypeError("An Analyze mesh-field call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze mesh-field call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_mesh_field_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_MESH_FIELD_CAPABILITY_NAME, _execute)
    )


def analyze_mesh_field_runtime_bindings(
    runtime: NativeAnalyzeMeshFieldRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeMeshFieldRuntime):
        raise TypeError("runtime must be a NativeAnalyzeMeshFieldRuntime")
    return {ANALYZE_MESH_FIELD_CAPABILITY_NAME: runtime}
