# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM mesh refinements."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeMeshRefinementRuntime import NativeAnalyzeMeshRefinementRuntime
from VibeCADNativeAnalyzeMeshRefinementSchema import (
    ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation, NativeCapabilityRegistry


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeMeshRefinementRuntime):
        raise TypeError("An Analyze mesh-refinement call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze mesh-refinement call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_mesh_refinement_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME, _execute)
    )


def analyze_mesh_refinement_runtime_bindings(
    runtime: NativeAnalyzeMeshRefinementRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeMeshRefinementRuntime):
        raise TypeError("runtime must be a NativeAnalyzeMeshRefinementRuntime")
    return {ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME: runtime}
