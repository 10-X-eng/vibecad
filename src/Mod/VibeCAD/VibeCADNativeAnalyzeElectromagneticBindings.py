# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM electromagnetic constraint mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeElectromagneticRuntime import (
    NativeAnalyzeElectromagneticRuntime,
)
from VibeCADNativeAnalyzeElectromagneticSchema import (
    ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeElectromagneticRuntime):
        raise TypeError("An Analyze electromagnetic call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze electromagnetic call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_electromagnetic_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            _execute,
        )
    )


def analyze_electromagnetic_runtime_bindings(
    runtime: NativeAnalyzeElectromagneticRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeElectromagneticRuntime):
        raise TypeError("runtime must be a NativeAnalyzeElectromagneticRuntime")
    return {ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME: runtime}
