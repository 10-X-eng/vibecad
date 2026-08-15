# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM geometrical analysis features."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeGeometricalRuntime import NativeAnalyzeGeometricalRuntime
from VibeCADNativeAnalyzeGeometricalSchema import (
    ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeGeometricalRuntime):
        raise TypeError("An Analyze geometrical-feature call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze geometrical-feature call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_geometrical_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            _execute,
        )
    )


def analyze_geometrical_runtime_bindings(
    runtime: NativeAnalyzeGeometricalRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeGeometricalRuntime):
        raise TypeError("runtime must be a NativeAnalyzeGeometricalRuntime")
    return {ANALYZE_GEOMETRICAL_CAPABILITY_NAME: runtime}

