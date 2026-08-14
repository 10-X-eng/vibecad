# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for durable FEM result visualizations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeVisualizationRuntime import (
    NativeAnalyzeVisualizationRuntime,
)
from VibeCADNativeAnalyzeVisualizationSchema import (
    ANALYZE_VISUALIZATION_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeAnalyzeVisualizationRuntime):
        raise TypeError("An Analyze visualization call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze visualization call requires argument data.")
    return runtime.execute(arguments, ticket=ticket)


def register_analyze_visualization_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ANALYZE_VISUALIZATION_CAPABILITY_NAME,
            _execute,
        )
    )


def analyze_visualization_runtime_bindings(
    runtime: NativeAnalyzeVisualizationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeVisualizationRuntime):
        raise TypeError("runtime must be a NativeAnalyzeVisualizationRuntime")
    return {ANALYZE_VISUALIZATION_CAPABILITY_NAME: runtime}
