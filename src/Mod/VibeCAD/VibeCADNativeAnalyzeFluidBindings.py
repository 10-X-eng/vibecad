# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM fluid constraint mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeFluidRuntime import NativeAnalyzeFluidRuntime
from VibeCADNativeAnalyzeFluidSchema import ANALYZE_FLUID_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeFluidRuntime):
        raise TypeError("An Analyze fluid call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze fluid call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_fluid_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_FLUID_CAPABILITY_NAME, _execute)
    )


def analyze_fluid_runtime_bindings(
    runtime: NativeAnalyzeFluidRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeFluidRuntime):
        raise TypeError("runtime must be a NativeAnalyzeFluidRuntime")
    return {ANALYZE_FLUID_CAPABILITY_NAME: runtime}
