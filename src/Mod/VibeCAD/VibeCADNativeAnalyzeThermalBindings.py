# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM thermal conditions."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeThermalRuntime import NativeAnalyzeThermalRuntime
from VibeCADNativeAnalyzeThermalSchema import ANALYZE_THERMAL_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeThermalRuntime):
        raise TypeError("An Analyze thermal call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze thermal call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_thermal_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_THERMAL_CAPABILITY_NAME, _execute)
    )


def analyze_thermal_runtime_bindings(
    runtime: NativeAnalyzeThermalRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeThermalRuntime):
        raise TypeError("runtime must be a NativeAnalyzeThermalRuntime")
    return {ANALYZE_THERMAL_CAPABILITY_NAME: runtime}
