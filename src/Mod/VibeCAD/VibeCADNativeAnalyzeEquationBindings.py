# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for exact Native Elmer equation creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeEquationRuntime import NativeAnalyzeEquationRuntime
from VibeCADNativeAnalyzeEquationSchema import ANALYZE_EQUATION_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation, NativeCapabilityRegistry


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeEquationRuntime):
        raise TypeError("An Analyze equation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze equation call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_equation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_EQUATION_CAPABILITY_NAME, _execute)
    )


def analyze_equation_runtime_bindings(
    runtime: NativeAnalyzeEquationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeEquationRuntime):
        raise TypeError("runtime must be a NativeAnalyzeEquationRuntime")
    return {ANALYZE_EQUATION_CAPABILITY_NAME: runtime}
