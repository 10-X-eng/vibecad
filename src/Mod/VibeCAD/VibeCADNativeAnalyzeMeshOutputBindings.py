# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for FEM mesh output operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeMeshOutputRuntime import NativeAnalyzeMeshOutputRuntime
from VibeCADNativeAnalyzeMeshOutputSchema import ANALYZE_MESH_OUTPUT_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeAnalyzeMeshOutputRuntime):
        raise TypeError("An Analyze mesh-output call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze mesh-output call requires argument data.")
    return runtime.execute(arguments, ticket=ticket)


def register_analyze_mesh_output_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_MESH_OUTPUT_CAPABILITY_NAME, _execute)
    )


def analyze_mesh_output_runtime_bindings(
    runtime: NativeAnalyzeMeshOutputRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeMeshOutputRuntime):
        raise TypeError("runtime must be a NativeAnalyzeMeshOutputRuntime")
    return {ANALYZE_MESH_OUTPUT_CAPABILITY_NAME: runtime}
