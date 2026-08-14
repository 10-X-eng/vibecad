# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native paired FEM connections."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeConnectionRuntime import NativeAnalyzeConnectionRuntime
from VibeCADNativeAnalyzeConnectionSchema import ANALYZE_CONNECTION_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeConnectionRuntime):
        raise TypeError("An Analyze connection call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze connection call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_connection_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_CONNECTION_CAPABILITY_NAME, _execute)
    )


def analyze_connection_runtime_bindings(
    runtime: NativeAnalyzeConnectionRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeConnectionRuntime):
        raise TypeError("runtime must be a NativeAnalyzeConnectionRuntime")
    return {ANALYZE_CONNECTION_CAPABILITY_NAME: runtime}
