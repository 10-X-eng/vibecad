# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for FEM result graph operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeResultsRuntime import NativeAnalyzeResultsRuntime
from VibeCADNativeAnalyzeResultsSchema import ANALYZE_RESULTS_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeAnalyzeResultsRuntime):
        raise TypeError("An Analyze results call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze results call requires argument data.")
    return runtime.execute(arguments, ticket=ticket)


def register_analyze_results_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_RESULTS_CAPABILITY_NAME, _execute)
    )


def analyze_results_runtime_bindings(
    runtime: NativeAnalyzeResultsRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeResultsRuntime):
        raise TypeError("runtime must be a NativeAnalyzeResultsRuntime")
    return {ANALYZE_RESULTS_CAPABILITY_NAME: runtime}
